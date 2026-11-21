"""类目自动匹配回归测试：学习、停用词、四级优先级。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import category_map as CM  # noqa: E402
from aeclone.services.api_save import build_save_payload  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name} {extra}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


print("=" * 60)
print("类目映射测试")

t1 = [{"title": "Hydraulic Rock Drill Splitter for Excavator", "categoryId": "A1"},
      {"title": "Hydraulic Rock Drill Hammer for Excavator", "categoryId": "A1"},
      {"title": "Concrete Mixer Machine with fast speed", "categoryId": "B2"},
      {"title": "Concrete Mixer Truck for construction", "categoryId": "B2"}]
learned = CM.learn_from_products(t1)
check("学到关键词", len(learned) >= 4, f"({len(learned)})")
check("通用词被过滤", "with" not in learned and "fast" not in learned and "mini" not in learned)
check("含停用词的双词被过滤", not any("for" in k.split() for k in learned))
check("关键词→类目正确", learned.get("hydraulic") == "A1" and learned.get("concrete mixer") == "B2",
      f"{learned.get('hydraulic')}/{learned.get('concrete mixer')}")

check("长词优先匹配", CM.match("Hydraulic Rock Drill", {"rules": [], "learned": learned}) == "A1")
check("未知标题不匹配", CM.match("Some totally unknown item", {"rules": [], "learned": learned}) == "")
check("用户规则优先于学习表",
      CM.match("Hydraulic Rock Drill", {"rules": [{"kw": "hydraulic", "categoryId": "C1"}],
                                        "learned": learned}) == "C1")

# 学习需要过滤单类目噪声：纯一个类目时也学，但不该学通用词
one = [{"title": "Track Hydraulic Mini Bored Pile Rotary Piling Rig", "categoryId": "X"}]
l1 = CM.learn_from_products(one)
check("单类目也能学且无停用词", all(k not in CM._STOP for k in l1), sorted(l1)[:4])

# 四级优先级（build_save_payload）
tpl = {"id": "t", "categoryId": "TPL"}


class _Cfg:
    def __init__(self, auto):
        self.auto_detect_category = auto

    def __getattr__(self, k):
        return None


CM.save({"rules": [{"kw": "hydraulic", "categoryId": "RULE"}],
         "learned": {"concrete mixer": "LEARN"}})
try:
    p = build_save_payload({"id": "1", "categoryId": "OWN", "subject": "Hydraulic Rock Drill"},
                           tpl, action="save_only", cfg=_Cfg(True))
    check("规则 > 自带", str(p.get("categoryId")) == "RULE", str(p.get("categoryId")))
    p = build_save_payload({"id": "1", "categoryId": "OWN", "subject": "Concrete Mixer Truck"},
                           tpl, action="save_only", cfg=_Cfg(True))
    check("学习表 > 自带", str(p.get("categoryId")) == "LEARN", str(p.get("categoryId")))
    p = build_save_payload({"id": "1", "categoryId": "  OWN  ", "subject": "Random Widget"},
                           tpl, action="save_only", cfg=_Cfg(True))
    check("自带（去空格）", str(p.get("categoryId")) == "OWN", repr(p.get("categoryId")))
    p = build_save_payload({"id": "1", "categoryId": "", "subject": "Random Widget"},
                           tpl, action="save_only", cfg=_Cfg(True))
    check("空 -> 模板", str(p.get("categoryId")) == "TPL", str(p.get("categoryId")))
    p = build_save_payload({"id": "1", "categoryId": "0", "subject": "Random Widget"},
                           tpl, action="save_only", cfg=_Cfg(True))
    check("0 -> 模板", str(p.get("categoryId")) == "TPL", str(p.get("categoryId")))
    p = build_save_payload({"id": "1", "categoryId": "OWN", "subject": "Hydraulic Rock Drill"},
                           tpl, action="save_only", cfg=_Cfg(False))
    check("关闭自动分类 -> 模板", str(p.get("categoryId")) == "TPL", str(p.get("categoryId")))
finally:
    CM.save({"rules": [], "learned": {}})

print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)
