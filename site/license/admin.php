<?php require __DIR__.'/lib.php'; ?>
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>授权中心 · 管理后台</title>
<style>
:root{--bg:#0B1217;--card:#152129;--card2:#1B2A33;--line:#243640;--text:#E9F2F3;--muted:#93A8B0;--accent:#58C9B7;--danger:#FF9298}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"PingFang SC","Noto Sans SC",system-ui,sans-serif;line-height:1.6;font-size:13.5px}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
.nav{height:60px;display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line)}
.brand{font-weight:800}.sp{flex:1}
.btn{border:1px solid var(--line);background:transparent;color:var(--text);padding:8px 14px;border-radius:9px;cursor:pointer;font-size:13px}
.btn.primary{background:var(--accent);color:#062621;border-color:var(--accent);font-weight:700}
.btn.danger{color:var(--danger)}
input,select,textarea{background:var(--card2);border:1px solid var(--line);color:var(--text);padding:8px 10px;border-radius:8px;outline:none;font-size:13px}
.tabs{display:flex;gap:8px;margin:18px 0}
.tab{padding:8px 14px;border-radius:9px;border:1px solid var(--line);cursor:pointer;color:var(--muted)}
.tab.on{background:rgba(88,201,183,.12);color:#7fe0d0;border-color:rgba(88,201,183,.4)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi b{display:block;font-size:22px;color:var(--accent)}.kpi span{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);background:var(--card2);font-size:11.5px}
tr:last-child td{border-bottom:none}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.muted{color:var(--muted)}.ml{margin-left:auto}
.hide{display:none}.pill{font-size:11px;border:1px solid var(--line);border-radius:20px;padding:2px 8px;color:var(--muted)}
.hint{color:var(--muted);font-size:12px;margin-top:8px}
.login{max-width:340px;margin:120px auto;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:26px}
.login input{width:100%;margin:8px 0}
</style></head><body>
<div class="login" id="loginBox">
  <h2 style="margin-bottom:14px">管理后台登录</h2>
  <input id="admUser" placeholder="管理员账号">
  <input id="admPass" type="password" placeholder="密码">
  <button class="btn primary" id="admLogin" style="width:100%;margin-top:8px">登录</button>
  <div class="hint" id="admHint"></div>
</div>

<div id="panel" class="hide">
<div class="nav"><div class="wrap row" style="width:100%">
  <span class="brand">AuroraList 授权中心 · 管理后台</span><span class="sp"></span>
  <a class="btn" href="index.php" target="_blank">用户端</a>
  <button class="btn" id="admLogout">退出</button>
</div></div>
<div class="wrap">
  <div class="tabs">
    <div class="tab on" data-tab="dash">概览</div>
    <div class="tab" data-tab="pkg">套餐管理</div>
    <div class="tab" data-tab="ord">订单</div>
    <div class="tab" data-tab="usr">用户</div>
    <div class="tab" data-tab="lic">授权</div>
  </div>
  <div id="dash">
    <div class="kpis" id="stats"></div>
    <div class="card" style="margin-top:16px"><h3>最近心跳</h3><div id="hb" class="muted">加载中…</div></div>
  </div>
  <div id="pkg" class="hide">
    <div class="card"><div class="row">
      <input id="pName" placeholder="套餐名" style="width:180px">
      <input id="pPrice" placeholder="价格(元)" style="width:100px">
      <input id="pDays" placeholder="天数" value="365" style="width:90px">
      <input id="pPlat" placeholder="平台: alibaba,amazon" style="width:200px">
      <input id="pFeat" placeholder="功能: listing,category,ai_text" style="width:260px">
      <input id="pDesc" placeholder="说明" style="width:220px">
      <input type="hidden" id="pId">
      <button class="btn primary" id="pSave">保存</button><button class="btn" id="pReset">新增</button>
    </div><div class="hint">保存时会按套餐价格与天数生成授权；修改后对新订单生效。</div></div>
    <table id="pkgTable"><tr><th>ID</th><th>名称</th><th>平台</th><th>功能</th><th>价格</th><th>天数</th><th>状态</th><th>操作</th></tr></table>
  </div>
  <div id="ord" class="hide"><table id="ordTable"><tr><th>ID</th><th>订单号</th><th>用户</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th><th>操作</th></tr></table></div>
  <div id="usr" class="hide"><table id="usrTable"><tr><th>ID</th><th>邮箱</th><th>授权数</th><th>到期</th><th>已付订单</th><th>注册时间</th></tr></table></div>
  <div id="lic" class="hide"><table id="licTable"><tr><th>ID</th><th>用户</th><th>机器码</th><th>平台</th><th>到期</th><th>状态</th><th>操作</th></tr></table></div>
</div></div>

<script>
const api=(a,d)=>fetch('api.php?action='+a,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})}).then(r=>r.json());
const $=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
const platName={alibaba:'Alibaba',aliexpress:'速卖通',amazon:'Amazon',tiktok:'TikTok',shopee:'Shopee'};
async function login(){const r=await api('admin_login',{user:$('#admUser').value,password:$('#admPass').value});
  if(r.code!==0){$('#admHint').textContent=r.msg;return;} $('#loginBox').classList.add('hide');$('#panel').classList.remove('hide');loadAll();}
