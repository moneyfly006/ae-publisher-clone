"""task-5：12 号页「Alibaba 刊登」逐控件深测（离线沙箱；不点真实保存确认）。"""
import json
import os
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path("/Users/apple/Projects/alibaba-lister")
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, label, sandbox, walk  # noqa: E402
from aeclone.ui import app as ui  # noqa: E402

RESULT = []
PID = "220114598434958580"
BTN = tuple(getattr(ft, n) for n in
            ("ElevatedButton", "TextButton", "OutlinedButton", "IconButton",
             "FilledButton", "FilledTonalButton") if hasattr(ft, n))


def rec(name, ok, detail=""):
    RESULT.append(dict(check=name, ok=bool(ok), detail=str(detail)[:600]))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(detail)[:200] if detail else ""))


def build(tab=0):
    import os
    os.environ["AURORA_TAB"] = str(tab)
    app = build_app()
    app._switch(12)
    return app


def controls(app):
    return list(walk(app.content.controls))


def find_button(app, *needles):
    for c in controls(app):
        if isinstance(c, BTN):
            text = getattr(c, "text", None) or getattr(c, "label", None) or label(c)
            if any(n in str(text) for n in needles):
                return c
    return None


def click(app, btn):
    btn.on_click(SimpleNamespace(control=btn, data="", page=app.page))


def texts(app):
    return [c for c in controls(app) if isinstance(c, ft.Text)]


def text_values(app):
    return [c.value for c in texts(app) if isinstance(c.value, str)]


def find_text(app, prefix):
    for t in texts(app):
        if isinstance(t.value, str) and t.value.startswith(prefix):
            return t
    return None


def find_field(app, label_text, exact=False):
    for c in controls(app):
        if isinstance(c, ft.TextField):
            lab = str(getattr(c, "label", "") or "")
            if (lab == label_text) if exact else (label_text in lab):
                return c
    return None


def toast(app):
    sb = getattr(app.page, "snack_bar", None)
    return str(getattr(getattr(sb, "content", None), "value", "") or "") if sb and getattr(sb, "open", False) else ""


