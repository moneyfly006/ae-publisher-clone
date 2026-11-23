"""平台 UI 匹配测试：Alibaba 移除速卖通专属 UI，其他平台保持完整。

这是「多平台融合」的边界测试：
- ALIBABA 页面清单不含 6/7（SMT 价格页、变种库存页），含 12 号 Alibaba 刊登页；
- ALIEXPRESS 保留 0-11 全部页面，且不显示 12 号页；
- platform capability 开关只影响 Alibaba（未声明的平台默认保留全部能力）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.platforms import (  # noqa: E402
    ALIBABA, ALIEXPRESS, AMAZON, PLATFORMS, PLATFORM_CAPS, cap, get_platform,
)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)
        print(f"  ✗ {name} {detail}")


def test_alibaba_pages():
    idx = {p.index for p in ALIBABA.pages}
    check("Alibaba 不含 SMT 价格页(6)", 6 not in idx, sorted(idx))
    check("Alibaba 不含 SMT 变种库存页(7)", 7 not in idx, sorted(idx))
    check("Alibaba 含 12 号刊登页", 12 in idx)
    check("Alibaba 含必备通用页 0/3/4/5/8/9/10/11",
          {0, 3, 4, 5, 8, 9, 10, 11}.issubset(idx), sorted(idx))
    labels = {p.index: p.label for p in ALIBABA.pages}
    check("店小秘 Cookie 连接页存在", labels.get(1) == "Alibaba 连接", labels.get(1))
    check("Alibaba 不出现「店铺与模板/价格/SKU」导航名",
          not {"店铺与模板", "价格 / 阶梯 / 批发", "SKU · 库存 · 套装"} & set(labels.values()))


def test_aliexpress_unchanged():
    idx = {p.index for p in ALIEXPRESS.pages}
    check("速卖通保留 0-11 全部页面", idx == set(range(12)), sorted(idx))
    check("速卖通不显示 12 号 Alibaba 刊登页", 12 not in idx)


def test_other_platforms_unchanged():
    # TikTok 已实现（左侧导航已按功能拆分）单独断言，见 test_tiktok_new_platform_boundary。
    for pid in ("amazon", "shopee"):
        pf = PLATFORMS[pid]
        idx = {p.index for p in pf.pages}
        check(f"{pid} 页面清单非空且不含 12", bool(idx) and 12 not in idx, sorted(idx))
        # 未声明能力的平台默认保留（不因 Alibaba 的裁剪被影响）
        check(f"{pid} 仍保留 SMT 队列能力（默认值）", cap(pid, "smt_queue") is True)


def test_tiktok_new_platform_boundary():
    """TikTok 已实现：左侧导航按功能拆页（0 + 15–21 + 10/11，14 为隐藏完整路由），
    且关闭契约 §4.1 列出的 SMT 专属能力。"""
    pf = PLATFORMS["tiktok"]
    check("TikTok implemented=True", pf.implemented is True)
    check("TikTok 可见页=0/22-28/10/11",
          [p.index for p in pf.pages if not p.group.startswith("_")] ==
          [0, 22, 23, 24, 25, 26, 27, 28, 10, 11],
          [(p.index, p.label) for p in pf.pages])
    check("TikTok 分组=工作空间/商品准备/内容处理/发布与运行/工具与设置",
          pf.groups() == ["工作空间", "商品准备", "内容处理", "发布与运行", "工具与设置"],
          pf.groups())
    check("TikTok 含 14 号隐藏完整工作台", pf.page(14) is not None
          and pf.page(14).group.startswith("_"))
    check("TikTok 导航不含隐藏分组", "__internal__" not in pf.groups())
    check("TikTok 不显示 12 号 Alibaba 刊登页", pf.page(12) is None)
    # 契约 §4.1：SMT 专属能力全关（TikTok 走 14 号页）
    for name in ("smt_wizard", "smt_pipeline_actions", "smt_run_stop",
                 "smt_pipeline_stats", "smt_store_selector", "smt_resume",
                 "smt_extra_ai", "smt_prompt_library", "smt_detail_assembly",
                 "marketing_images", "smt_queue", "scheduled_publish",
                 "smt_sku_page", "legacy_price_page", "smt_category_source",
                 "smt_product_settings", "smt_attr_templates",
                 "eu_qualification", "smt_shop_template", "smt_template_ready"):
        check(f"TikTok 关闭 {name}", cap("tiktok", name) is False)
    check("TikTok 关闭 ICBU 刊登", cap("tiktok", "icbu_listing") is False)
    # 不得影响其它平台
    check("速卖通未被 TikTok 裁剪影响", cap("aliexpress", "smt_queue") is True)


def test_caps():
    check("Alibaba 关闭 SMT 队列", cap("alibaba", "smt_queue") is False)
    check("Alibaba 关闭营销图", cap("alibaba", "marketing_images") is False)
    check("Alibaba 关闭定时发布", cap("alibaba", "scheduled_publish") is False)
    check("Alibaba 关闭欧盟资质", cap("alibaba", "eu_qualification") is False)
    check("Alibaba 开启 ICBU 刊登", cap("alibaba", "icbu_listing") is True)
    check("速卖通保留 SMT 队列", cap("aliexpress", "smt_queue") is True)
    check("速卖通保留营销图", cap("aliexpress", "marketing_images") is True)
    check("速卖通不显示 ICBU 刊登", cap("aliexpress", "icbu_listing") is False)
    check("未知平台默认保留能力", cap("unknown-platform", "smt_queue") is True)
    # 能力 map 只应包含已知平台
    check("PLATFORM_CAPS 仅含已注册平台",
          set(PLATFORM_CAPS) <= set(PLATFORMS), set(PLATFORM_CAPS) - set(PLATFORMS))


def main():
    print("=" * 60)
    print("多平台 UI 裁剪边界测试")
    for fn in (test_alibaba_pages, test_aliexpress_unchanged,
               test_other_platforms_unchanged, test_tiktok_new_platform_boundary,
               test_caps):
        print(f"[{fn.__name__}]")
        fn()
    print("=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
