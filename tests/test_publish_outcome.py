"""统一成功语义：待发布=进入待发布；直接发布=最终审核中/在线。"""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aeclone.services.publish_outcome import judge

FAILED=[]
def chk(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")



def test_publishing_is_pending_not_success():
    from types import SimpleNamespace
    from aeclone.services.publisher import Publisher, SaveResult
    class _Dxm:
        def _get(self, ep, params):
            return {"data": {"product": {"id": "p", "dxmOfflineState": "publishing"}}}
    pub = object.__new__(Publisher)
    pub.cfg = SimpleNamespace(template_product_id="", declared_value="50", custom_attributes=[])
    pub.dxm = _Dxm()
    pub._await_publish_outcome = lambda *a, **k: None
    out = pub.publish_only("p", SaveResult(ok=True, message=""))
    chk("发布中不判成功、不判失败", (not out.ok) and bool(getattr(out, "pending", False)), out)
    chk("发布中提示待确认/不重复", "待确认" in out.message and "重复" in out.message, out.message)


def main():
 print("="*60)
 chk("保存到待发布：editingRequired 成功", judge("save_to_waiting","editingRequired")["ok"])
 chk("保存到待发布：waitPublish 成功", judge("3","waitPublish")["ok"])
 chk("保存到待发布：draft 失败", not judge("save_to_waiting","draft")["ok"])
 chk("直接发布：auditing 成功", judge("publish","auditing")["ok"])
 chk("直接发布：online 成功", judge("publish","online")["ok"])
 chk("直接发布：waitPublish 不算成功", not judge("publish","waitPublish")["ok"])
 chk("直接发布：publishing 不算成功", not judge("publish","publishing")["ok"])
 chk("直接发布：publishFail 失败", not judge("publish","publishFail")["ok"])
 test_publishing_is_pending_not_success()
 # 后端规则不能把“已提交发布”算发布成功
 pages=(ROOT/"deploy/license-center-v2/app/pages.php").read_text(encoding="utf-8")
 chk("后端 publish_ok 不再包含“产品已提交发布”", "产品已提交发布" not in pages.split("'publish_ok'",1)[1].split("'pending'",1)[0])
 chk("后端增加 pending 待发布事件", "'pending'" in pages and "已保存到待发布" in pages)
 chk("后端 publish_ok 包含最终审核中", "平台审核中" in pages)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
