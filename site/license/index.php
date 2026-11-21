<?php require __DIR__.'/lib.php'; $SITE = cfg()['site_name']; ?>
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title><?=htmlspecialchars($SITE)?></title>
<link rel="icon" href="../assets/logo.png">
<style>
:root{--bg:#0B1217;--card:#152129;--card2:#1B2A33;--line:#243640;--text:#E9F2F3;--muted:#93A8B0;--accent:#58C9B7;--warn:#EAC078;--danger:#FF9298}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"PingFang SC","Noto Sans SC",system-ui,sans-serif;line-height:1.6}
a{color:inherit;text-decoration:none}.wrap{max-width:1120px;margin:0 auto;padding:0 22px}
.nav{height:64px;display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line);background:rgba(11,18,23,.8);position:sticky;top:0;backdrop-filter:blur(12px);z-index:20}
.brand{display:flex;align-items:center;gap:10px;font-weight:800}.brand img{width:30px;height:30px;border-radius:8px}
.nav .sp{flex:1}.btn{border:1px solid var(--line);background:transparent;color:var(--text);padding:9px 16px;border-radius:10px;cursor:pointer;font-size:13.5px}
.btn.primary{background:var(--accent);color:#062621;border-color:var(--accent);font-weight:700}
.btn.small{padding:6px 12px;font-size:12.5px}
.hero{padding:54px 0 30px}
.hero h1{font-size:40px;letter-spacing:-.02em}.hero h1 em{font-style:normal;color:var(--accent)}
.hero p{color:var(--muted);margin-top:10px;max-width:640px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px;margin:22px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
.pkg h3{font-size:18px;margin-bottom:4px}.pkg .price{font-size:30px;font-weight:800;color:var(--accent);margin:10px 0}
.pkg .price small{font-size:13px;color:var(--muted);font-weight:400}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.tag{font-size:11.5px;border:1px solid var(--line);border-radius:20px;padding:3px 9px;color:var(--muted)}
.tag.on{color:#7fe0d0;border-color:rgba(88,201,183,.45);background:rgba(88,201,183,.08)}
.desc{color:var(--muted);font-size:13px;min-height:38px}
.sec{margin:34px 0}.sec h2{font-size:22px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:13px}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600;font-size:12px;background:var(--card2)}
tr:last-child td{border-bottom:none}
.muted{color:var(--muted);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.kpi b{display:block;font-size:24px;color:var(--accent)}.kpi span{font-size:12px;color:var(--muted)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input,select{background:var(--card2);border:1px solid var(--line);color:var(--text);padding:9px 12px;border-radius:9px;font-size:13.5px;outline:none}
input:focus{border-color:var(--accent)}
.modal{position:fixed;inset:0;background:rgba(4,8,10,.72);display:none;place-items:center;z-index:50}
.modal.open{display:grid}.dlg{width:380px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px}
.dlg h3{margin-bottom:12px}.dlg .field{margin:10px 0}.dlg input{width:100%}
.tip{color:var(--warn);font-size:12.5px;margin-top:8px}
.st{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}
.st.ok{color:#7fe0d0;border-color:rgba(88,201,183,.5);background:rgba(88,201,183,.1)}
.st.bad{color:var(--danger);border-color:rgba(255,146,152,.4)}
footer{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:12.5px;margin-top:40px}
code{background:var(--card2);padding:2px 6px;border-radius:6px;font-size:12px;word-break:break-all}
</style></head><body>
<div class="nav"><div class="wrap row" style="width:100%">
  <a class="brand" href="../index.html"><img src="../assets/logo.png" alt="">极光上架 · 授权中心</a>
  <div class="sp"></div>
  <span id="who" class="muted"></span>
  <button class="btn small" id="btnLogin">登录 / 注册</button>
  <a class="btn small" href="../index.html">官网</a>
</div></div>

<div class="wrap">
  <div class="hero">
    <h1>按平台与功能包，<em>授权即开通</em></h1>
    <p>购买后授权自动下发到你的软件：左侧平台按套餐点亮，未开通的平台保持锁定。换机/续期在下方自助完成。</p>
  </div>

  <div class="sec" id="pkgSec"><h2>套餐</h2><div class="grid" id="pkgs"></div></div>

  <div id="userArea" style="display:none">
    <div class="sec"><h2>我的授权</h2>
      <div class="kpis" id="licKpis"></div>
      <div class="card" style="margin-top:14px">
        <div class="row"><b>机器码</b>
          <input id="mc" placeholder="软件 设置→授权 里的机器码" style="width:320px">
          <button class="btn" id="btnBind">绑定 / 更新</button>
          <span class="muted" id="bindHint"></span>
        </div>
        <div class="muted" style="margin-top:8px">只有绑定后的机器码才能自动拉取授权；未绑定可用“复制 Token”手动粘贴到软件。</div>
        <div class="row" style="margin-top:10px"><b>设备</b>
          <span class="muted" id="devInfo">未绑定</span>
          <button class="btn small" id="btnUnbind">解绑当前设备</button>
          <span class="muted">30 天内只能解绑一次</span>
        </div>
        <div class="row" style="margin-top:12px"><b>授权 Token</b>
          <button class="btn small" id="btnCopy">复制</button>
          <button class="btn small" id="btnShow">显示/隐藏</button>
        </div>
        <code id="tok" style="display:block;margin-top:10px;max-height:120px;overflow:auto"></code>
      </div>
    </div>

    <div class="sec"><h2>软件运行状态</h2><div class="grid" id="statusCards"></div></div>
    <div class="sec"><h2>刊登中的产品</h2><div id="prodBox" class="card muted">软件上报后会显示这里</div></div>
    <div class="sec"><h2>订单记录</h2><table id="ordTable"><tr><th>订单号</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th></tr></table></div>
  </div>
</div>
<footer><div class="wrap">极光上架 AuroraList · 授权中心 · 咨询 QQ 3219904322</div></footer>

<div class="modal" id="modal"><div class="dlg">
  <h3 id="dlgTitle">登录</h3>
  <div class="field"><input id="email" placeholder="邮箱"></div>
  <div class="field"><input id="password" type="password" placeholder="密码（至少 6 位）"></div>
  <div class="field" id="nameRow" style="display:none"><input id="name" placeholder="称呼（可选）"></div>
  <div class="row"><button class="btn primary" id="btnAuth">登录</button>
    <button class="btn" id="btnSwitch">去注册</button>
    <button class="btn" id="btnClose">取消</button></div>
  <div class="tip" id="authHint"></div>
</div></div>

<script>
const api = (a, data) => fetch('api.php?action='+a, {method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify(data||{})}).then(r=>r.json());
let ME = null, MODE='login';
const $ = s => document.querySelector(s);
const esc = s => String(s??'').replace(/[<>&"]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
const platName = {alibaba:'Alibaba 国际站',aliexpress:'速卖通',amazon:'Amazon',tiktok:'TikTok',shopee:'Shopee'};
const featName = {listing:'刊登',category:'类目',trade:'价格贸易',pricing:'价格',variants:'变种',ai_text:'AI 文案',ai_image:'AI 图片',audit:'审核',fba:'FBA'};

function openAuth(mode){MODE=mode; $('#dlgTitle').textContent = mode==='login'?'登录':'注册';
  $('#nameRow').style.display = mode==='login'?'none':'block';
  $('#btnAuth').textContent = mode==='login'?'登录':'注册';
  $('#btnSwitch').textContent = mode==='login'?'去注册':'去登录'; $('#authHint').textContent='';
  $('#modal').classList.add('open');}
$('#btnClose').onclick = ()=>$('#modal').classList.remove('open');
$('#btnSwitch').onclick = ()=>openAuth(MODE==='login'?'register':'login');
$('#btnAuth').onclick = async ()=>{
  const data = {email:$('#email').value.trim(), password:$('#password').value, name:$('#name').value.trim()};
  const r = await api(MODE==='login'?'login':'register', data);
  if(r.code!==0){$('#authHint').textContent=r.msg;return;}
  $('#modal').classList.remove('open'); await boot();
};

async function loadPackages(){
  const r = await api('packages'); const box = $('#pkgs'); box.innerHTML='';
  (r.data||[]).forEach(p=>{
    const plats = (p.platforms||[]).map(x=>`<span class="tag on">${platName[x]||x}</span>`).join('');
    const feats = (p.features||[]).map(x=>`<span class="tag">${featName[x]||x}</span>`).join('');
    box.insertAdjacentHTML('beforeend', `<div class="card pkg">
      <h3>${esc(p.name)}</h3><div class="desc">${esc(p.description||'')}</div>
      <div class="price">¥${p.price}<small> / ${p.days} 天</small></div>
      <div class="tags">${plats}${feats}</div>
      <button class="btn primary" data-pkg="${p.id}">购买 / 续费</button></div>`);
  });
  box.querySelectorAll('[data-pkg]').forEach(b=>b.onclick=()=>buy(+b.dataset.pkg));
}
async function buy(pid){
  if(!ME){openAuth('login'); return;}
  const r = await api('order_create',{package_id:pid});
  if(r.code!==0){alert(r.msg);return;}
  const d = r.data;
  if(d.pay_url){location.href=d.pay_url;return;}
  if(d.mock_pay){
    if(!confirm('未配置易支付：这是测试支付，确认模拟付款？')) return;
    const m = await api('order_mock_pay',{order_id:d.order_id});
    if(m.code!==0){alert(m.msg);return;}
    alert('支付成功，授权已开通'); await boot();
  }
}
async function boot(){
  const me = await api('me');
  ME = me.data.user;
  document.getElementById('userArea').style.display = ME?'block':'none';
  $('#btnLogin').textContent = ME?'退出登录':'登录 / 注册';
  $('#who').textContent = ME ? ME.email : '';
  if(!ME){ await loadPackages(); return; }
  const lic = me.data.license;
  const kp = $('#licKpis');
  if(lic){
    const exp = lic.expires_at, days = Math.ceil((new Date(exp)-new Date())/86400000);
    kp.innerHTML = `<div class="kpi"><b>${days>0?days:0}</b><span>剩余天数</span></div>
      <div class="kpi"><b style="font-size:16px">${exp}</b><span>到期日</span></div>
      <div class="kpi"><b style="font-size:16px">${lic.machine_code||'未绑定'}</b><span>机器码</span></div>
      <div class="kpi"><b style="font-size:15px">${Object.keys(lic.platforms||{}).map(x=>platName[x]||x).join(' / ')||'-'}</b><span>已开通平台</span></div>`;
    $('#mc').value = lic.machine_code||'';
    $('#devInfo').textContent = lic.machine_code ? ('已绑定 '+lic.machine_code) : '未绑定（软件登录会自动绑定）';
    const lr = await api('license');
    $('#tok').textContent = lr.data?.token || '';
  }else{
    kp.innerHTML = `<div class="kpi"><b>未开通</b><span>购买套餐后显示授权</span></div>`;
    $('#tok').textContent='';
  }
  // 运行状态
  const st = await api('status');
  const hb = st.data?.heartbeat;
  const sc = $('#statusCards');
  if(hb){
    const p = hb.payload||{};
    const rows = Object.entries(p.platforms||{}).map(([k,v])=>`<div class="kpi"><b style="font-size:15px">${platName[k]||k}</b><span>${esc(v.status||'已授权')} · 今日 ${esc(v.today??'-')}</span></div>`).join('');
    sc.innerHTML = `<div class="kpi"><b>${new Date(hb.ts*1000).toLocaleString()}</b><span>最近心跳</span></div>${rows}`;
    const prods = p.products||[];
    $('#prodBox').innerHTML = prods.length? `<table><tr><th>平台</th><th>商品</th><th>状态</th><th>更新时间</th></tr>
      ${prods.map(x=>`<tr><td>${platName[x.platform]||esc(x.platform)}</td><td>${esc(x.title)}</td><td>${esc(x.status)}</td><td>${esc(x.updated||'')}</td></tr>`).join('')}</table>` : '软件暂未上报商品';
  }else{
    sc.innerHTML = `<div class="kpi"><b>暂无</b><span>软件运行并联网后显示</span></div>`;
    $('#prodBox').textContent = '软件上报后会显示这里';
  }
  // 订单
  const ords = await api('orders');
  const t = $('#ordTable');
  t.innerHTML = '<tr><th>订单号</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th></tr>' +
    (ords.data||[]).map(o=>`<tr><td>${esc(o.trade_no)}</td><td>${esc(o.name||'')}</td><td>¥${(o.amount_cents/100).toFixed(2)}</td>
      <td><span class="st ${o.status==='paid'?'ok':'bad'}">${o.status==='paid'?'已支付':'待支付'}</span></td>
      <td>${new Date(o.created_at*1000).toLocaleString()}</td></tr>`).join('');
  await loadPackages();
}
$('#btnBind') && ($('#btnBind').onclick = async ()=>{
  const r = await api('bind_machine',{machine_code:$('#mc').value.trim()});
  $('#bindHint').textContent = r.code===0?'已绑定':'绑定失败：'+r.msg;
  $('#bindHint').style.color = r.code===0?'var(--accent)':'var(--danger)';
  await boot();
});
$('#btnUnbind') && ($('#btnUnbind').onclick = async ()=>{
  if(!confirm('解绑后当前电脑将无法自动拉取授权，确定？')) return;
  const r = await api('unbind');
  if(r.code!==0){alert(r.msg);return;}
  alert('已解绑，可在新电脑登录软件自动绑定'); await boot();
});
$('#btnCopy') && ($('#btnCopy').onclick = async ()=>{ try{ await navigator.clipboard.writeText($('#tok').textContent); alert('已复制'); }catch(e){ alert('请手动选择复制'); } });
$('#btnShow') && ($('#btnShow').onclick = ()=>{ const t=$('#tok'); t.style.display = t.style.display==='none'?'block':'none'; });
$('#btnLogin').addEventListener('click', async (e)=>{ if(ME){ await api('logout'); await boot(); } });
boot();
</script></body></html>
