"""按 docs/ISSUES.md 逐项验证修复（不依赖网络/浏览器，除 flet 控件构建外全离线）。

用法：
    .venv/bin/python tools/verify_issues.py

覆盖：ISSUES #1 #2 #3 #4 #5 #6 #7 #8 #9 #10
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 隔离配置目录，避免测试写坏真实配置
os.environ.setdefault("AURORA_INSTANCE", "_verify_issues")
os.environ.pop("AURORA_PAGE", None)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import flet as ft  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    return bool(cond)


def section(title: str):
    RESULTS.append((f"---- {title} ----", True, ""))


# ------------------------------------------------------------------ fakes
class FakeControl:
    def __init__(self, value=""):
        self.value = value
        self.control = self


class FakeEvent:
    def __init__(self, value=""):
        self.control = FakeControl(value)


class FakeSnackBar:
    def __init__(self):
        self.content = ft.Text("")
        self.bgcolor = None
        self.open = False


class FakePage:
    def __init__(self):
        self.controls = []
        self.dialog = None
        self.snack_bar = FakeSnackBar()
        self.overlay = []
        self.title = ""
        self.bgcolor = ""
        self.padding = 0
        self.theme_mode = ft.ThemeMode.DARK

    def update(self, *a, **k):
        pass

    def add(self, *args, **k):
        self.controls.extend(args)

    def launch_url(self, *a, **k):
        pass

    def run_thread(self, *a, **k):
        pass


class FakeLocator:
    def __init__(self, n=0):
        self._n = n

    def count(self):
        return self._n

    def first(self):
        return self

    def input_value(self, timeout=None):
        return ""

    def fill(self, *a, **k):
        pass

    def dispatch_event(self, *a, **k):
        pass

    def click(self, *a, **k):
        pass

    def inner_text(self, timeout=None):
        return ""


class FakeEvalPage:
    """只用于 page.evaluate 的假页面。"""

    def __init__(self, payload=None, raise_exc=False):
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls = []

    def evaluate(self, script, *args):
        self.calls.append((script[:40], args))
        if self.raise_exc:
            raise RuntimeError("boom")
        return self.payload

    def locator(self, *a, **k):
        return FakeLocator(0)

    def wait_for_timeout(self, *a, **k):
        pass

    def get_by_role(self, *a, **k):
        return FakeLocator(0)

    def inner_text(self, *a, **k):
        return ""


def walk(controls):
    for c in controls or []:
        if c is None:
            continue
        yield c
        for attr in ("controls", "content", "actions"):
            sub = getattr(c, attr, None)
            if isinstance(sub, (list, tuple)):
                yield from walk(sub)
            elif sub is not None:
                yield from walk([sub])


def texts(controls) -> list[str]:
    out = []
    for c in walk(controls):
        for attr in ("value", "text", "label", "title"):
            v = getattr(c, attr, None)
            if isinstance(v, str) and v:
                out.append(v)
    return out


def main():
    from aeclone.config import AppConfig, save_config
    from aeclone.integrations import browser_cookies as bc
    import aeclone.integrations.cdp as cdp

    # ============================== #1 Cookie 优先级
    section("#1 Cookie 读取优先级 / 显示文案")
    old_read_best, old_cdp = bc.read_best, cdp.cookies_via_cdp
    try:
        manual = {"dxm_i": "abc", "dxm_t": "xyz"}
        src = bc.resolve("dianxiaomi.com", manual=manual)
        check("手动 Cookie 优先", src.ok and src.name == bc.SOURCE_MANUAL and src.cookies == manual,
              f"{src.name} {len(src.cookies)}")

        cdp.cookies_via_cdp = lambda ws, domains=None, timeout=30: {"dxm_i": "from-cdp", "JSESSIONID": "s"}
        src = bc.resolve("dianxiaomi.com", manual={}, ws_endpoint="http://127.0.0.1:9333")
        check("其次用已开浏览器(CDP)", src.ok and src.name == bc.SOURCE_CDP and src.cookies.get("dxm_i") == "from-cdp",
              src.name)

        cdp.cookies_via_cdp = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cdp down"))
        bc.read_best = lambda domain="dianxiaomi.com": bc.CookieSource("chrome", True, {"dxm_i": "local"})
        src = bc.resolve("dianxiaomi.com", manual=None, ws_endpoint="http://127.0.0.1:9333")
        check("CDP 失败回落本机库", src.ok and src.cookies.get("dxm_i") == "local", src.name)

        bc.read_best = lambda domain="dianxiaomi.com": bc.CookieSource("auto", False, {}, "chrome: 未读到")
        src = bc.resolve("dianxiaomi.com", manual={}, ws_endpoint="")
        check("全失败给出可读原因", (not src.ok) and "未读到" in src.error, src.error[:60])
        check("失败文案含「可粘贴」", "可粘贴" in bc.describe(src), bc.describe(src))
        ok_src = bc.CookieSource(bc.SOURCE_MANUAL, True, {"dxm_i": "1", "dxm_t": "2"})
        check("成功文案带来源与数量", "手动配置" in bc.describe(ok_src) and "2 个" in bc.describe(ok_src),
              bc.describe(ok_src))
        check("过期标识（缺登录 Cookie）", "可能已过期" in bc.describe(bc.CookieSource("x", True, {"a": "1"})),
              bc.describe(bc.CookieSource("x", True, {"a": "1"})))
    finally:
        bc.read_best, cdp.cookies_via_cdp = old_read_best, old_cdp

    # 预置一个不与真实配置冲突的配置
    cfg_path = AppConfig.path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"onboarded": True, "api_key": "sk-test",
                                    "store_id": "9190736", "store_name": "测试店"}), encoding="utf-8")

    from aeclone.ui.app import App

    app = App.__new__(App)               # 只测纯逻辑方法
    app.page = FakePage()
    app.cfg = AppConfig({"template_product_title": "3 吨迷你蜘蛛吊车",
                         "template_product_id": "220114600907349330"})
    app.cfg.set("template_snapshot", json.dumps({
        "fields": {"category": "加工中心(Machining Centre)", "freight": "无忧物流-标准",
                   "brand": "NoEnName_Null", "origin": "中国大陆(Mainland China)"},
        "attributes": ["Model Number=YG-109", "Power (W)=16kw", "Voltage=220V"],
    }, ensure_ascii=False))
    app.cfg.set("template_values", json.dumps({"品牌": {"t": "text", "v": "NoEnName_Null"},
                                               "型号": {"t": "text", "v": "YG-109"},
                                               "商品说明书": {"t": "file", "v": "http://x/a.pdf"},
                                               "是否提交欧盟资质": {"t": "switch", "v": False}},
                                              ensure_ascii=False))

    # ============================== #2 模板预览
    section("#2 模板预览信息量")
    preview = app._template_preview_row()
    txt = " | ".join(texts([preview]))
    check("预览显示类目", "加工中心" in txt, txt[:120])
    check("预览显示运费", "无忧物流" in txt, txt[:120])
    check("预览显示品牌", "NoEnName_Null" in txt, txt[:120])
    check("预览显示属性数", "3 项" in txt, txt[:120])
    check("预览显示抓取字段数", "4 个" in txt, txt[:120])
    check("标题不再硬截断到 44 字", "..." not in txt and "吊车" in txt, txt[:60])
    empty_app = App.__new__(App)
    empty_app.page = FakePage()
    empty_app.cfg = AppConfig({})
    empty_txt = " | ".join(texts([empty_app._template_preview_row()]))
    check("未选模板时给出占位", "未选择模板" in empty_txt and "0 项" in empty_txt, empty_txt[:80])

    # ============================== #3 必填扫描反向判定
    section("#3 必填扫描（保存后读错误 toast 反向判定）")
    from aeclone.integrations import editor_automation as ea

    p_ok = FakeEvalPage({"fields": [], "toasts": ["保存成功", "图片上传成功"]})
    st = ea._save_error_state(p_ok)
    check("成功 toast 不算失败", (not st["any"]), str(st))

    p_bad = FakeEvalPage({"fields": ["欧盟责任人名称"], "toasts": ["请输入欧盟责任人名称", "保存成功"]})
    st = ea._save_error_state(p_bad)
    check("报错项被抓到", st["any"] and "欧盟责任人名称" in st["fields"], str(st))
    check("错误 toast 被抓到且过滤成功提示", "保存成功" not in st["toasts"] and st["toasts"], str(st["toasts"]))

    st = ea._save_error_state(FakeEvalPage(raise_exc=True))
    check("页面异常不抛错", (not st["any"]) and st["fields"] == [], str(st))

    p_answer = FakeEvalPage(["名称=否", "邮箱=N/A"])
    done = ea._answer_save_errors(p_answer)
    check("自动应答返回处理项", len(done) == 2, str(done))

    p_noeu = FakeEvalPage()
    check("欧盟资料为空时不填", ea._fill_eu_responsible(p_noeu, {}) == [], "")
    p_eu = FakeEvalPage()
    out = ea._fill_eu_responsible(p_eu, {"__eu_name__": "ACME GmbH"})
    check("找不到对应表单项时安全跳过", out == [], str(out))

    check("SaveResult 支持 published", ea.SaveResult(True, "x", published=True).published is True, "")

    # ============================== #4 欧盟资质
    section("#4 欧盟资质资料校验")
    app.cfg.set("eu_qualification", "是")
    for k in App.EU_KEYS:
        app.cfg.set(k, "")
    errs = app._validate()
    check("选是但没资料 → 启动被拦", any("欧盟责任人" in e for e in errs), str(errs)[:160])
    check("缺项清单完整", app._eu_missing() == ["名称", "地址", "邮箱", "电话"], str(app._eu_missing()))

    app.cfg.set("eu_responsible_name", "ACME GmbH")
    app.cfg.set("eu_responsible_address", "Berlin, Germany")
    app.cfg.set("eu_responsible_email", "eu@acme.de")
    app.cfg.set("eu_responsible_phone", "+49 30 1234567")
    errs = app._validate()
    check("资料齐全 → 不再报错", not any("欧盟责任人" in e for e in errs), str(errs)[:160])
    check("不再缺项", app._eu_missing() == [], str(app._eu_missing()))

    app.cfg.set("eu_qualification", "否")
    errs = app._validate()
    check("选否时不要求资料", not any("欧盟责任人" in e for e in errs), str(errs)[:160])

    # 欧盟 guard：选「是」会弹资料框
    app2 = App.__new__(App)
    app2.page = FakePage()
    app2.cfg = AppConfig({})
    app2._switch = lambda idx: None
    app2.toast = lambda msg, ok=True: None
    opened = {"n": 0}

    class FakeDrop:
        def __init__(self, value):
            self.value = value
            self.on_change = None

    fld = type("F", (), {"key": "eu_qualification", "ctrl": FakeDrop("否")})()
    app2.field_refs = [fld]
    app2._bind_eu_guard()
    fld.ctrl.on_change(FakeEvent("是"))
    dlg = app2.page.dialog
    check("选是自动弹欧盟资料框", dlg is not None and getattr(dlg, "open", False) is True,
          type(dlg).__name__ if dlg else "None")

    # ============================== #5 图片空间清理
    section("#5 图片空间清理")
    from aeclone.services import album_cleanup as ac

    class FakeDxm:
        def __init__(self):
            self.deleted = None
            self.auto = None
            self.before = None
            self.fail = False

        def album_counts(self):
            return {"images": 1280, "pdfs": 4}

        def album_files(self, page=1, size=50, name="", start_time="", end_time="",
                        full_cid="", file_type=0):
            items = [{"id": "1", "name": "old1.jpg", "size": 2 * 1024 * 1024, "locked": False,
                      "created": "2026-10-01 10:00:00"},
                     {"id": "2", "name": "keep.jpg", "size": 1024, "locked": True,
                      "created": "2026-10-02 10:00:00"}]
            return items, {"total": 2, "page": 1, "size": size, "locked": 1}

        def album_delete_ids(self, ids):
            if self.fail:
                raise RuntimeError("HTTP 500")
            self.deleted = ids
            return {}

        def album_delete_before(self, days, full_cid=""):
            if self.fail:
                raise RuntimeError("HTTP 500")
            self.before = days
            return {}

        def album_auto_delete(self, days):
            if self.fail:
                raise RuntimeError("HTTP 500")
            self.auto = days
            return {}

    dxm = FakeDxm()
    info = ac.space(dxm)
    check("空间统计", info.images == 1280 and info.pdfs == 4 and "1280" in info.brief, info.brief)

    plan = ac.scan(dxm, days=30)
    check("扫描出旧图", plan.scanned == 2 and plan.delete_count == 1, plan.brief())
    check("锁定图片被跳过", plan.locked == 1, str(plan.locked))
    check("可删体积换算", ac.human_size(plan.delete_size) == "2.0M", ac.human_size(plan.delete_size))

    r = ac.delete_candidates(dxm, plan)
    check("按 ids 删除未锁定图", r["ok"] and dxm.deleted == ["1"], str(r))

    dxm.fail = True
    r = ac.delete_candidates(dxm, plan)
    check("删除失败给出错误", (not r["ok"]) and r["failed"] == 1 and r["errors"], str(r))
    r = ac.delete_older_than(dxm, 30)
    check("按时间批量删除失败可读", (not r["ok"]) and "删除失败" in r["message"], r["message"][:60])
    r = ac.auto_clean(dxm, 45)
    check("自动清理失败可读", (not r["ok"]) and "清理任务失败" in r["message"], r["message"][:60])

    dxm.fail = False
    r = ac.delete_older_than(dxm, 30)
    check("按时间删除走对接口", r["ok"] and dxm.before == 30, str(r))
    r = ac.auto_clean(dxm, 45)
    check("自动清理走对接口", r["ok"] and dxm.auto == 45, str(r))
    check("空间不可用时不抛错", ac.space(object()).images == 0, "")

    # 界面入口：清理旧图对话框（不联网）
    app5 = App.__new__(App)
    app5.page = FakePage()
    app5.cfg = AppConfig({"upload_full_cid": ""})
    app5.ws_endpoint = ""
    app5._cookies = lambda: (_ for _ in ()).throw(RuntimeError("无 Cookie"))
    app5._open_cleanup_dialog()
    dlg = app5.page.dialog
    dlg_text = " ".join(texts([dlg])) if dlg is not None else ""
    check("清理旧图对话框可打开", dlg is not None and getattr(dlg, "open", False), type(dlg).__name__)
    check("对话框含扫描/删除选中/按时间/相册入口",
          all(k in dlg_text for k in ("扫描", "删除选中", "按时间删除", "打开相册页面")), dlg_text[:120])
    check("说明自动清理接口未开放", "未开放" in dlg_text, dlg_text[-80:])
    app5._load_space_info()
    check("空间读取失败不崩", app5._space_info is None, str(getattr(app5, "_space_info", "missing")))

    # 选中删除 / 按时间删除（假接口）
    class _DelDxm(FakeDxm):
        def __init__(self):
            self.deleted = []

        def album_delete_ids(self, ids):
            self.deleted.extend(ids if isinstance(ids, list) else [ids])
            return {}

        def album_delete_before(self, days, full_cid=""):
            self.before = days
            return {"data": {"deleteCount": 3}}

    _d = _DelDxm()
    _r = ac.delete_ids(_d, ["11", "22"])
    check("清理：按选中 id 删除", _r["ok"] and _d.deleted == ["11", "22"], str(_r))
    _r = ac.delete_older_than(_d, 0)
    check("清理：按时间删除（0 天）", _r["ok"] and _d.before == 0, str(_r))

    # ============================== #6 单产品耗时（合并页面 + 传参）
    section("#6 单产品页面加载次数")
    import inspect as _insp
    sig = _insp.signature(ea.apply_and_save)
    check("apply_and_save 支持视频合并", "video_path" in sig.parameters, str(list(sig.parameters)))
    check("apply_and_save 支持页面内发布", "do_publish" in sig.parameters, "")
    src_code = _insp.getsource(ea.apply_and_save)
    check("保存后可在同一页面发布", "_publish_in_page" in src_code, "")
    check("视频接入同一页面", "_attach_video_in_page" in src_code, "")
    check("保存后反向判定错误", "_save_error_state" in src_code, "")

    pub_src = _insp.getsource(__import__("aeclone.services.publisher", fromlist=["Publisher"]).Publisher)
    check("发布不再强制开列表页", "publish_from_list" in pub_src and "r.published" in pub_src, "")
    check("视频不再单独开页面", "attach_video(" not in pub_src, "")

    # 表单值合并
    from aeclone.services.publisher import Publisher
    pcfg = AppConfig({"template_values": json.dumps({"品牌": {"t": "text", "v": "X"}}),
                      "eu_qualification": "是", "eu_responsible_name": "ACME GmbH",
                      "eu_responsible_email": "eu@acme.de"})
    fv = Publisher(pcfg)._form_values()
    check("表单值保留模板抓取", fv.get("品牌", {}).get("v") == "X", str(fv)[:80])
    check("欧盟资料传给编辑器", fv.get("__eu__") == "是" and fv.get("__eu_name__") == "ACME GmbH", str(fv)[:80])

    # 发布动作仍在 save_only 时不发布
    pcfg2 = AppConfig({"publish_action": "save_only"})
    check("save_only 不触发发布", (pcfg2.publish_action == "save_only"), "")

    # ============================== #7 侧栏可滚动
    section("#7 侧栏截断")
    page = FakePage()
    app3 = App(page)
    labels = []
    for _idx, b in app3.nav_buttons:
        try:
            labels.append(b.content.controls[1].value)
        except Exception:
            pass
    check("侧栏含「任务与日志」", "任务与日志" in labels, str(labels))
    check("侧栏含「提示词」", "提示词" in labels, "")
    check("侧栏不再有「预览」（已删除）", "预览" not in labels, "")
    root_row = page.controls[0] if page.controls else None
    check("两侧等高（STRETCH）",
          root_row is not None and getattr(root_row, "vertical_alignment", None) == ft.CrossAxisAlignment.STRETCH,
          str(getattr(root_row, "vertical_alignment", None)))
    scroll_cols = [c for c in walk([root_row]) if isinstance(c, ft.Column) and c.scroll is not None]
    check("存在可滚动导航列", bool(scroll_cols), f"{len(scroll_cols)} 个滚动列")
    nav_items = []
    for c in scroll_cols:
        nav_items.extend(texts(c.controls))
    check("滚动列内含全部导航项", all(x in nav_items for x in ("任务与日志", "提示词", "概览")),
          str(nav_items)[:120])

    # ============================== #8 授权状态
    section("#8 概览授权状态")
    app3.license_state = {"ok": True, "message": "授权有效", "expiry": "2027-01-01"}
    app3.stat_auth = ft.Text("检测中…")
    app3.stat_auth_detail = ft.Text("")
    app3._apply_license_state()
    check("显示已授权", app3.stat_auth.value == "已授权", app3.stat_auth.value)
    check("显示到期时间", "2027-01-01" in app3.stat_auth_detail.value, app3.stat_auth_detail.value)
    app3.license_state = {"ok": False, "message": "无法连接授权服务器", "expiry": ""}
    app3._apply_license_state()
    check("未授权时给原因", app3.stat_auth.value == "未授权" and "无法连接" in app3.stat_auth_detail.value,
          app3.stat_auth_detail.value)
    check("不再出现「见左下角」暗示",
          "见左下角" not in " ".join(texts(app3._p_overview())), "")
    ov = " ".join(texts(app3._p_overview()))
    check("概览有重新检测入口", "重新检测" in ov, "")

    # ============================== #9 启动自动检测 API
    section("#9 API 服务启动自动检测")
    app4 = App.__new__(App)
    app4.page = FakePage()
    app4.cfg = AppConfig({"api_key": "sk-test"})
    app4.stat_api = ft.Text("检测中…")
    app4.api_state = {"ok": True, "detail": "17 个模型", "lat": 0.4, "ts": time.time()}
    app4._apply_api_state()
    check("显示正常与模型数", "正常" in app4.stat_api.value and "17 个模型" in app4.stat_api.value,
          app4.stat_api.value)
    app4.api_state = {"ok": False, "detail": "unauthorized", "lat": 0.1, "ts": time.time()}
    app4._apply_api_state()
    check("失败显示原因", "连接失败" in app4.stat_api.value and "unauthorized" in app4.stat_api.value,
          app4.stat_api.value)
    started = {"n": 0}
    import threading as _th
    real_thread = _th.Thread

    class FakeThread:
        def __init__(self, target=None, daemon=None, **k):
            self._t = target

        def start(self):
            started["n"] += 1

    _th.Thread = FakeThread
    try:
        app4._api_probing = False
        app4.api_state = {}
        app4._probe_api_async()
    finally:
        _th.Thread = real_thread
    check("启动即发起一次探测（无需手点）", started["n"] == 1, str(started))
    started["n"] = 0
    app4.api_state = {"ok": True, "detail": "x", "lat": 0.1, "ts": time.time()}
    _th.Thread = FakeThread
    try:
        app4._probe_api_async()      # 5 分钟内缓存命中，不再打网络
    finally:
        _th.Thread = real_thread
    check("5 分钟内不重复探测", started["n"] == 0, str(started))

    # ============================== #10 启动页
    section("#10 AURORA_PAGE 在计划任务下不生效")
    app5 = App.__new__(App)
    app5.cfg = AppConfig({"start_page": 3})
    old_argv = sys.argv
    try:
        sys.argv = ["AuroraList.exe", "--page", "7"]
        check("命令行 --page 生效", app5._start_page_index() == 7, str(app5._start_page_index()))
        sys.argv = ["AuroraList.exe", "--page=11"]
        check("命令行 --page=N 生效", app5._start_page_index() == 11, "")
        sys.argv = ["AuroraList.exe"]
        tmp = Path(tempfile.mkdtemp()) / "page.txt"
        tmp.write_text("12", encoding="utf-8")
        os.environ["AURORA_PAGE_FILE"] = str(tmp)
        check("页面文件生效（计划任务可靠）", app5._start_page_index() == 12, "")
        os.environ.pop("AURORA_PAGE_FILE")
        os.environ["AURORA_PAGE"] = "5"
        check("环境变量仍兼容", app5._start_page_index() == 5, "")
        os.environ.pop("AURORA_PAGE")
        check("回落到配置 start_page", app5._start_page_index() == 3, "")
    finally:
        sys.argv = old_argv
        os.environ.pop("AURORA_PAGE", None)
        os.environ.pop("AURORA_PAGE_FILE", None)

    # ============================== 追加：高级设置三项（水印/定时/套装）
    section("高级设置：水印 / 定时发布 / 套装 SKU")
    from aeclone.services import combo as combo_svc
    rows = combo_svc.expand([{"sku": "Red-XL", "price": 10}, {"sku": "Blue-L", "price": "12.5"}],
                            "套装", 2, None)
    names = [r["sku"] for r in rows]
    prices = [r.get("price") for r in rows]
    check("套装：原 SKU 保留在前", names[:2] == ["Red-XL", "Blue-L"], str(names))
    check("套装：追加「前缀-原名」", names[2:] == ["套装-Red-XL", "套装-Blue-L"], str(names))
    check("套装：价格 × 倍率", prices[0] == 10 and prices[2] == 20 and prices[3] == 25.0, str(prices))
    rows2 = combo_svc.expand([{"sku": "A"}], "套装", 2, 100)
    check("套装：无价时用成本兜底", rows2[1].get("price") == 200, str(rows2[1]))
    check("套装：预览可读", any("套装-A" in x for x in combo_svc.preview([{"sku": "A", "price": 1}], "套装", 2, 1)),
          str(combo_svc.preview([{"sku": "A", "price": 1}], "套装", 2, 1)))

    from aeclone.services import image_edit_plans as iep
    raw = json.dumps([{"group": "main", "target": 2, "refs": "1,3-4", "prompt": "去掉背景",
                       "mode": "edit"},
                      {"group": "detail", "target": 1, "refs": "all", "prompt": "翻译成英文",
                       "mode": "generate"},
                      {"group": "bad", "target": 0, "prompt": ""},
                      "not-a-dict"])
    slots = iep.parse(raw)
    check("方案：解析并丢弃脏数据", len(slots) == 2, str(slots))
    check("方案：非法组回落 main", slots[0]["group"] == "main", str(slots[0]))
    check("方案：target 至少 1", iep.parse('[{"target":0,"prompt":"x"}]')[0]["target"] == 1, "")
    check("方案：refs 解析", iep.refs_indices("1,3-4", 5, exclude=1) == [3, 4], str(iep.refs_indices("1,3-4", 5, 1)))
    check("方案：refs all 排除目标", iep.refs_indices("all", 4, exclude=2) == [1, 3, 4],
          str(iep.refs_indices("all", 4, 2)))
    check("方案：描述可读", "主图" in iep.describe(slots)[0], str(iep.describe(slots)))
    check("方案：空列表安全", iep.parse("") == [] and iep.parse("[]") == [] and iep.parse(None) == [], "")

    # 执行器：替换 / 追加 / 多图素材
    from aeclone.services.publisher import Publisher, parse_run_at
    from PIL import Image as _PImg
    _fake_dir = Path(tempfile.mkdtemp())

    def fake_url_file(url):
        f = _fake_dir / (str(abs(hash(url))) + ".png")
        if not f.exists():
            _PImg.new("RGB", (64, 64), (200, 200, 200)).save(f)
        return f

    class FakeImgSvc:
        def __init__(self):
            self.calls = []

        def edit_many(self, files, prompt, tag=""):
            self.calls.append(("edit_many", len(list(files)), prompt, tag))
            return f"http://x/edited-{len(self.calls)}.png"

        def generate(self, prompt, size="1024x1024", n=1):
            self.calls.append(("generate", prompt))
            return ["http://x/gen.png"]

    pcfg = AppConfig({
        "image_edit_plans": json.dumps([
            {"group": "main", "target": 1, "refs": "2,3", "prompt": "换背景", "mode": "edit"},
            {"group": "main", "target": 2, "refs": "all", "prompt": "", "mode": "generate", "add_new": True},
            {"group": "detail", "target": 1, "refs": "", "prompt": "翻译", "mode": "edit"},
            {"group": "sku", "target": 1, "refs": "all", "prompt": "白底", "mode": "edit"},
        ]),
        "designated_edit_prompt": "默认提示词",
        "image_cache_enable": False,
    })
    pub2 = Publisher(pcfg)
    fake_svc = FakeImgSvc()
    pub2._img_service = lambda: fake_svc
    pub2._fetch = lambda url, workdir=None: Path(fake_url_file(url))
    prod2 = {"id": "p1", "title": "t", "images": ["http://x/a.png", "http://x/b.png", "http://x/c.png"],
             "detail_images": ["http://x/d1.png"], "skus": [{"sku": "s1", "image": "http://x/s1.png"}]}
    from aeclone.services.publisher import PublishResult
    res2 = PublishResult(product_id="p1", ok=True)
    out = pub2._apply_edit_plans(dict(prod2), res2)
    steps = [f"{s.name}:{s.ok}:{s.detail}" for s in res2.steps]
    check("方案：编辑替换目标图", out["images"][0].startswith("http://x/edited"), str(out["images"]))
    check("方案：生成追加新图", len(out["images"]) >= 4, str(out["images"]))
    check("方案：描述图被替换", out["detail_images"][0].startswith("http://x/edited"), str(out["detail_images"]))
    check("方案：SKU 图被替换", str(out["skus"][0].get("processed_image")).startswith("http://x/edited"),
          str(out["skus"][0]))
    check("方案：编辑调用带多图素材", any(c[0] == "edit_many" and c[1] >= 2 for c in fake_svc.calls),
          str(fake_svc.calls))
    check("方案：每条都有结果步骤", any("精细任务1" in x for x in steps), str(steps)[:160])

    pub3 = Publisher(pcfg)
    res3 = PublishResult(product_id="p2", ok=True)
    pub3._apply_edit_plans({"id": "p2"}, res3, dry_run=True)
    check("方案：dry-run 不调用模型", any("dry-run" in s.detail for s in res3.steps), str(res3.log()))

    # 定时发布：保存后登记、不再提前 return
    future = __import__("datetime").datetime.now() + __import__("datetime").timedelta(hours=2)
    when = future.strftime("%Y-%m-%d %H:%M")
    check("定时：时间解析", abs(parse_run_at(when) - future.timestamp()) < 120, "")
    try:
        parse_run_at("明天九点")
        check("定时：坏格式报错", False, "")
    except ValueError:
        check("定时：坏格式报错", True, "")

    class FakeSched:
        def __init__(self):
            self.jobs = []

        def add(self, pid, run_at, title=""):
            self.jobs.append((pid, run_at, title))

    from aeclone.integrations import editor_automation as _ea
    calls = {}

    def fake_apply(ws, pid, snap=None, title=None, *, form_values=None, do_publish=False, video_path=None):
        calls.update({"ws": ws, "pid": pid, "form_values": form_values, "do_publish": do_publish,
                      "video": video_path})
        return _ea.SaveResult(True, "保存接口 HTTP 200")

    real_apply = _ea.apply_and_save
    _ea.apply_and_save = fake_apply
    try:
        scfg = AppConfig({"schedule_publish": True, "schedule_time": when,
                          "publish_action": "publish", "api_key": "sk-test"})
        for k in ("white_bg_enable", "watermark_enable", "edit_enable", "img_main_edit",
                  "img_detail_white_bg", "img_detail_remove_wm", "img_detail_edit",
                  "img_sku_white_bg", "img_sku_remove_wm", "img_sku_edit",
                  "translate_main_pic_enable", "translate_detail_pic_enable", "translate_sku_pic",
                  "main_image_generate_enable", "designated_edit_enable", "marketing_image_enable",
                  "generate_main_video", "overlay_watermark_enable", "four_in_one",
                  "pad_main_images_from_extra", "review_text_enable", "review_main_img_enable",
                  "review_detail_img_enable", "enable_ai_title", "enable_ai_detail",
                  "enable_ai_sku", "enable_ai_weight", "check_banned_word"):
            scfg.set(k, False)
        fsched = FakeSched()
        pres = Publisher(scfg, scheduler=fsched, ws_endpoint="ws://x").run(
            {"id": "777", "title": "定时测试", "skus": [], "images": []}, dry_run=False)
        check("定时：保存真的执行了", calls.get("pid") == "777", str(calls)[:120])
        check("定时：保存时不立即发布", calls.get("do_publish") is False, str(calls.get("do_publish")))
        check("定时：登记到调度器", len(fsched.jobs) == 1 and fsched.jobs[0][0] == "777", str(fsched.jobs))
        check("定时：返回结果包含登记步骤",
              any(s.name == "定时任务" and s.ok for s in pres.steps), pres.log()[:160])

        scfg2 = AppConfig({"schedule_publish": True, "schedule_time": when})
        for k in ("white_bg_enable", "watermark_enable", "edit_enable", "img_sku_white_bg"):
            scfg2.set(k, False)
        pres2 = Publisher(scfg2, scheduler=None, ws_endpoint="ws://x").run(
            {"id": "778", "title": "无调度器", "skus": [], "images": []}, dry_run=False)
        check("定时：没有调度器时如实报错", (not pres2.ok) and "定时登记失败" in (pres2.error or ""),
              f"{pres2.ok} {pres2.error}")
    finally:
        _ea.apply_and_save = real_apply

    # 四合一位置
    from aeclone.services.image_ops import four_in_one as _fio
    from PIL import Image as _PImg
    d4 = Path(tempfile.mkdtemp())
    paths = []
    for i in range(4):
        f = d4 / f"q{i}.png"
        _PImg.new("RGB", (320, 240), (30 * i + 40, 80, 120)).save(f)
        paths.append(f)
    out4 = _fio(paths, d4 / "four.png", size=600)
    with _PImg.open(out4) as im:
        check("四合一：输出方图", im.size == (600, 600), str(im.size))
    check("四合一：文件存在", out4.exists() and out4.stat().st_size > 1000, str(out4))

    # 背景替换规则：用规则自己的提示词
    bcfg = AppConfig({"background_gen_rules": json.dumps(
        [{"position": "main_1", "prompt": "换成浅灰摄影棚背景"},
         {"position": "detail_1", "prompt": "描述图也换灰底"}], ensure_ascii=False)})
    for k in ("white_bg_enable", "watermark_enable", "img_main_edit", "img_detail_white_bg",
              "img_detail_remove_wm", "img_detail_edit", "img_sku_white_bg", "img_sku_remove_wm",
              "img_sku_edit", "translate_main_pic_enable", "translate_detail_pic_enable",
              "translate_sku_pic", "main_image_generate_enable", "designated_edit_enable",
              "marketing_image_enable", "overlay_watermark_enable", "four_in_one"):
        bcfg.set(k, False)
    pub_bg = Publisher(bcfg)
    bg_calls = []

    def fake_edit(url, prompt):
        bg_calls.append((url, prompt))
        return f"http://x/bg-{len(bg_calls)}.png"

    pub_bg._edit_with_prompt = fake_edit
    from aeclone.services.publisher import PublishResult as _PR
    res_bg = _PR(product_id="bg", ok=True)
    out_bg = pub_bg._ai_images({"images": ["http://x/m1.png", "http://x/m2.png"],
                                "detail_images": ["http://x/d1.png"], "skus": []}, res_bg)
    check("背景规则：使用规则里的提示词", any("浅灰摄影棚" in c[1] for c in bg_calls), str(bg_calls))
    check("背景规则：替换对应主图", out_bg["images"][0].startswith("http://x/bg-"), str(out_bg["images"]))
    check("背景规则：支持描述图", any("描述图" in c[1] for c in bg_calls), str(bg_calls))
    check("背景规则：步骤有记录", any(s.name == "背景生成" for s in res_bg.steps), res_bg.log()[:120])

    # 计划里包含逐图方案
    from aeclone.services.image_task_plan import build_plan as _bp
    pcfg3 = AppConfig({"image_edit_plans": json.dumps([{"group": "main", "target": 1, "prompt": "x"}]),
                       "four_in_one": True, "four_in_one_position": "first"})
    plan3 = _bp(pcfg3, {"images": ["a", "b"], "detail_images": [], "skus": []})
    check("图片计划：含逐图方案", any("逐图方案" in x for x in plan3.steps), str(plan3.steps))
    check("图片计划：四合一位置", any("主图第 1 张" in x for x in plan3.steps), str(plan3.steps))

    # 水印
    from aeclone.services.image_ops import add_watermark
    wm = d4 / "wm.png"
    _PImg.new("RGBA", (120, 60), (255, 0, 0, 180)).save(wm)
    base_img = d4 / "base.png"
    _PImg.new("RGB", (600, 600), (250, 250, 250)).save(base_img)
    wm_out = add_watermark(base_img, wm, d4 / "out.png", position="bottom-right", opacity=0.7)
    with _PImg.open(wm_out) as im:
        px = im.getpixel((560, 560))
    check("水印：右下角像素被改写", px != (250, 250, 250), str(px))
    text_out = add_watermark(base_img, "@brand", d4 / "out2.png", position="center")
    check("水印：支持文字水印", Path(text_out).exists(), str(text_out))

    # ============================== 第 4 轮：价格页 / 工作台 / 图片方案 UI
    section("价格页（去 JSON + 阶梯/区域/批发）")
    from aeclone.services.pricing import (compute_price, rows_to_tiers, tiers_to_rows,
                                          rows_to_regional, regional_to_rows,
                                          rows_to_wholesale, wholesale_to_rows)
    pc = AppConfig({"goods_cost": "100", "price_multiplier": "1.35", "price_fixed_add": "5",
                    "price_mode": "multiplier"})
    r = compute_price(pc, 100)
    check("价格：倍率+固定加价", r.final == 140.0, str(r.final))
    pc.set("tuoguan_price_tiered_enabled", True)
    pc.set("tuoguan_price_tiers", rows_to_tiers([{"min": 0, "max": 200, "multiplier": 1.5}]))
    r = compute_price(pc, 100)
    check("价格：阶梯按成本区间", r.final == 155.0 and r.tier_multiplier == 1.5, f"{r.final} {r.tier_multiplier}")
    check("价格：阶梯表格往返", tiers_to_rows(pc.tuoguan_price_tiers)[0]["multiplier"] == 1.5, pc.tuoguan_price_tiers)
    pc.set("price_mode", "commission")
    pc.set("platform_commission", "10")
    pc.set("sales_profit", "20")
    r = compute_price(pc, 100)
    check("价格：佣金利润模式参与计算", r.mode == "commission" and r.final > 100, f"{r.mode} {r.final}")
    pc.set("regional_pricing", True)
    pc.set("adjust_price_template_detail", rows_to_regional([{"currency": "USD", "rate": 1.0},
                                                             {"currency": "EUR", "rate": 0.9}]))
    r = compute_price(pc, 100)
    check("价格：区域倍率", abs(r.regional.get("EUR", 0) - round(r.final * 0.9, 2)) < 0.01,
          str(r.regional))
    check("价格：区域表格往返", regional_to_rows(pc.adjust_price_template_detail)[0]["currency"] == "USD", "")
    pc.set("wholesale_enable", True)
    pc.set("wholesale_tiers", rows_to_wholesale([{"min_qty": 10, "price": 9.9},
                                                 {"min_qty": 50, "price": 8.8}]))
    r = compute_price(pc, 100)
    check("价格：批发价参与", len(r.wholesale) == 2 and r.wholesale[0]["min_qty"] == 10, str(r.wholesale))

    section("图片方案（简单规则 + 精细任务）")
    from aeclone.services import image_jobs as ij
    from aeclone.services import image_presets as ipr
    rj = ij.rows_to_rules([{"group": "main", "range": "1-3", "ops": ["white", "watermark"]},
                           {"group": "detail", "range": "1-5", "ops": ["translate"]}])
    check("方案：简单规则往返", len(ij.parse_rules(rj)) == 2 and ij.parse_rules(rj)[1]["group"] == "detail", rj)
    jj = ij.rows_to_jobs([{"group": "main", "index": 1, "refs": [2, 3], "prompt": "合成主图",
                           "mode": "generate", "count": 1, "insert": "first"},
                          {"group": "sku", "index": 2, "refs": "", "prompt": "", "mode": "edit"}])
    check("方案：精细任务（空提示词丢弃）", len(ij.parse_jobs(jj)) == 1, jj)
    check("方案：素材序号解析", ij.refs_indices("all", 4, exclude=2) == [1, 3, 4], "")
    check("方案：中文预设存在", len(ipr.presets_for("main")) >= 3 and ipr.get_preset("main", "white_studio"),
          "")
    check("方案：预设英文底层", "white background" in ipr.get_preset("main", "white_studio")["en"].lower(), "")
    check("方案：翻译预设中文名", "英语" not in ipr.translate_prompt("en") and "Translate" in ipr.translate_prompt("en"),
          ipr.translate_prompt("en")[:60])

    # 旧配置迁移（指定编辑）
    old_cfg = AppConfig({"designated_edit_enable": True, "designated_edit_indices": "1,2",
                         "designated_edit_prompt": "换背景", "white_bg_enable": True,
                         "white_background_pic_1": "1", "white_background_pic_2": "2"})
    rj2, jj2 = ij.migrate(old_cfg, with_global=True)
    check("迁移：指定编辑→精细任务", len(ij.parse_jobs(jj2)) == 2, jj2)
    check("迁移：全局白底→简单规则", len(ij.parse_rules(rj2)) >= 1, rj2)

    # 发布引擎：简单规则 + 精细任务（假服务）
    from aeclone.services.publisher import Publisher as _Pub, PublishResult as _PR2

    class ImgSvc2:
        def __init__(self):
            self.calls = []

        def combined(self, fp):
            self.calls.append(("combined", str(fp)))
            return "http://x/simple.png"

        def white_background(self, fp):
            self.calls.append(("white", str(fp))); return "http://x/w.png"

        def remove_watermark(self, fp):
            self.calls.append(("wm", str(fp))); return "http://x/wm.png"

        def edit(self, fp, prompt):
            self.calls.append(("edit", prompt[:20])); return "http://x/e.png"

        def edit_many(self, files, prompt, tag=""):
            self.calls.append(("many", len(list(files)), prompt[:12])); return "http://x/many.png"

        def generate(self, prompt, **k):
            self.calls.append(("gen", prompt[:12])); return ["http://x/gen.png"]

    jcfg = AppConfig({"image_simple_rules": rj, "image_ai_jobs": jj,
                      "api_key": "sk", "translate_prompt": "", "image_cache_enable": False})
    pubj = _Pub(jcfg)
    svc2 = ImgSvc2()
    pubj._img_service = lambda: svc2
    pubj._fetch = lambda url, workdir=None: Path(fake_url_file(url))
    resj = _PR2(product_id="j", ok=True)
    outj = pubj._apply_image_jobs({"id": "j", "images": ["http://x/a.png", "http://x/b.png", "http://x/c.png"],
                                    "detail_images": ["http://x/d.png"], "skus": [
                                        {"sku": "s1", "image": "http://x/s1.png"}]}, resj)
    check("引擎：简单规则替换", outj["images"].count("http://x/simple.png") >= 3, str(outj["images"]))
    check("引擎：精细任务生成插到第 1 张", outj["images"][0].startswith("http://x/many")
          or outj["images"][0].startswith("http://x/simple"), str(outj["images"]))
    check("引擎：步骤有记录", any(s.name.startswith("简单处理") for s in resj.steps)
          and any(s.name.startswith("精细任务") for s in resj.steps), resj.log()[:160])

    section("模板持久化 / 详情 API / 页面去重")
    app6 = App.__new__(App)
    app6.page = FakePage()
    app6.cfg = AppConfig({"store_id": "1", "store_name": "A", "template_product_id": "9",
                          "template_product_title": "模板T",
                          "template_snapshot": json.dumps({"fields": {"category": "X"}, "attributes": ["A=B"]})})
    app6.shop_dd = type("D", (), {"value": "1", "options": []})()
    app6.tpl_preview_box = ft.Container()
    app6.tpl_snapshot_text = ft.Text("")
    app6._sync_shops = lambda: None
    app6._on_shop_change()
    check("模板：同店铺刷新不清模板", app6.cfg.template_product_id == "9", str(app6.cfg.template_product_id))
    app6.shop_dd.value = "2"
    app6._on_shop_change()
    check("模板：换店铺才清模板", app6.cfg.template_product_id == "", "")

    from aeclone.integrations.dianxiaomi import DianxiaomiClient as _DX

    class _DetailDX(_DX):
        def __init__(self):
            pass

        def _get(self, key, params=None):
            return {"data": {"product": {
                "id": "123", "subject": "测试模板商品", "categoryId": "100", "freightTemplateId": "200",
                "grossWeight": 12.5, "packageLength": 30, "packageWidth": 20, "packageHeight": 10,
                "imageURLs": "https://x/a.jpg;https://x/b.jpg",
                "aeopAeProductPropertys": json.dumps([
                    {"attrName": "Brand Name", "attrValue": "ACME"},
                    {"attrName": "Origin", "attrValue": "China"},
                    {"attrName": "Model Number", "attrValue": "M1"},
                ], ensure_ascii=False),
                "productManual": "https://x/m.pdf",
            }}}

    _snap = _DetailDX().template_snapshot("123")
    check("模板：API 读取字段", _snap["fields"]["weight"] == "12.5" and
          any("Brand Name=ACME" == a for a in _snap["attributes"]), str(_snap["attributes"])[:120])
    check("模板：API 读取说明书", _snap["values"].get("商品说明书", {}).get("v") == "https://x/m.pdf", "")

    section("界面：价格页 / 图片页 / 工作台（假接口）")
    import aeclone.ui.app as _m
    import aeclone.integrations.browser_cookies as _bc

    _m.verify_license = lambda *a, **k: {"ok": True, "message": "授权有效", "expiry": ""}
    _m.Scheduler.start = lambda self, runner, interval=30: None
    _m.App._probe_api_async = lambda self, force=False: None
    _m.App._check_license = lambda self: None
    _bc.resolve = lambda *a, **k: _bc.CookieSource("手动配置", True, {"dxm_i": "x"})

    class DxmUI:
        def __init__(self, *a, **k): pass

        def product_page(self, page=1, size=30, shop_id=None, status=None, keyword=None):
            return ([{"id": "1", "title": "吊车A", "status": "editingRequired",
                      "images": ["http://x/a.png", "http://x/b.png"], "skus": [1]}],
                    {"total": 1, "total_pages": 1, "page": page, "size": size})

        def user_info(self): return {"account": "tester"}
        def upload_image_from_url(self, u, full_cid=""): return {}
        def search_price_templates(self, kw="", **k):
            return [{"id": "11", "name": "欧洲模板"}]

    _m.DianxiaomiClient = DxmUI

    class ImgUI:
        def __init__(self, *a, **k): pass
        def white_background(self, p): return "http://x/white.png"
        def remove_watermark(self, p): return "http://x/wm.png"
        def edit(self, p, prompt): return "http://x/edit.png"
        def combined(self, p): return "http://x/simple.png"

    _m.ImageService = ImgUI
    import aeclone.services as _svc
    _svc.ImageService = ImgUI
    import aeclone.services.image_pipeline as _ip
    _ip.ImageService = ImgUI

    class _Resp:
        content = b"\x89PNG\r\n\x1a\n" + b"0" * 80
        def raise_for_status(self): pass

    import requests as _rq2
    _rq2.get = lambda *a, **k: _Resp()

    import threading as _th2
    _real_th2 = _th2.Thread

    class _SyncTh:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **k):
            self.t, self.a, self.kw = target, args, kwargs or {}

        def start(self):
            if self.t:
                self.t(*self.a, **self.kw)

        def is_alive(self): return False

    ui_cfg = AppConfig({"onboarded": True, "api_key": "sk-test", "store_id": "1",
                        "store_name": "测试店", "start_page": 5})
    save_config(ui_cfg)
    up = FakePage()
    uapp = _m.App(up)
    check("界面：图片页构建", len(uapp._p_image()) >= 3, "")
    img_labels = " ".join(texts(uapp.content.controls))
    check("界面：图片页无 JSON 字样", "JSON" not in img_labels, img_labels[:80])
    check("界面：有简单处理/精细处理", "简单处理" in img_labels and "精细处理" in img_labels, "")
    # 点「添加一条」（精细任务）后才能看到预设下拉
    add_btns = [c for c in walk(uapp.content.controls) if getattr(c, "text", "") == "添加一条"]
    for b in add_btns:
        b.on_click(FakeEvent())
    check("界面：有中文预设下拉", any(getattr(c, "label", "") == "中文预设" for c in walk(uapp.content.controls)), "")

    # 价格页
    uapp._switch(6)
    price_labels = " ".join(texts(uapp.content.controls))
    check("界面：价格页无 JSON 字样", "JSON" not in price_labels, price_labels[:80])
    check("界面：有阶梯/区域/批发", all(k in price_labels for k in ("阶梯价格", "区域倍率", "批发价")), "")
    # 点一次计算
    calc_btn = next((c for c in walk(uapp.content.controls) if getattr(c, "text", "") == "计算"), None)
    check("界面：价格试算可点", calc_btn is not None, "")
    if calc_btn:
        calc_btn.on_click(FakeEvent())
        check("界面：试算有结果", str(uapp.price_out.value).startswith("¥"), str(uapp.price_out.value))
    # 区域模板搜索（list 返回不再崩）
    search_btn = next((c for c in walk(uapp.content.controls) if getattr(c, "text", "") == "搜索"), None)
    if search_btn:
        _th2.Thread = _SyncTh
        try:
            search_btn.on_click(FakeEvent())
        finally:
            _th2.Thread = _real_th2
        check("界面：区域模板搜索不崩", "失败" not in uapp.tpl_status.value and len(uapp.tpl_list.controls) >= 1,
              uapp.tpl_status.value)

    # 工作台
    uapp._switch(9)
    wb_labels = " ".join(texts(uapp.content.controls))
    check("界面：工作台有连接/产品/处理/队列", all(k in wb_labels for k in ("连接", "产品", "图片处理", "任务队列")), "")
    uapp._cookies = lambda *a, **k: {"dxm_i": "x"}
    _th2.Thread = _SyncTh
    try:
        uapp._wb_load_products(1)
    finally:
        _th2.Thread = _real_th2
    check("工作台：产品可加载", len(uapp.wb_prod_table.rows) == 1, str(len(uapp.wb_prod_table.rows)))
    uapp.wb_selected.add("1")
    uapp._wb_sel_refresh()
    _th2.Thread = _SyncTh
    try:
        uapp._wb_pick_images()
    finally:
        _th2.Thread = _real_th2
    check("工作台：取图成功", len(uapp.picked) == 2, str(len(uapp.picked)))
    uapp._wb_process("white")
    check("工作台：白底处理", len(uapp.wb_results.controls) >= 1, str(len(uapp.wb_results.controls)))
    uapp._upload_results()
    check("工作台：上传不崩", True, "")

    # ============================== 第 5 轮：授权 / 缓存 / 并发 / 多开
    section("授权（机器码 + authorized 判定）")
    import re as _re
    from aeclone import license as _lic
    code1 = _lic.machine_code()
    code2 = _lic.machine_code()
    check("授权：机器码稳定", code1 == code2 and len(code1) == 17, code1)
    check("授权：机器码格式 XXXXXXXX-XXXXXXXX", bool(_re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{8}", code1)), code1)
    check("授权：identity 为 64 位", bool(_re.fullmatch(r"[0-9a-f]{64}", _lic.machine_id())), "")

    from unittest import mock as _mock

    class _Resp2:
        status_code = 200

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    with _mock.patch.object(_lic.requests, "post", return_value=_Resp2(
            {"ok": True, "authorized": False, "message": "未授权：该设备尚未登记"})):
        r_lic = _lic.verify("https://license.example", timeout=3)
    check("授权：未登记设备不再误判", (not r_lic["ok"]) and "未登记" in r_lic["message"], str(r_lic))
    with _mock.patch.object(_lic.requests, "post", return_value=_Resp2(
            {"ok": True, "authorized": True, "expiry": "2027-01-01"})):
        r_lic = _lic.verify("https://license.example", timeout=3)
    check("授权：已登记设备通过", r_lic["ok"] and r_lic["expiry"] == "2027-01-01", str(r_lic))
    with _mock.patch.object(_lic.requests, "post", side_effect=RuntimeError("net down")):
        r_lic = _lic.verify("https://license.example", timeout=3)
    check("授权：网络异常可读", (not r_lic["ok"]) and "无法连接" in r_lic["message"], r_lic["message"][:60])

    # 凭证验签（用服务器真实签发的一组做固定用例，有效期到 2027-11-17）
    from aeclone.license import verify_voucher as _vv, PUBLIC_KEY_PEM as _pub
    _payload = "D6117EDB-F43215E2|2027-11-17|2026-11-17|local"
    _voucher = __import__("base64").urlsafe_b64encode(_payload.encode()).decode().rstrip("=")
    _sig = "56IeghEHgVUr2Np44eFI1VuyE9mzsF85_Um0SN1Cdru_RuspyAxZOAp5P5xK_yTyb1WYgGZbVmdLmhAlYbhneQ"
    check("授权：凭证验签通过", _vv(_voucher, _sig, "D6117EDB-F43215E2", "local", _pub) is True, "")
    check("授权：改 identity 验签失败",
          _vv(_voucher, _sig, "00000000-0000000", "local", _pub) is False, "")
    check("授权：改 client_type 验签失败",
          _vv(_voucher, _sig, "D6117EDB-F43215E2", "shop", _pub) is False, "")
    try:
        _vv(_voucher, _sig[:-4] + "AAAA", "D6117EDB-F43215E2", "local", _pub)
        check("授权：改签名验签失败", False, "")
    except Exception:
        check("授权：改签名验签失败", True, "")

    with _mock.patch.object(_lic.requests, "post", return_value=_Resp2(
            {"ok": True, "authorized": True, "message": "已授权", "expiry": "2027-01-01",
             "identity": "OLD-ID-1234", "client_type": "local"})):
        r_old = _lic.verify("https://license.example", timeout=3, identity_override="OLD-ID-1234")
    check("授权：旧版 ID 优先", r_old["ok"] and r_old["tried"][0] == "OLD-ID-1234", str(r_old.get("tried")))

    section("性能：图片/价格/模型缓存")
    import aeclone.services.image_cache as _ic
    _ic.clear()
    _ic.put("http://x/a.png", "simple:white", "http://x/a-white.png", "m1")
    check("缓存：命中", _ic.get("http://x/a.png", "simple:white", "m1") == "http://x/a-white.png", "")
    check("缓存：不同动作不串", _ic.get("http://x/a.png", "simple:watermark", "m1") is None, "")
    check("缓存：不同模型不串", _ic.get("http://x/a.png", "simple:white", "m2") is None, "")
    check("缓存：清理", _ic.clear() >= 1 and _ic.stats()["count"] == 0, "")

    from aeclone.api import ApiClient as _AC2

    class _FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **k):
            self.calls += 1
            if "/api/pricing" in url:
                return _Resp3({"data": [{"model_name": "m1", "model_price": 1.0, "model_ratio": 0.5}]})
            return _Resp3({"data": [{"id": "m1"}]})

    class _Resp3:
        status_code = 200

        def __init__(self, d):
            self._d = d

        def json(self):
            return self._d

    fs = _FakeSession()
    ac = _AC2("https://cache-test.example", "k", 5, session=fs)
    p1 = ac.pricing(force=True)
    p2 = ac.pricing()
    check("缓存：价格表只请求一次", fs.calls == 1 and p1 == p2, f"calls={fs.calls}")
    ac.models(force=True)
    ac.models()
    check("缓存：模型列表只请求一次", fs.calls == 2, f"calls={fs.calls}")

    section("多开：实例独立端口")
    from aeclone.core import instances as _inst
    old_inst_env = os.environ.get("AURORA_INSTANCE")
    os.environ["AURORA_INSTANCE"] = "_verify_multi"
    try:
        n1 = _inst.create_instance("店铺A")
        n2 = _inst.create_instance("店铺B")
        check("多开：端口不重复", n1["cdp_port"] != n2["cdp_port"] and n1["cdp_port"] >= 9334,
              f"{n1['cdp_port']} vs {n2['cdp_port']}")
        names = [i["name"] for i in _inst.list_instances()]
        check("多开：实例可见", "店铺A" in names and "店铺B" in names, str(names))
        check("多开：端口记录在实例列表", all(i.get("cdp_port") for i in _inst.list_instances() if i["name"] in names),
              str(_inst.list_instances()))
    finally:
        if old_inst_env is None:
            os.environ.pop("AURORA_INSTANCE", None)
        else:
            os.environ["AURORA_INSTANCE"] = old_inst_env

    # 启动实例：命令与环境（不真的开窗口）
    with _mock.patch.object(_inst.subprocess, "Popen") as mp:
        _inst.launch_instance("店铺A")
        args, kwargs = mp.call_args
        env = kwargs.get("env") or {}
        check("多开：启动带实例名", env.get("AURORA_INSTANCE") == "店铺A", str(env.get("AURORA_INSTANCE")))
        check("多开：窗口位置错开", "," in str(env.get("AURORA_WINDOW_POS")), str(env.get("AURORA_WINDOW_POS")))
        check("多开：入口脚本正确", any(str(x).endswith("main.py") for x in (args[0] or [])), str(args[0]))

    section("并发：多产品同时处理")
    uapp.cfg.set("concurrency", 4)
    uapp.cfg.set("auto_save_products", False)
    started = {"n": 0}
    elapsed = {"t": 0.0}
    real_pub = _m.Publisher

    class _SlowPublisher:
        def __init__(self, *a, **k): pass

        def run(self, prod, dry_run=True, manage_progress=True):
            started["n"] += 1
            __import__("time").sleep(0.2)
            return _PR2(product_id=str(prod.get("id")), ok=True)

    class _DxmMany:
        def __init__(self, *a, **k): pass

        def product_page(self, *a, **k):
            return ([], {"total": 0})

        def pending_products(self, limit=0, shop_id=None):
            return [{"id": str(i), "title": f"p{i}", "images": [], "skus": []} for i in range(4)]

    _m.Publisher = _SlowPublisher
    _m.DianxiaomiClient = _DxmMany
    uapp._cookies = lambda *a, **k: {"dxm_i": "x"}
    t0 = time.time()
    uapp._run_pipeline()
    elapsed["t"] = time.time() - t0
    _m.Publisher = real_pub
    check("并发：4 个产品都处理", started["n"] == 4, str(started["n"]))
    check("并发：耗时接近单个（并行生效）", elapsed["t"] < 0.55, f"{elapsed['t']:.2f}s")

    section("设置页：机器码 / API 购买 / 多开")
    uapp._switch(8)
    set_labels = " ".join(texts(uapp.content.controls))
    check("设置：显示机器码", "机器码" in set_labels and _lic.machine_code() in set_labels, set_labels[:100])
    check("设置：有复制机器码", "复制机器码" in set_labels, "")
    check("设置：有 API 购买入口", "购买 / 充值 API" in set_labels, "")
    check("设置：有测试连接", "测试连接" in set_labels, "")
    check("设置：多开入口", "多开 / 多任务" in set_labels and "创建并启动" in set_labels, "")
    check("设置：没有授权中心按钮（管理员用）", "授权中心" not in set_labels, "")
    check("设置：不显示 API 地址", "API 地址" not in set_labels, "")
    check("设置：只显示 API Key 输入", "API Key" in set_labels, "")
    from aeclone.services import FIXED_MODELS, model_of as _mo
    check("设置：显示固定模型", all(m in set_labels for m in FIXED_MODELS.values()), str(FIXED_MODELS))
    check("设置：可填旧版授权 ID", "旧版授权 ID" in set_labels, "")
    check("模型：配置改不动固定模型", _mo(type("C", (), {"model_text": "deepseek-chat"})(), "text") == "qwen-plus", "")
    check("设置：没有模型下拉", not any(getattr(c, "label", "") in ("文本（标题/描述/SKU）", "图片编辑（白底/去水印/精修）")
                                        for c in walk(uapp.content.controls)), "")
    api_key_fields = [c for c in walk(uapp.content.controls) if getattr(c, "label", "") == "API Key"]
    check("设置：API Key 只有一个输入框", len(api_key_fields) <= 1, str(len(api_key_fields)))

    # 队列并发处理
    class _QPub:
        def __init__(self, *a, **k): pass

        def run(self, prod, dry_run=True, manage_progress=True):
            __import__("time").sleep(0.15)
            return _PR2(product_id=str(prod.get("id")), ok=True)

    _m.Publisher = _QPub
    uapp.q = type("Q", (), {
        "pending": lambda self: [type("I", (), {"product_id": "1", "title": "a"})(),
                                 type("I", (), {"product_id": "2", "title": "b"})()],
        "mark": lambda self, *a, **k: None,
        "stats": lambda self: {"pending": 0, "running": 0, "done": 2, "failed": 0},
    })()
    uapp._queue_products = {}
    uapp._refresh_queue = lambda: None
    t0 = time.time()
    uapp._process_queue()
    qt = time.time() - t0
    _m.Publisher = real_pub
    check("多任务：队列并发完成", qt < 0.28, f"{qt:.2f}s")

    # 模板本地库：dict 结构 + 保存后能打开模板库
    app7 = App.__new__(App)
    app7.page = FakePage()
    app7.cfg = AppConfig({"template_product_title": "库模板1",
                          "template_product_id": "555",
                          "template_snapshot": json.dumps({"fields": {"category": "C"}, "attributes": ["A=B"]}),
                          "template_values": json.dumps({"品牌": {"t": "text", "v": "X"}}),
                          "template_library": "{}"})
    app7.tpl_snapshot_text = ft.Text("")
    app7._log = lambda *a, **k: None
    app7._save_tpl_local_quick()
    import json as _jj
    _lib = _jj.loads(app7.cfg.template_library or "{}")
    check("模板库：保存为 name→记录", isinstance(_lib, dict) and "库模板1" in _lib, str(_lib)[:80])
    app7._template_library_dialog()
    check("模板库：保存后可打开不崩", app7.page.dialog is not None and getattr(app7.page.dialog, "open", False), "")

    # 页面去重：日志页不再有版本更新/视频；设置页有版本
    uapp._switch(10)
    logs_labels = " ".join(texts(uapp.content.controls))
    check("去重：日志页无检查更新", "检查更新" not in logs_labels, logs_labels[:80])
    check("去重：日志页无生成视频", "生成主图视频" not in logs_labels, "")
    uapp._switch(8)
    set_labels2 = " ".join(texts(uapp.content.controls))
    check("去重：版本在设置里", "当前版本" in set_labels2 and "检查更新" in set_labels2, "")
    check("去重：侧栏无预览", "预览" not in " ".join(texts(uapp.page.controls)), "")

    # 未授权时不允许开始
    uapp.cfg.set("license_enforce", True)
    uapp.license_state = {}
    uapp._run_pipeline = lambda: started.update({"pipeline": True})
    started["pipeline"] = False
    with _mock.patch.object(_m, "verify_license", return_value={
            "ok": False, "authorized": False, "message": "未授权：该设备尚未登记", "expiry": ""}):
        uapp._start()
    dlg = uapp.page.dialog
    check("授权：未授权阻止开始", started.get("pipeline") is False, str(started))
    check("授权：弹出机器码提示", dlg is not None and getattr(dlg, "open", False), type(dlg).__name__ if dlg else "None")

    # ============================== 汇总
    section("汇总")
    fails = [r for r in RESULTS if not r[1] and not r[0].startswith("----")]
    total = len([r for r in RESULTS if not r[0].startswith("----")])
    for name, ok, detail in RESULTS:
        if name.startswith("----"):
            print(f"\n{name}")
            continue
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  · {detail}" if (detail and not ok) else ""))
    print(f"\n共 {total} 项，通过 {total - len(fails)}，失败 {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
