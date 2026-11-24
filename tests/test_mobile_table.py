"""PC 表格转手机详情：每个单元格/行不能黏成一行。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.api_save import build_mobile_detail

FAILED=[]
def check(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 html='''<p>Intro</p>
 <table><thead><tr><th>Model</th><th>Power</th><th>Weight</th></tr></thead>
 <tbody><tr><td>YG-20D</td><td>46KW</td><td>2000kg</td></tr>
 <tr><td>YG-30D</td><td>60KW</td><td>2500kg</td></tr></tbody></table>
 <h3>参数</h3><table><tr><td>产地</td><td>中国</td></tr><tr><td>质保</td><td>1年</td></tr></table><p>End</p>'''
 d=json.loads(build_mobile_detail(html))
 texts=[t.get("content","") for m in d["moduleList"] if m.get("type")=="text" for t in m.get("texts",[])]
 joined="\n".join(texts)
 check("三列表格按行保留", "Model | Power | Weight" in joined and "YG-20D | 46KW | 2000kg" in joined, joined)
 check("两列表格用参数冒号", "产地：中国" in joined and "质保：1年" in joined, joined)
 check("表格外正文保留", "Intro" in joined and "End" in joined and "参数" in joined, joined)
 check("不再黏成 ModelPowerWeight", "ModelPowerWeight" not in joined, joined)
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0

if __name__=="__main__": sys.exit(main())
