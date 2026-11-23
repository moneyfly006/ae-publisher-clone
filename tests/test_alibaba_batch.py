"""Alibaba 采集箱批量处理：类目过滤、逐商品类目、失败不中断。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import alibaba_batch as AB  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)
        print(f"  ✗ {name} {detail}")


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.detail = []

    def list_products(self, page=1, page_size=100, dxm_state=None):
        return {"data": {"page": {"list": self.rows, "totalSize": len(self.rows)}}}


class FakeCfg:
    alibaba_batch_category_id = "127726092"
    alibaba_category_id = "127726092"


def make_rows():
    return [
        {"id": "1", "subject": "Laser screed", "categoryId": "127726092"},
        {"id": "2", "subject": "Pool", "categoryId": "202107201"},
        {"id": "3", "subject": "Drill rig", "categoryId": "127726092"},
    ]


class FakeRes:
    def __init__(self, ok=True, score=4.8):
        self.ok = ok
        self.saved = ok
        self.auditing = False
        self.score = score
        self.error = ""
        self.save_result = {"data": {"msg": "ok"}}


def test_filter_and_per_product_category():
    rows = make_rows()
    calls = []

    def fake_run(pid, cfg, **kw):
        calls.append((pid, kw.get("overrides")))
        return FakeRes()

    with patch.object(AB, "run_alibaba_product", side_effect=fake_run), \
            patch.object(AB, "make_client", return_value=FakeClient(rows)):
        res = AB.run_collect_batch(FakeCfg(), limit=10, only_current_category=True,
                                   client=FakeClient(rows), current_category="127726092")
    check("只处理匹配类目（处理 2 个）", len(calls) == 2, calls)
    check("不匹配类目记 skipped", sum(1 for x in res if x.get("skipped")) == 1, res)
    check("明细带跳过原因", any("类目 202107201" in x["error"] for x in res))
    check("每个商品保留自己的类目", all(
        ov and ov.get("category_id") == pid_to_cat[pid]
        for pid, ov in calls for pid_to_cat in [{"1": "127726092", "3": "127726092"}]))
    return res


def test_closed_filter_processes_all():
    rows = make_rows()
    calls = []

    def fake_run(pid, cfg, **kw):
        calls.append(pid)
        return FakeRes()

    with patch.object(AB, "run_alibaba_product", side_effect=fake_run):
        res = AB.run_collect_batch(FakeCfg(), limit=10, only_current_category=False,
                                   client=FakeClient(rows), current_category="127726092")
    check("关闭过滤后处理全部 3 个", len(calls) == 3, calls)
    check("无跳过", not any(x.get("skipped") for x in res))


def test_failure_does_not_stop_batch():
    rows = make_rows()
    seq = iter([FakeRes(ok=False), FakeRes(ok=True)])

    def fake_run(pid, cfg, **kw):
        return next(seq)

    with patch.object(AB, "run_alibaba_product", side_effect=fake_run):
        res = AB.run_collect_batch(FakeCfg(), limit=10, only_current_category=True,
                                   client=FakeClient(rows), current_category="127726092")
    check("单条失败不中止", len(res) == 3 and sum(1 for x in res if x.get("ok")) == 1, res)




def test_precheck_is_read_only_and_uses_op2():
    rows = make_rows()
    calls = []

    def fake_run(pid, cfg, **kw):
        calls.append(kw)
        return FakeRes(ok=True, score=4.7)

    with patch.object(AB, "run_alibaba_product", side_effect=fake_run):
        res = AB.precheck_collect_batch(FakeCfg(), limit=10,
                                        client=FakeClient(rows), current_category="127726092")
    check("预检全部记录 precheck", all(x.get("precheck") for x in res), res)
    check("预检使用 dry_run=True", all(c.get("dry_run") is True for c in calls), calls)
    check("预检使用 op=2", all(str(c.get("op")) == "2" for c in calls), calls)




class DetailClient(FakeClient):
    def product_detail(self, pid):
        return {"id": pid, "keywords": "dth drilling rig, mine drilling, portable rig"}

    def required_attributes(self, cid):
        return []


def test_batch_preserves_product_keywords():
    ov = AB._prepare_overrides(DetailClient([]), {"id": "1", "categoryId": "127726092"},
                               FakeCfg())
    check("批量不覆盖商品自己的关键词", "dth drilling rig" in str(ov.get("keywords") or ""), ov)




def test_wait_publish_list_params():
    from aeclone.integrations.alibaba import AlibabaClient
    c = AlibabaClient({})
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return {"data": {"page": {"list": []}}}
    c._get = fake_get
    c.list_products(1, 20, "waitPublish")
    check("待发布列表用 offline+waitPublish", seen.get("dxmState") == "offline"
          and seen.get("dxmOfflineState") == "waitPublish", seen)




def test_op3_rejected_for_wait_publish_source():
    rows = make_rows()
    calls = []

    def fake_run(pid, cfg, **kw):
        calls.append(pid); return FakeRes()

    with patch.object(AB, "run_alibaba_product", side_effect=fake_run):
        res = AB.run_collect_batch(FakeCfg(), op="3", limit=10, state="waitPublish",
                                   only_current_category=False, client=FakeClient(rows))
    check("待发布来源 op3 不发请求", calls == [], calls)
    check("待发布来源 op3 明确跳过", all(x.get("skipped") for x in res), res)
    check("op3 跳过原因可读", any("采集箱" in str(x.get("error")) for x in res), res)


def main():
    print("=" * 60)
    test_filter_and_per_product_category()
    test_closed_filter_processes_all()
    test_failure_does_not_stop_batch()
    test_precheck_is_read_only_and_uses_op2()
    test_batch_preserves_product_keywords()
    test_wait_publish_list_params()
    test_op3_rejected_for_wait_publish_source()
    print("=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
