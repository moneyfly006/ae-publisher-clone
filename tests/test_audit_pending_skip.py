#!/usr/bin/env python3
"""「商品审核中」处理测试。

用户日志里出现过：
    ✗ 异常: 保存接口 /api/smtProduct/add.json HTTP 200 · 站点返回：
        isv.13PRODUCT_IN_AUDIT_PENDING ... Product is pending in audit

这不是代码 bug，是商品正在审核，改不了。正确处理是「跳过、下次再处理」，
而不是把它算成失败、也不是弹一个吓人的异常。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aeclone.services.publisher import PublishResult, is_audit_pending  # noqa: E402


def t1_detect_official_code():
    print("\n[测试1] 官方错误码能识别")
    msg = ("保存接口 /api/smtProduct/add.json HTTP 200 · 站点返回："
           "您的产品编辑错误：isv.13PRODUCT_IN_AUDIT_PENDING, null:"
           "PRODUCT_IN_AUDIT_PENDING:Product is pending in audit p")
    assert is_audit_pending(msg) is True
    print("  ✓ 识别 PRODUCT_IN_AUDIT_PENDING")


def t2_detect_english_text():
    print("\n[测试2] 只有英文描述也能识别")
    assert is_audit_pending("Product is pending in audit") is True
    print("  ✓ 识别 'pending in audit'")


def t3_not_confused_with_other_errors():
    print("\n[测试3] 其它保存错误不被误判")
    for msg in ("必填项缺失：品牌", "HTTP 500 internal error",
                "主图替换失败，已取消保存", "", None):
        assert is_audit_pending(msg) is False, msg
    print("  ✓ 空串 / 必填缺失 / 500 / 主图失败 都不误判")


def t4_result_flag():
    print("\n[测试4] PublishResult 有 skipped_auditing 标记且默认 False")
    r = PublishResult(product_id="1", ok=False)
    assert r.skipped_auditing is False
    r.skipped_auditing = True
    assert r.skipped_auditing is True
    print("  ✓ 默认 False，可显式置位")


def t5_case_insensitive():
    print("\n[测试5] 大小写不敏感")
    assert is_audit_pending("product_in_audit_pending")
    assert is_audit_pending("Product Is Pending In Audit")
    print("  ✓ 全小写 / 混合大小写都识别")


if __name__ == "__main__":
    tests = [t1_detect_official_code, t2_detect_english_text,
             t3_not_confused_with_other_errors, t4_result_flag, t5_case_insensitive]
    ok = fail = 0
    for t in tests:
        try:
            t()
            ok += 1
        except AssertionError as ex:
            fail += 1
            print(f"  ✗ 失败: {ex}")
        except Exception as ex:
            fail += 1
            import traceback
            print(f"  ✗ 异常: {ex}")
            traceback.print_exc()
    print("\n" + "=" * 56)
    print(f"结果: {ok} 通过, {fail} 失败")
    print("=" * 56)
    sys.exit(0 if fail == 0 else 1)
