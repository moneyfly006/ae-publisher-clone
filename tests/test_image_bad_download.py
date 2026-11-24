"""下载内容损坏/不是图片时，四合一跳过坏图而不是整件失败。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image
from aeclone.services.image_ops import four_in_one

FAILED=[]
def check(n,c,d=""):
 if c: print(f"  ✓ {n}")
 else: FAILED.append(n); print(f"  ✗ {n} {d}")

def main():
 print("="*60)
 with tempfile.TemporaryDirectory() as td:
  d=Path(td); paths=[]
  for i,color in enumerate(("red","blue","green")):
   p=d/f"{i}.png"; Image.new("RGB",(100,100),color).save(p); paths.append(p)
  bad=d/"bad.png"; bad.write_bytes(b"<html>not an image</html>")
  out=d/"out.png"; four_in_one([paths[0],bad,paths[1],paths[2]],out)
  check("坏图被跳过，仍生成四合一",out.exists() and out.stat().st_size>0)
  check("坏图也不阻断",Image.open(out).size==(1200,1200))
 print("="*60)
 if FAILED: print(f"FAILED: {len(FAILED)}"); return 1
 print("PASS: 全部通过"); return 0
if __name__=="__main__": sys.exit(main())
