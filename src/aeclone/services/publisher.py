"""发布流水线：产品 → 属性/变种 → AI 文本 → 图片 → 价格 → 上传 → 保存。

默认 dry-run：只做规划与本地计算，不写店小秘；真实保存需显式 dry_run=False
且店小秘接口已在 dianxiaomi.ENDPOINTS 校正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .text_pipeline import TextPipeline
from .image_pipeline import ImagePipeline
from .variants import auto_map, check_consistency, limit_skus
from .progress import BUS, emit


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


_FAIL_WORDS = ("失败", "错误", "不能", "只有", "无效", "不存在", "未找到", "不允许",
               "异常", "未填写", "请填写", "不能为空", "必填",
               "isv.", "error", "failed", "invalid", "not allowed", "cannot")


def is_failure_msg(msg: str) -> bool:
    """店小秘有些接口 code=0 但 data.msg 是「操作失败，只有处在采集箱中的产品…」。

    只判断 code==0 会把这类失败当成成功，导致「日志说成功、列表里找不到商品」。
    """
    m = str(msg or "").strip()
    if not m:
        return False
    # 成功语义白名单：店小秘会用「产品已提交发布，请在『发布中』、『发布失败』
    # 或『在线产品』中查看」这种带状态名的句子，里面含「失败」字样，
    # 不能据此判失败。
    _ok_marks = ("已提交发布", "已提交", "提交成功", "发布成功", "保存成功",
                 "操作成功", "success")
    low_all = m.lower()
    if any(k in m or k in low_all for k in _ok_marks):
        return False
    low = m.lower()
    return any(w in m or w in low for w in _FAIL_WORDS)


def is_audit_pending(message: str) -> bool:
    """速卖通「商品审核中」的保存失败——应跳过，不算失败。"""
    m = str(message or "").upper()
    return ("PRODUCT_IN_AUDIT_PENDING" in m) or ("PENDING IN AUDIT" in m)


@dataclass
class PublishResult:
    product_id: str
    ok: bool
    steps: list[Step] = field(default_factory=list)
    error: str = ""
    # 商品在速卖通审核中：不算失败，下次再处理
    skipped_auditing: bool = False
    preview: bool = False

    @property
    def incomplete_steps(self) -> list[Step]:
        return [step for step in self.steps if not step.ok]

    @property
    def completion_label(self) -> str:
        if self.preview:
            return "预演完成（未保存商品）" if self.ok else "预演失败（未保存商品）"
        if not self.ok:
            return "编辑失败"
        if self.incomplete_steps:
            return f"已保存，{len(self.incomplete_steps)} 项处理未完成"
        return "编辑成功"

    def log(self) -> str:
        lines = [f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}" for s in self.steps]
        return "\n".join(lines)


@dataclass
class SaveResult:
    ok: bool
    message: str
    title: str = ""
    published: bool = False
    pending: bool = False       # 平台处理中/结果待确认，既不成功也不失败
    steps: list = field(default_factory=list)


# 模板商品 edit.json 的进程内缓存：批量跑时同一模板只取一次（省一次 API 往返）
# 注意：用锁防止并发批量时多线程同时获取同一模板，避免重复 API 请求。
import threading as _threading
_TPL_EDIT_CACHE: dict[str, tuple[float, dict]] = {}
_TPL_EDIT_LOCK = _threading.Lock()
_TPL_EDIT_TTL = 600.0


def _cached_template_edit(dxm, tpl_pid: str) -> dict:
    import time as _t
    key = str(tpl_pid)
    with _TPL_EDIT_LOCK:
        hit = _TPL_EDIT_CACHE.get(key)
        if hit and (_t.time() - hit[0]) < _TPL_EDIT_TTL:
            return dict(hit[1])
    # 锁外发请求，避免持锁期间阻塞其他线程
    tpl = dxm._get("product_detail", {"id": key}).get("data", {}).get("product", {}) or {}
    if tpl:
        with _TPL_EDIT_LOCK:
            _TPL_EDIT_CACHE[key] = (_t.time(), dict(tpl))
    return dict(tpl)


SCALE_RULE = ("如画面中出现人物：人物身高必须与机械设备真实比例一致（人物不得高于设备主体），"
             "操作姿势与站位合理，严禁人比机器还高。")


def with_scale_rule(prompt: str) -> str:
    """给含人物的图片提示词补一条真实比例约束（机械设备尤其重要）。"""
    p = str(prompt or "")
    if not p:
        return p
    low = p.lower()
    if any(w in p for w in ("人", "女", "男", "工人", "操作员")) or any(
            w in low for w in ("person", "people", "man", "woman", "worker", "operator")):
        if "比例" not in p:
            p = p.rstrip() + "\n" + SCALE_RULE
    return p


def add_step(res, name, ok, detail=""):
    """记录步骤结果，同时实时上报到进度总线。"""
    res.steps.append(Step(name, bool(ok), str(detail)))
    emit(str(name), "", str(detail)[:140], "OK" if ok else "ERROR",
         product=str(getattr(res, "product_id", "") or ""))
    if ok:
        BUS.done_stage()


def parse_run_at(text: str) -> float:
    """把「2026-11-12 09:00」/「2026-11-12T09:00」解析成时间戳（本地时区）。"""
    import datetime as _dt
    import time as _t
    raw = str(text or "").strip().replace("T", " ")
    if not raw:
        raise ValueError("定时时间为空")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m-%d %H:%M"):
        try:
            dt = _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if dt.year == 1900:
            now = _dt.datetime.now()
            dt = dt.replace(year=now.year)
            if dt < now:
                dt = dt.replace(year=now.year + 1)
        return _t.mktime(dt.timetuple())
    raise ValueError(f"时间格式不对：{text}（示例 2026-11-12 09:00）")


_parse_run_at = parse_run_at   # 兼容内部旧名


class Publisher:
    def __init__(self, cfg, dxm=None, text_service=None, image_service=None, ws_endpoint: str = "",
                 scheduler=None):
        self.cfg = cfg
        self.dxm = dxm
        self.text = text_service
        self.image = image_service
        self.ws_endpoint = ws_endpoint
        self.scheduler = scheduler
        self.image_pipeline = ImagePipeline(cfg)
        self._img_svc = image_service
        self._size_rejects: list[tuple[str, str]] = []
        self._album_urls: set[str] = set()

    # ---------- 单步 ----------
    def _normalize(self, product: dict) -> dict:
        # Keep the source snapshot intact for write-back comparison and retries.
        from copy import deepcopy
        p = deepcopy(product)
        p["title"] = (p.get("title") or "").strip()
        prefix = (getattr(self.cfg, "custom_sku_prefix", "") or "").strip()
        if not getattr(self.cfg, "custom_sku_prefix_enable", True):
            prefix = ""
        only_prefix = getattr(self.cfg, "only_sku_prefix_checkbox", False)
        split_ch = getattr(self.cfg, "sku_split_char", "-") or "-"
        stock_value = getattr(self.cfg, "stock_quantity", "")
        stock = str(stock_value if stock_value is not None else "").strip()
        for i, sku in enumerate(p.get("skus") or [], 1):
            name = str(sku.get("sku") or sku.get("name") or sku.get("skuCode") or "").strip()
            # 记住原始名字：后面可能被前缀/自动编码改名，SKU 图替换要能用原名匹配
            if name and not sku.get("original_sku"):
                sku["original_sku"] = name
            if prefix and only_prefix:
                # 「只用前缀」= 不保留原 SKU 名，直接 前缀-序号
                name = f"{prefix}{split_ch}{i:03d}"
            elif prefix and not name.startswith(prefix):
                name = f"{prefix}{split_ch}{name}" if name else f"{prefix}{split_ch}{i:03d}"
            if getattr(self.cfg, "auto_generate_sku_code", False):
                name = f"{prefix}{split_ch}{i:03d}" if prefix else f"SKU{split_ch}{i:03d}"
            sku["sku"] = name
            if stock:
                sku["stock"] = stock
        if getattr(self.cfg, "combo_enable", False):
            from .combo import expand as _combo_expand
            try:
                factor = float(getattr(self.cfg, "combo_multiplier", "2") or 2)
            except Exception:
                factor = 2.0
            pref = str(getattr(self.cfg, "combo_prefix", "套装") or "套装").strip() or "套装"
            fallback = None
            try:
                fallback = float(p.get("cost") or getattr(self.cfg, "goods_cost", 0) or 0) or None
            except Exception:
                fallback = None
            p["skus"] = _combo_expand(list(p.get("skus") or []), pref, factor, fallback)
        p["skus"] = p.get("skus") or []
        p["images"] = p.get("images") or []
        return p

    def _template_values_cached(self, tpl_pid: str) -> dict:
        """读模板产品的完整表单值，进程内缓存（同一批只读一次）。"""
        _c = getattr(self, "_tpl_cache", None)
        if _c is None:
            self._tpl_cache = {}
            _c = self._tpl_cache
        if tpl_pid in _c:
            return _c[tpl_pid]
        vals: dict = {}
        try:
            if self.dxm is not None:
                _tpl = _cached_template_edit(self.dxm, tpl_pid)
                if _tpl:
                    # 从已缓存的 edit.json 直接构造，避免再发一次请求
                    import json as _jj
                    amap = {}
                    try:
                        for _a in _jj.loads(_tpl.get("aeopAeProductPropertys") or "[]"):
                            if isinstance(_a, dict) and _a.get("attrName"):
                                amap[str(_a["attrName"]).lower()] = str(_a.get("attrValue") or "")
                    except Exception:
                        pass
                    if amap.get("brand name") or amap.get("brand"):
                        vals["品牌"] = {"t": "text", "v": amap.get("brand name") or amap.get("brand")}
                    if amap.get("origin") or amap.get("place of origin"):
                        vals["产地"] = {"t": "text", "v": amap.get("origin") or amap.get("place of origin")}
                    if _tpl.get("grossWeight"):
                        vals["包装后重量"] = {"t": "text", "v": str(_tpl.get("grossWeight"))}
                    _sz = "x".join(str(_tpl.get(k) or "") for k in ("packageLength", "packageWidth", "packageHeight"))
                    if _sz.strip("x"):
                        vals["包装后尺寸"] = {"t": "text", "v": _sz}
                    if _tpl.get("productManual"):
                        vals["商品说明书"] = {"t": "file", "v": str(_tpl.get("productManual"))}
                else:
                    snap = self.dxm.template_snapshot(tpl_pid) or {}
                    vals = snap.get("values") or {}
        except Exception:
            vals = {}
        _c[tpl_pid] = vals
        return vals

    def _form_values(self, product: dict | None = None) -> dict:
        """模板抓取到的表单值 + 欧盟资质/责任人（编辑器按 label 回填）。"""
        import json as _jf
        try:
            fv = _jf.loads(getattr(self.cfg, "template_values", "") or "{}")
            if not isinstance(fv, dict):
                fv = {}
        except Exception:
            fv = {}
        fv["__eu__"] = getattr(self.cfg, "eu_qualification", "否")
        fv["__eu_name__"] = getattr(self.cfg, "eu_responsible_name", "")
        fv["__eu_address__"] = getattr(self.cfg, "eu_responsible_address", "")
        fv["__eu_email__"] = getattr(self.cfg, "eu_responsible_email", "")
        fv["__eu_phone__"] = getattr(self.cfg, "eu_responsible_phone", "")
        # 草稿商品缺的必填项：货值 / 是否原箱 / 服务模板 / 运费模板（能填就填）
        # 货值：配置为空时给个安全默认值（草稿必填，不填就存不进去）
        fv["__declared__"] = str(getattr(self.cfg, "declared_value", "") or "").strip() or "50"
        fv["__original_box__"] = "是" if getattr(self.cfg, "original_box", False) else "否"
        _st_tpl = str(getattr(self.cfg, "service_template", "") or "").strip() \
            or "Service Template for New Sellers"
        fv["__service_template__"] = _st_tpl
        # 类目属性默认值（模板读不到时用）
        fv["__high_concerned__"] = str(getattr(self.cfg, "high_concerned_chemical", "否") or "否")
        fv["__charge_status__"] = str(getattr(self.cfg, "charge_status", "否") or "否")
        # 保存后去向：save_only / save_to_waiting / publish
        fv["__publish_action__"] = str(getattr(self.cfg, "publish_action", "save_only") or "save_only")
        # 运费模板：配置为空时用商品自带的运费模板名（列表接口里的 shippingTempateName）
        _ft = str(getattr(self.cfg, "freight_template", "") or "").strip()
        if not _ft:
            _ft = str((product or {}).get("shipping_template") or "").strip()
        if not _ft:
            # 该店实际存在的模板名（只读探测得到的选项之一）
            _ft = "Shipping Cost Template for New Sellers"
        fv["__freight_template__"] = _ft
        return fv

    # ---------- 逐图编辑方案（图生图 / 多图素材）----------
    def _img_service(self):
        if self._img_svc is None:
            from ..api import ApiClient
            from . import ImageService
            # 图片接口给足 180 秒：qwen-image-3.0 正常 50~130 秒，
            # 60/120 秒都会偶发超时导致「图片没变化」；超时后仍会降级到 turbo
            _to = max(180.0, min(float(self.cfg.timeout or 60), 240.0))
            self._img_svc = ImageService(
                ApiClient(self.cfg.api_base, self.cfg.api_key, _to), self.cfg)
        return self._img_svc

    def _pmap(self, fn, items, workers: int | None = None):
        """小工具：按图片并发数并行执行，保持输入顺序；单张失败返回 None。

        中止支持：用户点「停止」后，还没开始的图片直接返回 None，
        不再白烧 AI 调用。
        """
        items = list(items)
        if not items:
            return []
        _raw_fn = fn

        def fn(x):  # noqa: F811
            if self._aborted():
                return None
            return _raw_fn(x)
        try:
            n = int(workers if workers is not None else getattr(self.cfg, "image_concurrency", 4) or 4)
        except Exception:
            n = 4
        n = max(1, min(8, n, len(items)))
        if n == 1:
            out = []
            for x in items:
                try:
                    out.append(fn(x))
                except Exception:
                    out.append(None)
            return out
        import concurrent.futures as _cf
        out = [None] * len(items)

        def run(i, x):
            try:
                out[i] = fn(x)
            except Exception:
                out[i] = None

        with _cf.ThreadPoolExecutor(max_workers=n) as ex:
            list(ex.map(lambda pair: run(*pair), list(enumerate(items))))
        return out

    def _prompt_model(self) -> str:
        """带用户提示词的改图统一用 3.0（turbo 会忽略提示词）。"""
        try:
            from . import model_of
            return model_of(self.cfg, "image_prompt")
        except Exception:
            return ""

    def _edit_with_prompt(self, url: str, prompt: str) -> str:
        """对单张 URL 用指定提示词做一次编辑，返回新 URL。"""
        _m = self._prompt_model()
        _act = f"edit:{_m}:" + (prompt or "")
        _hit = self._cache_get(url, _act, _m)
        if _hit:
            return _hit
        svc = self._img_service()
        fp = self._fetch(url)
        try:
            out = str(svc.edit(fp, prompt, model=_m or None))
        except TypeError:
            # 注入的旧版图片服务没有 model 参数
            out = str(svc.edit(fp, prompt))
        self._cache_put(url, _act, out, _m)
        return out

    # ---------- 图片结果缓存（同图同处理只算一次）----------
    def _cache_get(self, url: str, action: str, model: str = "") -> str | None:
        if not getattr(self.cfg, "image_cache_enable", True):
            return None
        try:
            from .image_cache import get as _get
            return _get(str(url), str(action), model or "",
                        int(getattr(self.cfg, "image_cache_days", 7) or 7))
        except Exception:
            return None

    def _cache_put(self, url: str, action: str, out: str, model: str = "") -> None:
        if not getattr(self.cfg, "image_cache_enable", True) or not out:
            return
        try:
            from .image_cache import put as _put
            _put(str(url), str(action), str(out), model or "")
        except Exception:
            pass

    def _img_model(self) -> str:
        try:
            from . import model_of
            return model_of(self.cfg, "image_edit")
        except Exception:
            return ""

    def _fetch(self, url: str, workdir=None):
        """下载图片到本地（供多图素材上传用）；本地路径直接返回。"""
        import tempfile
        from pathlib import Path as _P
        import requests as _rq
        s = str(url or "")
        if s and not s.startswith(("http://", "https://", "data:")):
            lp = _P(s)
            if lp.exists():
                return lp
        d = _P(workdir or tempfile.mkdtemp())
        name = s.split("?")[0].split("/")[-1] or "img.png"
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            name += ".png"
        fp = d / f"{abs(hash(url)) % 10**8}_{name}"
        if not fp.exists():
            r = _rq.get(s, timeout=60)
            r.raise_for_status()
            fp.write_bytes(r.content)
        return fp

    # ---------- 图片尺寸校验（速卖通 ≥800×800）----------
    def _min_size(self) -> int:
        try:
            return max(1, int(getattr(self.cfg, "img_min_size", 800) or 800))
        except Exception:
            return 800

    def _size_ok(self, url: str) -> tuple[bool, str]:
        """校验一张图的实际像素；用本地已下载文件，不额外联网。"""
        if not str(url or ""):
            return False, "图片地址为空"
        if not getattr(self.cfg, "img_min_size_enable", True):
            return True, "尺寸校验已关闭"
        try:
            from .image_validation import check
            # 复用「图片转存」阶段已经下载过的文件，避免再下一遍（慢一倍）
            _lf = (getattr(self, "_local_files", None) or {}).get(str(url))
            if _lf is not None:
                try:
                    from pathlib import Path as _Pf
                    if _Pf(_lf).exists():
                        return check(_Pf(_lf), self._min_size())
                except Exception:
                    pass
            d = getattr(self, "_size_tmp", None)
            if d is None or not getattr(d, "exists", lambda: False)():
                import tempfile
                from pathlib import Path as _P
                d = _P(tempfile.mkdtemp(prefix="ae_size_"))
                self._size_tmp = d
            return check(self._fetch(url, d), self._min_size())
        except Exception as ex:  # noqa: BLE001
            return False, f"无法读取尺寸：{str(ex)[:60]}"

    def _vet(self, url: str, fallback: str = "") -> str:
        """尺寸不合格就退回 fallback（通常是原图），合格则用新图。"""
        if not str(url or ""):
            return fallback or url
        ok, info = self._size_ok(url)
        if ok:
            return url
        try:
            self._size_rejects.append((str(url), str(info)))
        except Exception:
            pass
        return fallback or ""

    @staticmethod
    def _group_urls(p: dict, group: str) -> list[str]:
        if group == "sku":
            out = []
            for sku in p.get("skus") or []:
                out.append(str(sku.get("processed_image") or sku.get("image") or sku.get("imageUrl") or ""))
            return out
        key = "detail_images" if group == "detail" else "images"
        return [str(u) for u in (p.get(key) or [])]

    @staticmethod
    def _set_group_url(p: dict, group: str, idx: int, url: str) -> bool:
        if group == "sku":
            skus = p.get("skus") or []
            if 0 <= idx < len(skus):
                skus[idx]["processed_image"] = url
                return True
            return False
        key = "detail_images" if group == "detail" else "images"
        lst = list(p.get(key) or [])
        while len(lst) <= idx:
            lst.append("")
        lst[idx] = url
        p[key] = lst
        return True

    def _apply_simple_ops(self, url: str, ops: list[str]) -> str:
        """对单张图做简单处理（白底/去水印/翻译），返回新 URL。"""
        svc = self._img_service()
        ops = [o for o in (ops or []) if o in ("white", "watermark", "translate")]
        if "translate" in ops and getattr(self.cfg, "translate_image_understanding", False):
            pipeline = getattr(self, "image_pipeline", None)
            if pipeline is not None and not pipeline.has_text(str(url)):
                ops = [op for op in ops if op != "translate"]
        if not ops:
            return url
        import json
        _act = "simple:v2:" + json.dumps([sorted(ops), str(getattr(self.cfg, 'translate_prompt', ''))], ensure_ascii=False)
        _hit = self._cache_get(url, _act, self._img_model())
        if _hit:
            return _hit
        fp = self._fetch(url)
        if set(ops) == {"white", "watermark"}:
            out = str(svc.combined(fp))
            self._cache_put(url, _act, out, self._img_model())
            return out
        cur = url
        for op in ops:
            if op == "white":
                new = svc.white_background(fp)
            elif op == "watermark":
                new = svc.remove_watermark(fp)
            else:
                from .image_presets import translate_prompt
                new = svc.edit(fp, (getattr(self.cfg, "translate_prompt", "") or "").strip()
                               or translate_prompt("en"))
            cur = str(new)
            if ops[-1] != op:
                fp = self._fetch(cur)
        if cur != str(url):
            self._cache_put(url, _act, cur, self._img_model())
        return cur

    def _aborted(self) -> bool:
        """外部（界面「停止」按钮）请求中止时返回 True。"""
        cb = getattr(self, "should_stop", None)
        try:
            return bool(cb()) if callable(cb) else False
        except Exception:
            return False

    def _halt(self, res, where: str = ""):
        """被「停止」打断：标记结果并立即返回，不继续花钱/写商品。"""
        res.ok = False
        res.stopped = True
        res.error = "已按请求停止"
        add_step(res, "停止", True,
                 "已按请求停止" + (f"（在「{where}」之前中断）" if where else ""))
        return res

    def _run_interruptible(self, fn, timeout: float = 300):
        """在子线程里跑一个耗时调用，主线程每 0.2s 检查停止标记。

        返回 (status, value)：status = ok / stop / error。
        停止时直接返回，不等 API 超时（后台线程自行结束）。
        """
        import threading as _th
        box: dict = {}

        def _w():
            try:
                box["v"] = fn()
            except Exception as ex:  # noqa: BLE001
                box["e"] = ex

        t = _th.Thread(target=_w, daemon=True)
        t.start()
        waited = 0.0
        while t.is_alive():
            if self._aborted():
                return "stop", None
            t.join(0.2)
            waited += 0.2
            if waited >= timeout:
                return "error", TimeoutError("调用超时")
        if "e" in box:
            return "error", box["e"]
        return "ok", box.get("v")

    def _apply_image_jobs(self, p: dict, res, dry_run: bool = False) -> dict:
        """执行「简单规则 + 精细任务」（新引擎；旧配置自动迁移）。"""
        from .image_jobs import (parse_rules, parse_jobs, refs_indices, migrate,
                                 GROUP_LABELS, merged_jobs, rules_from_switches)
        # 简单编辑以界面开关为准（老规则可能是迁移残留，会导致“没勾也执行”）
        rules = rules_from_switches(self.cfg)
        _, jobs = merged_jobs(self.cfg)
        collage_jobs = [job for job in jobs if job.get("group") == "main"
                        and job.get("preset") == "四合一主图"]
        jobs = [job for job in jobs if job not in collage_jobs]
        collage = bool(getattr(self.cfg, "four_in_one", False) or collage_jobs)
        _mode = str(getattr(self.cfg, "image_edit_mode", "fine") or "fine").strip().lower()
        _general = _mode in ("general", "simple")
        _general_prompt = str(getattr(self.cfg, "img_general_prompt", "") or "").strip() \
            or str(getattr(self.cfg, "edit_prompt", "") or "").strip()
        if not rules and not jobs and not collage and not (_general and _general_prompt):
            try:
                rj, jj = migrate(self.cfg)
                # 简单处理只读取当前开关；已关闭的开关不能被旧规则重新开启。
                jobs = parse_jobs(jj)
            except Exception:
                rules, jobs = [], []
        if (getattr(self.cfg, "designated_edit_enable", False)
                and not str(getattr(self.cfg, "designated_edit_prompt", "") or "").strip()):
            add_step(res, "主图指定编辑", False, "已开启，但提示词为空，未生效")
        if not rules and not jobs and not collage and not (_general and _general_prompt):
            return p
        if dry_run:
            _parts = []
            if collage:
                _parts.append("四合一本地拼图（不调用 AI）")
            if _general:
                _a = str(getattr(self.cfg, "img_general_main_from", "1") or "1")
                _b = str(getattr(self.cfg, "img_general_main_to", "8") or "8")
                try:
                    _n = len(self._group_urls(p, "main"))
                    _ai = max(0, min(int(_b), _n) - max(1, int(_a)) + 1)
                except Exception:
                    _ai = 0
                _parts.append(f"通用编辑：主图 {_a}-{_b}，预计调用 AI {_ai} 次")
            if rules:
                _parts.append(f"简单规则 {len(rules)} 条")
            if jobs:
                _never = {
                    "main": bool(getattr(self.cfg, "img_fine_main_enable", True)),
                    "detail": bool(getattr(self.cfg, "img_fine_detail_enable", False)),
                    "sku": bool(getattr(self.cfg, "img_fine_sku_enable", False)),
                }
                _act = [j for j in jobs if _never.get(j["group"], False)]
                _parts.append(f"精细任务 {len(jobs)} 条（会执行 {len(_act)} 条）")
            add_step(res, "图片方案", True, "dry-run 跳过实际调用 · " + "；".join(_parts or ["无"]))
            return p
        if collage:
            position = (collage_jobs[0].get("insert") if collage_jobs
                        and not getattr(self.cfg, "four_in_one", False) else None)
            self._apply_four_in_one(p, res, enabled=True, position=position)

        svc = self._img_service()
        try:
            self._size_rejects = []
        except Exception:
            pass
        # ---- 简单规则（图片简单编辑：白底/去水印/翻译）----
        # 先按图片序号归并操作，保留各自范围，重叠处一次调用完成。
        from .image_index import parse_indices as _rule_indices
        grouped = {}
        for rule in rules:
            group = rule["group"]
            total = len(self._group_urls(p, group))
            for index in _rule_indices(rule.get("range") or "", total):
                ops = grouped.setdefault((group, index), [])
                ops.extend(op for op in rule["ops"] if op not in ops)
        batches = {}
        for (group, index), ops in grouped.items():
            batches.setdefault((group, tuple(ops)), []).append(index)
        rules = [{"group": group, "ops": list(ops),
                  "range": ",".join(str(i) for i in indices)}
                 for (group, ops), indices in batches.items()]
        for rule in rules:
            if self._aborted():
                add_step(res, "停止", True, "已按请求停止（图片处理中断）")
                return p
            group = rule["group"]
            urls = self._group_urls(p, group)
            if not urls:
                continue
            from .image_index import parse_indices as _pi
            idx = _pi(rule.get("range") or "1-20", len(urls)) or []
            # 空地址（比如 SKU 还没有图）直接跳过，别拿 "" 去下载/调用，白花一次失败
            _pairs = [(i, urls[i - 1]) for i in idx if str(urls[i - 1] or "").strip()]
            _skipped = len(idx) - len(_pairs)
            idx = [i for i, _u in _pairs]
            src = [u for _i, u in _pairs]
            if not src:
                add_step(res, f"简单处理·{GROUP_LABELS.get(group, group)}", True,
                         f"范围内没有可用图片（跳过 {_skipped} 个空地址）")
                continue
            vals = self._pmap(lambda u: self._apply_simple_ops(u, rule["ops"]), src)
            done = failed = 0
            rejected = 0
            for i, old_u, new in zip(idx, src, vals):
                if new and str(new) != str(old_u):
                    _vet_fn = getattr(self, "_vet", None)
                    vetted = (_vet_fn(str(new), str(old_u))
                              if callable(_vet_fn) else str(new))
                    if not vetted:
                        rejected += 1
                        continue
                    if str(vetted) != str(new):
                        rejected += 1
                    self._set_group_url(p, group, i - 1, str(vetted))
                    done += 1
                elif new is None:
                    failed += 1
            tail = f" / 尺寸不合格 {rejected}" if rejected else ""
            if _skipped:
                tail += f" / 跳过空图 {_skipped}"
            add_step(res, f"简单处理·{GROUP_LABELS.get(group, group)}", failed == 0 and done > 0,
                     f"成功 {done} / 失败 {failed}{tail}")

        # ---- 图片通用编辑：两种模式二选一----
        if _general:
            return self._apply_general_edit(p, res)

        # ---- 精细化编辑：按分组启用 + 逐图提示词 ----
        _group_enable = {
            "main": bool(getattr(self.cfg, "img_fine_main_enable", True)),
            "detail": bool(getattr(self.cfg, "img_fine_detail_enable", False)),
            "sku": bool(getattr(self.cfg, "img_fine_sku_enable", False)),
        }
        if not any(_group_enable.values()):
            add_step(res, "精细化编辑", True, "主图/详情图/SKU 图都未启用，跳过 AI 编辑")
            return p
        _detail_touched = False
        _ai_made_detail: set[str] = set()

        # ---- 精细任务 ----
        for n, job in enumerate(jobs, 1):
            if self._aborted():
                add_step(res, "停止", True, "已按请求停止（精细任务中断）")
                return p
            group = job["group"]
            if not _group_enable.get(group, False):
                add_step(res, f"精细任务{n}", True,
                         f"{GROUP_LABELS.get(group, group)} 编辑未启用，跳过该条任务")
                continue
            urls = self._group_urls(p, group)
            if not urls:
                continue
            tgt_i = int(job["index"]) - 1
            target = urls[tgt_i] if 0 <= tgt_i < len(urls) else ""
            refs = refs_indices(job.get("refs"), len(urls), exclude=int(job["index"]))
            ref_urls = [urls[r - 1] for r in refs if 0 <= r - 1 < len(urls) and urls[r - 1]]
            prompt = with_scale_rule(str(job.get("prompt") or "").strip())
            if not prompt:
                add_step(res, f"精细任务{n}", False, "提示词为空")
                continue
            if job["mode"] == "edit" and not target:
                add_step(res, f"精细任务{n}", False, f"{GROUP_LABELS.get(group, group)} 第{job['index']}张不存在")
                continue
            import json as _json
            count = max(1, min(3, int(job.get("count") or 1))) if job["mode"] == "generate" else 1
            # The cache stores one URL, so multi-output jobs must not reuse it.
            cacheable = count == 1
            _pm = self._prompt_model()
            _cache_action = "many:v3:" + _json.dumps(
                [_pm, job["mode"], prompt, target or "", ref_urls[:4],
                 job.get("ref_paths") or []], ensure_ascii=False)
            _hit = self._cache_get(target or (ref_urls[0] if ref_urls else ""), _cache_action,
                                   _pm) if cacheable and (target or ref_urls) else None
            if _hit:
                made = [_hit]
                insert = job.get("insert") or ("first" if job["mode"] == "generate" else "replace")
                if insert == "first":
                    urls.insert(0, _hit); self._set_group_list(p, group, urls)
                elif insert == "last":
                    urls.append(_hit); self._set_group_list(p, group, urls)
                else:
                    self._set_group_url(p, group, tgt_i, _hit)
                add_step(res, f"精细任务{n}", True, "命中缓存（同一张图之前处理过）")
                if group == "detail":
                    _detail_touched = True
                    _ai_made_detail.add(str(_hit))
                continue
            try:
                import tempfile as _tf
                from pathlib import Path as _P
                workdir = _P(_tf.mkdtemp())
                files = []
                # 先本地参考图（图1~图N），再产品原图（参考图引用顺序）
                for _lp in (job.get("ref_paths") or [])[:5]:
                    try:
                        _p = _P(str(_lp))
                        if _p.exists():
                            files.append(_p)
                    except Exception:
                        pass
                if job["mode"] == "edit" and target:
                    files.append(self._fetch(target, workdir))
                for u in ref_urls[:4]:
                    files.append(self._fetch(u, workdir))
                if not files and target:
                    files.append(self._fetch(target, workdir))
                if not files:
                    raise RuntimeError("没有可用素材图")
                made = []
                # 四合一已在本地处理；普通任务按模型输入上限执行。
                _use = list(files)[:3]
                if len(files) > 3:
                    # 这是模型的已知输入上限，不是失败；自动截取前 3 张继续出图。
                    add_step(res, f"精细任务{n}·素材", True,
                             f"模型最多接受 3 张输入，已自动使用前 3 张（原 {len(files)} 张）")
                for _ in range(count):
                    try:
                        u = svc.edit_many(_use, prompt,
                                          tag=f"精细任务{n}·{GROUP_LABELS.get(group, group)}",
                                          model=_pm or None)
                    except TypeError:
                        u = svc.edit_many(_use, prompt,
                                          tag=f"精细任务{n}·{GROUP_LABELS.get(group, group)}")
                    if u:
                        made.append(str(u))
                if not made:
                    raise RuntimeError("接口未返回图片")
            except Exception as ex:  # noqa: BLE001
                try:
                    from ..api import parse_balance as _pb
                    _bal = _pb(ex)
                except Exception:
                    _bal = None
                if _bal is not None:
                    # 余额不足：不算失败，跳过 AI 编辑并记下余额，供 UI 提示充值
                    try:
                        self.cfg.set("api_balance", _bal)
                    except Exception:
                        pass
                    self._quota_low = _bal
                    add_step(res, f"精细任务{n}", True,
                             f"余额不足（剩 ¥{_bal:.3f}），本次跳过 AI 图片编辑")
                    continue
                _em = str(ex)
                if any(k in _em.lower() for k in ("timeout", "timed out", "read timed out")):
                    add_step(res, f"精细任务{n}", False,
                             "AI 生图超时（模型响应慢），已保留原图；可稍后重跑这一张")
                else:
                    add_step(res, f"精细任务{n}", False, _em[:100])
                continue
            # 尺寸校验：不合格的产出直接不要（replace 时保留原图）
            _rej = 0
            _raw = list(made)
            made = []
            _size_fn = getattr(self, "_size_ok", None)
            for u in _raw:
                if callable(_size_fn):
                    ok, info = _size_fn(u)
                else:
                    ok, info = True, "跳过尺寸校验"
                if ok:
                    made.append(u)
                else:
                    _rej += 1
                    try:
                        self._size_rejects.append((str(u), str(info)))
                    except Exception:
                        pass
            if _rej:
                try:
                    _ms = int(getattr(self, "_min_size", lambda: 800)())
                except Exception:
                    _ms = 800
                add_step(res, f"精细任务{n}·尺寸", False,
                         f"{_rej} 张低于 {_ms}px 未采用（{info[:40]}）")
            if not made:
                add_step(res, f"精细任务{n}", False, "产出图尺寸全部不合格，已保留原图")
                continue
            insert = job.get("insert") or ("first" if job["mode"] == "generate" else "replace")
            if insert == "first":
                for u in reversed(made):
                    urls.insert(0, u)
                self._set_group_list(p, group, urls)
            elif insert == "last":
                urls.extend(made)
                self._set_group_list(p, group, urls)
            else:
                for k, u in enumerate(made):
                    self._set_group_url(p, group, tgt_i + k, u)
            if made and cacheable:
                self._cache_put(target or (ref_urls[0] if ref_urls else ""), _cache_action,
                                made[0], _pm)
            add_step(res, f"精细任务{n}", True,
                     f"{GROUP_LABELS.get(group, group)} · 素材 {len(files)} 张 · "
                     + ("插到第1张" if insert == "first" else "放到最后" if insert == "last" else "替换原图")
                     + (f" · 模型 {_pm}" if _pm else ""))
            if group == "detail" and made:
                _detail_touched = True
                _ai_made_detail.update(str(u) for u in made)

        # 详情图「删除原有详情图，仅保留编辑结果」
        if _detail_touched and bool(getattr(self.cfg, "img_fine_detail_replace", False)):
            _d = [str(u) for u in (p.get("detail_images") or []) if str(u)]
            _keep = [u for u in _d if u in _ai_made_detail]
            if _keep and len(_keep) < len(_d):
                p["detail_images"] = _keep
                add_step(res, "详情图仅保留 AI 结果", True,
                         f"删除原有 {len(_d) - len(_keep)} 张，保留 {len(_keep)} 张")
        return p

    def _keep_local(self, p: dict, res=None) -> None:
        """开关 keep_main_images_local：把本次处理过的主图另存到本地，方便留档。

        只写本地文件，不改商品、不改 URL，出错也不影响上架。
        """
        if not getattr(self.cfg, "keep_main_images_local", False):
            return
        try:
            from ..config import config_dir
            from pathlib import Path as _P
            out_dir = _P(config_dir()) / "kept_images" / str(p.get("id") or "product")
            out_dir.mkdir(parents=True, exist_ok=True)
            saved = 0
            for i, u in enumerate((p.get("images") or [])[:8], 1):
                try:
                    fp = self._fetch(str(u))
                    ext = fp.suffix or ".png"
                    dst = out_dir / f"{i:02d}_{abs(hash(str(u))) % 10**8}{ext}"
                    if not dst.exists():
                        dst.write_bytes(fp.read_bytes())
                    saved += 1
                except Exception:
                    continue
            if saved and res is not None:
                add_step(res, "本地留档", True, f"{saved} 张 → {out_dir}")
        except Exception as ex:  # noqa: BLE001
            if res is not None:
                add_step(res, "本地留档", False, f"保存失败：{str(ex)[:60]}")

    def _apply_general_edit(self, p: dict, res) -> dict:
        """图片通用编辑：一条提示词按范围逐张编辑主图（通用编辑模式）。"""
        prompt = str(getattr(self.cfg, "img_general_prompt", "") or "").strip() \
            or str(getattr(self.cfg, "edit_prompt", "") or "").strip()
        if not prompt:
            add_step(res, "通用编辑", False, "提示词为空，跳过")
            return p
        urls = self._group_urls(p, "main")
        if not urls:
            add_step(res, "通用编辑", True, "没有主图，跳过")
            return p

        def _int(name: str, default: int) -> int:
            try:
                return int(str(getattr(self.cfg, name, "") or "").strip())
            except Exception:
                return default

        a = max(1, _int("img_general_main_from", 1))
        b = _int("img_general_main_to", len(urls))
        b = min(max(b, a), len(urls))
        if a > len(urls):
            add_step(res, "通用编辑", True, f"主图只有 {len(urls)} 张，范围 {a}-{b} 无图可编辑")
            return p
        idx = list(range(a, b + 1))
        src = [urls[i - 1] for i in idx]
        vals = self._pmap(lambda u: self._edit_with_prompt(u, prompt), src)
        done = failed = rejected = 0
        for i, old_u, new in zip(idx, src, vals):
            if new and str(new) != str(old_u):
                _vet_fn = getattr(self, "_vet", None)
                vetted = (_vet_fn(str(new), str(old_u))
                          if callable(_vet_fn) else str(new))
                if not vetted or str(vetted) != str(new):
                    rejected += 1
                if not vetted:
                    continue
                self._set_group_url(p, "main", i - 1, str(vetted))
                done += 1
            elif new is None:
                failed += 1
        tail = f" / 尺寸不合格 {rejected}（已保留原图）" if rejected else ""
        add_step(res, "通用编辑·主图", failed == 0 and done > 0,
                 f"范围 {a}-{b} · 成功 {done} / 失败 {failed}{tail}")
        return p

    @staticmethod
    def _set_group_list(p: dict, group: str, urls: list[str]) -> None:
        if group == "sku":
            skus = p.get("skus") or []
            for sku, u in zip(skus, urls):
                if u:
                    sku["processed_image"] = u
            return
        key = "detail_images" if group == "detail" else "images"
        p[key] = list(urls)

    # 兼容旧名（测试与老调用）
    def _apply_template(self, p: dict, res) -> dict:
        """套用模板快照：类目/运费/属性（属性缺失时用模板的）。"""
        import json as _j
        try:
            snap = _j.loads(getattr(self.cfg, "template_snapshot", "") or "{}")
        except Exception:
            snap = {}
        fields = snap.get("fields") or {}
        if not fields:
            _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
            add_step(res, "模板参数", True,
                     "模板由 API 直存自动合并（无需要单独套用）" if _tid
                     else "未选模板：请到「商品设置 → 模板」选择模板商品")
            return p
        p["template_fields"] = fields
        # 模板只作为「类目/运费/重量/尺寸等字段」的参考；自定义属性不复制到新产品，
        # 否则新产品会多出一整套重复属性。
        p["template_attributes"] = []
        specs = getattr(self, "_pending_specs", {}) or {}
        for key, label in (("spec_weight", "Weight"), ("spec_length", "Length"),
                           ("spec_width", "Width"), ("spec_height", "Height")):
            v = (getattr(self.cfg, key, "") or "").strip()
            if v:
                specs[label] = v
        if getattr(self.cfg, "product_type", ""):
            specs["Product Type"] = self.cfg.product_type
        if getattr(self.cfg, "declared_value", ""):
            specs["Declared Value"] = self.cfg.declared_value
        if specs:
            p["specs"] = specs
            amap = dict(p.get("attributes_map") or {})
            amap.update(specs)
            p["attributes_map"] = amap
        res.steps.append(Step("模板参数", True,
                              f"类目 {fields.get('category','-')} / 运费 {fields.get('freight','-')}"
                              f" · 自定义属性仅参考、不复制"))
        return p

    def _map_variants(self, p: dict) -> dict:
        attrs = [a.get("name", "") for a in (p.get("attributes") or []) if isinstance(a, dict)]
        target = [a.get("name", "") for a in (p.get("target_attributes") or [])]
        mapping = auto_map(attrs, target) if target else {}
        p["attr_mapping"] = mapping
        raw_limit = getattr(self.cfg, "max_sku_count", 256)
        original_count = len(p["skus"])
        p["skus"] = limit_skus(p["skus"], int(raw_limit) if raw_limit is not None else 0)
        p["_sku_limit_applied"] = len(p["skus"]) < original_count
        required = [a.get("name", "") for a in (p.get("target_attributes") or [])]
        p["sku_issues"] = [f"{i.sku}: {i.reason}" for i in check_consistency(p["skus"], required)]
        return p

    def _ship_dims(self, p: dict, res=None):
        """返回 (weight, length, width, height)。

        开关 prefer_collected_weight 打开且商品采集到了重量/尺寸时优先用采集值，
        否则退回「设置」里填的统一规格值。
        """
        # 用户明确要求：设置里填了尺寸/重量，就以设置为准，不被采集值覆盖。
        # 只有当设置为空时，才回落到采集值。
        _spec_filled = all(str(getattr(self.cfg, k, "") or "").strip()
                           for k in ("spec_weight", "spec_length", "spec_width", "spec_height"))
        use_collected = bool(getattr(self.cfg, "prefer_collected_weight", False)) and not _spec_filled
        fields = p.get("fields") or {}
        cw = str(fields.get("weight") or "").strip() if use_collected else ""
        cs = str(fields.get("size") or "").strip() if use_collected else ""

        # 采集重量单位可能是克，超过 500 时自动转换为 kg
        if cw:
            try:
                cw_f = float(cw)
                if cw_f > 500:
                    cw = f"{cw_f / 1000:.3f}"
            except Exception:
                pass

        w = cw or str(getattr(self.cfg, "spec_weight", "") or "").strip()
        l = str(getattr(self.cfg, "spec_length", "") or "").strip()
        wd = str(getattr(self.cfg, "spec_width", "") or "").strip()
        h = str(getattr(self.cfg, "spec_height", "") or "").strip()

        # 设置里没填 → 按模板产品补（用户要求：一律以模板产品为准）
        if not (w and l and wd and h):
            try:
                _pid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                _tv = self._template_values_cached(_pid) if _pid else {}
                def _tval(name):
                    it = (_tv or {}).get(name) or {}
                    return str((it or {}).get("v") or "").strip() if isinstance(it, dict) else ""
                if not w:
                    w = _tval("包装后重量")
                _sz = _tval("包装后尺寸")
                if _sz:
                    _nums = [x for x in str(_sz).replace("*", "x").split("x") if x.strip()]
                    if len(_nums) >= 1 and not l:
                        l = _nums[0].strip()
                    if len(_nums) >= 2 and not wd:
                        wd = _nums[1].strip()
                    if len(_nums) >= 3 and not h:
                        h = _nums[2].strip()
            except Exception:
                pass

        if cs:
            parts = [x.strip() for x in cs.lower().replace("*", "x").replace("×", "x").split("x") if x.strip()]
            if len(parts) == 3 and all(x.replace(".", "", 1).isdigit() for x in parts):
                l, wd, h = parts
                if res is not None:
                    add_step(res, "尺寸", True, f"用采集尺寸 {l}x{wd}x{h}")
        if cw and res is not None:
            add_step(res, "重量", True, f"用采集重量 {cw} kg")
        return (w or None, l or None, wd or None, h or None)

    def _price(self, p: dict) -> dict:
        """统一走 services.pricing.compute_price，保证试算与刊登一致。

        基准价格优先用采集到的产品价格（p["price"]），不依赖手动填写的成本。
        """
        from .pricing import compute_price, PriceResult
        # 直接用采集到的产品价格作为基准，倍率模式下即为售价
        import math
        base = float(p.get("price") or p.get("cost") or getattr(self.cfg, "goods_cost", 0) or 0)
        if not math.isfinite(base) or base < 0:
            raise ValueError("商品计价基准必须是有效的非负数")
        if (str(getattr(self.cfg, "price_mode", "multiplier")) != "fixed"
                and getattr(self.cfg, "write_price_to_product", True) and base <= 0):
            from .price_recovery import existing_sku_prices
            rows = p.get("_source_sku_rows") or (p.get("raw") or {}).get("aeopAeProductSKUs")
            try:
                prices = existing_sku_prices(rows)
            except ValueError as error:
                raise ValueError(f"未读取到采集价格，默认成本也未填写；{error}") from error
            p["_preserve_existing_prices"] = True
            p.pop("regional", None)
            p.pop("wholesale", None)
            p["price_result"] = PriceResult(0, 1, 0, 0, mode="preserve",
                note=f"缺少采集成本，自动保留 {len(prices)} 个 SKU 原售价（{min(prices):.2f}～{max(prices):.2f}），未应用倍率")
            return p
        p.pop("_preserve_existing_prices", None)
        r = compute_price(self.cfg, base)
        if r.regional:
            p["regional"] = dict(r.regional)
        if r.wholesale:
            p["wholesale"] = r.wholesale
        p["price_result"] = r
        return p

    def _run_ops(self, url: str, white_bg: bool, remove_wm: bool, edit: bool,
                 prompt: str | None = None) -> str:
        import json
        _act = "ops:v2:" + json.dumps([bool(white_bg), bool(remove_wm), bool(edit), prompt or ""], ensure_ascii=False)
        _hit = self._cache_get(url, _act, self._img_model())
        if _hit:
            return _hit
        # 白底 + 去水印 同时开启时，合并成一次调用
        if white_bg and remove_wm and not edit:
            try:
                import tempfile as _tf, requests as _rq
                from pathlib import Path as _P
                from ..api import ApiClient
                from . import ImageService
                d = _P(_tf.mkdtemp()); fp = d / "in.png"
                fp.write_bytes(_rq.get(str(url), timeout=90).content)
                svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
                out = svc.combined(fp) or str(url)
                self._cache_put(url, _act, out, self._img_model())
                return out
            except Exception:
                pass
        # 优先用注入的图片服务（图片测试台/离线测试会注入），没有再走内置流水线
        svc = getattr(self, "_img_svc", None)
        if svc is not None:
            try:
                fp = self._fetch(str(url))
                if white_bg and remove_wm and not edit:
                    fn = getattr(svc, "combined", None)
                    out = str(fn(fp) if callable(fn) else svc.white_background(fp))
                elif edit:
                    out = str(svc.edit(fp, prompt or ""))
                elif white_bg:
                    out = str(svc.white_background(fp))
                else:
                    out = str(svc.remove_watermark(fp))
                if out:
                    self._cache_put(url, _act, out, self._img_model())
                    return out
            except Exception:
                pass
        ip = self.image_pipeline
        r = ip.run(str(url), white_bg=white_bg, remove_wm=remove_wm, edit=edit, prompt=prompt)
        out = r.last_url() or str(url)
        if out != str(url):
            self._cache_put(url, _act, out, self._img_model())
        return out

    def _process_urls(self, urls: list[str], limit: int = 8, white_bg: bool = True,
                      remove_wm: bool = False, edit: bool = False,
                      prompt: str | None = None) -> tuple[list[str], int]:
        src = list(urls[:limit])
        vals = self._pmap(lambda u: self._run_ops(u, white_bg, remove_wm, edit, prompt), src)
        out, done = [], 0
        for u, nu in zip(src, vals):
            if nu:
                out.append(str(nu)); done += 1
            else:
                out.append(str(u))
        return out, done

    def _ai_images(self, p: dict, res, handled_simple: bool = False) -> dict:
        """主图 / 详情图 / SKU 图 三条分支，按配置范围处理。"""
        from .image_index import pick

        # 新引擎（简单规则 / 精细任务）已经覆盖的分组，旧路径不要再做一遍，
        # 否则同一张图会被白底/去水印加工两次：多花一次钱、多等一轮。
        _covered: set[str] = set()
        try:
            from .image_jobs import parse_jobs as _pj, rules_from_switches as _rfs
            # 检查简单规则和精细任务覆盖的分组（简单规则由开关决定）
            _covered = {r["group"] for r in _rfs(self.cfg)}
            _covered.update(j["group"] for j in _pj(getattr(self.cfg, "image_ai_jobs", "[]")))
        except Exception:
            _covered = set()
        if _covered:
            add_step(res, "图片路径", True,
                     "已由图片方案处理：" + "/".join(sorted(_covered)) + "（旧路径自动跳过，避免重复扣费）")

        # 主图
        main = p.get("images", [])
        # 关键：范围输入框有默认值（1-3 / 1-5），不能拿它当"开关打开"。
        # 只有对应开关真的勾选时，才处理该类操作。
        _wb_on = bool(self.cfg.white_bg_enable)
        _wm_on = bool(self.cfg.watermark_enable)
        _edit_on = bool(getattr(self.cfg, "img_main_edit", False))
        _edit_prompt = str(getattr(self.cfg, "edit_prompt", "") or "").strip()
        if _edit_on and not _edit_prompt:
            add_step(res, "主图精细编辑", False, "未设置编辑提示词（AI 图片→通用编辑），已跳过")
            _edit_on = False
        main_ops = (_wb_on or _wm_on or _edit_on
                    or (self.cfg.background_gen_rules or "").strip() not in ("", "[]"))
        if main and main_ops and "main" not in _covered:
            # 白底范围（仅开关打开时生效）
            wb_expr = ""
            if _wb_on and (self.cfg.white_background_pic_1 or self.cfg.white_background_pic_2):
                wb_expr = f"{self.cfg.white_background_pic_1}-{self.cfg.white_background_pic_2}"
            # 去水印范围（仅开关打开时生效）
            wm_expr = ""
            if _wm_on and (getattr(self.cfg, "remove_watermark_pic_1", "")
                           or getattr(self.cfg, "remove_watermark_pic_2", "")):
                wm_expr = f"{getattr(self.cfg, 'remove_watermark_pic_1', '1')}-{getattr(self.cfg, 'remove_watermark_pic_2', '5')}"
            edit_expr = (getattr(self.cfg, "single_edit_main_pic_indices", "") or "") if _edit_on else ""
            selected = pick(main, wb_expr or wm_expr or edit_expr) if (wb_expr or wm_expr or edit_expr) else main
            if selected:
                out, done = self._process_urls(selected,
                                               white_bg=_wb_on,
                                               remove_wm=_wm_on,
                                               edit=_edit_on,
                                               prompt=_edit_prompt or None)
                idx = [i for i, u in enumerate(main) if u in selected]
                for i, u in zip(idx, out):
                    main[i] = u
                p["images"] = main
                add_step(res, "主图处理", done > 0, f"{done}/{len(out)} 张")

        # 详情图
        detail = p.get("detail_images") or []
        detail_ops = (getattr(self.cfg, "img_detail_white_bg", False)
                      or getattr(self.cfg, "img_detail_remove_wm", False)
                      or getattr(self.cfg, "img_detail_edit", False)
                      or getattr(self.cfg, "translate_detail_pic_enable", False))
        if detail and detail_ops and "detail" not in _covered:
            selected = pick(detail, self.cfg.white_background_detail_pic_indices)
            if selected:
                out, done = self._process_urls(selected, limit=20,
                                               white_bg=getattr(self.cfg, "img_detail_white_bg", False),
                                               remove_wm=getattr(self.cfg, "img_detail_remove_wm", False),
                                               edit=getattr(self.cfg, "img_detail_edit", False))
                idx = [i for i, u in enumerate(detail) if u in selected]
                for i, u in zip(idx, out):
                    detail[i] = u
                p["detail_images"] = detail
                add_step(res, "详情图处理", done > 0, f"{done}/{len(out)} 张")

        # 主图文字翻译
        if (not handled_simple and "main" not in _covered
                and getattr(self.cfg, "translate_main_pic_enable", False) and main
                and getattr(self.cfg, "translate_image_understanding", False)):
            kept = []
            for it in main:
                _u = it.get("url") if isinstance(it, dict) else str(it)
                try:
                    _has = self.image_pipeline.has_text(str(_u))
                except Exception:
                    _has = True
                if _has:
                    kept.append(it)
                else:
                    add_step(res, "翻译预检", True, f"无文字，跳过 {str(_u)[-20:]}")
            main = kept or main

        if not handled_simple and "main" not in _covered and getattr(self.cfg, "translate_main_pic_enable", False) and main:
            from .image_index import pick as _pk
            expr = getattr(self.cfg, "translate_main_pic_indices", "") or ""
            targets = _pk(main, expr) if expr else main[:2]
            n = 0
            for i, u in enumerate(main):
                if u in targets:
                    try:
                        _tp = (getattr(self.cfg, "translate_prompt", "") or "").strip()
                        main[i] = self._run_ops(u, False, False, True, prompt=_tp or None)
                        n += 1
                    except Exception:
                        pass
            if n:
                p["images"] = main
                add_step(res, "主图翻译", True, f"{n} 张")

        # 详情图文字翻译
        if not handled_simple and "detail" not in _covered and getattr(self.cfg, "translate_detail_pic_enable", False) and detail:
            from .image_index import pick as _pick
            idx_expr = getattr(self.cfg, "translate_detail_pic_indices", "") or ""
            targets = _pick(detail, idx_expr) if idx_expr else detail[:3]
            tr_done = 0
            for i, u in enumerate(detail):
                if u in targets:
                    try:
                        _tp = (getattr(self.cfg, "translate_prompt", "") or "").strip()
                        detail[i] = self._run_ops(u, False, False, True, prompt=_tp or None)
                        tr_done += 1
                    except Exception:
                        pass
            if tr_done:
                p["detail_images"] = detail
                add_step(res, "详情图翻译", True, f"{tr_done} 张")

        # SKU 图
        if ((self.cfg.white_background_sku_pic and not handled_simple and "sku" not in _covered)
                or getattr(self.cfg, "img_sku_edit", False)):
            skus = p.get("skus") or []
            done = 0
            try:
                import json as _j3
                repl = _j3.loads(getattr(self.cfg, "sku_image_replace", "") or "{}")
            except Exception:
                repl = {}
            def _one_sku(sku):
                img = sku.get("processed_image") or sku.get("image") or sku.get("imageUrl")
                name = str(sku.get("sku") or "")
                if name in repl and repl[name]:
                    sku["image"] = repl[name]
                    sku["processed_image"] = repl[name]
                    img = repl[name]
                if not img:
                    return None
                _skp = (getattr(self.cfg, "img_sku_edit_prompt", "") or "").strip() or None
                return self._run_ops(
                    str(img),
                    white_bg=False if handled_simple else getattr(self.cfg, "img_sku_white_bg", True),
                    remove_wm=False if handled_simple else getattr(self.cfg, "img_sku_remove_wm", False),
                    edit=getattr(self.cfg, "img_sku_edit", False),
                    prompt=_skp)

            from .image_index import parse_indices as _pidx
            _sel_idx = _pidx(str(getattr(self.cfg, "white_background_sku_pic_indices", "") or "1-20"),
                             len(skus))
            _sel = [skus[i - 1] for i in _sel_idx]
            _sv = self._pmap(_one_sku, _sel)
            for sku, _u in zip(_sel, _sv):
                if _u:
                    sku["processed_image"] = str(_u)
                    done += 1
            if done:
                add_step(res, "SKU图处理", True, f"{done} 张")

        # SKU 图文字翻译
        if not handled_simple and "sku" not in _covered and getattr(self.cfg, "translate_sku_pic", False):
            _tp = (getattr(self.cfg, "translate_prompt", "") or "").strip()
            _all_skus = (p.get("skus") or [])
            from .image_index import parse_indices as _pidx2
            _t_idx = _pidx2(str(getattr(self.cfg, "translate_sku_pic_indices", "") or
                                getattr(self.cfg, "white_background_sku_pic_indices", "") or "1-20"),
                            len(_all_skus))
            _skus = [_all_skus[i - 1] for i in _t_idx]
            def _tr_sku(sku):
                img = sku.get("processed_image") or sku.get("image")
                if not img:
                    return None
                return self._run_ops(str(img), False, False, True, prompt=_tp or None)
            _vals = self._pmap(_tr_sku, _skus)
            n = 0
            for sku, u in zip(_skus, _vals):
                if u:
                    sku["processed_image"] = str(u); n += 1
            if n:
                add_step(res, "SKU图翻译", True, f"{n} 张")

        # 背景生成（AI 换背景）：每条规则用自己的提示词，不再复用全局 edit_prompt
        rules_raw = (getattr(self.cfg, "background_gen_rules", "") or "").strip()
        if rules_raw not in ("", "[]"):
            try:
                import json as _j
                rules = _j.loads(rules_raw)
            except Exception:
                rules = []
            done_bg = 0
            failed_bg = 0
            for rule in (rules or [])[:6]:
                if not isinstance(rule, dict):
                    continue
                pos = str(rule.get("position", "") or "")
                prompt = str(rule.get("prompt", "") or "").strip() or \
                    (getattr(self.cfg, "background_gen_sku_prompt", "") or "").strip()
                if not prompt:
                    continue
                try:
                    if pos.startswith("main"):
                        parts = pos.split("_")
                        idx = (int(parts[1]) - 1) if len(parts) > 1 and parts[1].isdigit() else 0
                        imgs = p.get("images") or []
                        if 0 <= idx < len(imgs):
                            p["images"][idx] = self._edit_with_prompt(str(imgs[idx]), prompt)
                            done_bg += 1
                    elif pos.startswith("detail"):
                        parts = pos.split("_")
                        idx = (int(parts[1]) - 1) if len(parts) > 1 and parts[1].isdigit() else 0
                        dimgs = p.get("detail_images") or []
                        if 0 <= idx < len(dimgs):
                            dimgs[idx] = self._edit_with_prompt(str(dimgs[idx]), prompt)
                            p["detail_images"] = dimgs
                            done_bg += 1
                    elif pos in ("all_main", "main_all"):
                        for i, u in enumerate(p.get("images") or []):
                            p["images"][i] = self._edit_with_prompt(str(u), prompt)
                            done_bg += 1
                except Exception:
                    failed_bg += 1
            if done_bg or failed_bg:
                add_step(res, "背景生成", failed_bg == 0 and done_bg > 0,
                         f"成功 {done_bg} / 失败 {failed_bg}")
        return p

    @staticmethod
    def _url_readable(url: str, timeout: int = 12) -> bool:
        """回读一次，确认图片地址真的能公开访问。

        店小秘 COS 上传返回的可能是带签名的私有地址（去掉 query 后 403），
        直接写进商品会导致主图裂开。宁可退回图床，也不要交一个坏地址。
        """
        try:
            import requests as _rq
            r = _rq.get(url, timeout=timeout, stream=True)
            ok = 200 <= r.status_code < 300
            ctype = (r.headers.get("Content-Type") or "").lower()
            try:
                r.close()
            except Exception:
                pass
            return ok and ("image" in ctype or ctype == "")
        except Exception:
            return False

    def _upload_to_album(self, p: dict, res=None) -> dict:
        """开关 prefer_dianxiaomi_album：把处理后的图直接传进店小秘「图片空间」。

        走 cos_upload（取签名 → PUT → 回调登记），传完用店小秘返回的地址替换原 URL，
        这样就不需要外部图床了。

        失败的单张不做替换（保留原地址），并在步骤里写清楚，绝不静默改坏。
        """
        if not getattr(self.cfg, "prefer_dianxiaomi_album", False) or self.dxm is None:
            return p
        try:
            self._album_urls
        except AttributeError:
            self._album_urls = set()
        try:
            from ..integrations.cos_upload import upload_image as _album_up
        except Exception as ex:  # noqa: BLE001
            if res is not None:
                add_step(res, "传店小秘相册", False, f"模块不可用：{str(ex)[:60]}")
            return p

        # 已在店小秘/速卖通域名上的图不用再传
        skip_hosts = ("alicdn.com", "dianxiaomi.com", "dxm.com")
        full_cid = str(getattr(self.cfg, "upload_full_cid", "") or "")
        done = failed = skipped = 0
        _errs: list[str] = []
        _dead = False          # 店小秘上传接口已停用：第一次失败后不再逐张重试
        for key in ("images", "detail_images"):
            urls = p.get(key) or []
            for i, u in enumerate(urls):
                su = str(u or "")
                if not su:
                    skipped += 1
                    continue
                if su.startswith("http") and any(h in su for h in skip_hosts):
                    skipped += 1
                    continue
                if _dead:
                    skipped += 1
                    continue
                try:
                    # 本地文件直接用；网络地址先下到本地
                    if su.startswith("http"):
                        fp = self._fetch(su)
                    else:
                        from pathlib import Path as _Ploc
                        fp = _Ploc(su)
                        if not fp.exists():
                            skipped += 1
                            continue
                    r = _album_up(self.dxm, fp, full_cid=full_cid)
                    new_u = str(getattr(r, "url", "") or "")
                    if new_u.startswith("http") and getattr(self.cfg, "album_verify_url", True):
                        if not self._url_readable(new_u):
                            add_step(res, "传店小秘相册", False,
                                     f"第{i + 1}张上传成功但地址打不开，已保留原地址")
                            failed += 1
                            continue
                    if new_u.startswith("http"):
                        urls[i] = new_u
                        try:
                            self._album_urls.add(new_u)
                        except Exception:
                            pass
                        done += 1
                    else:
                        failed += 1
                except Exception as _ex:
                    failed += 1
                    _em = f"{type(_ex).__name__}: {str(_ex)[:90]}"
                    try:
                        _errs.append(f"第{i + 1}张 {_em}")
                    except Exception:
                        pass
                    if "参数缺失" in _em:
                        _dead = True     # 接口整体不可用，剩余图片直接跳过
                        # 该接口已确认停用；不再把“失败/参数缺失”写进日志，
                        # 避免用户把已知无效的店小秘相册错误误判成商品保存问题。
                        try:
                            _errs.clear()
                            failed -= 1
                            skipped += 1
                        except Exception:
                            pass
            p[key] = urls
        if res is not None:
            _msg = f"成功 {done} / 失败 {failed} / 跳过 {skipped}"
            if _errs:
                _msg += " · " + "；".join(_errs[:3])
            # 店小秘 getSign 接口已不可用（统一返回「上传失败，参数缺失！」）：
            # 这不是致命错误，图片本来就已经在图床上，商品照常保存。
            _all_param_missing = bool(_errs) and all("参数缺失" in e for e in _errs)
            if _all_param_missing:
                # 已知接口停用：不写任何错误/失败步骤，图片沿用原地址。
                pass
            elif _dead and done == 0:
                # 第一次失败后已判定接口不可用：静默跳过。
                pass
            else:
                # 全部跳过（图片已经在图床/店小秘域名）不是失败；只有真实失败才算失败。
                add_step(res, "传店小秘相册", failed == 0, _msg)
        return p

    def _publicize_images(self, p: dict, res, dry_run: bool = False) -> dict:
        """把私有图片下载后转存到我们的服务器，得到公网可访问 URL。

        已经是公网托管 URL 的图片（以 moneyfly.club 或 alicdn 开头）跳过重复托管。
        dry-run 时跳过下载/上传（预演只需计划，不需要真实公网 URL），速度快 20 倍以上。
        """
        if dry_run:
            add_step(res, "图片转存", True, f"dry-run 跳过（{len(p.get('images') or [])} 张）")
            return p
        import tempfile
        from pathlib import Path as _P
        from .hosting import upload as host_upload
        
        # 跳过已托管的公网图片
        # 注意：要把「我们自己的图床」也算进来，否则刚处理完的图会被反复重传。
        # 以前只列了 moneyfly / alicdn，导致自建图床（132.226.1.44:8099）的图每轮都重传。
        try:
            from .hosting import DEFAULT_HOST as _HOST
            _host_key = _HOST.split("//")[-1].rstrip("/")
        except Exception:
            _host_key = ""
        hosted_patterns = tuple(x for x in ("moneyfly.club", "alicdn.com", "ae01.alicdn.com",
                                            _host_key) if x)
        all_images = p.get("images", [])
        images = all_images[:8]  # 只处理前8张
        _album = getattr(self, "_album_urls", set())
        needs_upload = [i for i, u in enumerate(images)
                        if not any(pattern in str(u).lower() for pattern in hosted_patterns)
                        and str(u) not in _album]
        
        if not needs_upload:
            add_step(res, "图片转存", True, f"已全部公网（{len(images)} 张）")
            return p
            
        d = _P(tempfile.mkdtemp())
        cookies = None
        if self.dxm is not None:
            try:
                cookies = self.dxm.cookies_dict()
            except Exception:
                cookies = None
        
        _upload_errors: list[tuple[int, str]] = []

        def _one(i):
            u = images[i]
            try:
                local = _P(str(u))
                if not str(u).startswith(("http://", "https://")) and local.is_file():
                    fp = local
                else:
                    r = __import__("requests").get(str(u), timeout=60, cookies=cookies)
                    r.raise_for_status()
                    fp = d / f"pub_{i}.png"; fp.write_bytes(r.content)
                try:
                    self._local_files[str(images[i])] = fp   # 供尺寸校验复用，避免二次下载
                except Exception:
                    pass
                return i, host_upload(fp)
            except Exception as _ex:
                # 不静默：记下第几张、什么原因，主线程统一写进步骤，避免「图没上传成功但没人知道」
                try:
                    _upload_errors.append((i + 1, f"{type(_ex).__name__}: {str(_ex)[:80]}"))
                except Exception:
                    pass
                return i, str(u)
        
        results = self._pmap(_one, needs_upload) or []
        for _r in results:
            if not _r:
                continue
            try:
                i, url = _r
            except Exception:
                continue
            images[i] = url
        if _upload_errors:
            _detail = "；".join(f"第{n}张 {e}" for n, e in _upload_errors[:3])
            if res is not None:
                add_step(res, "图片上传", False,
                         f"{len(_upload_errors)} 张没能上传到图床，已退回原地址：{_detail}")

        # ---- 尺寸校验：速卖通要求 ≥800×800，不合格的不写回去 ----
        if getattr(self.cfg, "img_min_size_enable", True) and images:
            ms = self._min_size()
            bad: list[tuple[int, str, str]] = []
            # 并行校验尺寸（每张都要下载一次，串行会很慢）
            _items = list(enumerate(images))

            def _chk(pair):
                _i, _u = pair
                _ok, _info = self._size_ok(str(_u))
                return (_i, str(_u), str(_info), _ok)

            _res = self._pmap(_chk, _items) or []
            for _r in _res:
                if _r and not _r[3]:
                    bad.append((_r[0], _r[1], _r[2]))
            if bad:
                try:
                    self._size_rejects.extend((b[1], b[2]) for b in bad)
                except Exception:
                    pass
                if bool(getattr(self.cfg, "img_min_size_drop_invalid", False)):
                    bad_idx = {b[0] for b in bad}
                    images = [u for i, u in enumerate(images) if i not in bad_idx]
                    add_step(res, "尺寸校验", False,
                             f"移除 {len(bad)} 张 <{ms}px 的图（剩 {len(images)} 张）")
                else:
                    add_step(res, "尺寸校验", False,
                             f"{len(bad)} 张 <{ms}px 未删除，仅告警：" +
                             "；".join(f"第{b[0] + 1}张 {b[2][:24]}" for b in bad[:3]))
            else:
                add_step(res, "尺寸校验", True, f"{len(images)} 张全部 ≥{ms}px")

        # 只更新前8张，保留后续图片
        p["images"] = images + all_images[8:]
        add_step(res, "图片转存", True, f"{len(needs_upload)} 张已转公网，{len(images) - len(needs_upload)} 张已复用")
        return p

    def _local_image_ops(self, p: dict, res) -> dict:
        """水印 / 四合一：下载 → 本地处理 → 托管 → 回填 URL。
        
        重要：只修改需要处理的图片，其他图片保持原样。
        - 水印：替换对应位置的图片
        - 四合一：生成新图插入（不删除原图）
        """
        import tempfile
        import requests as _rq
        from pathlib import Path as _P
        from .image_ops import add_watermark, four_in_one
        from .hosting import upload as host_upload

        original_images = list(p.get("images", []))  # 保存原始列表
        d = _P(tempfile.mkdtemp())
        local = []
        local_indices = []  # 记录下载的图片对应的原始索引
        
        for i, u in enumerate(original_images[:8]):
            try:
                r = _rq.get(str(u), timeout=60); r.raise_for_status()
                fp = d / f"src_{i}.png"; fp.write_bytes(r.content)
                local.append(fp)
                local_indices.append(i)
            except Exception:
                continue
        
        if not local:
            return p

        # 水印处理：替换对应位置
        if self.cfg.watermark:
            watermarked = []
            for i, fp in enumerate(local):
                try:
                    wm_fp = add_watermark(fp, self.cfg.watermark, d / f"wm_{i}.png")
                    watermarked.append((local_indices[i], wm_fp))
                except Exception:
                    # 失败则保持原图
                    pass
            
            # 上传水印图并替换对应位置
            for orig_idx, fp in watermarked:
                try:
                    url = host_upload(fp)
                    original_images[orig_idx] = url
                except Exception:
                    pass

        # 四合一：生成新图并插入（不删除原图）
        four_url = None
        if self.cfg.four_in_one and len(local) >= 2:
            try:
                four_fp = four_in_one(local[:4], d / "four.png")
                four_url = host_upload(four_fp)
            except Exception:
                pass
        
        if four_url:
            position = str(getattr(self.cfg, "four_in_one_position", "append") or "append")
            if position == "first":
                original_images.insert(0, four_url)
                add_step(res, "四合一", True, "已插入到第一位")
            else:
                original_images.append(four_url)
                add_step(res, "四合一", True, "已追加到末尾")

        # 回传店小秘相册：水印图先进图片库
        album_ok = 0
        if (self.cfg.watermark and self.dxm is not None
                and getattr(self.cfg, "watermark_upload_album", True)
                and getattr(self.cfg, "prefer_dianxiaomi_album", False)):
            full_cid = getattr(self.cfg, "upload_full_cid", "") or ""
            for img_url in original_images[:8]:  # 只上传前8张
                if img_url != p.get("images", [])[original_images.index(img_url) if img_url in (p.get("images", [])) else -1]:
                    # 只上传被修改过的图片
                    try:
                        self.dxm.upload_image_from_url(str(img_url), full_cid=full_cid)
                        album_ok += 1
                    except Exception:
                        continue
            if album_ok:
                add_step(res, "水印回传相册", True, f"{album_ok} 张已入店小秘图片库")

        p["images"] = original_images
        
        msg_parts = []
        if self.cfg.watermark:
            msg_parts.append("水印已应用")
        if four_url:
            msg_parts.append("四合一已生成")
        
        add_step(res, "本地图片处理", True, " · ".join(msg_parts) if msg_parts else "完成")
        return p

    def _review_content(self, p: dict, res) -> bool:
        """Review verdicts are metadata, never replacement product content."""
        import re
        block = bool(getattr(self.cfg, "review_block_on_fail", False))

        def check(label, call):
            try:
                verdict = call()
                if not isinstance(verdict, str) or not verdict.strip():
                    raise ValueError("审查返回空结果")
                match = re.fullmatch(r"(PASS|FAIL)(?:\s*[|｜:].*)?", verdict.strip(), re.I | re.S)
                if not match:
                    raise ValueError("审查结果格式无法识别，应返回 PASS 或 FAIL|原因")
                ok = match.group(1).upper() == "PASS"
                add_step(res, label, ok, verdict[:160])
                if not ok and block:
                    res.ok = False
                    res.error = f"{label}不合规，已停止保存"
                    return False
            except Exception as ex:
                add_step(res, label, False, str(ex)[:160])
                if block:
                    res.ok = False
                    res.error = f"{label}未完成，已停止保存：{ex}"
                    return False
            return True

        for group, key, enabled in (("detail", "detail_images", "review_detail_img_enable"),
                                     ("main", "images", "review_main_img_enable")):
            if not getattr(self.cfg, enabled, False) or not p.get(key):
                continue
            urls = list(p[key])
            if group == "detail":
                first = max(0, int(getattr(self.cfg, "detail_review_first_count", 0) or 0))
                last = max(0, int(getattr(self.cfg, "detail_review_last_count", 0) or 0))
                urls = (urls[:first] if first else []) + (urls[-last:] if last else []) if first or last else urls
            reviewer = ImagePipeline(self.cfg)
            for index, url in enumerate(dict.fromkeys(urls), 1):
                label = f"{'详情' if group == 'detail' else '主'}图审查{index}"
                if not check(label, lambda u=url: reviewer.review(str(u), group)):
                    return False
        if getattr(self.cfg, "review_text_enable", False) and p.get("detail_html"):
            def text_verdict():
                result = TextPipeline(self.cfg).review_text(p["detail_html"])
                if not result.ok:
                    raise ValueError(result.error or "文本审查请求失败")
                return result.value
            if not check("文本审查", text_verdict):
                return False
        return True

    # ---------- 主流程 ----------
    def _save_with_relax(self, payload: dict, tpl, res=None) -> dict:
        """保存失败时逐级放宽 payload 重试。

        店小秘对「属性/SKU 维度与类目不匹配」只回一句「保存失败！」，
        所以失败后按 relaxed_payload(level=1,2) 依次重试。
        """
        from .api_save import relaxed_payload
        try:
            return self.dxm.save_product_via_api(payload)
        except Exception as ex:  # noqa: BLE001
            _msg = str(ex)
            if "保存失败" not in _msg:
                raise
            for _lv in (1, 2):
                try:
                    q = relaxed_payload(payload, _lv, tpl)
                    r = self.dxm.save_product_via_api(q)
                    if res is not None:
                        add_step(res, "保存兼容重试", True,
                                 f"第 {_lv} 级放宽后保存成功（原结构被店小秘拒绝）")
                    return r
                except Exception:  # noqa: BLE001
                    continue
            raise

    def _market_ok(self, url: str, ratio: str) -> bool:
        """检查已有营销图尺寸是否符合平台要求。

        marketImg1(场景图)：≥750x1000 且 3:4
        marketImg2(白底图)：≥800x800 且 1:1
        """
        if not url:
            return False
        try:
            import io as _io
            import requests as _rq
            from PIL import Image as _Img
            _url = str(url)
            if _url.startswith(("http://", "https://")):
                raw = _rq.get(_url, timeout=25).content
                _im = _Img.open(_io.BytesIO(raw))
            else:
                _im = _Img.open(_url)
            w, h = _im.size
        except Exception:
            return False
        if ratio == "3:4":
            return w >= 750 and h >= 1000 and abs(w / max(h, 1) - 0.75) <= 0.03
        return w >= 800 and h >= 800 and abs(w / max(h, 1) - 1.0) <= 0.05

    def _fallback_market_images(self, target: dict, tpl: dict | None, res=None) -> list[str]:
        """图床失败时，用模板营销图/原图里的合规图兜底，避免平台报“请补充营销图”。"""
        out1 = out2 = ""
        # 1) 模板商品已有营销图，直接复用（最稳，尺寸已合规）
        for src in (tpl or {},):
            try:
                m1 = str(src.get("marketImg1") or "").strip()
                m2 = str(src.get("marketImg2") or "").strip()
                if m1 and self._market_ok(m1, "3:4"):
                    out1 = m1
                if m2 and self._market_ok(m2, "1:1"):
                    out2 = m2
            except Exception:
                pass
        # 2) 目标商品原有营销图
        try:
            cur1 = str(target.get("marketImg1") or "").strip()
            cur2 = str(target.get("marketImg2") or "").strip()
            if not out1 and cur1 and self._market_ok(cur1, "3:4"):
                out1 = cur1
            if not out2 and cur2 and self._market_ok(cur2, "1:1"):
                out2 = cur2
        except Exception:
            pass
        # 3) 从主图/附加图里找 3:4、1:1 合规图，直接用 CDN 地址
        if not out1 or not out2:
            try:
                import requests as _rq, io as _io
                from PIL import Image as _Img
                cands = []
                for key in ("imageURLs", "extraImages"):
                    raw = str(target.get(key) or "")
                    for u in raw.replace("|", ";").split(";"):
                        u = u.strip()
                        if u.startswith("http") and u not in cands:
                            cands.append(u)
                for u in cands[:12]:
                    try:
                        im = _Img.open(_io.BytesIO(_rq.get(u, timeout=20).content))
                        w, h = im.size
                        if not out1 and abs(w / max(h, 1) - 0.75) <= 0.03 and w >= 750 and h >= 1000:
                            out1 = u
                        if not out2 and abs(w / max(h, 1) - 1.0) <= 0.05 and w >= 800 and h >= 800:
                            out2 = u
                    except Exception:
                        continue
                    if out1 and out2:
                        break
            except Exception:
                pass
        if res is not None and (out1 or out2):
            add_step(res, "营销图", True, "图床不可用，已使用模板/原图合规营销图兜底")
        return [x for x in (out1, out2) if x]

    def _fix_market_images(self, cur1: str, cur2: str, base_img: str, res=None) -> list[str]:
        """已有营销图不合规（尺寸/比例不对）就重新生成，返回 [m1, m2]（可能为空）。

        m1 = marketImg1 = 3:4 场景图；m2 = marketImg2 = 1:1 白底图。
        """
        if self._market_ok(cur1, "3:4") and self._market_ok(cur2, "1:1"):
            return []
        out = self._market_images_for(base_img, res)
        if out and res is not None:
            add_step(res, "营销图", True, "已有营销图尺寸不合规 → 已重新生成")
        return out

    def _market_images_for(self, image_url: str, res=None) -> list[str]:
        """用主图本地裁切出营销图 [1:1, 3:4] 并上传（免费，约 0.5 秒）。

        营销图是发布必填项：缺了会报「请补充营销图(1:1白底图)、(3:4场景图)后重新发布」。
        """
        if not image_url:
            return []
        try:
            import tempfile as _tf
            import requests as _rq
            from pathlib import Path as _P
            from .image_ops import make_marketing
            from .hosting import upload as _up
        except Exception:
            return []
        try:
            d = _P(_tf.mkdtemp())
            _raw_url = str(image_url)
            if _raw_url.startswith(("http://", "https://")):
                src = d / "m0.png"
                src.write_bytes(_rq.get(_raw_url, timeout=40).content)
            else:
                # 本地裁切图/临时文件不能交给 requests；否则 Windows 会报
                # No connection adapters were found for 'C:\...\Temp'。
                src = _P(_raw_url)
                if not src.is_file():
                    raise ValueError("营销图主图本地文件不存在")
            # 顺序很重要！店小秘/速卖通规定：
            #   marketImg1 = 场景图，必须 3:4 且最短边 ≥750（生成 800x1067，兼容 ≥800 主图门槛）
            #   marketImg2 = 白底图，必须 ≥800x800 且 1:1
            out = []
            for i, size in enumerate(((800, 1067), (800, 800))):
                fp = d / f"mk{i}.jpg"
                make_marketing(src, fp, size)
                out.append(_up(fp))
            if res is not None and out:
                add_step(res, "营销图", True, f"{len(out)} 张已生成（本地裁切，店小秘同款，免费）")
            return out
        except Exception as ex:  # noqa: BLE001
            if res is not None:
                add_step(res, "营销图", False, str(ex)[:70])
            return []

    def _ensure_min_image_size(self, images: list[str], res=None) -> list[str]:
        """平台硬性要求：主图宽高不能小于 100，太小会报 isv.134000000002。

        采集来的长条图常见 202x66，这里本地放大到 800x800（白底补边）再上传替换；
        下载/放大失败则保留原图（不阻断保存）。
        """
        import io as _io
        import tempfile as _tf
        import requests as _rq
        from pathlib import Path as _P
        out: list[str] = []
        for u in images:
            s = str(u or "")
            if not s.startswith("http"):
                out.append(s)
                continue
            try:
                raw = _rq.get(s, timeout=30).content
                from PIL import Image as _Img
                w, h = _Img.open(_io.BytesIO(raw)).size
            except Exception:
                out.append(s)
                continue
            if w >= 100 and h >= 100:
                out.append(s)
                continue
            try:
                from .image_ops import make_marketing
                from .hosting import upload as _up
                d = _P(_tf.mkdtemp())
                sp = d / "src.png"
                sp.write_bytes(raw)
                op = d / "out.jpg"
                make_marketing(sp, op, (800, 800))
                out.append(_up(op))
                if res is not None:
                    add_step(res, "图片尺寸", True, f"{w}x{h} 过小 → 已放大 800x800")
            except Exception as ex:  # noqa: BLE001
                if res is not None:
                    add_step(res, "图片尺寸", False, f"{w}x{h} 放大失败：{str(ex)[:40]}")
                out.append(s)
        return out

    def _shrink_detail_images(self, html: str, res, max_mb: float = 4.5) -> str:
        """描述图 > 5MB 会被站点拒绝，这里统一检测并压缩重传。

        平台规则：「描述图大小不能超过5M」。压缩到 ≤4.5MB、最长边 ≤1600，
        再上传图床替换 URL。失败就保留原图（不阻断保存）。
        """
        import re as _re
        if not html:
            return html
        try:
            import io as _io
            import requests as _rq
            from PIL import Image as _Img
            from .hosting import upload as _up
        except Exception:
            return html

        urls = _re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=_re.I)
        if not urls:
            return html
        mapping: dict[str, str] = {}
        for u in list(dict.fromkeys(urls)):
            if not u.startswith("http"):
                continue
            try:
                r = _rq.get(u, timeout=40)
                raw = r.content
                if len(raw) <= max_mb * 1024 * 1024:
                    continue                      # 没超限，不动
                im = _Img.open(_io.BytesIO(raw))
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                if max(im.size) > 1600:
                    im.thumbnail((1600, 1600), _Img.LANCZOS)
                buf = _io.BytesIO()
                q = 85
                while True:
                    buf.seek(0); buf.truncate()
                    im.save(buf, format="JPEG", quality=q, optimize=True)
                    if buf.tell() <= max_mb * 1024 * 1024 or q <= 45:
                        break
                    q -= 10
                import tempfile as _tf
                from pathlib import Path as _P
                fp = _P(_tf.mkdtemp()) / "shrunk.jpg"
                fp.write_bytes(buf.getvalue())
                nu = _up(fp)
                if nu:
                    mapping[u] = str(nu)
                    add_step(res, "描述图压缩", True,
                             f"{len(raw) / 1048576:.1f}MB → {buf.tell() / 1048576:.1f}MB")
            except Exception:
                continue
        if not mapping:
            return html
        out = html
        for k, v in mapping.items():
            out = out.replace(k, v)
        add_step(res, "描述图压缩", True, f"{len(mapping)} 张超 5MB 已压缩并替换")
        return out

    def _dedupe_product_skus(self, target: dict, res=None) -> dict:
        """统一发货地后可能产生重复规格组合，合并重复 SKU 再提交。"""
        import json as _j
        try:
            from .product_data import sku_rows as _rows, clean_sku_rows as _clean
            from .sku_recovery import _merge_duplicate_rows
            rows = _clean(_rows(target.get("aeopAeProductSKUs")))
            merged, n = _merge_duplicate_rows(rows)
            if n:
                out = dict(target)
                out["aeopAeProductSKUs"] = _j.dumps(merged, ensure_ascii=False)
                if res is not None:
                    try:
                        add_step(res, "SKU 自动合并", True,
                                 f"发货地统一后合并 {n} 个重复规格组合")
                    except Exception:
                        pass
                return out
        except Exception:
            pass
        return target

    def _china_origin_value(self, category_id: str) -> tuple[str, str]:
        """从类目 Origin 枚举里找中国大陆；找不到用阿里国际站通用值。"""
        cid = str(category_id or "").strip()
        cache = getattr(self, "_origin_value_cache", None)
        if not isinstance(cache, dict):
            cache = self._origin_value_cache = {}
        if cid in cache:
            return cache[cid]
        out = ("9441741844", "Mainland China")
        try:
            import json as _j
            import re as _re
            for a in (self.dxm.category_attributes(cid) if self.dxm and cid else []):
                if not isinstance(a, dict) or str(a.get("arrtNameId") or "") != "219":
                    continue
                vals = a.get("values") or "[]"
                if isinstance(vals, str):
                    vals = _j.loads(vals)
                for v in vals if isinstance(vals, list) else []:
                    names = v.get("names") or {}
                    label = f"{names.get('en') or ''} {names.get('zh') or ''}".strip()
                    if _re.search(r"mainland china|china|中国", label, _re.I):
                        out = (str(v.get("id") or ""), label)
                        break
                break
        except Exception:
            pass
        cache[cid] = out
        return out

    def _repair_origin_attribute(self, target: dict, res=None) -> dict:
        """强制系统属性 Origin 为中国大陆，清理旧的自定义产地属性。"""
        if not isinstance(target, dict):
            return target
        try:
            import json as _j
            cid = str(target.get("categoryId") or "").strip()
            oid, oname = self._china_origin_value(cid)
            if not oid:
                return target
            props = _j.loads(target.get("aeopAeProductPropertys") or "[]")
            if not isinstance(props, list):
                props = []
            cleaned = []
            changed = 0
            for a in props:
                if not isinstance(a, dict):
                    continue
                if str(a.get("attrNameId") or "") == "219":
                    if str(a.get("attrValueId") or "") != oid:
                        changed += 1
                    continue
                name = str(a.get("attrName") or "").strip().lower()
                if name in ("origin", "place of origin", "country of origin", "产地", "原产地"):
                    changed += 1
                    continue
                cleaned.append(a)
            cleaned.append({"attrNameId": "219", "attrValueId": oid, "attrName": "Origin"})
            target = dict(target)
            target["aeopAeProductPropertys"] = _j.dumps(cleaned, ensure_ascii=False)
            if res is not None and changed:
                try:
                    add_step(res, "产地统一", True, f"系统属性 Origin 已统一为 {oname}")
                except Exception:
                    pass
            return target
        except Exception:
            return target

    def _repair_ships_from(self, target: dict, res=None) -> dict:
        """清空 SKU 的「发货地」属性（保持页面不勾选）；系统 Origin 另行写中国。"""
        import json as _js
        if not isinstance(target, dict):
            return target
        skus = target.get("aeopAeProductSKUs")
        if isinstance(skus, str):
            try:
                skus = _js.loads(skus or "[]")
            except Exception:
                return target
        if not isinstance(skus, list) or not skus:
            return target
        changed = 0
        for s in skus:
            if not isinstance(s, dict):
                continue
            props = s.get("aeopSKUProperty")
            if not isinstance(props, list):
                continue
            kept = [p for p in props if not (
                isinstance(p, dict) and str(p.get("skuPropertyId") or "") == "200007763")]
            if len(kept) != len(props):
                changed += len(props) - len(kept)
                s["aeopSKUProperty"] = kept
        if changed:
            target = dict(target)
            target["aeopAeProductSKUs"] = _js.dumps(skus, ensure_ascii=False)
            if res is not None:
                try:
                    add_step(res, "SKU 发货地", True,
                             f"已清空 {changed} 个 SKU 的「发货地」勾选；系统 Origin 使用中国")
                except Exception:
                    pass
        return target

    def _repair_product_images(self, target: dict, tpl: dict | None, res=None) -> dict:
        """发布失败商品的主图/附加图/详情图统一去死链，优先用有效图替换。"""
        import re as _re
        if not isinstance(target, dict):
            return target
        cache = getattr(self, "_img_valid_cache", None)
        if not isinstance(cache, dict):
            cache = self._img_valid_cache = {}

        def ok(url: str) -> bool:
            u = str(url or "").strip()
            if not u.startswith("http"):
                return False
            if u not in cache:
                cache[u] = (self._check_main_image_for_publish([u]) == "")
            return bool(cache[u])

        def split(v):
            return [x.strip() for x in _re.split(r"[;|\n]", str(v or "")) if x.strip().startswith("http")]

        main = split(target.get("imageURLs"))
        extra = split(target.get("extraImages"))
        tpl_main = split((tpl or {}).get("imageURLs"))
        tpl_extra = split((tpl or {}).get("extraImages"))
        detail = str(target.get("detail") or "")
        detail_imgs = _re.findall(r'<img[^>]+src=["\']([^"\']+)', detail, _re.I)
        pool = []
        for u in main + extra + tpl_main + tpl_extra + detail_imgs:
            if u not in pool and ok(u):
                pool.append(u)
        out = dict(target)
        changed = 0
        if main:
            kept = [u for u in main if ok(u)]
            if not kept and pool:
                kept = pool[:max(1, min(6, len(pool)))]
            if kept and kept != main:
                out["imageURLs"] = ";".join(kept[:6])
                changed += 1
        if extra:
            kept_extra = [u for u in extra if ok(u)]
            if kept_extra != extra:
                out["extraImages"] = "|".join(kept_extra)
                changed += 1
        if detail_imgs:
            dead = {u for u in detail_imgs if not ok(u)}
            if dead:
                # 失效详情图用第一张有效主图替换；没有有效图就移除该 img，避免平台拒收。
                repl = (out.get("imageURLs") or "").split(";")[0].strip()
                new_detail = detail
                for u in dead:
                    new_detail = new_detail.replace(u, repl if repl else "")
                if not repl:
                    new_detail = _re.sub(r'<img[^>]+>', '', new_detail, flags=_re.I)
                out["detail"] = new_detail
                changed += 1
        if changed and res is not None:
            try:
                add_step(res, "图片自动修复", True,
                         f"已清理失效图片/替换可用图（{changed} 个字段）")
            except Exception:
                pass
        return out

    def _repair_main_images(self, target: dict, tpl: dict | None, res=None) -> dict | None:
        """主图 404/失效时，从 extraImages / 模板 / 详情里找可用图替换。"""
        import re as _re
        cands = []
        def add(v):
            for u in _re.split(r"[;|\n]", str(v or "")):
                u = u.strip()
                if u.startswith("http") and u not in cands:
                    cands.append(u)
        add(target.get("imageURLs"))
        add(target.get("extraImages"))
        if tpl:
            add(tpl.get("imageURLs"))
            add(tpl.get("extraImages"))
        # 详情 HTML 里的图片作为最后备选
        add(";".join(_re.findall(r'<img[^>]+src=["\']([^"\']+)', str(target.get("detail") or ""), _re.I)))
        valid = []
        for u in cands:
            if self._check_main_image_for_publish([u]) == "":
                valid.append(u)
            if len(valid) >= 6:
                break
        if not valid:
            return None
        out = dict(target)
        out["imageURLs"] = ";".join(valid)
        if res is not None:
            try:
                add_step(res, "主图自动修复", True,
                         f"已移除失效主图，替换为可用图（保留 {len(valid)} 张）")
            except Exception:
                pass
        return out

    def _await_publish_outcome(self, pid: str, action: str, timeout: float = 20.0):
        """保存/发布后回读最终状态；返回 outcome dict，无法判断时返回 None。"""
        import time as _t
        from .publish_outcome import judge
        deadline = _t.time() + max(2.0, float(timeout))
        last = None
        while _t.time() < deadline:
            _t.sleep(2.0)
            try:
                d = self.dxm._get("product_detail", {"id": str(pid)}).get("data", {}).get("product", {})
            except Exception:
                continue
            if not any(k in d for k in ("dxmOfflineState", "dxmState", "productStatusType", "errMsg")):
                return None
            state = str(d.get("dxmOfflineState") or d.get("productStatusType") or d.get("dxmState") or "")
            err = str(d.get("errMsg") or "")
            last = judge(action, state, err)
            # 状态字段存在但全为空、也没有错误：无法判断，不能据此判失败（测试桩/旧接口）。
            if not state and not err:
                return None
            if last["category"] in ("pending", "auditing", "online", "failed"):
                return last
        return None

    def publish_only(self, product_id: str, res) -> "SaveResult":
        """只把已保存的商品重新提交发布（op=2）；不跑 AI、不处理图片、不花钱。

        用于「编辑成功但发布失败」的商品重试。
        """
        from .api_save import build_save_payload
        import os
        if os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"):
            return SaveResult(ok=False, message="验收预演模式：未提交真实发布")

        if self.dxm is None:
            return SaveResult(ok=False, message="未连接店小秘")
        pid = str(product_id)
        try:
            target = self.dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
        except Exception as ex:  # noqa: BLE001
            return SaveResult(ok=False, message=f"读取商品失败：{str(ex)[:100]}")
        if not target:
            return SaveResult(ok=False, message="没取到商品详情")
        _state = str(target.get("productStatusType") or target.get("dxmState")
                     or target.get("dxmOfflineState") or "").strip()
        if _state in ("auditing", "online", "onSelling", "publishSuccess"):
            return SaveResult(ok=True, published=True,
                              message=f"商品当前状态为 {_state}，已在审核/在线，无需重复提交")
        if _state == "publishing":
            # 发布中不能判成功；继续回读，只有进入审核/在线才成功。
            _out = self._await_publish_outcome(pid, "publish", timeout=30)
            if _out is not None and _out.get("ok"):
                return SaveResult(ok=True, published=True,
                                  message=f"商品已从发布中确认进入：{_out.get('label')}")
            if _out is not None and _out.get("category") == "failed":
                return SaveResult(ok=False, published=False,
                                  message=f"商品发布中后失败：{_out.get('label')}（{_out.get('state')}）")
            return SaveResult(ok=False, published=False, pending=True,
                              message="商品当前仍在发布中，平台尚未返回审核结果；结果待确认，请稍后刷新，不要重复发布")
        if _state in ("waitPublish", "editingRequired"):
            return SaveResult(ok=False, published=False,
                              message="商品仍在待发布，未进入平台审核；请提交发布而不是保存")
        tpl = None
        _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
        if _tid:
            try:
                tpl = _cached_template_edit(self.dxm, _tid)
            except Exception:
                tpl = None
        target = self._repair_product_images(target, tpl, res)
        try:
            from .api_save import build_mobile_detail as _bmd0
            target = dict(target)
            target["mobileDetail"] = _bmd0(str(target.get("detail") or ""))
        except Exception:
            pass
        # 平台主图不接受 webp：重试发布同样要先把目标商品 webp 主图/详情图转 JPG
        try:
            _wt = {"images": [x for x in str(target.get("imageURLs") or "").split(";") if x],
                   "detail": str(target.get("detail") or "")}
            self._fix_webp_images(_wt, res)
            _changed = False
            if _wt.get("images"):
                _new_urls = ";".join(_wt["images"])
                if _new_urls != str(target.get("imageURLs") or ""):
                    target = dict(target); target["imageURLs"] = _new_urls; _changed = True
            if _wt.get("detail") and _wt["detail"] != str(target.get("detail") or ""):
                target = dict(target); target["detail"] = _wt["detail"]; _changed = True
        except Exception as _we:  # noqa: BLE001
            self._step_safe(res, "主图转JPG", False, str(_we)[:80])
        _img_err = self._check_main_image_for_publish(
            [x for x in str(target.get("imageURLs") or "").split(";") if x], res)
        if _img_err:
            _fixed_target = self._repair_main_images(target, tpl, res)
            if _fixed_target is None:
                return SaveResult(ok=False, message=_img_err)
            target = _fixed_target
            _img_err = self._check_main_image_for_publish(
                [x for x in str(target.get("imageURLs") or "").split(";") if x], res)
            if _img_err:
                return SaveResult(ok=False, message=_img_err)

        # User-triggered publication retries share the same free data repair checks.
        # Do not rerun AI or submit stale/ambiguous variant data unchanged.
        try:
            from .product_data import hydrate_product
            from .sku_recovery import recover_skus
            from .customs_metadata import refreshed_customs
            repaired, note = recover_skus(hydrate_product({"id": pid}, target), self.dxm)
            target = repaired.get("raw") or target
            if note:
                add_step(res, "SKU 自动修复", True, note)
            hs_code = refreshed_customs(target, tpl, query=getattr(self.dxm, "query_customs_metadata", None))
            if hs_code is not None:
                target = dict(target, hsCode=hs_code)
                add_step(res, "海关属性自动修复", True, "已核对最新版本，海关编码与原属性保持一致")
        except ValueError as error:
            add_step(res, "重试前数据检查", False, str(error))
            return SaveResult(ok=False, message=str(error)[:500])
        # 营销图是发布必填：缺了就本地裁切补上（免费）
        _mk = []
        try:
            _imgs = [x for x in str(target.get("imageURLs") or "").split(";") if x.strip()]
            _base = _imgs[0] if _imgs else ""
            _mk = self._fix_market_images(
                str(target.get("marketImg1") or ""),
                str(target.get("marketImg2") or ""), _base, res)
            if not _mk:
                _mk = self._fallback_market_images(target, tpl, res)
            if _mk:
                target = dict(target)
                target["marketImg1"] = _mk[0]
                if len(_mk) > 1:
                    target["marketImg2"] = _mk[1]
        except Exception:
            pass
        # 描述图 >5MB 会被站点拒绝：只发布这条路也要压缩（否则会一直失败）
        try:
            _d0 = str(target.get("detail") or "")
            _d1 = self._shrink_detail_images(_d0, res)
            if _d1 != _d0:
                target = dict(target)
                target["detail"] = _d1
                from .api_save import build_mobile_detail as _bmd2
                target["mobileDetail"] = _bmd2(_d1)
        except Exception:
            pass

        target = self._repair_ships_from(target, res)
        target = self._dedupe_product_skus(target, res)
        target = self._resolve_auto_category(target, res)
        target = self._repair_origin_attribute(target, res)
        # 发布失败重试也必须走运费模板预审/安全模板切换，否则 DAILY_TPL 会一直失败。
        self._smt_shipping_preflight(target, res, tpl)
        _attr_ov = self._smt_attr_overrides(target, tpl, res)
        try:
            payload = build_save_payload(
                target, tpl, action="publish",
                overrides=_attr_ov or None,
                custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None),
                cargo_price=str(getattr(self.cfg, "declared_value", "") or "") or "50",
                cfg=self.cfg)
            if hs_code is not None and str(payload.get("categoryId")) != str(target.get("categoryId")):
                raise ValueError("发布类目与海关属性查询类目不同，已停止重试；请确认类目设置")
            resp = self.dxm.save_product_via_api(payload)
        except ValueError as ex:
            return SaveResult(ok=False, message=str(ex)[:300])
        except Exception as ex:  # noqa: BLE001
            return SaveResult(ok=False, message=f"发布失败：{str(ex)[:200]}")
        _code = resp.get("code") if isinstance(resp, dict) else None
        if _code == 0:
            _d = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            _m = str(_d.get("msg") or resp.get("msg") or "")
            if is_failure_msg(_m):
                return SaveResult(ok=False, message=f"发布失败：{_m[:200]}")
            _out = self._await_publish_outcome(pid, "publish")
            if _out is not None and not _out.get("ok"):
                _pend = _out.get("category") in ("processing", "uncertain")
                return SaveResult(ok=False, published=False, pending=_pend,
                                  message=("已提交，平台仍在处理中，发布结果待确认，不要重复发布"
                                           if _pend else
                                           f"已提交但未确认发布成功：{_out.get('label')}（{_out.get('state') or '状态未知'}）"))
            return SaveResult(ok=True, message=f"发布已确认：{_out.get('label') if _out else '已提交'}（商品ID {pid}）",
                              published=True)
        _m2 = str(resp.get("msg") or "")
        if any(k in _m2 for k in ("PRODUCT_IN_AUDIT_PENDING", "audit process", "正在审核中", "审核中")):
            return SaveResult(ok=True, published=True,
                              message=f"已在审核中（商品ID {pid}）：无需重复发布")
        if "DAILY_TPL" in _m2 or "运费模板" in _m2:
            _r = self._smt_retry_freight(payload, target, res)
            if _r is not None:
                _out = self._await_publish_outcome(pid, "publish")
                if _out is not None and not _out.get("ok"):
                    return SaveResult(ok=False, published=False,
                                      message=f"换模板后仍未进入审核：{_out.get('label')}")
                return SaveResult(ok=True, published=True,
                                  message=f"发布已确认进入审核（商品ID {pid}）")
        return SaveResult(ok=False, message=f"发布失败：{_m2[:200]}")

    def publish_failed_auto(self, product_id: str, res, attempts: int = 3,
                            wait: float = 4.0) -> "SaveResult":
        """发布失败商品：自动修复 -> 提交 -> 回读，仍有平台错误则再修复重试。"""
        import time as _t
        last = None
        pid = str(product_id)
        for attempt in range(max(1, int(attempts))):
            try:
                out = self.publish_only(pid, res)
            except Exception as ex:  # noqa: BLE001
                out = SaveResult(ok=False, message=f"发布异常：{str(ex)[:180]}")
            last = out
            if getattr(out, "pending", False):
                # 平台处理中：不要重复提交，等待用户稍后刷新；这不是失败。
                return out
            if not out.ok:
                if attempt + 1 < attempts:
                    _t.sleep(1.0)
                continue
            # 提交接口成功不代表平台异步处理成功，回读最终状态。
            try:
                _t.sleep(max(1.0, float(wait)))
                d = self.dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
                st = str(d.get("dxmOfflineState") or d.get("productStatusType") or "")
                err = str(d.get("errMsg") or "").strip()
                if err and st == "publishFail":
                    last = SaveResult(ok=False, message=f"平台仍判失败：{err[:220]}")
                    continue
                if st == "publishFail" and not err:
                    # 状态尚未刷新，继续等一轮；下一轮 publish_only 会重新回读并修复。
                    if attempt + 1 < attempts:
                        _t.sleep(1.0)
                        continue
                return out
            except Exception:
                return out
        return last or SaveResult(ok=False, message="发布失败")

    def _check_main_image_for_publish(self, images, res=None) -> str:
        """发布前校验主图可访问且确实是图片；返回中文错误（空=通过）。

        实测 11 条历史发布失败为「主图处理出错：*.webp」——源图在店小秘 COS 已 404
        或为 webp。webp 会在 _fix_webp_images 转换；源图 404 必须让用户换图，
        不能带着坏图去提交平台。
        """
        import requests as _rq
        urls = [str(u) for u in (images or []) if str(u).startswith("http")]
        if not urls:
            return ""
        url = urls[0]
        try:
            r = _rq.get(url, timeout=20, stream=True)
            if int(r.status_code) != 200:
                return (f"主图链接失效（HTTP {r.status_code}）：{url[:110]}；"
                        "请重新采集/替换主图后再发布")
            chunk = b""
            try:
                chunk = r.raw.read(32) or b""
            except Exception:
                chunk = b""
            ct = str(r.headers.get("content-type") or "").lower()
            magic_ok = (chunk.startswith(b"\x89PNG") or chunk.startswith(b"\xff\xd8")
                        or chunk.startswith(b"RIFF") or chunk.startswith(b"GIF8"))
            if not (ct.startswith("image/") or magic_ok):
                return (f"主图不是有效图片（content-type={ct or '未知'}）：{url[:110]}；"
                        "请重新采集/替换主图后再发布")
        except Exception as ex:  # noqa: BLE001
            return f"主图无法访问（{str(ex)[:80]}）：{url[:110]}；请检查图片链接后再发布"
        return ""

    def _step_safe(self, res, name: str, ok: bool, detail: str = "") -> None:
        """记录步骤；SaveResult（publish_only 重试路径）没有 steps 字段时忽略。"""
        try:
            add_step(res, name, ok, detail)
        except Exception:
            pass

    def _fix_webp_images(self, p: dict, res) -> None:
        """主图/详情图里的 .webp → JPG 再上传（平台主图不支持 webp）。"""
        def _conv(url: str) -> str:
            u = str(url or "")
            if not u.startswith("http"):
                return u
            try:
                import tempfile as _tf, requests as _rq
                from pathlib import Path as _P
                from .hosting import upload as _up
                from .image_ops import compress_jpeg
                raw = _rq.get(u, timeout=60).content
                _need = u.lower().split("?")[0].endswith(".webp") or len(raw) > 5 * 1024 * 1024
                if not _need:
                    return u
                fp = _P(_tf.mkdtemp()) / "m.jpg"
                compress_jpeg(raw, fp, max_bytes=4_800_000, max_side=1600)
                return _up(fp)
            except Exception:
                return u

        try:
            imgs = list(p.get("images") or [])
            new = [_conv(u) for u in imgs]
            if new != imgs and any(x.lower().split("?")[0].endswith(".jpg") for x in new):
                p["images"] = new
                self._step_safe(res, "主图转JPG", True, "webp 主图已转成 JPG 并重新上传")
        except Exception:
            pass
        try:
            dimgs = list(p.get("detail_images") or [])
            newd = [_conv(u) for u in dimgs]
            if newd != dimgs:
                p["detail_images"] = newd
        except Exception:
            pass
        try:
            import re as _re_w
            html = str(p.get("detail") or "")
            if html:
                urls = set(_re_w.findall(r'<img[^>]+src=["\']([^"\']+)', html, _re_w.I))
                changed = False
                for uu in urls:
                    nv = _conv(uu)
                    if nv != uu:
                        html = html.replace(uu, nv)
                        changed = True
                if changed:
                    p["detail"] = html
                    self._step_safe(res, "详情图压缩", True, "超过 5MB / webp 的详情图已压缩并重新上传")
        except Exception:
            pass

    def _apply_four_in_one(self, p: dict, res, enabled=None, position=None) -> None:
        """四合一主图：本地 PIL 拼 2~4 张主图，插入首位/末尾（不调 AI）。"""
        if not (getattr(self.cfg, "four_in_one", False) if enabled is None else enabled):
            return
        try:
            import tempfile as _tf4
            from pathlib import Path as _P4
            from .image_ops import four_in_one as _four4
            _urls4 = self._group_urls(p, "main")
            if len(_urls4) < 2:
                add_step(res, "四合一", False, "主图不足 2 张，跳过")
                return
            _d4 = _P4(_tf4.mkdtemp())
            _imgs4 = []
            for _i4, _u4 in enumerate(_urls4[:4]):
                try:
                    _f4 = self._fetch(str(_u4), _d4)
                    _imgs4.append(_f4)
                except Exception:
                    continue
            if len(_imgs4) < 2:
                add_step(res, "四合一", False, "可用主图不足 2 张，跳过")
                return
            _out4 = _d4 / "four.png"
            _four4(_imgs4, _out4)
            # 与其它本地图片一起在统一转存阶段上传，拼图本身不联网、不调用 AI。
            _url4 = str(_out4)
            _cur4 = list(p.get("images") or [])
            if str(position or getattr(self.cfg, "four_in_one_position", "append") or "append") == "first":
                p["images"] = [_url4] + _cur4
                _where = "第一位"
            else:
                p["images"] = _cur4 + [_url4]
                _where = "末尾"
            add_step(res, "四合一", True, f"本地拼图（{len(_imgs4)} 张，不调 AI）→ 插入{_where}")
        except Exception as ex:  # noqa: BLE001
            add_step(res, "四合一", False, str(ex)[:80])

    def _apply_sku_image_replace(self, p: dict, res) -> None:
        """SKU 图替换：只要配置了映射就生效（不依赖 SKU 白底开关）。"""
        try:
            import json as _jsr
            repl = _jsr.loads(getattr(self.cfg, "sku_image_replace", "") or "{}")
        except Exception:
            repl = {}
        if not isinstance(repl, dict) or not repl:
            return
        skus = list(p.get("skus") or [])
        _norm = lambda x: str(x or "").strip().lower().replace(" ", "")
        # 映射表可能按「模板 SKU 名」填，商品上的名字可能带前缀/编码，做多键匹配
        _lookup = {_norm(k): str(v).strip() for k, v in repl.items() if str(v or "").strip()}
        hit = 0
        for sku in skus:
            cands = [sku.get("sku"), sku.get("name"), sku.get("skuCode"),
                     sku.get("original_sku")]
            url = ""
            for c in cands:
                url = _lookup.get(_norm(c), "")
                if url:
                    break
            if url:
                sku["image"] = url
                sku["processed_image"] = url      # api_save 从 processed_image 取图
                hit += 1
        # 名字实在对不上、且数量一致时，按顺序替换（用户按模板顺序填的）
        if hit == 0 and len(_lookup) == len(skus) and skus:
            for sku, (k, v) in zip(skus, repl.items()):
                sku["image"] = str(v)
                sku["processed_image"] = str(v)
                hit += 1
            add_step(res, "SKU 图替换", True, f"按顺序替换 {hit} 个 SKU（名称未匹配，改用顺序）")
            return
        add_step(res, "SKU 图替换", hit > 0,
                 f"{hit} 个 SKU 已换成指定图片" if hit else "映射里的 SKU 名没有匹配到商品")

    def _prepare_video(self, p: dict, res, product: dict, dry_run: bool):
        """主图视频：模板视频 / 生成轮播视频 → 上传店小秘 → p["csp_video_id"]。

        失败只记日志不阻断保存（视频是可选增强）。
        """
        if dry_run or self.dxm is None:
            return
        from pathlib import Path
        # 视频来源：兼容老开关（use_template_video / generate_main_video）
        _sv = str(getattr(self.cfg, "video_source", "") or "").strip().lower()
        if _sv not in ("collected", "auto", "template", "generated", "none"):
            if getattr(self.cfg, "use_template_video", False):
                _sv = "template"
            elif getattr(self.cfg, "generate_main_video", False):
                _sv = "generated"
            else:
                _sv = "collected"
        if _sv == "none":
            p["csp_video_id"] = ""          # 明确不要视频
            p["video_url"] = ""             # 同时清掉采集/模板留下的视频地址
            p["clear_video"] = True
            add_step(res, "视频", True, "已选择「不要视频」，将清空商品原有视频")
            return
        if _sv in ("collected", "auto"):
            _vu = ""
            try:
                _raw = (product or {}).get("raw") or {}
                _vu = str(_raw.get("videoUrl") or _raw.get("video_url") or "").strip()
            except Exception:
                _vu = ""
            if _vu:
                p["video_url"] = _vu
                add_step(res, "视频", True, "使用采集到的产品视频（写回 videoUrl）")
                return
            if _sv == "collected":
                add_step(res, "视频", True, "使用采集到的产品视频（未检测到视频 URL）")
                return
            # auto：没有采集视频 → 继续走主图轮播生成
        src_video: str = ""
        raw = product.get("raw") or {}
        # ① 模板视频：自动读取模板商品视频链接，不允许本地路径
        if _sv == "template":
            _vu = ""
            try:
                _tpl_id = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                if self.dxm is not None and _tpl_id:
                    _tpl = _cached_template_edit(self.dxm, _tpl_id)
                    _vu = str(_tpl.get("videoUrl") or _tpl.get("video_url") or "").strip()
            except Exception:
                _vu = ""
            if _vu:
                p["video_url"] = _vu
                add_step(res, "视频", True, "使用模板商品的视频（videoUrl 写回）")
                return
            add_step(res, "视频", False, "模板视频：模板商品没有视频链接，已跳过")
            return
        # ② 生成轮播视频（本地 ffmpeg 合成，不调 AI）
        if not src_video and _sv in ("generated", "auto") and p.get("images"):
            try:
                import tempfile as _tf
                from .video import generate_carousel_video
                d = Path(_tf.mkdtemp())
                imgs = []
                for i, u in enumerate(p["images"][:6]):
                    try:
                        imgs.append(self._fetch(str(u), d))
                    except Exception:
                        continue
                if imgs:
                    output_dir = str(getattr(self.cfg, "video_output_dir", "") or "").strip()
                    target_dir = Path(output_dir).expanduser() if output_dir else d
                    target_dir.mkdir(parents=True, exist_ok=True)
                    import uuid
                    out = target_dir / f"carousel-{uuid.uuid4().hex[:12]}.mp4"
                    generate_carousel_video(
                        imgs, out,
                        slot_seconds=float(getattr(self.cfg, "video_slot_seconds", 2.5) or 2.5),
                        transition_seconds=float(getattr(self.cfg, "video_transition_seconds", .5) or .5),
                        size=str(getattr(self.cfg, "video_size", "1080x1080") or "1080x1080"),
                        cover=bool(getattr(self.cfg, "video_cover", True)),
                        fade_edges=bool(getattr(self.cfg, "video_fade_edges", True)))
                    src_video = str(out)
                    p["main_video"] = src_video
                    add_step(res, "生成轮播视频", True, f"{len(imgs)} 张图已合成")
            except Exception as ex:  # noqa: BLE001
                add_step(res, "生成轮播视频", False, str(ex)[:80])
                return
        if not src_video:
            return
        # 视频只上传店小秘图片空间（COS → cosDxmCallBack），不经过任何自有 VPS。
        try:
            from ..integrations.cos_upload import upload_video
            add_step(res, "视频上传", True, "开始上传店小秘，视频较大时需要等待…")
            _timeout = int(getattr(self.cfg, "video_upload_timeout", 180) or 180)
            vid = upload_video(self.dxm, Path(src_video), timeout=_timeout)
            if vid:
                p["csp_video_id"] = vid
                add_step(res, "视频上传", True, f"已上传店小秘，cspVideoId={vid[:24]}")
                return
        except Exception as ex:  # noqa: BLE001
            add_step(res, "视频上传", False, f"店小秘视频上传失败：{str(ex)[:160]}")

    def _category_sku_score(self, category_id: str, target: dict) -> int:
        """类目与现有 SKU 属性值的匹配分：越高越适合当前 SKU。"""
        if not category_id or self.dxm is None:
            return 0
        cache = getattr(self, "_cat_sku_score_cache", None)
        if not isinstance(cache, dict):
            cache = self._cat_sku_score_cache = {}
        key = (str(category_id), str(target.get("aeopAeProductSKUs") or ""))
        if key in cache:
            return cache[key]
        try:
            import json as _j
            defs = self.dxm.category_attributes(str(category_id)) or []
            vals = {}
            for a in defs:
                if not isinstance(a, dict) or not a.get("sku"):
                    continue
                raw = a.get("values") or "[]"
                if isinstance(raw, str):
                    raw = _j.loads(raw)
                vals[str(a.get("arrtNameId") or "")] = {
                    str(v.get("id") or "") for v in raw if isinstance(v, dict)}
            skus = target.get("aeopAeProductSKUs")
            if isinstance(skus, str):
                skus = _j.loads(skus or "[]")
            score = 0
            for row in skus if isinstance(skus, list) else []:
                for prop in (row.get("aeopSKUProperty") or []):
                    pid = str(prop.get("skuPropertyId") or "")
                    vid = str(prop.get("propertyValueId") or "")
                    if pid in vals:
                        score += 2 if vid in vals[pid] else -2
            cache[key] = score
            return score
        except Exception:
            cache[key] = 0
            return 0

    def _resolve_auto_category(self, target: dict, res=None) -> dict:
        """在属性补全前确定类目：关键词规则 → 商品类目 → 平台预测类目。"""
        if not isinstance(target, dict):
            return target
        if not bool(getattr(self.cfg, "auto_detect_category", True)):
            return target
        cid = str(target.get("categoryId") or "").strip()
        if cid and cid not in ("0", "-1", "None", "null"):
            # 已有类目也可能是上一轮自动预测错误留下的；如果它与 SKU 明显不兼容，
            # 而模板类目兼容，则在自动模式下纠正。
            try:
                _tpl_id = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                if _tpl_id and self.dxm is not None:
                    _tpl = self.dxm._get("product_detail", {"id": _tpl_id}).get("data", {}).get("product", {}) or {}
                    _tpl_cat = str(_tpl.get("categoryId") or "").strip()
                    if _tpl_cat and _tpl_cat != cid:
                        _s_cur = self._category_sku_score(cid, target)
                        _s_tpl = self._category_sku_score(_tpl_cat, target)
                        if _s_tpl >= 2 and _s_tpl > _s_cur + 2:
                            target = dict(target)
                            target["categoryId"] = _tpl_cat
                            if res is not None:
                                try:
                                    add_step(res, "自动类目", True,
                                             f"原类目 {cid} 与 SKU 不兼容，已纠正为模板类目 {_tpl_cat}")
                                except Exception:
                                    pass
                            return target
            except Exception:
                pass
            return target
        title = str(target.get("subject") or target.get("title") or "")
        mapped = ""
        try:
            from .category_map import match as _cat_match
            mapped = str(_cat_match(title) or "").strip()
        except Exception:
            mapped = ""
        if not mapped:
            try:
                from .smt_attributes import suggest_category_id
                mapped = str(suggest_category_id(target) or "").strip()
            except Exception:
                mapped = ""
        if mapped and mapped not in ("0", "-1", "None", "null"):
            _mapped = mapped
            # 平台预测类目不一定兼容采集数据里的 SKU 属性值（如插件/颜色 id）。
            # 若模板类目与 SKU 更匹配，优先模板类目，避免 CHK_BASIC_ONEOF。
            try:
                from .product_data import sku_rows as _sku_rows
                _tpl_id = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                if _tpl_id and _tpl_id != _mapped and self.dxm is not None:
                    _tpl = self.dxm._get("product_detail", {"id": _tpl_id}).get("data", {}).get("product", {}) or {}
                    _tpl_cat = str(_tpl.get("categoryId") or "").strip()
                    if _tpl_cat and _tpl_cat != _mapped:
                        _s_new = self._category_sku_score(_mapped, target)
                        _s_tpl = self._category_sku_score(_tpl_cat, target)
                        if _s_tpl > _s_new:
                            if res is not None:
                                try:
                                    add_step(res, "自动类目", True,
                                             f"平台预测类目 {_mapped} 与 SKU 不兼容，改用模板类目 {_tpl_cat}")
                                except Exception:
                                    pass
                            _mapped = _tpl_cat
            except Exception:
                pass
            target = dict(target)
            target["categoryId"] = _mapped
            if res is not None:
                try:
                    if not any(s.name == "自动类目" for s in getattr(res, "steps", [])):
                        add_step(res, "自动类目", True,
                                 f"自动采用类目 {_mapped}，随后按该类目检查属性")
                except Exception:
                    pass
        return target

    def _smt_attr_overrides(self, target: dict, tpl: dict | None, res) -> dict:
        """保存/发布前补齐速卖通类目必填属性（只补缺失、不覆盖已有；纯本地规则）。

        - ``cfg.smt_attr_autofill`` 默认 True，可关闭；
        - 类目属性定义来自店小秘只读接口 ``category_attributes``；
        - 只返回 ``aeopAeProductPropertys`` 覆盖项，失败不阻断保存（add_step 记录原因）。
        """
        self._smt_attr_missing = []
        if not bool(getattr(self.cfg, "smt_attr_autofill", True)):
            return {}
        if self.dxm is None or not isinstance(target, dict):
            return {}
        try:
            from .smt_attributes import parse_product_attributes, repair_product_attributes
            cid = str(target.get("categoryId") or "").strip()
            if not cid or cid in ("0", "-1", "None", "null"):
                return {}
            try:
                attrs = self.dxm.category_attributes(cid) or []
            except Exception as ex:  # noqa: BLE001
                add_step(res, "属性补全", False, f"读取类目属性失败，跳过：{str(ex)[:60]}")
                return {}
            if not attrs:
                return {}
            tpl_props = None
            if tpl:
                raw = tpl.get("aeopAeProductPropertys") or tpl.get("productProperties")
                if raw:
                    try:
                        tpl_props = parse_product_attributes(raw)
                    except Exception:
                        tpl_props = None
            rep = repair_product_attributes(target, attrs, template_props=tpl_props)
            new_json = (rep.get("payload") or {}).get("aeopAeProductPropertys")
            if not new_json:
                return {}
            # 采集数据的品牌/产地常是不在新类目枚举里的自由文本；平台会拒绝。
            # 按类目枚举选择安全值：品牌优先 NONE，产地优先中国大陆，其余唯一值兜底。
            _repaired_enum = 0
            _fixed_missing = set()
            try:
                import json as _j
                _defs = [a for a in attrs if isinstance(a, dict)]
                _by_id = {str(a.get("arrtNameId") or ""): a for a in _defs}
                _props = _j.loads(new_json or "[]")
                if not isinstance(_props, list):
                    _props = []
                for miss in (rep.get("missing") or []):
                    if not isinstance(miss, dict):
                        continue
                    _nid = str(miss.get("name_id") or "")
                    _def = _by_id.get(_nid)
                    if not _def:
                        continue
                    _vals = _def.get("values") or "[]"
                    if isinstance(_vals, str):
                        _vals = _j.loads(_vals)
                    _label = (str(_def.get("nameEn") or "") + " " + str(_def.get("nameZh") or "")).lower()
                    _prefs = []
                    if "brand" in _label or "品牌" in _label:
                        _prefs = ["none", "no brand", "unbranded"]
                    elif "origin" in _label or "产地" in _label:
                        _prefs = ["mainland china", "china", "中国大陆"]
                    _pick = None
                    for _p in _prefs:
                        for _v in _vals if isinstance(_vals, list) else []:
                            _names = (_v.get("names") or {})
                            _txt = (str(_names.get("en") or "") + " " +
                                    str(_names.get("zh") or "")).lower()
                            if _p in _txt:
                                _pick = _v
                                break
                        if _pick:
                            break
                    if not _pick and isinstance(_vals, list) and len(_vals) == 1:
                        _pick = _vals[0]
                    if not _pick:
                        continue
                    _nm = str(_def.get("nameEn") or _def.get("nameZh") or "")
                    _props = [x for x in _props if not (
                        isinstance(x, dict) and
                        (str(x.get("attrName") or "").strip().lower() == _nm.strip().lower()
                         or str(x.get("attrNameId") or "") == _nid))]
                    _props.append({"attrNameId": _nid,
                                   "attrValueId": str(_pick.get("id") or ""),
                                   "attrName": _nm})
                    _repaired_enum += 1
                    _fixed_missing.add(_nid)
                if _repaired_enum:
                    new_json = _j.dumps(_props, ensure_ascii=False)
            except Exception:
                _repaired_enum = 0
            before = rep.get("before") or {}
            after = rep.get("after") or {}
            missing = [m for m in (rep.get("missing") or [])
                       if str((m or {}).get("name_id") or "") not in _fixed_missing]
            # AI 兜底：只对“本地补不出的必填属性”调用一次，按类目枚举校验后写入。
            if missing and bool(getattr(self.cfg, "smt_attr_ai_autofill", True)) \
                    and str(getattr(self.cfg, "api_key", "") or "").strip():
                try:
                    from . import model_of as _model_of
                    from ..api import ApiClient as _ApiClient
                    from .smt_attributes import (ai_suggest_missing as _ai_suggest,
                        parse_category_attributes as _parse_cats)
                    _api = _ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)
                    _model = _model_of(self.cfg, "text")
                    def _chat(messages, max_tokens=400):
                        return _api.chat(_model, messages, max_tokens=max_tokens)
                    _cats = _parse_cats(attrs)
                    _sugg = _ai_suggest(missing, _cats, _chat,
                                        title=str(target.get("subject") or ""),
                                        detail=str(target.get("detail") or ""))
                    _ai_fixed = 0
                    for _nid, _v in (_sugg or {}).items():
                        _nm = str(_v.get("name") or _nid)
                        _vid = str(_v.get("value_id") or "")
                        _text = str(_v.get("value") or "")
                        _prop = ({"attrNameId": _nid, "attrValueId": _vid, "attrName": _nm}
                                 if _vid else {"attrName": _nm, "attrValue": _text})
                        _props = [x for x in _props if not (
                            isinstance(x, dict) and
                            (str(x.get("attrNameId") or "") == _nid or
                             str(x.get("attrName") or "").strip().lower() == _nm.strip().lower()))]
                        _props.append(_prop)
                        _fixed_missing.add(_nid)
                        _ai_fixed += 1
                    if _ai_fixed:
                        new_json = _j.dumps(_props, ensure_ascii=False)
                        missing = [m for m in missing
                                   if str((m or {}).get("name_id") or "") not in _fixed_missing]
                        add_step(res, "属性AI补全", True, f"AI 按类目枚举补了 {_ai_fixed} 项必填属性")
                except Exception as _ai_ex:  # noqa: BLE001
                    add_step(res, "属性AI补全", False, f"AI 属性建议跳过：{str(_ai_ex)[:80]}")
            # 最后兜底：AI 仍补不出的必填属性，按类目枚举选择安全值 / 原文值填上，
            # 而不是把“属性不能为空”留给平台拒绝。
            if missing:
                try:
                    _fallback_fixed = 0
                    for _m in list(missing):
                        _nid = str((_m or {}).get("name_id") or "")
                        _def = _by_id.get(_nid)
                        if not _def:
                            continue
                        _vals = _def.get("values") or "[]"
                        if isinstance(_vals, str):
                            try: _vals = _j.loads(_vals)
                            except Exception: _vals = []
                        _label = (str(_def.get("nameEn") or "") + " " +
                                  str(_def.get("nameZh") or "")).lower()
                        _nm = str(_def.get("nameEn") or _def.get("nameZh") or _nid)
                        _pick = None
                        _prefs = []
                        if "brand" in _label or "品牌" in _label:
                            _prefs = ["none", "no brand", "unbranded", "other"]
                        elif "origin" in _label or "产地" in _label:
                            _prefs = ["mainland china", "china", "中国大陆", "other"]
                        else:
                            _prefs = ["other", "none", "not applicable", "no", "n/a"]
                        for _p in _prefs:
                            for _v in (_vals if isinstance(_vals, list) else []):
                                _names = (_v.get("names") or {}) if isinstance(_v, dict) else {}
                                _txt = (str(_names.get("en") or "") + " " +
                                        str(_names.get("zh") or "")).lower()
                                if _p in _txt:
                                    _pick = _v
                                    break
                            if _pick:
                                break
                        if _pick is None and isinstance(_vals, list) and _vals:
                            _pick = _vals[0]
                        if isinstance(_pick, dict) and _pick.get("id"):
                            _props = [x for x in _props if not (
                                isinstance(x, dict) and
                                (str(x.get("attrNameId") or "") == _nid or
                                 str(x.get("attrName") or "").strip().lower() == _nm.strip().lower()))]
                            _props.append({"attrNameId": _nid,
                                           "attrValueId": str(_pick.get("id") or ""),
                                           "attrName": _nm})
                        else:
                            # 自由文本属性：使用原商品标题/描述中的真实内容，至少不留空。
                            _text = str(target.get("subject") or target.get("title") or "").strip()
                            if not _text:
                                _text = "Other"
                            _props = [x for x in _props if not (
                                isinstance(x, dict) and
                                str(x.get("attrName") or "").strip().lower() == _nm.strip().lower())]
                            _props.append({"attrName": _nm, "attrValue": _text[:200]})
                        _fixed_missing.add(_nid)
                        _fallback_fixed += 1
                    if _fallback_fixed:
                        new_json = _j.dumps(_props, ensure_ascii=False)
                        missing = [m for m in missing
                                   if str((m or {}).get("name_id") or "") not in _fixed_missing]
                        add_step(res, "属性兜底补全", True,
                                 f"按类目枚举/原文自动补了 {_fallback_fixed} 项必填属性")
                except Exception as _fb_ex:  # noqa: BLE001
                    add_step(res, "属性兜底补全", False, f"兜底补全跳过：{str(_fb_ex)[:80]}")
            self._smt_attr_missing = missing
            detail = (f"系统属性 {before.get('system_attributes', '?')}→"
                      f"{after.get('system_attributes', '?')}；"
                      f"自定义属性 {after.get('custom_attributes', '?')}")
            if _repaired_enum:
                detail += f"；已按类目枚举自动替换 {_repaired_enum} 项"
            if missing:
                _r = str((missing[0] or {}).get("reason") or "").strip()
                detail += f"；仍缺必填 {len(missing)} 项" + (f"（{_r[:40]}）" if _r else "")
            else:
                detail += "；必填已齐"
            add_step(res, "属性补全", True, detail)
            return {"aeopAeProductPropertys": str(new_json)}
        except Exception as ex:  # noqa: BLE001
            add_step(res, "属性补全", False, f"跳过（{str(ex)[:60]}）")
            return {}

    def _smt_freight_choices(self, target: dict) -> list[dict]:
        try:
            sid = str(target.get("shopId") or getattr(self.cfg, "store_id", "") or "")
            return self.dxm.freight_templates(sid) if self.dxm is not None and sid else []
        except Exception:
            return []

    def _smt_safe_freight_id(self, target: dict) -> str:
        choices = self._smt_freight_choices(target)
        ids = {str(x.get("templateId") or ""): str(x.get("templateName") or "") for x in choices}
        configured = str(getattr(self.cfg, "smt_freight_template_id", "") or "").strip()
        if configured and configured in ids:
            return configured
        for tid, name in ids.items():
            if tid == "1000" or "new seller" in name.casefold() or "新卖家" in name:
                return tid
        return next((tid for tid in ids if tid), "")

    def _smt_shipping_preflight(self, target: dict, res, tpl: dict | None = None) -> bool:
        """运费模板预审：选模板时沿用模板物流，失败商品才切安全模板。"""
        if not isinstance(target, dict) or self.dxm is None:
            return False
        state = str(target.get("dxmOfflineState") or "") or str(target.get("productStatusType") or "")
        err = str(target.get("errMsg") or "")
        _tpl_freight = str((tpl or {}).get("freightTemplateId") or "").strip()
        if not _tpl_freight and tpl:
            try:
                import json as _j
                _ts = tpl.get("aeopAeProductSKUs")
                if isinstance(_ts, str):
                    _ts = _j.loads(_ts)
                if isinstance(_ts, list) and _ts:
                    _tpl_freight = str((_ts[0] or {}).get("freightTemplateId") or "").strip()
            except Exception:
                _tpl_freight = ""
        _old_freight = str(target.get("freightTemplateId") or "").strip()
        _shipping_failed = (state == "publishFail" or "DAILY_TPL" in err
                            or "运费模板" in err)
        # 模板商品可能来自另一个店铺；它自己的运费模板在目标店铺不存在。
        # 必须检查目标店铺模板列表，不能跨店铺套用模板 id。
        try:
            _choices_ids = {str(x.get("templateId") or "") for x in self._smt_freight_choices(target)}
        except Exception:
            _choices_ids = set()
        if _tpl_freight and _choices_ids and _tpl_freight not in _choices_ids:
            add_step(res, "运费模板预审", True,
                     f"模板商品模板 {_tpl_freight} 不属于当前店铺，已改用当前店铺模板")
            _tpl_freight = ""
        if _tpl_freight and not (_shipping_failed and _old_freight == _tpl_freight):
            # 模板商品优先：把模板的运费模板写到目标商品及 SKU。
            # 但失败商品若和模板用的是同一个失效模板，必须切安全模板。
            target["freightTemplateId"] = _tpl_freight
            try:
                import json as _j
                skus = target.get("aeopAeProductSKUs")
                if isinstance(skus, str):
                    skus = _j.loads(skus or "[]")
                if isinstance(skus, list):
                    for sku in skus:
                        if isinstance(sku, dict):
                            sku["freightTemplateId"] = _tpl_freight
                    target["aeopAeProductSKUs"] = _j.dumps(skus, ensure_ascii=False)
            except Exception:
                pass
            add_step(res, "运费模板预审", True, f"沿用模板商品运费模板 {_tpl_freight}")
            return True
        safe = self._smt_safe_freight_id(target)
        if _shipping_failed and safe and safe != _old_freight:
            target["freightTemplateId"] = safe
            try:
                import json as _j
                skus = target.get("aeopAeProductSKUs")
                if isinstance(skus, str):
                    skus = _j.loads(skus or "[]")
                if isinstance(skus, list):
                    for sku in skus:
                        if isinstance(sku, dict):
                            sku["freightTemplateId"] = safe
                    target["aeopAeProductSKUs"] = _j.dumps(skus, ensure_ascii=False)
            except Exception:
                pass
            add_step(res, "运费模板预审", True,
                     f"发布失败商品自动切换运费模板：{_old_freight or '空'} → {safe}")
            return True
        if not safe:
            add_step(res, "运费模板预审", False, "店铺没有可用运费模板，无法自动切换")
            return False
        old = str(target.get("freightTemplateId") or "")
        choices = {str(x.get("templateId") or "") for x in self._smt_freight_choices(target)}
        # 发布失败，或现有模板不在店铺模板列表里：自动切换到安全模板。
        should_switch = (state == "publishFail" or "DAILY_TPL" in err or "运费模板" in err
                         or (choices and old and old not in choices))
        if should_switch and old != safe:
            target["freightTemplateId"] = safe
            try:
                import json as _j, copy as _c
                skus = target.get("aeopAeProductSKUs")
                if isinstance(skus, str):
                    skus = _j.loads(skus or "[]")
                if isinstance(skus, list):
                    for sku in skus:
                        if isinstance(sku, dict):
                            sku["freightTemplateId"] = safe
                    target["aeopAeProductSKUs"] = _j.dumps(skus, ensure_ascii=False)
            except Exception:
                pass
            add_step(res, "运费模板预审", True,
                     f"发布失败商品自动切换运费模板：{old or '空'} → {safe}")
        if not should_switch:
            add_step(res, "运费模板预审", True, f"沿用运费模板 {old or '空'}（预审通过）")
        # 产地缺失/冲突是此错误的常见伴随项；交给现有属性补全在下一步处理。
        try:
            raw = str(target.get("aeopAeProductPropertys") or "[]")
            import json as _j
            attrs = _j.loads(raw) if isinstance(raw, str) else raw
            origin = next((a for a in attrs if isinstance(a, dict) and
                           str(a.get("attrName") or a.get("attrNameId") or "").casefold()
                           in ("origin", "219", "place of origin")), None)
            if not origin or not str(origin.get("attrValue") or origin.get("attrValueId") or "").strip():
                add_step(res, "产地预审", False, "商品缺少产地（Origin），会自动用类目安全值补齐")
        except Exception:
            pass
        return True

    def _smt_retry_freight(self, payload: dict, target: dict, res):
        """API 报 DAILY_TPL 时，用安全模板重试一次保存。"""
        safe = self._smt_safe_freight_id(target)
        if not safe or str(payload.get("freightTemplateId") or "") == safe:
            return None
        try:
            import copy as _c
            retry = _c.deepcopy(payload)
            retry["freightTemplateId"] = safe
            try:
                import json as _j
                skus = retry.get("aeopAeProductSKUs")
                if isinstance(skus, str):
                    skus = _j.loads(skus or "[]")
                if isinstance(skus, list):
                    for sku in skus:
                        if isinstance(sku, dict):
                            sku["freightTemplateId"] = safe
                    retry["aeopAeProductSKUs"] = _j.dumps(skus, ensure_ascii=False)
            except Exception as _sku_ex:
                # SKU 运费更新失败：只记日志，仍用主字段修复后继续重试
                from .progress import emit as _emit
                _emit("运费模板修复", "", f"SKU运费更新失败（继续尝试）：{str(_sku_ex)[:80]}", "WARN")
            resp = self.dxm.save_product_via_api(retry)
            if isinstance(resp, dict) and resp.get("code") == 0:
                add_step(res, "运费模板自动修复", True,
                         f"发布失败：已自动切换运费模板 {safe} 并重试成功")
                return resp
        except Exception as _retry_ex:
            # 重试保存失败：记录日志，调用方会继续尝试其他修复路径
            from .progress import emit as _emit
            _emit("运费模板修复", "", f"重试保存失败：{str(_retry_ex)[:100]}", "WARN")
        return None

    def _save_via_api(self, p: dict, res, action: str, ship, images: list[str] | None):
        """用 add.json 的 API 直存路径保存（绕开 DOM）。

        返回 SaveResult；无法走 API（没模板/没 dxm）时返回 None，由调用方回退 DOM。
        """
        from .api_save import build_save_payload, build_ai_overrides

        if self.dxm is None:
            return None
        _tpl_pid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
        if not _tpl_pid:
            return None

        pid = str(p.get("id", ""))
        if str(action or "") == "publish":
            _img_err = self._check_main_image_for_publish(images or p.get("images"), res)
            if _img_err:
                add_step(res, "主图校验", False, _img_err[:160])
                return SaveResult(ok=False, message=_img_err)
        try:
            target = self.dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
            tpl = None
            if bool(getattr(self.cfg, "apply_template_on_save", True)):
                tpl = _cached_template_edit(self.dxm, _tpl_pid)
            target = self._repair_product_images(target, tpl, res)
            try:
                from .api_save import build_mobile_detail as _bmd0
                target = dict(target)
                target["mobileDetail"] = _bmd0(str(target.get("detail") or ""))
            except Exception:
                pass
        except Exception as ex:
            add_step(res, "API 直存", False, f"读取商品详情失败：{str(ex)[:80]}")
            return None
        if not target:
            add_step(res, "API 直存", False, "没取到目标商品详情")
            return None
        # 店小秘的 add.json 只接受「采集箱」商品；待发布/审核中/在线商品会返回
        # 「操作失败，只有处在采集箱中的产品才能执行此操作」
        _st = str(target.get("productStatusType") or "").strip()
        if _st and _st not in ("", "draft", "offline", "waitPublish"):
            _hint = {
                "editingRequired": "该商品在「待发布」",
                "auditing": "该商品在「审核中」",
                "onSelling": "该商品已「在线」",
            }.get(_st, f"该商品状态为 {_st}")
            add_step(res, "API 直存", False,
                     f"{_hint}，店小秘只允许编辑采集箱商品；请先在店小秘把它移入采集箱")
            return SaveResult(ok=False,
                              message=f"{_hint}，店小秘只允许编辑采集箱商品；请先移入采集箱再运行")

        try:
            from .sku_recovery import apply_sku_repair
            target = apply_sku_repair(target, p)
            # 历史/模板合并后仍可能留下相同规格组合；平台会整件拒绝保存。
            # 按配置自动合并（保留首条、库存累加），避免用户手动改 SKU 才能发。
            try:
                import json as _jsk
                from .product_data import sku_rows as _sku_rows, clean_sku_rows as _clean_skus
                from .sku_recovery import _merge_duplicate_rows
                _rows = _clean_skus(_sku_rows(target.get("aeopAeProductSKUs")))
                _merged, _n = _merge_duplicate_rows(_rows)
                if _n:
                    target = dict(target)
                    target["aeopAeProductSKUs"] = _jsk.dumps(_merged, ensure_ascii=False)
                    add_step(res, "SKU 自动修复", True,
                             f"已自动合并 {_n} 个重复规格组合（保留首条、库存累加）")
            except Exception:
                pass
            from .customs_metadata import validate_customs_repair
            validate_customs_repair(target, p)
            # 主图数量上限
            _imgs = images
            _cap = int(getattr(self.cfg, "editor_main_images_max", 6) or 6)
            if _imgs and _cap > 0:
                _imgs = list(_imgs)[:_cap]
            # 平台硬性要求：主图宽高 ≥100，太小会报 isv.134000000002
            if _imgs:
                _imgs = self._ensure_min_image_size(list(_imgs), res)
            _pr = p.get("price_result")
            try:
                _pf = float(getattr(_pr, "final", 0) or 0)
            except Exception:
                _pf = 0
            if _pf > 999999:
                add_step(res, "价格上限", True,
                         f"计算价 ¥{_pf:.2f} 超过速卖通上限 999999，已截断为 999999")
            overrides = build_ai_overrides(target, p, self.cfg, images=_imgs, ship=ship,
                                           template=tpl)
            # 类目必填属性补全（只补缺失；AI 已改属性时以 AI 值为基础再补）
            try:
                _merged_target = dict(target)
                if isinstance(overrides, dict) and overrides.get("aeopAeProductPropertys"):
                    _merged_target["aeopAeProductPropertys"] = overrides["aeopAeProductPropertys"]
                _attr_ov = self._smt_attr_overrides(_merged_target, tpl, res)
                if _attr_ov:
                    overrides = dict(overrides or {})
                    overrides.update(_attr_ov)
            except Exception as _attr_ex:  # noqa: BLE001
                add_step(res, "属性补全", False, f"跳过（{str(_attr_ex)[:60]}）")

            # 营销图是发布必填项：没有就本地裁切补上（免费，不做会报「请补充营销图」）
            try:
                _base_img = ""
                if _imgs:
                    _base_img = str(_imgs[0])
                else:
                    _iu = [x for x in str(target.get("imageURLs") or "").split(";") if x.strip()]
                    _base_img = _iu[0] if _iu else ""
                _mk2 = self._fix_market_images(
                    str(overrides.get("marketImg1") or target.get("marketImg1") or ""),
                    str(overrides.get("marketImg2") or target.get("marketImg2") or ""),
                    _base_img, res)
                if _mk2:
                    overrides["marketImg1"] = _mk2[0]
                    if len(_mk2) > 1:
                        overrides["marketImg2"] = _mk2[1]
            except Exception:
                pass

            # ★ 描述图 >5MB 会被站点拒绝：压缩并替换 URL（覆盖可能来自 AI 详情或无）
            try:
                _eff_detail = str((overrides or {}).get("detail") or target.get("detail") or "")
                _new_detail = self._shrink_detail_images(_eff_detail, res)
                if _new_detail != _eff_detail:
                    overrides = dict(overrides or {})
                    overrides["detail"] = _new_detail
                    from .api_save import build_mobile_detail as _bmd
                    overrides["mobileDetail"] = _bmd(_new_detail)
                    add_step(res, "详情", True, f"描述图已压缩，详情长度 {len(_new_detail)}")
            except Exception as _ex:  # noqa: BLE001
                add_step(res, "描述图压缩", False, str(_ex)[:80])
            target = self._repair_ships_from(target, res)
            target = self._dedupe_product_skus(target, res)
            target = self._resolve_auto_category(target, res)
            target = self._repair_origin_attribute(target, res)
            try:
                _mk_cur = self._fix_market_images(
                    str(target.get("marketImg1") or ""), str(target.get("marketImg2") or ""),
                    next(iter([x for x in str(target.get("imageURLs") or "").split(";") if x]), ""), res)
                if not _mk_cur:
                    _mk_cur = self._fallback_market_images(target, tpl, res)
                if _mk_cur:
                    target = dict(target)
                    target["marketImg1"] = _mk_cur[0]
                    if len(_mk_cur) > 1:
                        target["marketImg2"] = _mk_cur[1]
            except Exception:
                pass
            self._smt_shipping_preflight(target, res, tpl)
            payload = build_save_payload(
                target, tpl, action=action, overrides=overrides,
                custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None),
                cargo_price=str(getattr(self.cfg, "declared_value", "") or "") or "50",
                cfg=self.cfg)
            if p.get("hs_code_override") is not None and str(payload.get("categoryId")) != str(target.get("categoryId")):
                raise ValueError("发布类目与海关属性查询类目不同，已停止保存；请确认类目设置")
            resp = self.dxm.save_product_via_api(payload)
        except ValueError as ex:  # 数据安全类错误（如缺类目）：明确报错，不回退
            add_step(res, "保存中止", False, str(ex)[:200])
            return SaveResult(ok=False, message=str(ex)[:300])
        except Exception as ex:  # noqa: BLE001
            _msg = str(ex)[:300] or type(ex).__name__
            # 商品在速卖通审核中：平台不允许再次编辑保存。这不是失败，日志不要打 ✗，
            # 否则用户会以为商品保存坏了。
            if is_audit_pending(_msg):
                add_step(res, "API 直存", True, "商品在审核中，本次跳过保存（不是失败）")
                return SaveResult(ok=True, message="商品在审核中，已跳过保存")
            # 发布模式（op=2）被拒时降级为「保存」（op=1）：商品先落到待发布，
            # 用户可在店小秘里手动发布。常见于 SKU 缺速卖通侧信息
            # （isv.13CHK_SKU_FREIGHT_TEMPLATE_NULL）。
            # 运费模板错误：先自动换店铺安全模板重试，再考虑降级保存。
            if "DAILY_TPL" in _msg or "运费模板" in _msg:
                _r = self._smt_retry_freight(payload, target, res)
                if _r is not None:
                    _out = self._await_publish_outcome(pid, action)
                    if _out is not None and not _out.get("ok"):
                        return SaveResult(ok=False, published=False,
                                          message=f"换模板后仍未达成目标状态：{_out.get('label')}")
                    return SaveResult(ok=True, published=True,
                                      message="发布成功（运费模板已自动修复）")
            if action == "publish":
                try:
                    _fallback = dict(payload)
                    _fallback["op"] = 1
                    resp2 = self.dxm.save_product_via_api(_fallback)
                    _c2 = resp2.get("code") if isinstance(resp2, dict) else None
                    _m2 = str((resp2.get("data") or {}).get("msg") if isinstance(resp2.get("data"), dict)
                              else resp2.get("msg") or "")
                    if _c2 == 0 and not is_failure_msg(_m2):
                        add_step(res, "API 直存", True,
                                 f"发布被拒（{_msg[:80]}），已改为保存到「待发布」，请在店小秘手动发布")
                        # 直接发布动作不得把“仅保存到待发布”当成功。
                        return SaveResult(ok=False, published=False,
                                          message="发布被拒，仅保存到待发布，未进入审核（不算发布成功）")
                except Exception:
                    pass
            add_step(res, "API 直存", False, f"保存失败：{_msg[:200]}")
            return SaveResult(ok=False, message=f"保存失败：{_msg}")

        _code = resp.get("code") if isinstance(resp, dict) else None
        if _code == 0:
            _msg = ((resp.get("data") or {}).get("msg") if isinstance(resp.get("data"), dict)
                    else str(resp.get("msg") or ""))
            if is_failure_msg(_msg):
                if "DAILY_TPL" in _msg or "运费模板" in _msg:
                    _r = self._smt_retry_freight(payload, target, res)
                    if _r is not None:
                        _out = self._await_publish_outcome(pid, action)
                        if _out is not None and not _out.get("ok"):
                            return SaveResult(ok=False, published=False,
                                              message=f"换模板后仍未达成目标状态：{_out.get('label')}")
                        return SaveResult(ok=True, published=True,
                                          message="发布成功（运费模板已自动修复）")
                add_step(res, "API 直存", False, f"保存失败：{_msg[:200]}")
                return SaveResult(ok=False, message=f"保存失败：{_msg[:200]}")
            _pid2 = ""
            try:
                _d = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                _pid2 = str(_d.get("productId") or _d.get("id") or str(p.get("id", "")))
            except Exception:
                _pid2 = pid
            _out = self._await_publish_outcome(_pid2, action)
            if _out is not None and not _out.get("ok"):
                _pend = _out.get("category") in ("processing", "uncertain")
                return SaveResult(ok=False, published=False, pending=_pend,
                                  message=("已提交，平台仍在处理中，发布结果待确认，不要重复发布"
                                           if _pend else
                                           f"已提交但未达成目标状态：{_out.get('label')}（{_out.get('state') or '状态未知'}）"))
            return SaveResult(ok=True,
                              message=f"已确认{_out.get('label') if _out else '保存'}（商品ID {_pid2}）",
                              published=(action in ("publish", "save_to_waiting")))
        _msg = str(resp.get("msg") or "") or "保存失败"
        return SaveResult(ok=False, message=f"API 直存：{_msg[:200]}")

    def _hydrate_product(self, product: dict) -> dict:
        from .product_data import hydrate_product
        raw = product.get("raw") or {}
        if "aeopAeProductSKUs" in raw:
            return hydrate_product(product, raw)
        get = getattr(getattr(self, "dxm", None), "_get", None)
        if not callable(get):
            return product
        detail = get("product_detail", {"id": str(product.get("id", ""))})
        detail = (detail.get("data") or {}).get("product") or {}
        if not detail:
            raise ValueError("没有读取到商品详情")
        return hydrate_product(product, detail)

    def _apply_workbench_overrides(self, product: dict) -> dict:
        """图片工作台按商品 ID 保存的主图/详情图覆盖。"""
        try:
            import json as _j
            raw = getattr(self.cfg, "workbench_image_overrides", "{}") or "{}"
            data = _j.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            return product
        ov = data.get(str(product.get("id") or "")) if isinstance(data, dict) else None
        if not isinstance(ov, dict):
            return product
        out = dict(product)
        main = [str(x) for x in (ov.get("main") or []) if str(x).strip()]
        detail = [str(x) for x in (ov.get("detail") or []) if str(x).strip()]
        if main:
            out["images"] = main + [x for x in (out.get("images") or []) if x not in main]
        if detail:
            out["detail_images"] = detail
        return out

    def run(self, product: dict, dry_run: bool = True, manage_progress: bool = True) -> PublishResult:
        # ---- 核心层授权校验：改 UI 配置绕不过（必须伪造服务端 ECDSA 签名）----
        if not dry_run:
            try:
                from ..core.guard import require as _require_license
            except Exception:
                _require_license = None      # 导入失败不拦截（测试/异常环境）
            if _require_license is not None:
                try:
                    _require_license(self.cfg)
                except Exception as _le:
                    return PublishResult(product_id=str(product.get("id", "")), ok=False,
                                         error=f"授权校验未通过：{_le}")
        res = PublishResult(product_id=str(product.get("id", "")), ok=True)
        # 自动化测试/验收默认不真实保存，避免误改客户商品
        import os as _os
        if _os.environ.get("AURORA_TEST") or _os.environ.get("AURORA_NO_SAVE"):
            dry_run = True
        if dry_run:
            res.preview = True
            # 预演：把「不改数据也不花钱」的步骤都跑一遍，让用户看到到底会发生什么。
            # 不调用 AI、不下载图片、不连接浏览器、不写配置、不保存商品。
            res.steps.append(Step("预演", True, "不调用 AI、不下载图片、不连接浏览器、不保存商品"))
            try:
                p = self._normalize(product)
                self._apply_sku_image_replace(p, res)
                res.steps.append(Step("标准化", True,
                                      f"{len(p.get('skus') or [])} 个 SKU / "
                                      f"{len(p.get('images') or [])} 张主图"))
            except Exception as ex:  # noqa: BLE001
                res.steps.append(Step("标准化", False, f"失败：{str(ex)[:80]}"))
                res.ok = False
                res.error = "预演：标准化失败"
                return res

            # 价格（纯计算，不联网）
            try:
                self._price(p)
                pr = p.get("price_result")
                if pr is not None:
                    _note = getattr(pr, "note", "") or getattr(pr, "mode", "")
                    _final = getattr(pr, "final", None)
                    res.steps.append(Step("价格计算", True, _note if getattr(pr, "mode", "") == "preserve" else f"{_note} · ¥{_final}"))
            except Exception as ex:  # noqa: BLE001
                res.steps.append(Step("价格计算", False, str(ex)[:80]))

            # 图片计划（纯计算）
            try:
                from .image_task_plan import build_plan
                plan = build_plan(self.cfg, p)
                if getattr(plan, "steps", None):
                    res.steps.append(Step("图片计划", not getattr(plan, "blockers", False),
                                          "；".join(plan.steps[:4])))
                for _w in getattr(plan, "warnings", [])[:3]:
                    res.steps.append(Step("图片计划提醒", True, _w))
            except Exception as ex:  # noqa: BLE001
                # 预演也要如实反映「计划算不出来」：如果这里静默跳过，用户会以为
                # 图片处理已规划好，真实运行时却直接失败。失败步骤会汇总到
                # res.ok=False + res.error（见下方 failures 聚合）。
                res.steps.append(Step("图片计划", False, f"生成失败：{str(ex)[:80]}"))

            # 图片方案摘要（新引擎自带 dry_run 分支，不调 AI）
            try:
                before = len(p.get("images") or [])
                self._apply_image_jobs(p, res, dry_run=True)
                after = len(p.get("images") or [])
                if before != after:
                    res.steps.append(Step("图片结果", True, f"预计 {before} → {after} 张"))
            except Exception as ex:  # noqa: BLE001
                res.steps.append(Step("图片方案", False, str(ex)[:80]))

            # 缺少详情时无法确认 SKU 身份；已有详情的多 SKU 可核验后写回。
            if (getattr(self.cfg, "write_sku_codes_to_product", True)
                    and len(product.get("skus") or []) > 1
                    and not product.get("_source_sku_rows")):
                res.steps.append(Step("保存能力检查", False,
                                      "多 SKU 缺少原始详情，真实执行前必须重新读取并核对变种身份"))

            failures = [step for step in res.steps if not step.ok]
            if failures:
                res.ok = False
                res.error = "预演失败：" + "；".join(f"{step.name}：{step.detail}" for step in failures)

            res.steps.append(Step("保存到店小秘", True, "dry-run：未写入；点「真实写入」才会保存"))
            return res
        # Check known editor limitations before any paid processing or uploads.
        if getattr(self.cfg, "combo_enable", False):
            res.ok = False
            res.error = "套装 SKU 尚未支持安全创建编辑器行，请关闭套装后再执行"
            res.steps.append(Step("保存能力检查", False, res.error))
            return res
        try:
            hydrate = getattr(self, "_hydrate_product", None)
            if callable(hydrate):
                product = hydrate(product)
            product = self._apply_workbench_overrides(product)
            from .sku_recovery import recover_skus
            product, repair_note = recover_skus(product, getattr(self, "dxm", None))
            if repair_note:
                res.steps.append(Step("SKU 自动修复", True, repair_note))
            # 合并重复变种后行数会变少：同步重建待处理 SKU 列表，否则后面的
            # 身份匹配会因为「数量不一致」把整单判失败。
            try:
                _rows = product.get("_source_sku_rows") or []
                if repair_note and _rows and len(_rows) != len(product.get("skus") or []):
                    from .product_data import hydrate_product as _hp
                    product["skus"] = []
                    product = _hp(product, product.get("raw") or {})
                    res.steps.append(Step("SKU 重建", True,
                                          f"变种数量已同步为 {len(product['skus'])} 个"))
            except Exception as _ex:
                res.steps.append(Step("SKU 重建", False, str(_ex)[:80]))
            if product.get("_source_sku_rows"):
                from .api_save import match_processed_skus, validate_sku_combinations
                match_processed_skus(product["_source_sku_rows"], product.get("skus") or [])
                validate_sku_combinations(product["_source_sku_rows"])
        except Exception as ex:
            res.ok = False
            res.error = f"商品详情/SKU 身份校验失败：{ex}；本次未调用 AI"
            return res
        if (getattr(self.cfg, "write_sku_codes_to_product", True)
                and len(product.get("skus") or []) > 1 and not product.get("_source_sku_rows")):
            res.ok = False
            res.error = ("多 SKU 缺少可验证的商品详情，无法匹配原始变种；请重新读取商品；本次未调用 AI")
            res.steps.append(Step("保存能力检查", False, res.error))
            return res
        # 价格缺失必须在付费 AI 处理前发现，避免处理完成后才保存失败。
        try:
            self._price(product)
            if product.get("_preserve_existing_prices"):
                res.steps.append(Step("价格自动修复", True, product["price_result"].note))
        except (TypeError, ValueError) as error:
            res.ok = False
            res.error = f"价格预检失败：{error}；本次未调用 AI"
            res.steps.append(Step("价格预检", False, res.error))
            return res
        try:
            from .customs_metadata import refreshed_customs
            raw = product.get("raw") or {}
            error = str(raw.get("errMsg") or "")
            if "CHK_GLOBAL_PROPERTY_VERSION_ERROR" in error or "海关监管属性已升级" in error:
                template_id = str(getattr(self.cfg, "template_product_id", "") or "")
                query = getattr(self.dxm, "query_customs_metadata", None)
                template = _cached_template_edit(self.dxm, template_id) if not callable(query) and self.dxm is not None and template_id else None
                product["hs_code_override"] = refreshed_customs(raw, template, query=query)
                res.steps.append(Step("海关属性自动修复", True, "已获取更新版本并核对原海关编码和真实属性，保存前将再次核对"))
        except ValueError as error:
            res.ok = False
            res.error = f"海关监管属性预检失败：{error}；本次未调用 AI"
            res.steps.append(Step("海关监管属性预检", False, res.error))
            return res
        # 审核中的产品禁止再次编辑（店小秘会返回 PRODUCT_IN_AUDIT_PENDING，且会白跑 AI）
        _st = str(product.get("status_type") or product.get("status") or "")
        _err = str((product.get("raw") or {}).get("errMsg") or "")
        _state_known = False
        if self.dxm is not None and hasattr(self.dxm, "product_state"):
            try:
                _state = self.dxm.product_state(str(product.get("id", "")))
                _probe = str(_state.get("status") or "")
                # 草稿的 product_state 返回空 status，别用它把已知的 draft 覆盖掉
                if _probe:
                    _st = _probe
                    _state_known = True
                _err = str(_state.get("err") or "") or _err
            except Exception as _se:  # noqa: BLE001
                _state_known = False
                _err = f"状态查询失败：{str(_se)[:60]}"
        _editable = {"editingRequired", "offline", "publishFail", "draft", "waitPublish"}
        if (not _state_known and _st not in _editable) or _st in ("auditing", "onSelling", "published", "online") \
                or "PRODUCT_IN_AUDIT_PENDING" in _err:
            add_step(res, "跳过", False, f"产品状态 {_st or '未知'}，审核中/状态未知不编辑")
            res.ok = False
            res.error = f"产品状态 {_st or '未知'}，已跳过"
            return res
        try:
            if manage_progress:
                BUS.begin(9)
            emit("开始", f"产品 {product.get('id','')}", (product.get("title") or "")[:60], "INFO")
            from . import model_of as _model_of
            if not BUS.price_of(_model_of(self.cfg, "image_edit")):
                try:
                    from ..api import ApiClient as _AC
                    _rows = _AC(self.cfg.api_base, self.cfg.api_key, 20).pricing()
                    BUS.set_prices({r["model"]: r["per_call"] for r in _rows if r.get("per_call")})
                except Exception:
                    pass
            p = self._normalize(product)
            self._apply_sku_image_replace(p, res)
            if p.get("hs_code_override") is not None:
                add_step(res, "海关监管属性", True, "已取得同类目、同海关编码的更新监管属性，待保存时写入")
            add_step(res, "标准化", True,
                     f"{len(p['skus'])} 个 SKU / {len(p['images'])} 张图（上限 {self.cfg.max_sku_count}）")
            if p["skus"]:
                sample = p["skus"][0]
                add_step(res, "SKU 处理", True,
                         f"{sample.get('sku')} · 库存 {sample.get('stock', '-')}")

            specs = {}
            for key, label in (("spec_weight", "Weight"), ("spec_length", "Length"),
                               ("spec_width", "Width"), ("spec_height", "Height")):
                v = (getattr(self.cfg, key, "") or "").strip()
                if v:
                    specs[label] = v
            if getattr(self.cfg, "product_type", ""):
                specs["Product Type"] = self.cfg.product_type
            if getattr(self.cfg, "declared_value", ""):
                specs["Declared Value"] = self.cfg.declared_value
            if specs:
                p["specs"] = specs
                # 这些值是「喂给 AI 详情/模板」的上下文，不会作为商品属性写进编辑器
                add_step(res, "规格上下文", True,
                         "供 AI 详情使用（不写入商品属性）："
                         + ", ".join(f"{k}={v}" for k, v in specs.items())[:70])

            try:
                from .image_task_plan import build_plan
                plan = build_plan(self.cfg, p)
                if plan.steps:
                    add_step(res, "图片计划", True, "；".join(plan.steps[:4]))
                for _w in getattr(plan, "warnings", [])[:3]:
                    add_step(res, "图片计划提醒", True, _w)
                if plan.blockers:
                    # 计划里有无法执行的项：跳过该项继续，不中止整个商品
                    add_step(res, "图片计划提醒", True, "已跳过：" + "；".join(plan.blockers[:3]))
            except Exception as ex:  # noqa: BLE001
                add_step(res, "图片计划", False, "计划生成失败：" + str(ex)[:80])
                res.ok = False
                res.error = "图片计划生成失败，已停止"
                return res

            p = self._apply_template(p, res)
            p = self._map_variants(p)
            issues = p.get("sku_issues") or []
            _has_attrs = bool(p.get("attributes") or p.get("target_attributes"))
            _detail = (f"{len(p.get('attr_mapping') or {})} 项映射"
                       if _has_attrs else
                       "商品列表接口不含属性字段，本次未做名称映射")
            res.steps.append(Step("变种映射", not issues,
                                  _detail + (f"，{len(issues)} 个问题" if issues else "")))
            if issues:
                res.ok = False
                res.error = "SKU 一致性检查失败：" + "；".join(issues[:3])
                return res

            # 优先用注入的文本服务（便于离线测试/替换实现），没有再自建
            tp = self.text or TextPipeline(self.cfg)

            # ---- 标题 + SKU 并行（互不依赖，省一次往返）----
            _need_title = bool(self.cfg.enable_ai_title and p["title"])
            _need_sku = bool(self.cfg.enable_ai_sku and p.get("skus"))
            _title_res = None
            _sku_res = None
            _title_err = ""
            _sku_err = ""
            _parallel_err = ""
            _sku_names = [str(x.get("sku") or x.get("name") or "") for x in (p.get("skus") or [])]
            if _need_title or _need_sku:
                try:
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    _tp_sku = tp if self.text is not None else TextPipeline(self.cfg)

                    def _do_title():
                        return tp.make_title(p.get("title_en") or p["title"], p.get("brands"))

                    def _do_sku():
                        return _tp_sku.make_skus(_sku_names)

                    with _TPE(max_workers=2) as _ex:
                        _f_t = _ex.submit(_do_title) if _need_title else None
                        _f_s = _ex.submit(_do_sku) if _need_sku else None
                        if _f_t is not None:
                            import time as _t_wait
                            while not _f_t.done():
                                if self._aborted():
                                    return self._halt(res, "AI 标题")
                                _t_wait.sleep(0.2)
                            try:
                                _title_res = _f_t.result()
                            except Exception as _te:  # noqa: BLE001
                                _title_res = None
                                _title_err = str(_te)[:200]
                        if _f_s is not None:
                            import time as _t_wait2
                            while not _f_s.done():
                                if self._aborted():
                                    return self._halt(res, "AI SKU")
                                _t_wait2.sleep(0.2)
                            try:
                                _sku_res = _f_s.result()
                            except Exception as _se:  # noqa: BLE001
                                _sku_res = None
                                _sku_err = str(_se)[:200]
                except Exception as _pe:  # noqa: BLE001
                    _title_res = _sku_res = None
                    _parallel_err = str(_pe)[:200]

            if _title_res is not None:
                if _title_res.ok:
                    p["title_en"] = _title_res.value
                    add_step(res, "AI 标题", True, _title_res.value[:70])
                else:
                    add_step(res, "AI 标题", False, _title_res.error)
            elif _need_title:
                _why = _title_err or _parallel_err or "未知异常"
                add_step(res, "AI 标题", False,
                         f"调用异常，已跳过：{_why}"[:240])
            else:
                add_step(res, "AI 标题", True, "未启用，跳过")

            if self.cfg.enable_ai_detail and p["title"]:
                # 原文：优先用商品自带详情里去标签后的文字，让 AI 有真实信息可依
                _src_text = ""
                try:
                    import re as _re_d
                    _raw_detail = str((product or {}).get("raw", {}).get("detail") or "")
                    _src_text = _re_d.sub(r"<[^>]+>", " ", _raw_detail)
                    _src_text = _re_d.sub(r"\s+", " ", _src_text).strip()[:6000]
                except Exception:
                    _src_text = ""
                # 详情图：用「原详情里的图片」，保留并交给 AI 排版（不再换成主图）
                _src_imgs = []
                try:
                    import re as _re_i
                    _raw_html = str((product or {}).get("raw", {}).get("detail") or "")
                    for _u in _re_i.findall(r'<img[^>]+src=["\']([^"\']+)', _raw_html, _re_i.I):
                        _u = _u.strip()
                        if _u.startswith("//"):
                            _u = "https:" + _u
                        if _u.startswith("http") and _u not in _src_imgs:
                            _src_imgs.append(_u)
                    _src_imgs = _src_imgs[:12]
                except Exception:
                    _src_imgs = []
                # 合规过滤：剔除含联系方式/二维码/促销的详情图（失败则保留全部）
                if _src_imgs and getattr(self.cfg, "detail_image_compliance", True):
                    try:
                        _bad = tp.filter_detail_images(_src_imgs)
                        if _bad:
                            _kept = [u for i, u in enumerate(_src_imgs, 1) if i not in _bad]
                            add_step(res, "详情图合规", True,
                                     f"剔除 {len(_bad)} 张（联系方式/二维码/促销），保留 {len(_kept)} 张")
                            _src_imgs = _kept
                    except Exception:
                        pass
                _st, _val = self._run_interruptible(
                    lambda: tp.make_detail(p.get("title_en") or p["title"],
                                           p.get("attributes_map"),
                                           source_text=_src_text, images=_src_imgs))
                if _st == "stop":
                    return self._halt(res, "AI 详情")
                if _st == "error":
                    class _R:  # 与 TextResult 对齐的失败结果
                        ok = False
                        value = ""
                        error = str(_val)[:120]
                    r = _R()
                else:
                    r = _val
                add_step(res, "AI 详情", r.ok, (r.value[:60] if r.ok else r.error))
                if r.ok:
                    p["detail_html"] = r.value

            # 本地详情图：用户从电脑选的图片 → 上传图床 → 插入详情头/尾
            try:
                import json as _jli
                _lp = _jli.loads(getattr(self.cfg, "detail_local_images", "[]") or "[]")
            except Exception:
                _lp = []
            if _lp and getattr(self.cfg, "custom_detail_text_enable", True) and not dry_run:
                try:
                    from pathlib import Path as _Pl2
                    from .hosting import upload as _host_l
                    _urls = []
                    for _sp in (_lp if isinstance(_lp, list) else []):
                        try:
                            _f = _Pl2(str(_sp))
                            if _f.exists():
                                _urls.append(_host_l(_f))
                        except Exception:
                            continue
                    if _urls:
                        _mh = "".join(f'<p><img src="{u}" style="max-width:100%;" /></p>' for u in _urls)
                        _pos_l = str(getattr(self.cfg, "detail_local_image_position", "tail") or "tail").lower()
                        p["detail_local_images_html"] = _mh
                        add_step(res, "详情本地图", True, f"{len(_urls)} 张已插入（{_pos_l}）")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "详情本地图", False, str(ex)[:80])

            if _need_sku:
                if _sku_res is not None and _sku_res.ok:
                    new_names = _sku_res.value.splitlines()
                    for sku, nm in zip(p["skus"], new_names):
                        sku["optimized_name"] = nm
                    add_step(res, "AI SKU", True, _sku_res.value[:50].replace("\n", " "))
                elif _sku_res is not None:
                    add_step(res, "AI SKU", False, _sku_res.error)
                else:
                    _why_sku = _sku_err or _parallel_err or "未知异常"
                    add_step(res, "AI SKU", False,
                             f"调用异常，已跳过：{_why_sku}"[:240])

            # 注意：四合一和水印现在统一通过 _apply_image_jobs 处理
            # 如果用户使用旧配置 cfg.four_in_one 或 cfg.watermark，
            # migrate() 会自动迁移到 image_simple_rules 或 image_ai_jobs
            
            _img_ops = (self.cfg.white_bg_enable or self.cfg.watermark_enable or self.cfg.edit_enable
                        or getattr(self.cfg, "img_main_edit", False)
                        or getattr(self.cfg, "img_detail_white_bg", False)
                        or getattr(self.cfg, "img_detail_remove_wm", False)
                        or getattr(self.cfg, "img_detail_edit", False)
                        or getattr(self.cfg, "img_sku_white_bg", False)
                        or getattr(self.cfg, "img_sku_remove_wm", False)
                        or getattr(self.cfg, "img_sku_edit", False)
                        or (getattr(self.cfg, "background_gen_rules", "") or "").strip() not in ("", "[]")
                        or getattr(self.cfg, "translate_detail_pic_enable", False)
                        or getattr(self.cfg, "translate_main_pic_enable", False)
                        or getattr(self.cfg, "translate_sku_pic", False))
            # AI 主图生成（文生图 / 以现有主图为素材的图生图）
            if getattr(self.cfg, "main_image_generate_enable", False):
                try:
                    from ..api import ApiClient
                    from . import model_of as _model_of_gen
                    model = _model_of_gen(self.cfg, "image_gen")
                    prompt = with_scale_rule((getattr(self.cfg, "main_image_generate_prompt", "") or "").strip())
                    prompt = prompt or (p.get("title_en") or p.get("title", "") or "商品主图")
                    if "{title}" in prompt:
                        prompt = prompt.replace("{title}", p.get("title_en") or p.get("title", ""))
                    if "{subject}" in prompt:
                        prompt = prompt.replace("{subject}", p.get("title_en") or p.get("title", ""))
                    n = int(getattr(self.cfg, "main_image_generate_count", "1") or 1)
                    use_refs = bool(getattr(self.cfg, "main_image_generate_use_refs", True))
                    ref_urls = [u for u in (p.get("images") or [])[:4] if str(u).startswith("http")]
                    gen = []
                    if use_refs and ref_urls:
                        # 以产品现有主图为素材（多图输入），保持商品本体一致，而不是凭空文生图
                        svc = self._img_service()
                        import tempfile as _tf4
                        from pathlib import Path as _P4
                        _d = _P4(_tf4.mkdtemp())
                        files = []
                        for u in ref_urls:
                            try:
                                files.append(self._fetch(str(u), _d))
                            except Exception:
                                continue
                        for _ in range(min(n, 3)):
                            if not files:
                                break
                            try:
                                try:
                                    u = svc.edit_many(files, prompt, tag="主图生成(参考素材)",
                                                      model=self._prompt_model() or None)
                                except TypeError:
                                    u = svc.edit_many(files, prompt, tag="主图生成(参考素材)")
                                if u:
                                    gen.append(u)
                            except Exception:
                                break
                        if gen:
                            add_step(res, "AI主图生成", True, f"{len(gen)} 张（参考 {len(files)} 张素材）")
                    if not gen:
                        client = ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout)
                        for _ in range(min(n, 3)):
                            try:
                                u = client.images_generate(model, prompt)
                                if u:
                                    gen.append(u)
                            except Exception:
                                break
                        if gen:
                            add_step(res, "AI主图生成", True, f"{len(gen)} 张（纯文生图）")
                    
                    if gen:
                        # 配置生成图片的插入位置
                        position = str(getattr(self.cfg, "main_image_generate_position", "prepend") or "prepend")
                        original_images = p.get("images") or []
                        
                        if position == "replace_first" and original_images:
                            # 替换第一张，保留其余
                            p["images"] = gen[:1] + original_images[1:]
                        elif position == "append":
                            # 追加到末尾
                            p["images"] = original_images + gen
                        else:  # prepend (默认)
                            # 插入到最前面
                            p["images"] = gen + original_images
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "AI主图生成", False, str(ex)[:80])

            # 指定图片编辑统一由下方图片方案（含旧配置迁移）执行，避免同一图重复调用

            if getattr(self.cfg, "pad_main_images_from_extra", False):
                try:
                    need = int(getattr(self.cfg, "main_pic_count", "6") or 6)
                    imgs = p.get("images") or []
                    extras = [u for u in (p.get("detail_images") or []) if u not in imgs]
                    while len(imgs) < need and extras:
                        imgs.append(extras.pop(0))
                    if imgs:
                        p["images"] = imgs
                        add_step(res, "主图补齐", True, f"{len(imgs)} 张")
                except Exception:
                    pass

            if self._aborted():
                return self._halt(res, "图片处理")
            if (p.get("images") or p.get("detail_images") or p.get("skus")) and _img_ops:
                try:
                    p = self._ai_images(p, res, handled_simple=True)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片AI处理", False, str(ex)[:100])

            if getattr(self.cfg, "main_as_detail", False) and p.get("images"):
                dimgs = list(p.get("detail_images") or [])
                for u in p["images"]:
                    if u and u not in dimgs:
                        dimgs.append(u)
                p["detail_images"] = dimgs
                add_step(res, "主图转详情", True, f"详情图共 {len(dimgs)} 张")

            self._keep_local(p, res)

            # 图片方案（通用编辑 / 精细化编辑 / 简单规则；兼容旧逐图/指定编辑）
            # 注意：通用编辑是靠 img_general_prompt 触发的，rules/jobs 都是空——只判断后两者会漏掉整条链路
            _mode_gen = str(getattr(self.cfg, "image_edit_mode", "fine") or "fine").lower() in ("general", "simple")
            _has_general = _mode_gen and bool((getattr(self.cfg, "img_general_prompt", "") or "").strip())
            _has_img_jobs = any(
                (getattr(self.cfg, k, "") or "").strip() not in ("", "[]")
                for k in ("image_simple_rules", "image_ai_jobs", "image_edit_plans"))
            _has_img_jobs = _has_img_jobs or bool(getattr(self.cfg, "designated_edit_enable", False))
            _has_img_jobs = _has_img_jobs or _has_general
            _has_img_jobs = _has_img_jobs or bool(getattr(self.cfg, "four_in_one", False))
            # 只有旧开关（白底/去水印）时，规则由开关实时生成，也必须进新引擎
            try:
                from .image_jobs import rules_from_switches as _rfs2
                _has_img_jobs = _has_img_jobs or bool(_rfs2(self.cfg))
            except Exception:
                pass
            if _has_img_jobs:
                try:
                    p = self._apply_image_jobs(p, res, dry_run=dry_run)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片方案", False, str(ex)[:100])

            if getattr(self.cfg, "overlay_watermark_enable", False) and (self.cfg.watermark or "").strip():
                try:
                    import tempfile as _tf3
                    from pathlib import Path as _P3
                    import requests as _rq3
                    from .image_ops import add_watermark
                    from .hosting import upload as _host3
                    d3 = _P3(_tf3.mkdtemp()); n3 = 0
                    for i, u in enumerate((p.get("images") or [])[:6]):
                        try:
                            raw = _rq3.get(str(u), timeout=60).content
                            src = d3 / f"w{i}.png"; src.write_bytes(raw)
                            out = d3 / f"wo{i}.png"
                            add_watermark(src, self.cfg.watermark, out,
                                          position=getattr(self.cfg, "overlay_position", "bottom-right"),
                                          opacity=float(getattr(self.cfg, "overlay_opacity", "0.7") or 0.7))
                            p["images"][i] = _host3(out); n3 += 1
                        except Exception:
                            continue
                    if n3:
                        add_step(res, "水印叠加", True, f"{n3} 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "水印叠加", False, str(ex)[:80])

            # 营销图（基于主图生成，写进 marketImg1/marketImg2）
            # 说明：营销图是可选增强，单张 AI 生成常需 40~90 秒；默认关闭。
            # 开启时用短超时 + 单次尝试，失败立刻跳过，绝不拖住整个商品。
            if (getattr(self.cfg, "marketing_auto_generate", False)
                    and p.get("images") and not dry_run):
                _msrc = str(getattr(self.cfg, "marketing_source", "local") or "local").lower()
                try:
                    import tempfile as _tf, requests as _rq
                    from pathlib import Path as _Pm
                    d_m = _Pm(_tf.mkdtemp())
                    _src0 = str(p["images"][0])
                    if _src0.startswith(("http://", "https://")):
                        src = d_m / "m0.png"
                        src.write_bytes(_rq.get(_src0, timeout=30).content)
                    else:
                        # 本地/临时图片路径：不能交给 requests；直接使用本地文件。
                        src = _Pm(_src0)
                        if not src.is_file():
                            raise ValueError("主图本地文件不存在，无法生成营销图")

                    if _msrc == "local":
                        # 店小秘「一键生成」同款：本地裁切/补白，免费、秒出
                        from .image_ops import make_marketing
                        from .hosting import upload as _host_m
                        _mk = []
                        # 3:4 场景图在前（marketImg1），1:1 白底图在后（marketImg2）
                        for _i, _sz in enumerate(((800, 1067), (800, 800))):
                            _out = d_m / f"mk{_i}.jpg"
                            make_marketing(src, _out, _sz)
                            _mk.append(_host_m(_out))
                        if _mk:
                            p["market_images"] = _mk
                            add_step(res, "营销图", True,
                                     f"{len(_mk)} 张已生成（本地裁切，店小秘同款，免费）")
                        else:
                            add_step(res, "营销图", False, "本地裁切失败（已跳过）")
                    else:
                        from . import ImageService
                        from ..api import ApiClient as _ACm
                        _mto = int(float(getattr(self.cfg, "marketing_timeout", 45) or 45))
                        svc = ImageService(_ACm(self.cfg.api_base, self.cfg.api_key, min(_mto, 60)), self.cfg)
                        _mk = []
                        for _i in range(2):
                            try:
                                _u = svc.marketing(src, getattr(self.cfg, "marketing_image_prompt", ""), 1)
                                if _u and _u[0] not in _mk:
                                    _mk.append(_u[0])
                            except Exception:
                                break          # 失败不再重试，避免 2×超时
                        if _mk:
                            p["market_images"] = _mk
                            add_step(res, "营销图", True, f"{len(_mk)} 张已生成（AI 场景图）")
                        else:
                            add_step(res, "营销图", False, "AI 生成失败（已跳过，可改用本地裁切）")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "营销图", False, str(ex)[:80] + "（已跳过）")

            if getattr(self.cfg, "write_detail_html_to_product", True):
                # 只加本地详情图时必须保留原采集描述：部分采集商品 p 里没有 detail，
                # 只有 raw/detail；缺了它 compose_detail 会把正文当成空而只剩本地图。
                if not str(p.get("detail") or "").strip():
                    try:
                        _orig_detail = str((p.get("raw") or {}).get("detail") or "")
                        if not _orig_detail and self.dxm is not None:
                            _od = self.dxm._get("product_detail", {"id": str(p.get("id") or "")}).get("data", {}).get("product", {}) or {}
                            _orig_detail = str(_od.get("detail") or "")
                        if _orig_detail:
                            p["detail"] = _orig_detail
                    except Exception as _det_ex:
                        # 读取详情回填失败：记录但不阻断，详情组装继续用现有数据
                        from .progress import emit as _emit
                        _emit("详情回填", "", f"回读原始详情失败（已跳过）：{str(_det_ex)[:80]}", "WARN")
                from .detail_content import compose_detail
                _detail = compose_detail(p, self.cfg)
                if _detail:
                    p["detail_html"] = _detail
                    add_step(res, "详情内容组装", True,
                             f"保留/更新正文与 {len(p.get('detail_images') or [])} 张详情图")

            if p.get("images"):
                try:
                    p = self._publicize_images(p, res, dry_run=dry_run)
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "图片转存", False, str(ex)[:100])

            # 开了「出图直传相册」就先走那一步（换成店小秘自己的地址）
            if getattr(self.cfg, "prefer_dianxiaomi_album", False):
                p = self._upload_to_album(p, res)

            # 主图写回走编辑器「网络图片」时，不再上传店小秘相册（省一次上传，避免空间不足）
            _need_album = (not getattr(self.cfg, "prefer_dianxiaomi_album", False)
                           and not getattr(self.cfg, "write_main_images_to_product", True))
            if not dry_run and self.dxm and p.get("images") and _need_album:
                uploaded, failed, space_full = 0, 0, False

                def _up(u):
                    try:
                        self.dxm.upload_image_from_url(str(u), full_cid=getattr(self.cfg, "upload_full_cid", "") or "")
                        return "ok", ""
                    except Exception as ex:  # noqa: BLE001
                        return "err", str(ex)

                _res = self._pmap(_up, p["images"][:20], workers=4)
                for _u, _r in zip(p["images"][:20], _res):
                    if not _r:
                        continue
                    kind, msg = _r
                    if kind == "ok":
                        uploaded += 1
                    elif "空间不足" in msg or "空间已满" in msg or "no space" in msg.lower():
                        space_full = True
                    else:
                        failed += 1
                        p.setdefault("upload_errors", []).append(msg[:120])
                if space_full:
                    add_step(res, "图片上传", True,
                             "店小秘图片空间不足，已跳过上传（改用外链 URL，不影响刊登）")
                    emit("图片上传", "图片空间不足",
                         "已跳过，外链 URL 可正常使用；可到「图片工作台 → 清理旧图」腾空间", "WARN")
                    p["space_full"] = True
                else:
                    add_step(res, "图片上传", uploaded > 0 or failed == 0,
                             f"成功 {uploaded} / 失败 {failed}")

            if getattr(self.cfg, "pack_weight", ""):
                p.setdefault("specs", {})["Pack Weight"] = self.cfg.pack_weight

            if not self._review_content(p, res):
                return res

            if self.cfg.check_banned_word and p.get("title_en"):
                from .text_pipeline import find_banned, BANNED_DEFAULT
                hits = find_banned(p["title_en"], BANNED_DEFAULT)
                add_step(res, "违禁词检查", not hits, ("发现：" + ", ".join(hits)) if hits else "未发现")

            if self.cfg.enable_ai_weight and p.get("images"):
                try:
                    tp_w = TextPipeline(self.cfg)
                    rw = tp_w.estimate_weight(str(p["images"][0]), p.get("title_en") or p.get("title", ""))
                    p["ai_weight"] = rw.value[:200]
                    # 解析成结构化数值，自动填入规格（缺失才填，不覆盖手工值）
                    est = tp_w.estimate_weight_json(str(p["images"][0]), p.get("title_en") or p.get("title", ""), result=rw)
                    if est:
                        specs = p.setdefault("specs", {})
                        if est.get("weight_kg") and not specs.get("Weight"):
                            specs["Weight"] = f"{est['weight_kg']:g} kg"
                        if all(est.get(k) for k in ("length_cm", "width_cm", "height_cm")) and not specs.get("Size"):
                            specs["Size"] = (f"{est['length_cm']:g} x {est['width_cm']:g} x {est['height_cm']:g} cm")
                        add_step(res, "AI 估重数值", True,
                                 f"{specs.get('Weight','-')} / {specs.get('Size','-')}")
                    add_step(res, "AI 估重", rw.ok, rw.value[:40].replace("\n", " "))
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "AI 估重", False, str(ex)[:80])

            p = self._price(p)
            r = p["price_result"]
            extra = ""
            if p.get("regional"):
                extra = " · 区域 " + " ".join(f"{k}{v}" for k, v in list(p['regional'].items())[:3])
            add_step(res, "价格计算", True, r.note if r.mode == "preserve" else f"¥{r.final}（{r.note}）{extra}")

            # 定时发布：仍然要「现在保存」，到点再发布（以前这里直接 return，商品根本没保存）
            _sched_at = ""
            if getattr(self.cfg, "schedule_publish", False) and (getattr(self.cfg, "schedule_time", "") or "").strip():
                _sched_at = str(self.cfg.schedule_time).strip()
                p["scheduled_at"] = _sched_at
                add_step(res, "定时发布", True, f"先保存，到 {_sched_at} 自动发布")

            if dry_run:
                add_step(res, "保存到店小秘", True, "dry-run：未写入")
            else:
                # ws_endpoint 仅在 DOM 回退时需要；API 直存不需要浏览器
                if self.dxm is not None and hasattr(self.dxm, "product_state"):
                    try:
                        _pre = self.dxm.product_state(str(p.get("id", "")))
                        _pst = str(_pre.get("status") or "")
                        # 草稿的 product_state 返回空 status —— 用列表里已知的状态兜底，
                        # 否则草稿永远卡在"状态未知"。
                        if not _pst:
                            _pst = str(p.get("status_type") or p.get("status") or "")
                        if _pst not in ("editingRequired", "offline", "publishFail", "draft", "waitPublish"):
                            add_step(res, "保存前复核", False, f"当前状态 {_pst or '未知'}，已停止保存")
                            res.ok = False
                            res.error = f"保存前状态 {_pst or '未知'}，已停止"
                            return res
                    except Exception as _pe:  # noqa: BLE001
                        add_step(res, "保存前复核", False, "状态查询失败：" + str(_pe)[:60])
                        res.ok = False
                        res.error = "保存前无法确认商品状态，已停止"
                        return res
                # 发布动作：save_only=留在待发布 / publish=保存并发布
                _action = getattr(self.cfg, "publish_action", "") or "save_only"
                # 定时发布时先只保存，到点由调度器发布
                if _sched_at:
                    _action = "save_to_waiting"
                _ship = self._ship_dims(p, res)
                # 要写回的主图（与 DOM 路径同一条件）
                _img_for_save = (p.get("images") or None) \
                    if (p.get("images") or []) != (product.get("images") or []) \
                    else None
                # 记录改前/改后快照，供界面「对比预览」
                if not dry_run:
                    try:
                        from . import preview as _pv
                        _pv.record(
                            str(p.get("id", "")),
                            before={"title": product.get("title") or "",
                                    "detail": str(product.get("detail") or "")[:2000],
                                    "images": list(product.get("images") or [])[:6]},
                            after={"title": p.get("title_en") or p.get("title") or "",
                                   "detail": str(p.get("detail_html") or "")[:2000],
                                   "images": list(p.get("images") or [])[:6]},
                            title=p.get("title_en") or p.get("title") or "",
                        )
                    except Exception:
                        pass

                # 主图视频：模板视频 / 生成轮播视频（本地 ffmpeg）→ 上传店小秘 → cspVideoId
                if self._aborted():
                    return self._halt(res, "主图视频")
                self._prepare_video(p, res, product, dry_run)

                # 主图/详情图 webp → JPG（平台主图不接受 webp；实测 11 次发布失败根因）
                if not dry_run and p.get("images"):
                    try:
                        self._fix_webp_images(p, res)
                    except Exception as ex:  # noqa: BLE001
                        add_step(res, "主图转JPG", False, str(ex)[:80])

                # API 直存（唯一保存路径，无 DOM 模拟、支持并发）
                if self._aborted():
                    return self._halt(res, "保存")
                r = self._save_via_api(p, res, _action, _ship, _img_for_save)
                if r is None:
                    # 只有「没模板 / 没客户端」才会走到这里，提示要准确
                    _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
                    if not _tid:
                        raise RuntimeError("未选择产品模板：请到「商品设置 → 模板」选一个模板商品")
                    if self.dxm is None:
                        raise RuntimeError("未连接店小秘：请先到「店铺设置」读取 Cookie")
                    raise RuntimeError("保存前置条件不满足（模板或 Cookie 不可用）")
                if not r.ok and getattr(r, "skipped_no_sku", False):
                    add_step(res, "跳过", True, f"{r.message[:90]}（不算失败）")
                    res.ok = True
                    return res
                if not r.ok:
                    _msg = str(r.message or "")
                    if any(k in _msg for k in ("PRODUCT_IN_AUDIT_PENDING", "audit process", "正在审核中", "审核中")):
                        add_step(res, "保存到店小秘", True,
                                 f"[{p.get('id', '')}] 已在审核中，无需重复发布")
                        res.ok = True
                        try:
                            from . import publish_retry as _prA
                            _prA.remove(str(p.get("id", "")))
                        except Exception:
                            pass
                        return res
                    # 记到「发布失败重试」清单：只影响发布，不影响已保存的内容
                    try:
                        if _action in ("publish", "save_to_waiting"):
                            from . import publish_retry as _pr
                            _pr.record(str(p.get("id", "")), _msg, p.get("title_en") or p.get("title") or "")
                    except Exception:
                        pass
                    # 商品正在审核：不是代码问题，也不是本商品的错，标记跳过而不是失败重试
                    if is_audit_pending(_msg):
                        add_step(res, "保存到店小秘", False,
                                 "商品在速卖通审核中，本次跳过（审核通过后再处理）")
                        res.ok = False
                        res.error = "商品在审核中，已跳过"
                        res.skipped_auditing = True
                        return res
                    try:
                        from . import preview as _pvF
                        _pvF.mark(str(p.get("id", "")), "failed")
                    except Exception:
                        pass
                    raise RuntimeError(_msg)
                add_step(res, "保存到店小秘", True,
                         f"[{p.get('id', '')}] " + r.message[:185])
                try:
                    from . import preview as _pvm
                    _pvm.mark(str(p.get("id", "")),
                              "published" if getattr(r, "published", False) else "saved")
                except Exception:
                    pass
                try:
                    from . import publish_retry as _pr2
                    _pr2.remove(str(p.get("id", "")))
                except Exception:
                    pass
                if _sched_at:
                    _ok_sched = False
                    _err = ""
                    try:
                        import time as _t
                        run_at = _parse_run_at(_sched_at)
                        if run_at <= _t.time():
                            _err = f"时间已过（{_sched_at}）"
                        elif self.scheduler is None:
                            _err = "未启用调度器（请在软件内运行，不要用命令行 Publisher）"
                        else:
                            self.scheduler.add(str(p.get("id", "")), run_at,
                                               p.get("title_en") or p.get("title", ""))
                            _ok_sched = True
                    except Exception as _ex:  # noqa: BLE001
                        _err = str(_ex)[:80]
                    add_step(res, "定时任务", _ok_sched,
                             f"已登记 {_sched_at} 自动发布" if _ok_sched else f"登记失败：{_err}")
                    if not _ok_sched:
                        res.ok = False
                        res.error = (res.error + " " if res.error else "") + f"定时登记失败：{_err}"
                elif r.published:
                    add_step(res, "发布到速卖通", True,
                             "已提交发布 → 请到店小秘「审核中」或「在线产品」里查看")
                else:
                    add_step(res, "保存结果", True,
                             "已保存 → 请到店小秘「采集箱」里查看" if _action == "save_only" else
                             "已保存 → 请到店小秘「待发布」里查看（可手动发布）")

            if not dry_run and any(s.name == "保存到店小秘" and s.ok for s in res.steps):
                try:
                    from .edited import mark as _mark
                    _mark(str(p.get("id", "")), p.get("title_en") or p.get("title", ""), "saved")
                except Exception:
                    pass
            return res
        except Exception as e:  # noqa: BLE001
            res.ok = False
            res.error = str(e)
            emit("异常", "", str(e)[:160], "ERROR")
            add_step(res, "异常", False, str(e))
            return res


def demo_product() -> dict:
    return {
        "id": "DEMO-001",
        "title": "3 吨迷你蜘蛛吊车 20 米臂长",
        "cost": 35000,
        "skus": [{"sku": "A-1", "attributes": {"Model Number": "YG-109", "Power (W)": "16kw"}},
                 {"sku": "A-2", "attributes": {"Model Number": "YG-110", "Power (W)": "24kw"}}],
        "images": ["https://example.com/a.jpg"],
        "attributes": [{"name": "Model Number"}, {"name": "Power (W)"}],
        "target_attributes": [{"name": "型号"}, {"name": "功率"}],
    }


def run_many(pub: "Publisher", products: list[dict], workers: int = 4,
             dry_run: bool = True, on_done=None) -> list:
    """多线程并行处理多个产品（每个产品独立 publisher 实例，避免共享状态）。"""
    import concurrent.futures as _cf
    import copy as _copy

    results = []
    lock = __import__("threading").Lock()

    def work(prod):
        local_cfg = _copy.deepcopy(pub.cfg)
        local_pub = Publisher(local_cfg, dxm=pub.dxm, ws_endpoint=pub.ws_endpoint,
                              scheduler=getattr(pub, "scheduler", None))
        try:
            r = local_pub.run(prod, dry_run=dry_run, manage_progress=False)
        except Exception as e:  # noqa: BLE001
            r = PublishResult(product_id=str(prod.get("id", "")), ok=False, error=str(e))
        with lock:
            results.append(r)
        if on_done:
            try:
                on_done(r)
            except Exception:
                pass
        return r

    with _cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        list(ex.map(work, products))
    return results
