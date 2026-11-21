import requests, sys, json
ck = open('.tmp-probe/cookies.txt').read().strip()
s = requests.Session()
for part in ck.split(';'):
    if '=' in part:
        k, v = part.strip().split('=', 1)
        s.cookies.set(k, v, domain='.dianxiaomi.com')
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                  "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.dianxiaomi.com/"})
for path in ["/api/userIn.json", "/api/getVipActiveTime.json"]:
    try:
        r = s.get("https://www.dianxiaomi.com" + path, timeout=20)
        print(path, r.status_code, r.text[:400].replace('\n',' '))
    except Exception as e:
        print(path, "ERR", e)