def wait(fn, timeout=6.0, interval=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = fn()
        except Exception:
            v = None
        if v:
            return v
        time.sleep(interval)
    return None


def dialog_of(app):
    d = getattr(app, "_dlg", None)
    return d if d is not None and getattr(d, "open", False) else None


def dlg_text(dlg):
    out = []
    for c in walk([dlg]):
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        elif isinstance(c, (ft.TextButton, ft.ElevatedButton, ft.OutlinedButton)) and c.text:
            out.append(f"[btn]{c.text}")
    return out


# ---------------- 场景 ----------------
def s_switch_and_tabs():
    with sandbox(threads=True):
        app = build(0)
        tabs = [c for c in controls(app) if isinstance(c, ft.Tabs)]
        rec("页12 使用 Tabs 分栏", len(tabs) == 1, len(tabs))
        if tabs:
            rec("页12 Tab 标题", [t.text for t in tabs[0].tabs] ==
                ["连接与商品", "类目", "预设资料", "价格与贸易", "预演 / 保存"],
                [t.text for t in tabs[0].tabs])
        sw = None
        for c in controls(app):
            if isinstance(c, ft.Switch) and "AI 生成标题" in str(getattr(c, "label", "")):
                sw = c
        rec("页12 AI 付费开关存在且默认关", sw is not None and sw.value is False,
            getattr(sw, "label", None))
        rec("页12 AI 付费开关文案含「付费 API」", "付费 API" in str(getattr(sw, "label", "")),
            getattr(sw, "label", ""))
        # AURORA_TAB 可定位（截图/Tab 验证机制）
    with sandbox(threads=True):
        app = build(2)
        tabs = [c for c in controls(app) if isinstance(c, ft.Tabs)]
        rec("AURORA_TAB=2 生效（Tab 可定位截图）", tabs and tabs[0].selected_index == 2,
            tabs[0].selected_index if tabs else None)


def s_cookie_messages():
    with sandbox(threads=True):
        app = build(0)
        btn = find_button(app, "测试连接")
        rec("连接卡有「测试连接」", btn is not None)
        click(app, btn)
        st = wait(lambda: find_text(app, "连接失败：") or find_text(app, "连接成功："))
        rec("无 Cookie 时「测试连接」给出失败原因",
            st is not None and "没有可用 Cookie" in st.value, st.value if st else None)
        rec("失败提示可操作（指向店铺与登录/本页填写）",
            st is not None and ("店铺与登录" in st.value or "粘贴" in st.value),
            st.value if st else None)
        rec("失败状态用错误色", st is not None and st.color == ui.DANGER_C,
            getattr(st, "color", None))
        # 「读取商品」同类提示
        click(app, find_button(app, "读取商品"))
        st2 = wait(lambda: find_text(app, "读取商品失败："))
        rec("无 Cookie 时「读取商品」给出失败原因", st2 is not None, st2.value if st2 else None)


def s_product_picker():
    with sandbox(threads=True):
        app = build(0)
        dd = None
        for c in controls(app):
            if isinstance(c, ft.Dropdown) and str(getattr(c, "label", "")) == "商品状态":
                dd = c
        rec("「商品状态」下拉存在且有 草稿/在线 两态",
            dd is not None and [o.key for o in dd.options] == ["draft", "online"],
            [o.key for o in dd.options] if dd else None)
        rec("「商品状态」下拉无 on_change（改状态不会自动重载列表，需点读取商品）",
            dd is not None and dd.on_change is None, "on_change=" + str(getattr(dd, "on_change", None)))
        # 翻页空态提示
        click(app, find_button(app, "上一页"))
        rec("「上一页」在第 1 页有 toast 提示", "已经是第一页" in toast(app), toast(app))
        app.page.snack_bar.open = False
        click(app, find_button(app, "下一页"))
        rec("「下一页」只有 1 页时有 toast 提示", "已经是最后一页" in toast(app), toast(app))
        app.page.snack_bar.open = False
        # 按 ID 打开：空 ID
        app.cfg.set("alibaba_test_product_id", "")
        click(app, find_button(app, "按 ID 打开"))
        rec("「按 ID 打开」空 ID 时 toast 提示", "填一个商品 ID 再打开" in toast(app), toast(app))
        app.page.snack_bar.open = False
        # 按 ID 打开：填 ID（离线会失败，但不能静默）
        tf = find_field(app, "按商品 ID 打开")
        tf.value = PID
        click(app, find_button(app, "按 ID 打开"))
        st = wait(lambda: find_text(app, "正在读取商品") or find_text(app, "打开失败：")
                  or find_text(app, "已打开商品"))
        rec("「按 ID 打开」点击后立即显示读取中/结果", st is not None, st.value if st else None)
        if st is not None and st.value.startswith("打开失败："):
            rec("离线「按 ID 打开」失败提示含原因", len(st.value) > 8, st.value)


def s_category():
    with sandbox(threads=True):
        app = build(1)
        # 空关键词搜索
        before = [t.value for t in texts(app)]
        click(app, find_button(app, "搜索"))
        time.sleep(0.3)
        after = [t.value for t in texts(app)]
        rec("类目「搜索」空关键词应有提示（现状：无任何反馈）", before != after,
            "点击前后页面文本无变化" if before == after else "有反应")
        # 读取必填属性（未选类目）
        app.cfg.set("alibaba_category_id", "")
        click(app, find_button(app, "读取必填属性"))
        st = wait(lambda: find_text(app, "先搜索并选择一个类目"))
        rec("未选类目时「读取必填属性」有明确提示", st is not None, st.value if st else None)
        # 展开子类目（未选类目）
        click(app, find_button(app, "展开子类目"))
        st = wait(lambda: find_text(app, "加载子类目失败：") or find_text(app, "不是末级类目"))
        rec("未选类目时「展开子类目」提示是否明确",
            st is not None, st.value if st else "无反馈/超时")


def s_presets():
    with sandbox(threads=True) as cfg:
        app = build(2)
        cases = [
            ("产品分组 ID", "alibaba_group_id"),
            ("分组名称（本地）", "alibaba_group_name"),
            ("默认关键词（逗号分隔）", "alibaba_keywords"),
            ("公司名称（本地）", "alibaba_company_name"),
            ("公司网站（本地）", "alibaba_company_website"),
        ]
        for lab, key in cases:
            f = find_field(app, lab)
            if f is None:
                rec(f"预设字段存在：{lab}", False)
                continue
            f.value = "TEST-XYZ"
            f.on_change(SimpleNamespace(control=f, data="TEST-XYZ", page=app.page))
            ok = str(getattr(cfg, key, "") or "") == "TEST-XYZ"
            disk = False
            try:
                disk = cfg.path().exists() and "TEST-XYZ" in cfg.path().read_text(encoding="utf-8")
            except Exception:
                pass
            rec(f"预设「{lab}」写入配置并落盘", ok and disk, f"cfg={getattr(cfg, key, '')!r} disk={disk}")
        # 长文本
        f = find_field(app, "公司介绍（英文）")
        rec("公司介绍为多行输入", f is not None and f.multiline, getattr(f, "multiline", None))


def s_price_trade():
    with sandbox(threads=True) as cfg:
        app = build(3)
        dd = None
        sale = None
        for c in controls(app):
            if isinstance(c, ft.Dropdown):
                if str(getattr(c, "label", "")) == "定价方式":
                    dd = c
                elif str(getattr(c, "label", "")) == "销售方式":
                    sale = c
        rec("「定价方式」选项=保留原定价/阶梯/SKU/FOB 四态",
            dd is not None and [o.key for o in dd.options] ==
            ["", "ladder_price", "sku_price", "fob_price"],
            [o.key for o in dd.options] if dd else None)
        rec("「定价方式」带 tooltip 说明", dd is not None and bool(getattr(dd, "tooltip", "")),
            getattr(dd, "tooltip", None))
        ok_all = True
        details = []
        for key in ("ladder_price", "sku_price", "fob_price", ""):
            dd.value = key
            dd.on_change(SimpleNamespace(control=dd, data=key, page=app.page))
            exp = "1" if key else ""
            got = getattr(cfg, "alibaba_price_type", None), getattr(cfg, "alibaba_price_type_explicit", None)
            ok = got == (key, exp)
            ok_all &= ok
            details.append(f"{key or '保留原定价'}->{got}")
        rec("定价方式两态写配置（含 explicit 标记：只有显式选择才覆盖原定价）", ok_all, "; ".join(details))
        rec("「销售方式」两态 normal/batch",
            sale is not None and [o.key for o in sale.options] == ["normal", "batch"],
            [o.key for o in sale.options] if sale else None)
        if sale is not None:
            sale.value = "batch"
            sale.on_change(SimpleNamespace(control=sale, data="batch", page=app.page))
            rec("「销售方式」写配置", str(getattr(cfg, "alibaba_sale_type", "")) == "batch",
                getattr(cfg, "alibaba_sale_type", None))
        # 保存动作下拉
        opdd = None
        for c in controls(app):
            if isinstance(c, ft.Dropdown) and str(getattr(c, "label", "")) == "保存动作":
                opdd = c
        rec("「保存动作」选项=op=1/3/2 三态",
            opdd is not None and [o.key for o in opdd.options] == ["1", "3", "2"],
            [o.key for o in opdd.options] if opdd else None)
        rec("「保存动作」默认 op=1（保存草稿）", opdd is not None and opdd.value == "1",
            getattr(opdd, "value", None))
        rec("危险动作（op=2/3）在选项文案里标注影响",
            opdd is not None and any("审核" in (o.text or "") for o in opdd.options)
            and any("待发布" in (o.text or "") for o in opdd.options),
            [o.text for o in opdd.options] if opdd else None)
        # 阶梯价 JSON 输入与提示
        rec("阶梯价 JSON 输入存在", find_field(app, "阶梯价 JSON") is not None)
        rec("本地-only 字段有免责说明",
            any("只保存在本地" in v for v in text_values(app)),
            [v for v in text_values(app) if "本地" in v][:3])


def s_preview_save():
    with sandbox(threads=True):
        app = build(4)
        app.cfg.set("alibaba_test_product_id", "")
        click(app, find_button(app, "预演（不保存）"))
        st = wait(lambda: find_text(app, "填一个商品 ID 再校验"))
        rec("未选商品点「预演（不保存）」→ 提示填商品 ID", st is not None, st.value if st else None)
        app.cfg.set("alibaba_test_product_id", PID)
        click(app, find_button(app, "保存到 Alibaba"))
        st = wait(lambda: find_text(app, "保存前检查失败："))
        rec("「保存到 Alibaba」离线/无 Cookie 时失败提示可见（非静默）",
            st is not None, st.value if st else "无反馈/超时")
        rec("离线失败状态用错误色", st is not None and st.color == ui.DANGER_C,
            getattr(st, "color", None))
        # 有商品时先做模拟校验成功，验证二次确认弹窗 + 取消
        import importlib
        mod = importlib.import_module("aeclone.services.alibaba_publish")
        with patch.object(mod, "prepare_save",
                          lambda *a, **kw: ({"id": PID, "categoryId": "202107201"},
                                            {"subject": "模拟标题"}, [], [])):
            click(app, find_button(app, "保存到 Alibaba"))
            dlg = wait(lambda: dialog_of(app))
            rec("「保存到 Alibaba」→弹出二次确认弹窗", dlg is not None,
                dlg_text(dlg) if dlg else None)
            if dlg is not None:
                vals = dlg_text(dlg)
                rec("确认弹窗写明商品与动作", any(PID in v for v in vals) and
                    any("动作：" in v for v in vals), vals[:6])
                rec("确认弹窗提示「会写入店小秘/平台，不是本地预演」",
                    any("不是本地预演" in v for v in vals), vals)
                cancel = next((c for c in walk([dlg])
                               if isinstance(c, ft.TextButton) and c.text == "取消"), None)
                rec("确认弹窗有「取消」按钮", cancel is not None)
                if cancel is not None:
                    click(app, cancel)
                    rec("点「取消」后弹窗关闭且未保存", dialog_of(app) is None, "dialog closed")
        # AI 按钮：未选商品提示
        app.cfg.set("alibaba_test_product_id", "")
        click(app, find_button(app, "AI 预演（不保存）"))
        st = wait(lambda: find_text(app, "先选择一个商品"))
        rec("未选商品点「AI 预演」→ 提示先选商品", st is not None, st.value if st else None)


def s_ai_flow():
    with sandbox(threads=True):
        app = build(4)
        app.cfg.set("alibaba_test_product_id", PID)
        # AI 开关为开时，弹窗需提示会调用付费 API
        sw = next((c for c in controls(app) if isinstance(c, ft.Switch)
                   and "AI 生成标题" in str(getattr(c, "label", ""))), None)
        sw.value = True
        click(app, find_button(app, "AI 生成并保存"))
        dlg = wait(lambda: dialog_of(app))
        rec("「AI 生成并保存」→弹出二次确认弹窗", dlg is not None, dlg_text(dlg) if dlg else None)
        if dlg is not None:
            vals = dlg_text(dlg)
            rec("AI 确认弹窗写明商品/动作/付费提示",
                any(PID in v for v in vals) and any("动作：" in v for v in vals)
                and any("付费 API" in v for v in vals), vals[:6])
            cancel = next((c for c in walk([dlg])
                           if isinstance(c, ft.TextButton) and c.text == "取消"), None)
            if cancel is not None:
                click(app, cancel)
                rec("取消 AI 弹窗后关闭且未调用保存", dialog_of(app) is None)
        # AI 预演（不保存）：点后起后台线程，结果区显示分步 + 失败原因
        sw.value = False
        click(app, find_button(app, "AI 预演（不保存）"))
        ran = wait(lambda: find_text(app, "运行中…"))
        rec("「AI 预演（不保存）」点击后立即显示运行中（后台线程）", ran is not None,
            ran.value if ran else "无反馈")
        done = wait(lambda: find_text(app, "失败原因：") or find_text(app, "预演完成")
                    or find_text(app, "预演（不保存）失败"))
        steps = [c for c in controls(app)
                 if isinstance(c, ft.Text) and isinstance(c.value, str)
                 and ("连接 Alibaba" in c.value or "读取商品详情" in c.value)]
        rec("「AI 预演（不保存）」结果区展示分步结果", len(steps) > 0,
            [c.value for c in steps][:6])
        rec("「AI 预演（不保存）」失败时有可见原因（非静默）", done is not None,
            done.value if done else "无失败原因文本")
        rec("「AI 预演（不保存）」不弹保存确认、不写商品",
            dialog_of(app) is None, dialog_of(app))


def s_new_controls():
    # Lead 重点：按 ID 打开 / 生成属性预设模板 / AI 建议缺失属性 / Alibaba 行业预设套用预设
    with sandbox(threads=True):
        app = build(1)
        labels = [label(c) for c in controls(app)]
        for need in ("按 ID 打开", "生成属性预设模板", "AI 建议缺失属性", "套用预设"):
            btn = find_button(app, need)
            rec(f"新控件可点且有回调：{need}", btn is not None and callable(btn.on_click),
                getattr(btn, "text", None))
        # 生成属性预设模板：未选类目 → 失败提示可见
        app.cfg.set("alibaba_category_id", "")
        click(app, find_button(app, "生成属性预设模板"))
        st = wait(lambda: find_text(app, "生成模板失败：") or find_text(app, "先搜索并选择一个类目"))
        rec("「生成属性预设模板」未选类目时给出原因", st is not None, st.value if st else "无反馈")
        # AI 建议缺失属性：未选类目 → 失败提示可见
        click(app, find_button(app, "AI 建议缺失属性"))
        st2 = wait(lambda: find_text(app, "AI 建议失败：") or find_text(app, "先搜索并选择一个类目"))
        rec("「AI 建议缺失属性」未选类目时给出原因", st2 is not None, st2.value if st2 else "无反馈")
        # Alibaba 行业预设：套用预设 → 写 cfg + toast；真实环境（无 AURORA_TAB）会跳回第一个 Tab
        preset_dd = None
        for c in controls(app):
            if isinstance(c, ft.Dropdown) and c.options and any(
                    "集装箱" in str(getattr(o, "text", "") or "") for o in c.options):
                preset_dd = c
        rec("页12「Alibaba 行业预设」下拉存在且含行业选项",
            preset_dd is not None, [getattr(o, "text", "") for o in preset_dd.options][:6]
            if preset_dd else None)
        apply_btn = find_button(app, "套用预设")
        os.environ["AURORA_TAB"] = ""   # 真实运行无该环境变量
        before = dict(app.cfg.as_dict())
        click(app, apply_btn)
        after = dict(app.cfg.as_dict())
        changed = [k for k in after if before.get(k) != after.get(k)]
        rec("「套用预设」写入提示词配置",
            {"title_prompt", "detail_prompt", "sku_prompt"} <= set(changed), changed)
        rec("「套用预设」toast 确认", "已套用 Alibaba 预设" in toast(app), toast(app))
        tabs = [c for c in controls(app) if isinstance(c, ft.Tabs)]
        rec("「套用预设」后页面重建并跳回第 1 个 Tab（真实环境上下文丢失，P2）",
            bool(tabs) and tabs[0].selected_index == 0,
            tabs[0].selected_index if tabs else None)
        # 页 3 的 Alibaba 预设入口
    with sandbox(threads=True):
        app = build(0)
        app._switch(3)
        labels3 = [label(c) for c in controls(app)]
        rec("AI 文案页(3) 有 Alibaba 行业预设入口",
            any("Alibaba 行业预设" in x for x in labels3)
            and find_button(app, "套用预设") is not None,
            [x for x in labels3 if "Alibaba" in x][:3])


def main():
    scenarios = [s_switch_and_tabs, s_cookie_messages, s_product_picker, s_category,
                 s_presets, s_price_trade, s_preview_save, s_ai_flow, s_new_controls]
    for fn in scenarios:
        try:
            fn()
        except Exception:
            rec(f"场景 {fn.__name__} 异常", False, traceback.format_exc()[-1200:])
    fails = [r for r in RESULT if not r["ok"]]
    Path("/tmp/ui-page12-deep.json").write_text(
        json.dumps(dict(total=len(RESULT), fails=len(fails), results=RESULT),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(RESULT)} checks, {len(fails)} FAIL")
    for f in fails:
        print("  FAIL:", f["check"], "::", f["detail"][:300])
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
