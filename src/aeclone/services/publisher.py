"""发布流水线：产品 → 属性/变种 → AI 文本 → 图片 → 价格 → 上传 → 保存。

默认 dry-run：只做规划与本地计算，不写店小秘；真实保存需显式 dry_run=False
且店小秘接口已在 dianxiaomi.ENDPOINTS 校正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import calc_free_shipping, calc_multiplier, calc_with_commission
from .text_pipeline import TextPipeline
from .image_pipeline import ImagePipeline
from .variants import auto_map, check_consistency, limit_skus
from .progress import BUS, emit


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


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

    def log(self) -> str:
        lines = [f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}" for s in self.steps]
        return "\n".join(lines)


@dataclass
class SaveResult:
    ok: bool
    message: str
    title: str = ""
    published: bool = False


# 模板商品 edit.json 的进程内缓存：批量跑时同一模板只取一次（省一次 API 往返）
_TPL_EDIT_CACHE: dict[str, tuple[float, dict]] = {}
_TPL_EDIT_TTL = 600.0


def _cached_template_edit(dxm, tpl_pid: str) -> dict:
    import time as _t
    key = str(tpl_pid)
    hit = _TPL_EDIT_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < _TPL_EDIT_TTL:
        return dict(hit[1])
    tpl = dxm._get("product_detail", {"id": key}).get("data", {}).get("product", {}) or {}
    if tpl:
        _TPL_EDIT_CACHE[key] = (_t.time(), dict(tpl))
    return dict(tpl)


def add_step(res, name, ok, detail=""):
    """记录步骤结果，同时实时上报到进度总线。"""
    res.steps.append(Step(name, bool(ok), str(detail)))
    emit(str(name), "", str(detail)[:140], "OK" if ok else "ERROR")
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
        stock = getattr(self.cfg, "stock_quantity", "") or ""
        for i, sku in enumerate(p.get("skus") or [], 1):
            name = str(sku.get("sku") or sku.get("name") or "").strip()
            if prefix and not name.startswith(prefix) and not only_prefix:
                name = f"{prefix}{split_ch}{name}" if name else f"{prefix}{split_ch}{i}"
            if getattr(self.cfg, "auto_generate_sku_code", False):
                name = f"{prefix}{split_ch}{i:03d}" if prefix else f"SKU{split_ch}{i:03d}"
            sku["sku"] = name
            if getattr(self.cfg, "modify_stock_999", False):
                sku["stock"] = 999
            elif stock:
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
            # 图片接口超时最多 60 秒：turbo 正常 14 秒，超过基本就是卡死/网关超时，
            # 早点放弃、保留原图，别让一个商品堵 100 秒
            _to = min(float(self.cfg.timeout or 60), 60.0)
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

    def _edit_with_prompt(self, url: str, prompt: str) -> str:
        """对单张 URL 用指定提示词做一次编辑，返回新 URL。"""
        _act = "edit:" + (prompt or "")
        _hit = self._cache_get(url, _act, self._img_model())
        if _hit:
            return _hit
        svc = self._img_service()
        fp = self._fetch(url)
        out = str(svc.edit(fp, prompt))
        self._cache_put(url, _act, out, self._img_model())
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
        if not ops:
            return url
        _act = "simple:" + ",".join(sorted(ops)) + f":{str(getattr(self.cfg, 'translate_prompt', ''))[:40]}"
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

    def _apply_image_jobs(self, p: dict, res, dry_run: bool = False) -> dict:
        """执行「简单规则 + 精细任务」（新引擎；旧配置自动迁移）。"""
        if not getattr(self.cfg, "ai_image_enable", True):
            add_step(res, "图片方案", True, "AI图片处理总开关已关闭，跳过")
            return p
        from .image_jobs import parse_rules, parse_jobs, refs_indices, migrate, GROUP_LABELS
        rules = parse_rules(getattr(self.cfg, "image_simple_rules", "[]"))
        jobs = parse_jobs(getattr(self.cfg, "image_ai_jobs", "[]"))
        _mode = str(getattr(self.cfg, "image_edit_mode", "fine") or "fine").strip().lower()
        _general = _mode in ("general", "simple")
        _general_prompt = str(getattr(self.cfg, "img_general_prompt", "") or "").strip() \
            or str(getattr(self.cfg, "edit_prompt", "") or "").strip()
        if not rules and not jobs and not (_general and _general_prompt):
            try:
                rj, jj = migrate(self.cfg)
                rules, jobs = parse_rules(rj), parse_jobs(jj)
            except Exception:
                rules, jobs = [], []
        if not rules and not jobs and not (_general and _general_prompt):
            return p
        if dry_run:
            _parts = []
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

        svc = self._img_service()
        try:
            self._size_rejects = []
        except Exception:
            pass
        # ---- 简单规则（图片简单编辑：白底/去水印/翻译）----
        for rule in rules:
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

        # ---- 四合一：本地 PIL 拼图（免费、确定、无张数限制）----
        if getattr(self.cfg, "four_in_one", False):
            try:
                import tempfile as _tf4, requests as _rq4
                from pathlib import Path as _P4
                from .image_ops import four_in_one as _four4
                from .hosting import upload as _host4
                _urls4 = self._group_urls(p, "main")
                if len(_urls4) >= 2:
                    _d4 = _P4(_tf4.mkdtemp())
                    _imgs4 = []
                    for _i4, _u4 in enumerate(_urls4[:4]):
                        try:
                            _f4 = _d4 / f"f{_i4}.png"
                            _f4.write_bytes(_rq4.get(str(_u4), timeout=60).content)
                            _imgs4.append(_f4)
                        except Exception:
                            continue
                    if len(_imgs4) >= 2:
                        _out4 = _d4 / "four.png"
                        _four4(_imgs4, _out4)
                        _url4 = _host4(_out4)
                        _cur4 = list(p.get("images") or [])
                        if str(getattr(self.cfg, "four_in_one_position", "append") or "append") == "first":
                            p["images"] = [_url4] + _cur4
                            _where = "第一位"
                        else:
                            p["images"] = _cur4 + [_url4]
                            _where = "末尾"
                        add_step(res, "四合一", True,
                                 f"本地拼图（{len(_imgs4)} 张，不调 AI）→ 插入{_where}")
                    else:
                        add_step(res, "四合一", False, "可用主图不足 2 张，跳过")
                else:
                    add_step(res, "四合一", False, "主图不足 2 张，跳过")
            except Exception as _ex4:  # noqa: BLE001
                add_step(res, "四合一", False, str(_ex4)[:80])

        # ---- 图片通用编辑：两种模式二选一（对齐老软件）----
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
        _detail_before = len(p.get("detail_images") or [])
        _detail_touched = False
        _ai_made_detail: set[str] = set()

        # ---- 精细任务 ----
        for n, job in enumerate(jobs, 1):
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
            prompt = str(job.get("prompt") or "").strip()
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
            _cache_action = "many:v2:" + _json.dumps(
                [job["mode"], prompt, target or "", ref_urls[:4], job.get("ref_paths") or []],
                ensure_ascii=False)
            _hit = self._cache_get(target or (ref_urls[0] if ref_urls else ""), _cache_action,
                                   self._img_model()) if cacheable and (target or ref_urls) else None
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
                # 先本地参考图（图1~图N），再产品原图（老软件顺序）
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
                # 四合一：本地拼（免费、确定、无张数限制）。
                # 走 AI 的话 qwen-image-3.0 最多只吃 3 张输入，4 张会直接报
                # "too many input images"。
                # 只有明确选了「四合一主图」预设才走本地拼图；
                # 普通多图任务仍交给 AI（按 3 张上限截断），避免悄悄换掉用户要的效果
                _is_four = str(job.get("preset") or "") == "四合一主图"
                if _is_four and len(files) >= 2:
                    try:
                        import tempfile as _tf4
                        from pathlib import Path as _P4
                        from .image_ops import four_in_one as _four
                        from .hosting import upload as _host4
                        out4 = _P4(_tf4.mkdtemp()) / "four_in_one.png"
                        _four([_P4(f) for f in files[:4]], out4)
                        made = [str(_host4(out4))]
                        add_step(res, f"精细任务{n}·四合一", True,
                                 f"本地拼图（{min(4, len(files))} 张，不调 AI）")
                    except Exception as _ex4:  # noqa: BLE001
                        add_step(res, f"精细任务{n}·四合一", False,
                                 f"本地拼图失败：{str(_ex4)[:60]}，改用 AI")
                        made = []
                else:
                    made = []
                count = int(job.get("count") or 1) if job["mode"] == "generate" else 1
                if not made:
                    # qwen-image-3.0 的输入上限是 3 张，超了会被服务端拒
                    _use = list(files)[:3]
                    if len(files) > 3:
                        add_step(res, f"精细任务{n}·素材", False,
                                 f"模型最多接受 3 张输入，已用前 3 张（原 {len(files)} 张）")
                    for _ in range(max(1, min(3, count))):
                        u = svc.edit_many(_use, prompt,
                                          tag=f"精细任务{n}·{GROUP_LABELS.get(group, group)}")
                        if u:
                            made.append(str(u))
                if not made:
                    raise RuntimeError("接口未返回图片")
            except Exception as ex:  # noqa: BLE001
                add_step(res, f"精细任务{n}", False, str(ex)[:100])
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
                                made[0], self._img_model())
            add_step(res, f"精细任务{n}", True,
                     f"{GROUP_LABELS.get(group, group)} · 素材 {len(files)} 张 · "
                     + ("插到第1张" if insert == "first" else "放到最后" if insert == "last" else "替换原图"))
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
        """图片通用编辑：一条提示词按范围逐张编辑主图（老软件的「通用编辑」）。"""
        from .image_jobs import GROUP_LABELS
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
    def _apply_edit_plans(self, p: dict, res, dry_run: bool = False) -> dict:
        return self._apply_image_jobs(p, res, dry_run=dry_run)

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
        p["skus"] = limit_skus(p["skus"], int(raw_limit) if raw_limit is not None else 0)
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
        from .pricing import compute_price
        # 直接用采集到的产品价格作为基准，倍率模式下即为售价
        base = float(p.get("price") or p.get("cost") or 0)
        r = compute_price(self.cfg, base)
        if r.regional:
            p["regional"] = dict(r.regional)
        if r.wholesale:
            p["wholesale"] = r.wholesale
        p["price_result"] = r
        return p

    def _run_ops(self, url: str, white_bg: bool, remove_wm: bool, edit: bool,
                 prompt: str | None = None) -> str:
        _act = f"ops:{int(bool(white_bg))}{int(bool(remove_wm))}{int(bool(edit))}:{(prompt or '')[:60]}"
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
                      remove_wm: bool = False, edit: bool = False) -> tuple[list[str], int]:
        src = list(urls[:limit])
        vals = self._pmap(lambda u: self._run_ops(u, white_bg, remove_wm, edit), src)
        out, done = [], 0
        for u, nu in zip(src, vals):
            if nu:
                out.append(str(nu)); done += 1
            else:
                out.append(str(u))
        return out, done

    def _ai_images(self, p: dict, res) -> dict:
        """主图 / 详情图 / SKU 图 三条分支，按配置范围处理。"""
        if not getattr(self.cfg, "ai_image_enable", True):
            add_step(res, "图片AI处理", True, "总开关已关闭，跳过")
            return p
        from .image_index import pick

        # 新引擎（简单规则 / 精细任务）已经覆盖的分组，旧路径不要再做一遍，
        # 否则同一张图会被白底/去水印加工两次：多花一次钱、多等一轮。
        _covered: set[str] = set()
        try:
            from .image_jobs import parse_rules as _pr, parse_jobs as _pj
            # 检查简单规则和精细任务覆盖的分组
            _covered = {r["group"] for r in _pr(getattr(self.cfg, "image_simple_rules", "[]"))}
            _covered.update(j["group"] for j in _pj(getattr(self.cfg, "image_ai_jobs", "[]")))
        except Exception:
            _covered = set()
        if _covered:
            add_step(res, "图片路径", True,
                     "已由图片方案处理：" + "/".join(sorted(_covered)) + "（旧路径自动跳过，避免重复扣费）")

        # 主图
        main = p.get("images", [])
        main_ops = (self.cfg.white_bg_enable or self.cfg.watermark_enable
                    or getattr(self.cfg, "img_main_edit", False)
                    or (self.cfg.background_gen_rules or "").strip() not in ("", "[]"))
        if main and main_ops and "main" not in _covered:
            # 白底范围
            wb_expr = ""
            if self.cfg.white_background_pic_1 or self.cfg.white_background_pic_2:
                wb_expr = f"{self.cfg.white_background_pic_1}-{self.cfg.white_background_pic_2}"
            # 去水印范围（独立）
            wm_expr = ""
            if getattr(self.cfg, "remove_watermark_pic_1", "") or getattr(self.cfg, "remove_watermark_pic_2", ""):
                wm_expr = f"{getattr(self.cfg, 'remove_watermark_pic_1', '1')}-{getattr(self.cfg, 'remove_watermark_pic_2', '5')}"
            edit_expr = getattr(self.cfg, "single_edit_main_pic_indices", "") or ""
            selected = pick(main, wb_expr or wm_expr or edit_expr) if (wb_expr or wm_expr or edit_expr) else main
            if selected:
                out, done = self._process_urls(selected,
                                               white_bg=self.cfg.white_bg_enable or bool(wb_expr),
                                               remove_wm=self.cfg.watermark_enable or bool(wm_expr),
                                               edit=getattr(self.cfg, "img_main_edit", False) or bool(edit_expr))
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
        if getattr(self.cfg, "translate_main_pic_enable", False) and main and getattr(self.cfg, "translate_image_understanding", False):
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

        if getattr(self.cfg, "translate_main_pic_enable", False) and main:
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
        if getattr(self.cfg, "translate_detail_pic_enable", False) and detail:
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
        if self.cfg.white_background_sku_pic:
            skus = p.get("skus") or []
            done = 0
            try:
                import json as _j3
                repl = _j3.loads(getattr(self.cfg, "sku_image_replace", "") or "{}")
            except Exception:
                repl = {}
            def _one_sku(sku):
                img = sku.get("image") or sku.get("imageUrl")
                name = str(sku.get("sku") or "")
                if name in repl and repl[name]:
                    sku["image"] = repl[name]
                    img = repl[name]
                if not img:
                    return None
                _skp = (getattr(self.cfg, "img_sku_edit_prompt", "") or "").strip() or None
                return self._run_ops(
                    str(img),
                    white_bg=getattr(self.cfg, "img_sku_white_bg", True),
                    remove_wm=getattr(self.cfg, "img_sku_remove_wm", False),
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
        if getattr(self.cfg, "translate_sku_pic", False):
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
                except Exception:
                    failed += 1
            p[key] = urls
        if res is not None:
            add_step(res, "传店小秘相册", failed == 0 and done > 0,
                     f"成功 {done} / 失败 {failed} / 跳过 {skipped}")
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
        
        def _one(i):
            u = images[i]
            try:
                r = __import__("requests").get(str(u), timeout=60, cookies=cookies)
                r.raise_for_status()
                fp = d / f"pub_{i}.png"; fp.write_bytes(r.content)
                try:
                    self._local_files[str(images[i])] = fp   # 供尺寸校验复用，避免二次下载
                except Exception:
                    pass
                return i, host_upload(fp)
            except Exception:
                return i, str(u)
        
        results = self._pmap(_one, needs_upload)
        for i, url in results:
            images[i] = url

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
        if self.cfg.watermark and self.dxm is not None and getattr(self.cfg, "watermark_upload_album", True):
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

    def publish_only(self, product_id: str, res) -> "SaveResult":
        """只把已保存的商品重新提交发布（op=2）；不跑 AI、不处理图片、不花钱。

        用于「编辑成功但发布失败」的商品重试。
        """
        from .api_save import build_save_payload

        if self.dxm is None:
            return SaveResult(ok=False, message="未连接店小秘")
        pid = str(product_id)
        try:
            target = self.dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
        except Exception as ex:  # noqa: BLE001
            return SaveResult(ok=False, message=f"读取商品失败：{str(ex)[:100]}")
        if not target:
            return SaveResult(ok=False, message="没取到商品详情")
        tpl = None
        _tid = str(getattr(self.cfg, "template_product_id", "") or "").strip()
        if _tid:
            try:
                tpl = _cached_template_edit(self.dxm, _tid)
            except Exception:
                tpl = None
        try:
            payload = build_save_payload(
                target, tpl, action="publish",
                custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None),
                cargo_price=str(getattr(self.cfg, "declared_value", "") or "") or "50")
            resp = self.dxm.save_product_via_api(payload)
        except ValueError as ex:
            return SaveResult(ok=False, message=str(ex)[:300])
        except Exception as ex:  # noqa: BLE001
            return SaveResult(ok=False, message=f"发布失败：{str(ex)[:200]}")
        _code = resp.get("code") if isinstance(resp, dict) else None
        if _code == 0:
            _d = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            _m = str(_d.get("msg") or resp.get("msg") or "")
            return SaveResult(ok=True, message=f"只发布成功（商品ID {pid}）：{_m[:110]}", published=True)
        return SaveResult(ok=False, message=f"发布失败：{str(resp.get('msg') or '')[:200]}")

    def _prepare_video(self, p: dict, res, product: dict, dry_run: bool):
        """主图视频：模板视频 / 生成轮播视频 → 上传店小秘 → p["csp_video_id"]。

        失败只记日志不阻断保存（视频是可选增强）。
        """
        if dry_run or self.dxm is None:
            return
        from pathlib import Path
        # 视频来源：兼容老开关（use_template_video / generate_main_video）
        _sv = str(getattr(self.cfg, "video_source", "") or "").strip().lower()
        if _sv not in ("collected", "template", "generated", "none"):
            if getattr(self.cfg, "use_template_video", False):
                _sv = "template"
            elif getattr(self.cfg, "generate_main_video", False):
                _sv = "generated"
            else:
                _sv = "collected"
        if _sv == "none":
            p["csp_video_id"] = ""          # 明确不要视频
            add_step(res, "视频", True, "已选择「不要视频」，本次不写入视频")
            return
        if _sv == "collected":
            add_step(res, "视频", True, "使用采集到的产品视频（自动保留）")
            return
        src_video: str = ""
        # ① 模板视频（需填路径）
        if _sv == "template":
            _tp = str(getattr(self.cfg, "template_video_path", "") or "").strip()
            if _tp and Path(_tp).exists():
                src_video = _tp
            else:
                add_step(res, "视频", False, "模板视频：路径为空或文件不存在，已跳过")
                return
        # ② 生成轮播视频（本地 ffmpeg 合成，不调 AI）
        if not src_video and _sv == "generated" and p.get("images"):
            try:
                import tempfile as _tf
                import requests as _rq
                from .video import generate_carousel_video
                d = Path(_tf.mkdtemp())
                imgs = []
                for i, u in enumerate(p["images"][:6]):
                    _f = d / f"v{i}.jpg"
                    try:
                        _f.write_bytes(_rq.get(str(u), timeout=60).content)
                        imgs.append(_f)
                    except Exception:
                        continue
                if imgs:
                    out = d / "carousel.mp4"
                    generate_carousel_video(imgs, out)
                    src_video = str(out)
                    add_step(res, "生成轮播视频", True, f"{len(imgs)} 张图已合成")
            except Exception as ex:  # noqa: BLE001
                add_step(res, "生成轮播视频", False, str(ex)[:80])
                return
        if not src_video:
            return
        # 上传
        try:
            from ..integrations.cos_upload import upload_video
            vid = upload_video(self.dxm, Path(src_video))
            if vid:
                p["csp_video_id"] = vid
                add_step(res, "视频上传", True, f"cspVideoId={vid[:24]}")
        except Exception as ex:  # noqa: BLE001
            add_step(res, "视频上传", False, str(ex)[:80])

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
        try:
            target = self.dxm._get("product_detail", {"id": pid}).get("data", {}).get("product", {})
            tpl = None
            if bool(getattr(self.cfg, "apply_template_on_save", True)):
                tpl = _cached_template_edit(self.dxm, _tpl_pid)
        except Exception as ex:
            add_step(res, "API 直存", False, f"读取商品详情失败：{str(ex)[:80]}")
            return None
        if not target:
            add_step(res, "API 直存", False, "没取到目标商品详情")
            return None

        try:
            # 主图数量上限
            _imgs = images
            _cap = int(getattr(self.cfg, "editor_main_images_max", 6) or 6)
            if _imgs and _cap > 0:
                _imgs = list(_imgs)[:_cap]
            overrides = build_ai_overrides(target, p, self.cfg, images=_imgs, ship=ship)

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
            payload = build_save_payload(
                target, tpl, action=action, overrides=overrides,
                custom_attributes=(getattr(self.cfg, "custom_attributes", None) or None),
                cargo_price=str(getattr(self.cfg, "declared_value", "") or "") or "50")
            resp = self.dxm.save_product_via_api(payload)
        except ValueError as ex:  # 数据安全类错误（如缺类目）：明确报错，不回退
            add_step(res, "保存中止", False, str(ex)[:200])
            return SaveResult(ok=False, message=str(ex)[:300])
        except Exception as ex:  # noqa: BLE001
            _msg = str(ex)[:300] or type(ex).__name__
            add_step(res, "API 直存", False, f"保存失败：{_msg[:200]}")
            return SaveResult(ok=False, message=f"保存失败：{_msg}")

        _code = resp.get("code") if isinstance(resp, dict) else None
        if _code == 0:
            _msg = ((resp.get("data") or {}).get("msg") if isinstance(resp.get("data"), dict)
                    else str(resp.get("msg") or ""))
            _pid2 = ""
            try:
                _d = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                _pid2 = str(_d.get("productId") or _d.get("id") or str(p.get("id", "")))
            except Exception:
                _pid2 = pid
            return SaveResult(ok=True,
                              message=f"API 直存成功（商品ID {_pid2}）：{_msg[:110]}",
                              published=(action in ("publish", "save_to_waiting")))
        _msg = str(resp.get("msg") or "") or "保存失败"
        return SaveResult(ok=False, message=f"API 直存：{_msg[:200]}")

    def run(self, product: dict, dry_run: bool = True, manage_progress: bool = True) -> PublishResult:
        res = PublishResult(product_id=str(product.get("id", "")), ok=True)
        # 自动化测试/验收默认不真实保存，避免误改客户商品
        import os as _os
        if _os.environ.get("AURORA_TEST") or _os.environ.get("AURORA_NO_SAVE"):
            dry_run = True
        if dry_run:
            # 预演：把「不改数据也不花钱」的步骤都跑一遍，让用户看到到底会发生什么。
            # 不调用 AI、不下载图片、不连接浏览器、不写配置、不保存商品。
            res.steps.append(Step("预演", True, "不调用 AI、不下载图片、不连接浏览器、不保存商品"))
            try:
                p = self._normalize(product)
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
                    res.steps.append(Step("价格计算", True, f"{_note} · ¥{_final}"))
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

            # 多 SKU 拦截（真跑时也会拦）
            if (getattr(self.cfg, "write_sku_codes_to_product", True)
                    and len(product.get("skus") or []) > 1):
                res.steps.append(Step("保存能力检查", False,
                                      "多 SKU + SKU 编码写回：真跑时会被拦截（去「变种设置 → SKU 写回」关掉）"))

            res.steps.append(Step("保存到店小秘", True, "dry-run：未写入；点「真实写入」才会保存"))
            return res
        # Check known editor limitations before any paid processing or uploads.
        if getattr(self.cfg, "combo_enable", False):
            res.ok = False
            res.error = "套装 SKU 尚未支持安全创建编辑器行，请关闭套装后再执行"
            res.steps.append(Step("保存能力检查", False, res.error))
            return res
        if getattr(self.cfg, "write_sku_codes_to_product", True) and len(product.get("skus") or []) > 1:
            res.ok = False
            res.error = ("多 SKU 编码尚未支持可靠的行身份匹配：请到「变种设置 → SKU 写回」"
                         "取消勾选「SKU 编码写回商品」后重试；本次未调用 AI")
            res.steps.append(Step("保存能力检查", False, res.error))
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
            _sku_names = [str(x.get("sku") or x.get("name") or "") for x in (p.get("skus") or [])]
            if _need_title or _need_sku:
                try:
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    import copy as _copy
                    _tp_sku = tp if self.text is not None else TextPipeline(self.cfg)

                    def _do_title():
                        return tp.make_title(p.get("title_en") or p["title"], p.get("brands"))

                    def _do_sku():
                        return _tp_sku.make_skus(_sku_names)

                    with _TPE(max_workers=2) as _ex:
                        _f_t = _ex.submit(_do_title) if _need_title else None
                        _f_s = _ex.submit(_do_sku) if _need_sku else None
                        if _f_t is not None:
                            try:
                                _title_res = _f_t.result()
                            except Exception as _et:  # noqa: BLE001
                                _title_res = None
                        if _f_s is not None:
                            try:
                                _sku_res = _f_s.result()
                            except Exception as _es:  # noqa: BLE001
                                _sku_res = None
                except Exception:
                    _title_res = _sku_res = None

            if _title_res is not None:
                if _title_res.ok:
                    p["title_en"] = _title_res.value
                    add_step(res, "AI 标题", True, _title_res.value[:70])
                else:
                    add_step(res, "AI 标题", False, _title_res.error)
            elif _need_title:
                add_step(res, "AI 标题", False, "调用异常，已跳过")
            else:
                add_step(res, "AI 标题", True, "未启用，跳过")

            if self.cfg.enable_ai_detail and p["title"]:
                r = tp.make_detail(p.get("title_en") or p["title"], p.get("attributes_map"))
                add_step(res, "AI 详情", r.ok, (r.value[:60] if r.ok else r.error))
                if r.ok:
                    html = r.value
                    head = (self.cfg.detail_text_info or "").strip()
                    tail = (self.cfg.detail_text_info_tail or "").strip()
                    pos = (self.cfg.detail_text_position or "head").lower()
                    if getattr(self.cfg, "move_detail_text", False) and head:
                        pos = "head"
                    if getattr(self.cfg, "custom_detail_text_enable", True):
                        if head and pos in ("head", "both"):
                            html = f"<p>{head}</p>" + html
                        if tail and pos in ("tail", "both"):
                            html = html + f"<p>{tail}</p>"
                    p["detail_html"] = html

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
                        if p.get("detail_html"):
                            _cur = p["detail_html"]
                            if _pos_l in ("head", "both"):
                                _cur = _mh + _cur
                            if _pos_l in ("tail", "both"):
                                _cur = _cur + _mh
                            p["detail_html"] = _cur
                        else:
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
                    add_step(res, "AI SKU", False, "调用异常，已跳过")

            # 注意：四合一和水印现在统一通过 _apply_image_jobs 处理
            # 如果用户使用旧配置 cfg.four_in_one 或 cfg.watermark，
            # migrate() 会自动迁移到 image_simple_rules 或 image_ai_jobs
            
            _img_ops = (getattr(self.cfg, "ai_image_enable", True)
                        and (self.cfg.white_bg_enable or self.cfg.watermark_enable or self.cfg.edit_enable
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
                        or getattr(self.cfg, "translate_sku_pic", False)))
            # AI 主图生成（文生图 / 以现有主图为素材的图生图）
            if getattr(self.cfg, "main_image_generate_enable", False) and getattr(self.cfg, "ai_image_enable", True):
                try:
                    from ..api import ApiClient
                    from . import model_of as _model_of_gen
                    model = _model_of_gen(self.cfg, "image_gen")
                    prompt = (getattr(self.cfg, "main_image_generate_prompt", "") or "").strip()
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

            if (p.get("images") or p.get("detail_images") or p.get("skus")) and _img_ops:
                try:
                    p = self._ai_images(p, res)
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
            if _has_img_jobs and getattr(self.cfg, "ai_image_enable", True):
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
                    and getattr(self.cfg, "ai_image_enable", True) and p.get("images") and not dry_run):
                _msrc = str(getattr(self.cfg, "marketing_source", "local") or "local").lower()
                try:
                    import tempfile as _tf, requests as _rq
                    from pathlib import Path as _Pm
                    d_m = _Pm(_tf.mkdtemp())
                    src = d_m / "m0.png"
                    src.write_bytes(_rq.get(str(p["images"][0]), timeout=30).content)

                    if _msrc == "local":
                        # 店小秘「一键生成」同款：本地裁切/补白，免费、秒出
                        from .image_ops import make_marketing
                        from .hosting import upload as _host_m
                        _mk = []
                        for _i, _sz in enumerate(((800, 800), (750, 1000))):
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

            # 详情图必须在主图转详情、图片方案、营销图全部完成后组装
            if getattr(self.cfg, "write_detail_html_to_product", True) and p.get("detail_images"):
                _imgs_html = "".join(
                    f'<p><img src="{str(u)}" style="max-width:100%;" /></p>'
                    for u in p["detail_images"][:20] if str(u).startswith("http"))
                if _imgs_html:
                    p["detail_html"] = (p.get("detail_html") or "") + _imgs_html
                    add_step(res, "详情图写回", True, f"{min(len(p['detail_images']), 20)} 张")

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

            if getattr(self.cfg, "copy_sku_to_detail", False) and p.get("skus") and p.get("detail_html"):
                names = " / ".join(str(s.get("sku") or "") for s in p["skus"][:20])
                p["detail_html"] = p["detail_html"] + f"<p>SKU: {names}</p>"
                add_step(res, "SKU写入详情", True, f"{len(p['skus'])} 个")

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
            add_step(res, "价格计算", True, f"¥{r.final}（{r.note}）{extra}")

            if getattr(self.cfg, "generate_main_video", False) and p.get("images"):
                try:
                    import tempfile as _tf
                    from pathlib import Path as _P2
                    import requests as _rq2
                    from .video import build_slideshow, generate_carousel_video, probe as _vp

                    # 已存在视频且未允许覆盖 → 跳过
                    if p.get("main_video") and not getattr(self.cfg, "overwrite_existing_video", False):
                        add_step(res, "主图视频", True, "已存在视频，跳过")
                    elif getattr(self.cfg, "use_template_video", False) and getattr(self.cfg, "template_video_path", ""):
                        p["main_video"] = str(self.cfg.template_video_path)
                        add_step(res, "主图视频", True, "使用模板视频")
                    elif _vp()["available"]:
                        d2 = _P2(_tf.mkdtemp())
                        files = []
                        for i, u in enumerate(p["images"][:6]):
                            fp = d2 / f"v{i}.png"
                            try:
                                fp.write_bytes(_rq2.get(str(u), timeout=60).content); files.append(fp)
                            except Exception:
                                continue
                        if len(files) >= 2:
                            # 视频落到本地目录（老软件也是先存本地再上传）
                            try:
                                from pathlib import Path as _PV
                                vdir = _PV(getattr(self.cfg, "video_output_dir", "") or (_PV.home() / "Movies" / "AuroraList"))
                                vdir.mkdir(parents=True, exist_ok=True)
                                out = vdir / f"{p.get('id','product')}-{int(__import__('time').time())}.mp4"
                            except Exception:
                                out = d2 / "main.mp4"
                            generate_carousel_video(
                                files, out,
                                slot_seconds=float(getattr(self.cfg, "video_slot_seconds", 2.5) or 2.5),
                                transition_seconds=float(getattr(self.cfg, "video_transition_seconds", 0.5) or 0.5),
                                size=str(getattr(self.cfg, "video_size", "1080x1080") or "1080x1080"),
                                cover=bool(getattr(self.cfg, "video_cover", True)),
                                fade_edges=bool(getattr(self.cfg, "video_fade_edges", True)),
                            )
                            p["main_video"] = str(out)
                            add_step(res, "主图视频", True, str(out))
                        else:
                            add_step(res, "主图视频", False, "可用图片不足 2 张")
                except Exception as ex:  # noqa: BLE001
                    add_step(res, "主图视频", False, str(ex)[:80])

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
                _action = (getattr(self.cfg, "publish_action", "") or
                           ("publish" if getattr(self.cfg, "publish_after_save", False) else "save_only"))
                # 定时发布时先只保存，到点由调度器发布
                _ship = self._ship_dims(p, res)
                # 要写回的主图（与 DOM 路径同一条件）
                _img_for_save = (p.get("images") or None) \
                    if ((getattr(self.cfg, "write_main_images_to_product", True)
                         or getattr(self.cfg, "ai_image_enable", True))
                        and (p.get("images") or []) != (product.get("images") or [])) \
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
                self._prepare_video(p, res, product, dry_run)

                # API 直存（唯一保存路径，无 DOM 模拟、支持并发）
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
                    raise RuntimeError(_msg)
                add_step(res, "保存到店小秘", True,
                         f"[{p.get('id', '')}] " + r.message[:185])
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
                    add_step(res, "发布到速卖通", True, "同一页面完成（省一次列表页加载）")

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
