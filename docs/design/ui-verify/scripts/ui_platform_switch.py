"""task-5：平台切换 + Alibaba 平台守卫 + 锁定态验证（只读沙箱，临时脚本）。"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path("/Users/apple/Projects/alibaba-lister")
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, label, sandbox, walk  # noqa: E402
from aeclone.platforms import ALIBABA, ALIEXPRESS  # noqa: E402

RESULT = []
BTN = tuple(getattr(ft, n) for n in
            ("ElevatedButton", "TextButton", "OutlinedButton", "IconButton",
             "FilledButton", "FilledTonalButton") if hasattr(ft, n))


def blabel(c):
    return getattr(c, "label", None) or label(c)


def rec(name, ok, detail):
    RESULT.append(dict(check=name, ok=bool(ok), detail=str(detail)))
    print(("PASS " if ok else "FAIL ") + name + " :: " + str(detail)[:220])


def texts(app):
    return [c.value for c in walk(app.content.controls)
            if isinstance(c, ft.Text) and isinstance(c.value, str) and c.value]


def toast_text(app):
    sb = getattr(app.page, "snack_bar", None)
    if sb is not None and getattr(sb, "open", False):
        return str(getattr(getattr(sb, "content", None), "value", "") or "")
    return ""


def close_dialog(app):
    try:
        app.page.close_dialog()
    except Exception:
        pass


def nav_labels(app):
    return [b.content.controls[1].value for i, b in app.nav_buttons]


def nav_locks(app):
    out = {}
    for i, b in app.nav_buttons:
        ctls = list(b.content.controls)
        out[i] = len(ctls) > 2 and getattr(ctls[2], "name", "") == ft.Icons.LOCK_OUTLINE
    return out


def rail_state(app):
    return [getattr(badge, "tooltip", "") for badge in walk([app.platform_rail])
            if isinstance(badge, ft.Container) and getattr(badge, "tooltip", "")]


def scenario_full_license():
    with sandbox() as cfg:
        app = build_app()
        rec("默认平台=Alibaba", app.active_platform.id == "alibaba", app.active_platform.name)
        rec("Alibaba 导航 13 项", len(app.nav_buttons) == 13, nav_labels(app))
        rec("平台栏 5 个平台徽标", len(rail_state(app)) == 5, rail_state(app))

        # 发布队列页（8）在 Alibaba 下应为只读 ICBU 队列
        app._switch(8)
        t = texts(app)
        rec("页8 Alibaba=只读ICBU队列", any("Alibaba 商品队列（只读）" in x for x in t),
            [x for x in t if "队列" in x])
        rec("页8 Alibaba=草稿/在线/合计", all(any(k in x for x in t) for k in ("草稿", "在线", "合计")),
            [x for x in t if x in ("草稿", "在线", "合计")])
        rec("页8 Alibaba 无批量发布按钮", not any(
            blabel(b) and "批量发布" in blabel(b) for b in walk(app.content.controls)
            if isinstance(b, BTN)), [blabel(b) for b in walk(app.content.controls) if isinstance(b, BTN)])

        # Alibaba 刊登页
        app._switch(12)
        tabs = [c for c in walk(app.content.controls) if isinstance(c, ft.Tabs)]
        rec("页12 存在且 5 个 Tab", bool(tabs) and len(tabs[0].tabs) == 5,
            [tb.text for tb in tabs[0].tabs] if tabs else None)
        labels = [label(c) for c in walk(app.content.controls)]
        for need in ("读取商品", "按 ID 打开", "搜索", "读取必填属性", "展开子类目",
                     "预演（不保存）", "保存到 Alibaba", "AI 预演（不保存）", "AI 生成并保存"):
            rec(f"页12 控件存在：{need}", need in labels, "")

        # 侧栏「启动任务」在 Alibaba：应 toast + 跳页 12
        app._switch(0)
        before = toast_text(app)
        app._toggle_run()
        rec("Alibaba 侧栏启动→跳 12 号页", app._page_idx == 12, f"_page_idx={app._page_idx}")
        rec("Alibaba 侧栏启动→toast 明确提示", "Alibaba 商品请在" in toast_text(app),
            toast_text(app))
        rec("Alibaba 侧栏启动→日志 WARN 提示",
            any("已阻止在 Alibaba 平台启动速卖通批量管线" in str(x) for x in app.logs),
            app.logs[-2:] if app.logs else [])
        rec("Alibaba 侧栏启动→不启动批量任务", not getattr(app, "_running", False), "")

        # ⌘R 守卫
        app._switch(0)
        app._on_key(type("E", (), {"key": "r", "ctrl": False, "meta": True})())
        rec("Alibaba ⌘R→跳 12 号页", app._page_idx == 12, f"_page_idx={app._page_idx}")
        rec("Alibaba ⌘R→toast 明确提示", "Alibaba 商品请在" in toast_text(app), toast_text(app))

        # 概览页自身入口：去 Alibaba 刊登 on_click
        app._switch(0)
        go = [b for b in walk(app.content.controls)
              if isinstance(b, BTN) and blabel(b) and "去 Alibaba 刊登" in blabel(b)]
        rec("概览页有「去 Alibaba 刊登」入口", bool(go), [blabel(b) for b in go])
        if go:
            go[0].on_click(type("E", (), {"control": go[0], "data": "", "page": app.page})())
            rec("概览「去 Alibaba 刊登」→跳 12", app._page_idx == 12, f"_page_idx={app._page_idx}")

        # ---- 切换到 AliExpress ----
        app._switch_platform("aliexpress")
        rec("切换后 active_platform=aliexpress", app.active_platform.id == "aliexpress",
            app.active_platform.name)
        rec("AliExpress 导航 12 项且不含 Alibaba 刊登",
            len(app.nav_buttons) == 12 and "Alibaba 刊登" not in nav_labels(app),
            nav_labels(app))
        rec("AliExpress 导航 label 来自 ALIEXPRESS",
            nav_labels(app) == [p.label for p in ALIEXPRESS.pages], nav_labels(app))
        rec("切换后自动落到 AliExpress 首页", app._page_idx == 0, app._page_idx)
        rec("切换后 breadcrumb 刷新", "速卖通" in (app.breadcrumb.value or ""),
            app.breadcrumb.value)
        rec("切换后平台栏高亮刷新（tooltip 已开通）",
            all("已开通" in x for x in rail_state(app)), rail_state(app))
        app._switch(8)
        t = texts(app)
        rec("页8 AliExpress=发布队列（SMT，非只读）",
            any("Alibaba 商品队列（只读）" not in x for x in t)
            and any(x == "队列" for x in t), [x for x in t if "队列" in x])
        rec("页8 AliExpress 有批量发布/重试入口",
            any(blabel(b) and ("批量发布" in blabel(b) or "重试" in blabel(b))
                for b in walk(app.content.controls) if isinstance(b, BTN)),
            [blabel(b) for b in walk(app.content.controls)
             if isinstance(b, BTN) and blabel(b)])
        # AliExpress 下启动任务不应被 Alibaba 守卫拦截
        app._switch(0)
        toast = ""
        try:
            app._toggle_run()
        except Exception:
            pass
        toast = toast_text(app)
        rec("AliExpress 启动任务不被 Alibaba 守卫拦截",
            "Alibaba 商品请在" not in toast and app._page_idx == 10,
            f"page={app._page_idx} toast={toast}")
        close_dialog(app)

        # 切回 Alibaba
        app._switch_platform("alibaba")
        rec("切回 Alibaba 导航 13 项", len(app.nav_buttons) == 13, len(app.nav_buttons))
        rec("切回 Alibaba 12 号页可访问", app.active_platform.page(12) is not None,
            app.active_platform.page(12))
        app._switch(12)
        rec("切回后 12 号页仍渲染", any("Alibaba 刊登" in x for x in texts(app)),
            [x for x in texts(app) if "Alibaba 刊登" in x])
        return RESULT


def scenario_locked():
    with sandbox() as cfg:
        app = build_app()
        from aeclone.licensing import load_entitlements
        app.cfg.set("license_platforms", "alibaba:listing")
        app.entitlements = load_entitlements(app.cfg)
        app._rebuild_nav()
        app.platform_rail.content = app._build_platform_rail().content
        locks = nav_locks(app)
        allowed = [i for i, v in locks.items() if not v]
        locked = [i for i, v in locks.items() if v]
        rec("未开通功能包：导航出现锁图标", len(locked) > 0, f"locked={locked}")
        rec("未开通功能包：listing 页仍可用", set(allowed) == {0, 1, 8, 10, 11, 12},
            f"allowed={sorted(allowed)}")
        app._switch(2)  # category 功能包未开通
        t = texts(app)
        rec("点未开通页→功能包锁定页", any("功能未开通" in x for x in t),
            [x for x in t if "未开通" in x or "功能包" in x][:4])
        rec("锁定页给出开通入口", any(blabel(b) and "去授权网站开通" in blabel(b)
                                     for b in walk(app.content.controls)
                                     if isinstance(b, BTN)),
            [blabel(b) for b in walk(app.content.controls) if isinstance(b, BTN)])
        # 切到未授权平台
        app._switch_platform("aliexpress")
        t = texts(app)
        rec("切到未授权平台→平台锁定页", any("当前授权未包含该平台" in x for x in t),
            [x for x in t if "未包含" in x][:4])
        rec("未授权平台导航全部锁定",
            all(v for v in nav_locks(app).values()), nav_locks(app))
        rec("平台栏显示未开通", any("未开通" in x for x in rail_state(app)), rail_state(app))
        # 切回 Alibaba：锁定状态应刷新为部分可用
        app._switch_platform("alibaba")
        locks2 = nav_locks(app)
        rec("切回 Alibaba 后锁定态刷新（listing 解锁）",
            not locks2.get(0) and locks2.get(2) and locks2.get(3),
            locks2)
        app._switch(0)
        rec("切回 Alibaba 首页正常渲染", any("概览" in x or "工作空间" in x for x in texts(app)),
            texts(app)[:3])
        return RESULT


def main():
    out = {}
    for name, fn in (("full_license", scenario_full_license), ("locked", scenario_locked)):
        try:
            out[name] = fn()
        except Exception:
            out[name] = [dict(check=name + " 场景异常", ok=False,
                              detail=traceback.format_exc()[-1500:])]
    total = sum(len(v) for v in out.values())
    fails = [r for v in out.values() for r in v if not r["ok"]]
    Path("/tmp/ui-platform-switch.json").write_text(
        json.dumps(dict(total=total, fails=len(fails), results=out),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{total} checks, {len(fails)} FAIL")
    for f in fails:
        print("  FAIL:", f["check"], "::", f["detail"][:300])
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