$('#admLogin').onclick=login; $('#admPass').addEventListener('keydown',e=>{if(e.key==='Enter')login()});
$('#admLogout').onclick=async()=>{await api('admin_logout');location.reload()};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));t.classList.add('on');
  ['dash','pkg','ord','usr','lic'].forEach(k=>$('#'+k).classList.toggle('hide',k!==t.dataset.tab));
  if(t.dataset.tab==='pkg')loadPkgs(); if(t.dataset.tab==='ord')loadOrds();
  if(t.dataset.tab==='usr')loadUsr(); if(t.dataset.tab==='lic')loadLic();
});
async function loadAll(){loadStats();loadPkgs();loadOrds();loadUsr();loadLic();}
async function loadStats(){const r=await api('admin_stats');if(r.code!==0)return;const d=r.data;
  $('#stats').innerHTML=[['用户',d.users],['订单',d.orders],['已支付',d.paid],['收入', '¥'+d.revenue],['有效授权',d.licenses],['24h心跳',d.heartbeats24]]
    .map(([k,v])=>`<div class="kpi"><b>${v}</b><span>${k}</span></div>`).join('');}
async function loadPkgs(){const r=await api('admin_packages');const t=$('#pkgTable');
  t.innerHTML='<tr><th>ID</th><th>名称</th><th>平台</th><th>功能</th><th>价格</th><th>天数</th><th>状态</th><th>操作</th></tr>'+
   (r.data||[]).map(p=>`<tr><td>${p.id}</td><td>${esc(p.name)}</td><td>${(p.platforms||[]).map(x=>platName[x]||x).join(',')}</td>
     <td>${esc((p.features||[]).join(','))}</td><td>¥${p.price}</td><td>${p.days}</td><td>${p.active?'启用':'停用'}</td>
     <td><button class="btn" data-edit="${p.id}">编辑</button></td></tr>`).join('');
  t.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{
    const p=(r.data||[]).find(x=>x.id==b.dataset.edit); if(!p)return;
    $('#pId').value=p.id;$('#pName').value=p.name;$('#pPrice').value=p.price;$('#pDays').value=p.days;
    $('#pPlat').value=(p.platforms||[]).join(',');$('#pFeat').value=(p.features||[]).join(',');$('#pDesc').value=p.description||'';
  });
}
$('#pReset').onclick=()=>{['pId','pName','pPrice','pPlat','pFeat','pDesc'].forEach(k=>$('#'+k).value='');$('#pDays').value=365};
$('#pSave').onclick=async()=>{const r=await api('admin_packages',{id:+$('#pId').value||0,name:$('#pName').value,
  price:+$('#pPrice').value,days:+$('#pDays').value,platforms:$('#pPlat').value.split(',').map(s=>s.trim()).filter(Boolean),
  features:$('#pFeat').value.split(',').map(s=>s.trim()).filter(Boolean),description:$('#pDesc').value});
  if(r.code!==0){alert(r.msg);return;}loadPkgs();$('#pReset').click();};
async function loadOrds(){const r=await api('admin_orders');const t=$('#ordTable');
  t.innerHTML='<tr><th>ID</th><th>订单号</th><th>用户</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th><th>操作</th></tr>'+
   (r.data||[]).map(o=>`<tr><td>${o.id}</td><td>${esc(o.trade_no)}</td><td>${esc(o.email||'')}</td><td>${esc(o.package||'')}</td>
     <td>¥${(o.amount_cents/100).toFixed(2)}</td><td>${o.status==='paid'?'已支付':'待支付'}</td>
     <td>${new Date(o.created_at*1000).toLocaleString()}</td>
     <td>${o.status==='paid'?'-':`<button class="btn small" data-paid="${o.id}">标记已付并开通</button>`}</td></tr>`).join('');
  t.querySelectorAll('[data-paid]').forEach(b=>b.onclick=async()=>{const x=await api('admin_mark_paid',{order_id:+b.dataset.paid});
    alert(x.code===0?'已开通':'失败：'+x.msg);loadStats();loadOrds();loadLic();});
}
async function loadUsr(){const r=await api('admin_users');const t=$('#usrTable');
  t.innerHTML='<tr><th>ID</th><th>邮箱</th><th>授权数</th><th>到期</th><th>已付订单</th><th>注册时间</th></tr>'+
   (r.data||[]).map(u=>`<tr><td>${u.id}</td><td>${esc(u.email)}</td><td>${u.licenses||0}</td><td>${esc(u.expires_at||'-')}</td>
     <td>${u.paid_orders||0}</td><td>${new Date(u.created_at*1000).toLocaleDateString()}</td></tr>`).join('');}
async function loadLic(){const r=await api('admin_licenses');const t=$('#licTable');
  t.innerHTML='<tr><th>ID</th><th>用户</th><th>机器码</th><th>平台</th><th>到期</th><th>状态</th><th>操作</th></tr>'+
   (r.data||[]).map(l=>`<tr><td>${l.id}</td><td>${esc(l.email||'')}</td><td>${esc(l.machine_code||'未绑定')}</td>
     <td>${Object.keys(l.platforms||{}).map(x=>platName[x]||x).join(',')}</td><td>${esc(l.expires_at)}</td>
     <td>${l.revoked?'已吊销':'有效'}</td>
     <td><button class="btn danger" data-rev="${l.id}">吊销</button></td></tr>`).join('');
  t.querySelectorAll('[data-rev]').forEach(b=>b.onclick=async()=>{if(!confirm('吊销该授权？'))return;
    const x=await api('admin_revoke',{license_id:+b.dataset.rev});alert(x.code===0?'已吊销':'失败');loadLic();});
}
(async()=>{const me=await api('admin_stats'); if(me.code===0){$('#loginBox').classList.add('hide');$('#panel').classList.remove('hide');loadAll();}})();
</script></body></html>
