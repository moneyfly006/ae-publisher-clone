"""开通全部平台权限 + 修正授权服务器地址（本机配置，一次性维护脚本）。

背景：
- 旧配置 license_server = https://ali.moneyfly.club/license 会 308 到 /shop 且丢 query，
  客户端报「授权 404 / 未知操作」；正式地址是 https://license.moneyfly.club。
- 本机 ECDSA 授权凭证（voucher）已有效至 2027-11-17；平台可见性由 license_platforms
  （手动覆盖）或服务端 token 决定。服务端当前未给该机器返回 token，因此这里写入手动覆盖，
  一次开通所有平台的全部功能包。

用法：
    .venv/bin/python tools/grant_all_platforms.py [--cookie-file .tmp-probe/cookies.txt]
    .venv/bin/python tools/grant_all_platforms.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CANONICAL_SERVER = "https://license.moneyfly.club"


def all_platforms_override() -> str:
    """每个平台都授予「全部功能包」，确保任何平台的任何页面都可用。"""
    from aeclone.licensing import ALL_FEATURES, PLATFORM_FEATURES
    feats = "+".join(sorted(ALL_FEATURES))
    return ",".join(f"{pid}:{feats}" for pid in PLATFORM_FEATURES)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="开通全部平台权限（本机配置）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cookie-file", default=str(ROOT / ".tmp-probe" / "cookies.txt"),
                    help="可选：同时恢复 Amazon 店小秘 Cookie")
    ap.add_argument("--shop-id", default="9408005", help="Amazon 店铺 ID")
    args = ap.parse_args(argv)

    import time

    # license_platforms 覆盖仅显式开发环境生效（task-25 审计修复）
    os.environ.setdefault("AURORA_LICENSE_OVERRIDE", "1")

    from aeclone.config import get_config, save_config
    from aeclone.core.guard import check as guard_check
    from aeclone.license import canonical_server, machine_code
    from aeclone.licensing import PLATFORM_FEATURES, issue_token, load_entitlements

    cfg = get_config()
    override = all_platforms_override()
    changes = {}

    def setv(k, v):
        old = str(getattr(cfg, k, "") or "")
        if old != str(v):
            changes[k] = (old[:80], str(v)[:80])
            if not args.dry_run:
                cfg.set(k, v)

    setv("license_server", canonical_server(str(getattr(cfg, "license_server", "") or "")
                                            or CANONICAL_SERVER))
    setv("license_platforms", override)
    # 本机开发用签名 token（账号制 v3 后核心层 guard 只认 token）：
    # 全平台 + 一年有效期，仅用于本机调试，正式使用请登录授权账号。
    local_token = issue_token(
        machine_code(),
        {pid: list(feats) for pid, feats in PLATFORM_FEATURES.items()},
        time.strftime("%Y-%m-%d", time.localtime(time.time() + 365 * 86400)))
    setv("license_token", local_token)
    setv("license_token_ts", int(time.time()))
    if not str(getattr(cfg, "license_account_email", "") or "").strip():
        setv("license_account_email", "dev@local")
    if args.cookie_file and Path(args.cookie_file).exists():
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        if cookie:
            setv("amazon_cookies", cookie)
            setv("amazon_shop_id", str(args.shop_id or ""))
    if not args.dry_run:
        save_config(cfg)

    print("== 变更 ==")
    for k, (a, b) in changes.items():
        if k == "amazon_cookies":
            print(f"  {k}: {'<空>' if not a else a[:12] + '…'} -> {b[:12]}…")
        elif k == "license_platforms":
            print(f"  {k}: {a[:40] or '<空>'}… -> 全部平台/全部功能")
        else:
            print(f"  {k}: {a[:70] or '<空>'} -> {b[:70]}")
    if not changes:
        print("  （无变化）")

    ent = load_entitlements(cfg)
    print("\n== 授权解析 ==")
    print("  source:", ent.source, "| expired:", ent.expired)
    print("  platforms:", ", ".join(ent.platforms_list()) or "无")
    for pid, feats in sorted(ent.platforms.items()):
        print(f"    {pid}: {', '.join(sorted(feats))}")

    ok, why = guard_check(cfg)
    print("\n== 核心层凭证（真实保存/发布用）==")
    print("  guard:", "OK" if ok else "FAIL", "|", why)

    print("\n== 说明 ==")
    print("  license_platforms 只是本机调试覆盖；真实保存/发布由 core.guard 用 token 校验。")
    print("\n" + ("DRY-RUN：未写入配置" if args.dry_run else "已写入本机配置"))
    return 0 if all(ent.has_platform(p) for p in
                    ("alibaba", "aliexpress", "amazon", "tiktok", "shopee")) else 1


if __name__ == "__main__":
    sys.exit(main())
