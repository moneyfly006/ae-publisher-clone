"""经营类目范围预检（只读）测试。

背景：平台发布硬校验 PUB_BIZCHECK_CAT_PUB_RESTRICT（类目不在店铺经营类目范围）。
没有查询经营类目清单的接口，用「在线商品类目样本」做近似预检并给客户提醒。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services.alibaba_scope import (  # noqa: E402
    CUSTOMER_ADVICE, RESTRICT_CODE, check_category_scope,
)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)
        print(f"  ✗ {name} {detail}")


class FakeClient:
    def __init__(self, cats=None, total=None, boom=False):
        self.cats = cats or {}
        self.total = total
        self.boom = boom

    def online_category_scope(self, max_pages=10, page_size=100):
        if self.boom:
            raise RuntimeError("network down")
        return {"categories": self.cats, "scanned": 300,
                "total": self.total if self.total is not None else 1000, "pages": 3}


def test_category_found():
    r = check_category_scope(FakeClient({"202107201": 12}, total=1000), "202107201")
    check("命中类目 in_scope_guess=True", r.in_scope_guess)
    check("命中次数正确", r.evidence_count == 12, r.evidence_count)
    check("命中提示含「可发」", "可发" in r.message(), r.message())


def test_category_missing():
    r = check_category_scope(FakeClient({"100010741": 50}, total=1000), "202107201")
    check("未命中 in_scope_guess=False", not r.in_scope_guess)
    check("未命中提示含经营类目/受限", "经营类目" in r.message() and RESTRICT_CODE in r.message(),
          r.message())
    check("客户提醒含卖家中心路径", "店铺管理" in r.advice and "经营类目" in r.advice)
    check("CUSTOMER_ADVICE 与结果一致", r.advice == CUSTOMER_ADVICE)


def test_empty_category_and_network_error():
    r = check_category_scope(FakeClient(), "")
    check("空类目不误报可发", not r.in_scope_guess and "未选择类目" in r.detail)
    boom = check_category_scope(FakeClient(boom=True), "202107201")
    check("网络失败只提示不影响后续", "预检失败" in boom.detail and not boom.in_scope_guess)


def test_low_coverage_marks_conclusion_weak():
    r = check_category_scope(FakeClient({"100010741": 1}, total=5000), "202107201")
    r.scanned = 300
    check("覆盖率低时提示仅供参考", "仅供参考" in r.detail or "覆盖率" in r.detail, r.detail)


def main():
    print("=" * 60)
    print("经营类目范围预检测试")
    test_category_found()
    test_category_missing()
    test_empty_category_and_network_error()
    test_low_coverage_marks_conclusion_weak()
    print("=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
