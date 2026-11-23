"""速卖通发布失败：自动切换店铺可用的安全运费模板。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.publisher import Publisher  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


class FakeDxm:
    def freight_templates(self, shop_id):
        return [{"templateId": "50192654715", "templateName": "copy free shipping"},
                {"templateId": "1000", "templateName": "Shipping Cost Template for New Sellers"}]


class Res:
    def __init__(self):
        self.steps = []


def main():
    print("=" * 60)
    pub = Publisher.__new__(Publisher)
    pub.cfg = SimpleNamespace(store_id="9190736", smt_freight_template_id="")
    pub.dxm = FakeDxm()
    target = {"shopId": "9190736", "freightTemplateId": "50192654715",
              "dxmOfflineState": "publishFail",
              "errMsg": "DAILY_TPL_NOT_EXIT_OR_SEND_GOODS_COUNTRY_NOT_MATCH"}
    res = Res()
    changed = pub._smt_shipping_preflight(target, res)
    check("失败商品自动切换模板", changed and target["freightTemplateId"] == "1000",
          target.get("freightTemplateId"))
    check("预审步骤可见", any("运费模板" in (str(getattr(x,"name",""))+str(getattr(x,"detail",""))) for x in res.steps), res.steps)
    # 用户选了模板商品：必须沿用模板运费模板，不能被安全模板覆盖
    tpl = {"freightTemplateId": "TEMPLATE-FREIGHT",
           "aeopAeProductSKUs": '[{"freightTemplateId":"TEMPLATE-SKU"}]'}
    target2 = {"shopId": "9190736", "freightTemplateId": "OLD",
               "dxmOfflineState": "publishFail",
               "aeopAeProductSKUs": '[{"id":"1","freightTemplateId":"OLD-SKU"}]'}
    changed2 = pub._smt_shipping_preflight(target2, Res(), tpl)
    check("模板物流优先", changed2 and target2["freightTemplateId"] == "TEMPLATE-FREIGHT",
          target2.get("freightTemplateId"))
    check("模板 SKU 物流同步", "TEMPLATE-FREIGHT" in str(target2.get("aeopAeProductSKUs")),
          target2.get("aeopAeProductSKUs"))
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())
