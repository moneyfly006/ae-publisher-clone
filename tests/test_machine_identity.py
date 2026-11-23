"""机器码：多硬件组合应唯一；旧机器码仍作为兼容候选。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import aeclone.license as lic  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    with patch.object(lic, "_identity_components", return_value=["MACHINE-A", "SERIAL-A"]):
        a = lic.machine_code()
    with patch.object(lic, "_identity_components", return_value=["MACHINE-B", "SERIAL-B"]):
        b = lic.machine_code()
    with patch.object(lic, "_identity_components", return_value=["MACHINE-A", "SERIAL-A"]):
        a2 = lic.machine_code()
    check("不同硬件组合机器码不同", a != b, (a, b))
    check("同一硬件机器码稳定", a == a2, (a, a2))
    check("机器码仍为 8-8 格式", len(a) == 17 and a[8] == "-", a)
    with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
        cands = lic.identity_candidates()
        current = lic.machine_code()
    check("保留旧机器码兼容候选", len(cands) >= 3, cands)
    check("候选包含新码", current in cands, cands)
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
