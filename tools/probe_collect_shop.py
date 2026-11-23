from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aeclone.integrations.alibaba import AlibabaClient
c=AlibabaClient(Path('build/reference-cookie.txt').read_text())
raw=c._post('/alibabaIcbuShopInfoSync/list.json',data={})
Path('build/alibaba-shop-response.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
print('root keys:',list(raw))
data=raw.get('data')
print('data type:',type(data).__name__)
if isinstance(data,dict):
 print('data keys:',list(data))
 for k,v in data.items():
  if isinstance(v,list):print(k,'rows',len(v),'row keys',list(v[0]) if v and isinstance(v[0],dict) else [])
