"""粘贴的 Cookie 必须能被识别，并且各平台 Cookie 互相独立。

回归背景（用户反馈）：
- 设置要求「各平台 Cookie 分开」后，`_paste_cookie` 把 Cookie 写成
  `cfg.<platform>_cookies = "dxm_i=...; dxm_t=..."` 字符串；
- 但 `browser_cookies.resolve(manual=...)` 只认 dict，字符串被直接丢掉，
  于是粘贴完仍然提示「未读取到店小秘 Cookie」；
- 同时 `_cookie_status_text` 对字符串硬编码返回 True（假成功），各平台状态
  还会互相串。

本测试覆盖：
1. Cookie 文本解析（普通/带 Cookie: 前缀/换行/JSON/dict/Set-Cookie 属性）；
2. `resolve` 对手动字符串的识别；
3. UI 粘贴对话框 → 平台专属配置 + 平台镜像文件 → `_cookies()` 真能取到；
4. 三平台互不覆盖、`manual_cookies` 不再被写入；
5. 旧版 manual_cookies(dict) 迁移成字符串且可被识别；
6. 平台镜像里的旧 Cookie 不会把 config.json 里新粘贴的 Cookie 回退。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import flet as ft  # noqa: E402

import aeclone.integrations.browser_cookies as bc  # noqa: E402
from aeclone.config import AppConfig, config_dir, save_config  # noqa: E402
from aeclone.core import platform_config  # noqa: E402
from aeclone.integrations.cookie_parse import (  # noqa: E402
    cookie_header, missing_login_keys, parse_cookie_text,
)
from aeclone.platforms import get_platform  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

FAILED = []

REAL_RESOLVE = bc.resolve
REAL_DESCRIBE = bc.describe


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name} {detail}")


# ---------------------------------------------------------------------------
# 1. 文本解析
# ---------------------------------------------------------------------------
def test_parser():
    print("[1] Cookie 文本解析")
    plain = "dxm_i=abc; dxm_t=def"
    check("普通 Cookie 行", parse_cookie_text(plain) == {"dxm_i": "abc", "dxm_t": "def"})
    check("兼容旧语义（忽略 broken / 保留 = 值）",
          parse_cookie_text("a=1; b=2; broken; c=x=y") == {"a": "1", "b": "2", "c": "x=y"})
    check("空值", parse_cookie_text("") == {} and parse_cookie_text(None) == {})
    check("带 Cookie: 前缀",
          parse_cookie_text("Cookie: dxm_i=abc; dxm_t=def") == {"dxm_i": "abc", "dxm_t": "def"})
    check("换行分隔",
          parse_cookie_text("dxm_i=abc\ndxm_t=def") == {"dxm_i": "abc", "dxm_t": "def"})
    check("Set-Cookie 属性被忽略",
          parse_cookie_text("set-cookie: dxm_i=abc; Path=/; HttpOnly") == {"dxm_i": "abc"})
    check("JSON 数组", parse_cookie_text(
        '[{"name": "dxm_i", "value": "abc"}, {"name": "dxm_t", "value": "def"}]'
    ) == {"dxm_i": "abc", "dxm_t": "def"})
    check("JSON 对象", parse_cookie_text('{"dxm_i": "abc", "dxm_t": "def"}')
          == {"dxm_i": "abc", "dxm_t": "def"})
    check("dict 输入", parse_cookie_text({"dxm_i": "abc"}) == {"dxm_i": "abc"})
    check("Netscape cookies.txt 行",
          parse_cookie_text("www.dianxiaomi.com\tFALSE\t/\tFALSE\t0\tdxm_i\tabc")
          == {"dxm_i": "abc"})
    check("规范化 header 稳定排序", cookie_header("b=2; a=1") == "a=1; b=2")
    check("登录标识缺失检测",
          missing_login_keys("dxm_i=1") == ["dxm_t"]
          and missing_login_keys("dxm_i=1; dxm_t=2") == [])


def test_resolve():
    print("[2] resolve 识别手动 Cookie")
    src = bc.resolve("dianxiaomi.com", manual="dxm_i=abc; dxm_t=def", use_local=False)
    check("字符串手动 Cookie 被识别", src.ok and src.cookies.get("dxm_i") == "abc", src)
    check("状态文案显示已识别", "手动配置" in bc.describe(src) and "2 个" in bc.describe(src),
          bc.describe(src))
    src2 = bc.resolve("dianxiaomi.com", manual="Cookie: dxm_i=abc", use_local=False)
    check("带 Cookie: 前缀也识别", src2.ok and src2.cookies.get("dxm_i") == "abc", src2)
    src3 = bc.resolve("dianxiaomi.com", manual={"dxm_i": "abc"}, use_local=False)
    check("旧 dict 仍识别", src3.ok and src3.cookies.get("dxm_i") == "abc", src3)
    src4 = bc.resolve("dianxiaomi.com", manual="这行不是 Cookie", use_local=False)
    check("无效内容不报成功且有原因", (not src4.ok) and bool(src4.error), src4)


# ---------------------------------------------------------------------------
# 2. UI 粘贴 → 平台专属保存 → 可读取
# ---------------------------------------------------------------------------
def _paste(app, pid, text):
    app.active_platform = get_platform(pid)
    app._cookie_cache = None
    app._paste_cookie()
    dlg = app._dlg
    field = next(c for c in walk([dlg]) if isinstance(c, ft.TextField))
    field.value = text
    dlg.actions[1].on_click(None)      # 保存并同步


def test_ui_paste():
    print("[3] 粘贴对话框 → 平台 Cookie 保存/识别")
    with sandbox():
        app = build_app()
        app.cfg.set("manual_cookies", {})

        _paste(app, "aliexpress", "dxm_i=AE-ONLY; dxm_t=AE-T")
        _paste(app, "alibaba", "Cookie: dxm_i=ALI-ONLY\n dxm_t=ALI-T")
        _paste(app, "amazon", '{"dxm_i": "AMZ-ONLY", "dxm_t": "AMZ-T"}')
        _paste(app, "tiktok", "dxm_i=TT-ONLY; dxm_t=TT-T")
        _paste(app, "shopee", "dxm_i=SP-ONLY; dxm_t=SP-T")

        check("速卖通写入 aliexpress_cookies", "AE-ONLY" in str(app.cfg.aliexpress_cookies))
        check("阿里写入 alibaba_cookies", "ALI-ONLY" in str(app.cfg.alibaba_cookies))
        check("Amazon 写入 amazon_cookies", "AMZ-ONLY" in str(app.cfg.amazon_cookies))
        check("TikTok 写入 tiktok_cookies", "TT-ONLY" in str(app.cfg.tiktok_cookies))
        check("Shopee 写入 shopee_cookies", "SP-ONLY" in str(app.cfg.shopee_cookies))
        check("粘贴不再写共享 manual_cookies", app.cfg.manual_cookies == {}, app.cfg.manual_cookies)
        check("平台配置存的是可解析字符串",
              isinstance(app.cfg.aliexpress_cookies, str)
              and parse_cookie_text(app.cfg.aliexpress_cookies).get("dxm_i") == "AE-ONLY")

        files = {}
        for path in (config_dir() / "platforms").glob("*.json"):
            files[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        check("平台镜像文件已写出（重启不回退旧 Cookie）",
              files.get("aliexpress", {}).get("aliexpress_cookies") == app.cfg.aliexpress_cookies
              and files.get("amazon", {}).get("amazon_cookies") == app.cfg.amazon_cookies,
              sorted(files))

        # 恢复真实 resolve/describe：验证「界面层」不再假成功
        bc.resolve = REAL_RESOLVE
        bc.describe = REAL_DESCRIBE
        for pid, marker in (("aliexpress", "AE-ONLY"), ("alibaba", "ALI-ONLY"),
                            ("amazon", "AMZ-ONLY"), ("tiktok", "TT-ONLY"),
                            ("shopee", "SP-ONLY")):
            app.active_platform = get_platform(pid)
            app._cookie_cache = None
            app.cookie_src = None
            app._cookie_ts = 0.0
            ck = app._cookies()
            ok, text = app._cookie_status_text(refresh=False)
            check(f"{pid}: _cookies 识别到专属 Cookie",
                  ck.get("dxm_i") == marker, ck)
            check(f"{pid}: 状态为已识别", ok is True and "手动配置" in text, text)

        # 互不覆盖
        app.active_platform = get_platform("aliexpress")
        app._cookie_cache = None
        ae = app._cookies()
        check("速卖通不串阿里 Cookie", "ALI-ONLY" not in str(ae), ae)

        # 清空后状态回到未识别
        app._set_platform_cookie("aliexpress", "")
        app._cookie_cache = None
        app.cookie_src = None
        app._cookie_ts = 0.0
        ok, text = app._cookie_status_text(refresh=False)
        check("清空 Cookie 后状态为未识别", ok is False and "未检测" in text, text)

        # 旧共享 manual_cookies(dict) 在平台 Cookie 为空时，一次性迁移到当前平台
        app.cfg.set("manual_cookies", {"dxm_i": "LEGACY", "dxm_t": "LEG-T"})
        app.active_platform = get_platform("aliexpress")
        app._cookie_cache = None
        app.cookie_src = None
        app._cookie_ts = 0.0
        ck = app._cookies()
        check("旧共享 Cookie 迁移到当前平台并可识别", ck.get("dxm_i") == "LEGACY", ck)
        check("迁移后平台键为字符串",
              isinstance(app.cfg.aliexpress_cookies, str)
              and "LEGACY" in app.cfg.aliexpress_cookies, app.cfg.aliexpress_cookies)
        check("迁移后清空共享 manual_cookies", app.cfg.manual_cookies == {}, app.cfg.manual_cookies)

        # 上一版迁移代码把 dict 写进了平台键（类型错误），也必须自动字符串化
        app.cfg.set("alibaba_cookies", {"dxm_i": "DICT-ALI", "dxm_t": "DICT-T"})
        app.cfg.set("manual_cookies", {})
        app.active_platform = get_platform("alibaba")
        app._cookie_cache = None
        ck = app._cookies()
        check("平台键里的旧 dict 自动字符串化",
              ck.get("dxm_i") == "DICT-ALI"
              and isinstance(app.cfg.alibaba_cookies, str), app.cfg.alibaba_cookies)

        # 状态展示路径识别旧共享 Cookie，但**不改写配置**（构建页面不能悄悄迁移）
        app.cfg.set("manual_cookies", {"dxm_i": "SHARED", "dxm_t": "SHARED-T"})
        app.cfg.set("amazon_cookies", "")
        app.active_platform = get_platform("amazon")
        app.cookie_src = None
        app._cookie_ts = 0.0
        app._cookie_cache = None
        ok, text = app._cookie_status_text(refresh=False)
        check("状态路径可识别旧共享 Cookie", ok is True, (ok, text))
        check("状态路径不改写配置",
              app.cfg.amazon_cookies == ""
              and app.cfg.manual_cookies.get("dxm_i") == "SHARED",
              (app.cfg.amazon_cookies, app.cfg.manual_cookies))

        # 真正取 Cookie（_cookies）时才迁移到当前平台并清空共享键
        app.cfg.set("tiktok_cookies", "")
        ck = app._cookies()
        check("_cookies 触发旧 Cookie 迁移", ck.get("dxm_i") == "SHARED", ck)
        check("迁移只落到当前平台",
              "SHARED" in str(app.cfg.amazon_cookies)
              and app.cfg.tiktok_cookies == "",
              (app.cfg.amazon_cookies, app.cfg.tiktok_cookies))
        check("迁移后清空共享键", app.cfg.manual_cookies == {}, app.cfg.manual_cookies)
        app.active_platform = get_platform("tiktok")
        app.cookie_src = None
        app._cookie_ts = 0.0
        ok2, text2 = app._cookie_status_text(refresh=False)
        check("未迁移的平台不会拿到共享 Cookie", ok2 is False, (ok2, text2))


# ---------------------------------------------------------------------------
# 3. 旧版迁移 / 平台镜像不覆盖新 Cookie
# ---------------------------------------------------------------------------
def test_migration_and_persistence():
    print("[4] 旧 manual_cookies 迁移 / 平台镜像回退保护")
    with sandbox():
        # 本节验证真实解析逻辑，先把 sandbox 的 resolve/describe 桩换回实现。
        bc.resolve = REAL_RESOLVE
        bc.describe = REAL_DESCRIBE
        cfg = AppConfig()
        cfg.set("active_platform", "tiktok")
        cfg.set("manual_cookies", {"dxm_i": "LEG", "dxm_t": "LEG2"})
        platform_config.migrate_legacy_cookie(cfg)
        check("dict 旧 Cookie 迁移成字符串",
              cfg.tiktok_cookies == "dxm_i=LEG; dxm_t=LEG2", cfg.tiktok_cookies)
        check("迁移后清空共享 manual_cookies", cfg.manual_cookies == {})
        check("迁移结果写入平台镜像",
              "LEG" in (config_dir() / "platforms" / "tiktok.json").read_text(encoding="utf-8"))
        src = bc.resolve("dianxiaomi.com", manual=cfg.tiktok_cookies, use_local=False)
        check("迁移后的字符串可识别", src.ok and src.cookies.get("dxm_i") == "LEG", src)

        cfg2 = AppConfig()
        cfg2.set("aliexpress_cookies", "dxm_i=NEW")
        cfg2.set("aliexpress_shop_id", "shop-1")
        platform_config.save(cfg2, "aliexpress")
        (config_dir() / "platforms" / "aliexpress.json").write_text(
            json.dumps({"aliexpress_cookies": "dxm_i=OLD", "aliexpress_shop_id": "shop-2"}),
            encoding="utf-8")
        platform_config.load(cfg2, "aliexpress")
        check("platform_config.load 不回退新粘贴的 Cookie",
              cfg2.aliexpress_cookies == "dxm_i=NEW", cfg2.aliexpress_cookies)
        check("platform_config.load 仍加载其他平台隔离配置",
              cfg2.aliexpress_shop_id == "shop-2", cfg2.aliexpress_shop_id)

        # set_cookie 同时写 config.json 与平台镜像
        cfg3 = AppConfig()
        platform_config.set_cookie(cfg3, "shopee", "Cookie: dxm_i=SP; dxm_t=SP2")
        check("set_cookie 规范化并保存",
              cfg3.shopee_cookies == "dxm_i=SP; dxm_t=SP2", cfg3.shopee_cookies)
        check("set_cookie 同步平台镜像",
              "dxm_i=SP" in (config_dir() / "platforms" / "shopee.json").read_text(encoding="utf-8"))
        save_config(cfg3)
        check("平台 Cookie 键与平台一一对应",
              platform_config.cookie_key("alibaba") == "alibaba_cookies"
              and platform_config.cookie_key("aliexpress") == "aliexpress_cookies"
              and platform_config.cookie_key("amazon") == "amazon_cookies"
              and platform_config.cookie_key("tiktok") == "tiktok_cookies"
              and platform_config.cookie_key("shopee") == "shopee_cookies")


def test_alibaba_inline_field():
    """Alibaba 连接页的「专用 Cookie」输入框也必须保存/识别/清空正确。"""
    print("[5] Alibaba 连接页内联 Cookie 输入框")
    with sandbox():
        app = build_app(eager_editors=False)
        app.active_platform = get_platform("alibaba")
        app._switch(1)
        fields = [c for c in walk(app.content.controls) if isinstance(c, ft.TextField)]
        cookie_tf = next(t for t in fields if "Cookie" in str(getattr(t, "label", "")))
        event = type("E", (), {"control": cookie_tf})()

        cookie_tf.value = "Cookie: dxm_i=ALI-FIELD; dxm_t=ALI-T"
        cookie_tf.on_change(event)
        check("内联输入写入 alibaba_cookies",
              app.cfg.alibaba_cookies == "dxm_i=ALI-FIELD; dxm_t=ALI-T", app.cfg.alibaba_cookies)

        bc.resolve = REAL_RESOLVE
        bc.describe = REAL_DESCRIBE
        app._cookie_cache = None
        app.cookie_src = None
        app._cookie_ts = 0.0
        check("内联 Cookie 可被识别", app._cookies().get("dxm_i") == "ALI-FIELD")
        check("内联输入后状态为已识别",
              "使用本页填写的 Cookie" in str(app.cookie_status.value), app.cookie_status.value)

        cookie_tf.value = ""
        cookie_tf.on_change(event)
        check("清空内联 Cookie", app.cfg.alibaba_cookies == "", app.cfg.alibaba_cookies)
        app._cookie_cache = None
        app.cookie_src = None
        app._cookie_ts = 0.0
        ok, text = app._cookie_status_text(refresh=False)
        check("清空后不再显示已识别", ok is False and "未检测" in text, text)


def main():
    print("=" * 60)
    test_parser()
    test_resolve()
    test_ui_paste()
    test_migration_and_persistence()
    test_alibaba_inline_field()
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
