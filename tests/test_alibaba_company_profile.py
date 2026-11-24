"""Alibaba 公司资料预设：只含参考店铺可核实事实，可一键填入且可继续修改。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.alibaba_company import COMPANY_PROFILE, FACTS, apply_profile, company_desc, company_faq  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    check("公司名已预设", COMPANY_PROFILE["company_name"] == "Zhengzhou Yugong Construction Machinery Co., Ltd.")
    check("公司介绍含真实事实", "2023" in company_desc() and "RMB 50,000,000" in company_desc()
          and "TÜV Rheinland" in company_desc())
    check("FAQ 含 MOQ/定制/市场", "MOQ" in company_faq() and "customization" in company_faq()
          and "Southeast Asia" in company_faq())
    check("事实摘要不编造工厂自有", "collaborating factories" in FACTS.lower())
    class FakeCfg:
        def set(self, key, value):
            setattr(self, key, value)
    cfg = FakeCfg()
    saved = []
    values = apply_profile(cfg, lambda c: saved.append(True))
    check("一键写入四个字段", set(values) == {"alibaba_company_name", "alibaba_company_website",
                                              "alibaba_company_desc", "alibaba_company_faq"})
    check("写入后可继续修改", cfg.alibaba_company_name == COMPANY_PROFILE["company_name"])
    cfg.alibaba_company_desc = "custom"
    check("不被自动覆盖", cfg.alibaba_company_desc == "custom")
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
