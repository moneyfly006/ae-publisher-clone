"""只读获取商品编辑页的原箱选项定义；不保存商品，不输出账号或凭证。"""
from pathlib import Path
from urllib.parse import urljoin,urlparse
from concurrent.futures import ThreadPoolExecutor
import re,sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aeclone.config import AppConfig
from aeclone.integrations.dianxiaomi import DianxiaomiClient
cfg=AppConfig.load();client=DianxiaomiClient(cfg.manual_cookies or {},overrides=cfg.endpoint_overrides or {})
base='https://www.dianxiaomi.com'
r=client.session.get(base+'/web/smt/edit',params={'id':cfg.template_product_id},timeout=20);r.raise_for_status()
links=re.findall(r'<script[^>]+src\s*=\s*["\']([^"\']+)',r.text,re.I)
main=next(urljoin(base,x) for x in links if '/assets/index-' in x)
cache=Path('build/verification/platform-assets');cache.mkdir(parents=True,exist_ok=True)
def fetch(url):
 path=cache/Path(urlparse(url).path).name
 if path.exists():return (url,path.read_text())
 response=client.session.get(url,timeout=20);response.raise_for_status()
 path.write_text(response.text);return url,response.text
_,text=fetch(main)
route=re.search(r'name:[`"\']smtProductEdit[`"\'].*?import\([`"\']([^`"\']+)',text)
if not route:raise SystemExit('未找到商品编辑页面')
entry,source=fetch(urljoin(main,route.group(1)))
imports=list(dict.fromkeys(re.findall(r'["`]([.]/[^"`]+\.js)["`]',source)))
urls=[urljoin(entry,x) for x in imports if not re.search(r'antd|vendor|lodash|index-|vue-|vxe-|xlsx|CKEditor',x)]
with ThreadPoolExecutor(max_workers=6) as pool:
 sources=[(entry,source)]+list(pool.map(fetch,urls))
results=[]
for url,source in sources:
 for m in list(re.finditer('originalBox|是否原箱',source))[:15]:
  results.append(dict(asset=urlparse(url).path,context=source[max(0,m.start()-180):m.end()+300]))
Path('build/verification/platform-options.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(dict(script_count=len(sources),option_evidence_count=len(results)),ensure_ascii=False))
