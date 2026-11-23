<?php require __DIR__.'/lib.php'; ?>
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AuroraList 授权中心</title>
<link rel="stylesheet" href="/assets/portal-v2.css?v=2">
<link rel="icon" href="/shop/assets/logo.png">
<style>
:root{--bg:#0A1116;--panel:#111C23;--card:#16232B;--card2:#1C2C36;--line:#243842;--text:#E9F2F3;--muted:#8FA6AF;--accent:#58C9B7;--accent2:#2FA38F;--warn:#EAC078;--danger:#FF9298;--radius:16px}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 80% -10%,rgba(88,201,183,.10),transparent 60%),var(--bg);color:var(--text);font-family:"PingFang SC","Noto Sans SC",system-ui,-apple-system,sans-serif;line-height:1.65;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}img{display:block;max-width:100%}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
.nav{position:sticky;top:0;z-index:30;height:66px;display:flex;align-items:center;gap:16px;background:rgba(10,17,22,.82);border-bottom:1px solid var(--line);backdrop-filter:blur(14px)}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:16px}.brand img{width:30px;height:30px;border-radius:9px}
.nav .sp{flex:1}.nav .ann{color:var(--warn);font-size:12.5px;border:1px solid rgba(234,192,120,.3);background:rgba(234,192,120,.08);padding:4px 10px;border-radius:20px;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:transparent;color:var(--text);padding:9px 16px;border-radius:10px;cursor:pointer;font-size:13.5px;transition:.15s}
.btn:hover{border-color:rgba(88,201,183,.5)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#062621;font-weight:700}
.btn.primary:hover{filter:brightness(1.06)}
.btn.ghost{background:rgba(255,255,255,.02)}
.btn.small{padding:6px 12px;font-size:12.5px}
.btn.danger{color:var(--danger);border-color:rgba(255,146,152,.35)}
/* 未登录：营销 + 登录卡 */
.landing{display:grid;grid-template-columns:1.15fr .85fr;gap:52px;align-items:center;padding:64px 0 40px}
.eyebrow{font-size:12px;letter-spacing:.16em;color:var(--accent);font-weight:700}
.landing h1{font-size:46px;line-height:1.2;letter-spacing:-.02em;margin:14px 0 16px}
.landing h1 em{font-style:normal;color:var(--accent)}
.landing p.lead{color:var(--muted);font-size:16px;max-width:520px}
.feat{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:26px}
.feat div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;font-size:13px;color:var(--muted)}
.feat b{display:block;color:var(--text);font-size:13.5px;margin-bottom:2px}
.auth-card{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:28px;box-shadow:0 30px 70px rgba(0,0,0,.45)}
.auth-tabs{display:flex;gap:8px;margin-bottom:18px}
.auth-tab{flex:1;text-align:center;padding:9px;border-radius:10px;cursor:pointer;color:var(--muted);border:1px solid var(--line);font-size:13.5px}
.auth-tab.on{background:rgba(88,201,183,.12);color:#7fe0d0;border-color:rgba(88,201,183,.45)}
.field{margin:12px 0}label{display:block;font-size:12.5px;color:var(--muted);margin-bottom:5px}
input,select{width:100%;background:var(--card2);border:1px solid var(--line);color:var(--text);padding:11px 13px;border-radius:10px;font-size:14px;outline:none}
input:focus{border-color:var(--accent)}
.hint{font-size:12px;color:var(--muted);margin-top:6px}.err{color:var(--danger);font-size:12.5px;margin-top:8px}
/* 登录后 */
.layout{display:grid;grid-template-columns:210px 1fr;gap:26px;padding:26px 0 50px;align-items:start}
.side{position:sticky;top:90px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:12px}
.side .u{padding:10px 12px;border-bottom:1px solid var(--line);margin-bottom:8px}
.side .u b{display:block;font-size:13.5px;word-break:break-all}.side .u span{font-size:11.5px;color:var(--muted)}
.snav{display:flex;flex-direction:column;gap:2px}
.snav a{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:9px;font-size:13.5px;color:#b9c9cf;cursor:pointer}
.snav a:hover{background:rgba(255,255,255,.03)}
.snav a.on{background:rgba(88,201,183,.12);color:#7fe0d0}
.panel{display:none}.panel.on{display:block}
h2{font-size:24px;margin-bottom:6px}.sub{color:var(--muted);font-size:13.5px;margin-bottom:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi b{display:block;font-size:26px;color:var(--accent);line-height:1.3}.kpi b.sm{font-size:15px;color:var(--text)}
.kpi span{font-size:12px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:16px}
.card h3{font-size:15px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.pkg{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;display:flex;flex-direction:column;transition:.15s}
.pkg:hover{border-color:rgba(88,201,183,.45);transform:translateY(-2px)}
.pkg .top{display:flex;justify-content:space-between;align-items:start}
.pkg h3{font-size:17px;margin:0}.pkg .days{font-size:11.5px;color:var(--muted);border:1px solid var(--line);padding:2px 8px;border-radius:20px}
.pkg .price{font-size:32px;font-weight:800;color:var(--accent);margin:14px 0 4px}
.pkg .price small{font-size:13px;color:var(--muted);font-weight:400}
.pkg .desc{color:var(--muted);font-size:12.5px;min-height:36px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 16px}
.tag{font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}
.tag.p{color:#7fe0d0;border-color:rgba(88,201,183,.45);background:rgba(88,201,183,.08)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:13px}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);font-size:12px;background:var(--card2);font-weight:600}
tr:last-child td{border-bottom:none}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.muted{color:var(--muted);font-size:12.5px}
.st{font-size:11.5px;padding:2px 9px;border-radius:20px;border:1px solid var(--line)}
.st.ok{color:#7fe0d0;border-color:rgba(88,201,183,.5);background:rgba(88,201,183,.1)}
.st.bad{color:var(--danger);border-color:rgba(255,146,152,.4)}
code{background:var(--card2);padding:2px 7px;border-radius:6px;font-size:12px;word-break:break-all}
.empty{padding:26px;text-align:center;color:var(--muted);font-size:13px}
@media(max-width:900px){.landing{grid-template-columns:1fr}.layout{grid-template-columns:1fr}.side{position:static}.feat{grid-template-columns:1fr}.nav .ann{display:none}}

/* 移动端 / 微信 H5 优化 */
*{-webkit-tap-highlight-color:transparent}
body{-webkit-text-size-adjust:100%}
@media(max-width:760px){
  .top{height:auto;flex-wrap:wrap;padding:10px 14px;gap:8px}
  .top .pill{font-size:11px}
  .layout{grid-template-columns:1fr}
  .side{position:static;margin-bottom:12px;padding:10px}
  .side .u{display:none}
  .snav{flex-direction:row;overflow-x:auto;gap:6px;padding-bottom:4px}
  .snav a{white-space:nowrap;padding:8px 12px;background:rgba(255,255,255,.03);border-radius:8px}
  .main{padding:16px 14px}
  h1{font-size:22px}h2{font-size:19px}
  .kpis{grid-template-columns:repeat(2,1fr);gap:10px}
  .card{padding:14px;border-radius:12px}
  .btn{padding:11px 16px}
  input,select{font-size:16px}  /* 避免 iOS 聚焦缩放 */
  table{display:block;overflow-x:auto;white-space:nowrap}
  .landing{padding-top:26px}
  .auth-card{padding:18px}
  footer{padding-bottom:calc(22px + env(safe-area-inset-bottom))}
}
</style></head><body>
<div id="siteMessage" class="site-message" role="status" aria-live="polite" hidden></div>
<div class="nav"><div class="wrap" style="display:flex;align-items:center;gap:14px;width:100%">
  <a class="brand" href="https://ali.moneyfly.club/"><img src="/shop/assets/logo.png" alt="">极光上架 · 授权中心</a>
  <span class="ann" id="ann" style="display:none"></span>
  <div class="sp"></div>
  <span class="muted" id="navUser"></span>
  <a class="btn ghost small" href="https://ali.moneyfly.club/">返回官网</a>
  <button class="btn small" id="navAuth">登录 / 注册</button>
</div></div>

<!-- 未登录 -->
<div class="wrap" id="landing">
  <div class="landing">
    <div>
      <div class="eyebrow">AURORALIST LICENSE CENTER</div>
      <h1>按平台与功能包<br><em>购买，即刻开通</em></h1>
      <p class="lead">同一套软件，按你买的服务点亮平台：Alibaba、Amazon、速卖通各自独立授权。购买后自动下发到软件，换机与续期在线自助完成。</p>
      <div class="feat">
        <div><b>平台级授权</b>买哪个平台，就只解锁哪个平台</div>
        <div><b>功能包可选</b>刊登 / 类目 / AI 文案 / AI 图片 / 报价贸易</div>
        <div><b>自动下发</b>支付成功即签发，软件登录自动绑定</div>
        <div><b>在线自助</b>换机解绑、续费、授权状态随时查</div>
      </div>
    </div>
    <div class="auth-card">
      <div class="auth-tabs"><button type="button" class="auth-tab on" data-mode="login">登录</button><button type="button" class="auth-tab" data-mode="register">注册</button></div>
      <div class="field"><label>邮箱</label><input id="aEmail" autocomplete="username" aria-label="邮箱" placeholder="you@example.com" autocomplete="email"></div>
      <div class="field"><label>密码</label><input id="aPass" autocomplete="current-password" aria-label="登录密码" type="password" placeholder="至少 6 位" autocomplete="current-password"></div>
      <div class="field" id="nameField" style="display:none"><label>称呼（可选）</label><input id="aName" aria-label="称呼" placeholder="公司 / 称呼"></div>
      <div class="field" id="codeField" style="display:none"><label>邮箱验证码</label>
        <div style="display:flex;gap:8px"><input id="aCode" aria-label="邮箱验证码" placeholder="6 位验证码" style="flex:1">
          <button class="btn small" id="aSendCode" type="button">发送验证码</button></div></div>
      <div class="field" id="newPassField" style="display:none"><label>新密码</label><input id="aNewPass" aria-label="新密码" type="password" placeholder="至少 6 位"></div>
      <button class="btn primary" id="aSubmit" style="width:100%;justify-content:center;margin-top:6px">登录</button>
      <div class="err" id="aErr"></div>
      <div class="row" style="margin-top:12px;justify-content:space-between">
        <a class="muted" id="aForgot" style="cursor:pointer;font-size:12.5px">忘记密码？</a>
        <a class="muted" id="aBack" style="cursor:pointer;font-size:12.5px;display:none">返回登录</a>
      </div>
      <div class="hint" id="authHint" style="margin-top:10px">登录后查看平台权限、绑定设备和软件日志。原设备授权继续有效，无需重新注册。</div>
    </div>
  </div>
  <div style="padding:6px 0 46px">
    <h2 style="font-size:24px;margin-bottom:4px">套餐</h2>
    <div class="sub" style="margin-bottom:16px">登录后可直接购买；已有授权自动续期</div>
    <div class="grid" id="landingPkgs"></div>
  </div>
</div>

<!-- 登录后 -->
<div class="wrap" id="app" style="display:none">
  <div class="layout">
    <aside class="side">
      <div class="u"><b id="uEmail">-</b><span id="uSince"></span></div>
      <nav class="snav">
        <a class="on" href="?page=dash" data-p="dash">概览</a>
        <a href="?page=pkgs" data-p="pkgs">套餐购买</a>
        <a href="?page=lic" data-p="lic">我的授权</a>
        <a href="?page=devices" data-p="devices">设备管理</a>
        <a href="?page=invite" data-p="invite">邀请好友</a>
        <a href="?page=status" data-p="status">软件运行状态</a>
        <a href="?page=products" data-p="products">最近商品摘要</a>
<a href="?page=logs" data-p="logs">软件日志</a>
        <a href="?page=orders" data-p="orders">订单记录</a>
        <a href="?page=tickets" data-p="tickets">工单支持</a>
        <a href="?page=help" data-p="help">帮助</a>
      </nav>
      <div style="border-top:1px solid var(--line);margin-top:10px;padding-top:10px">
        <button class="btn small danger" id="logoutBtn" style="width:100%">退出登录</button>
      </div>
    </aside>
    <main>
      <section class="panel on" id="p-dash">
        <div class="card" id="expiryBanner" style="display:none;border-color:rgba(234,192,120,.5)">
          <div class="row"><span class="st bad">到期提醒</span><span id="expiryText" class="muted"></span>
          <a class="btn primary small" style="margin-left:auto" onclick="document.querySelector('.snav a[data-p=\'pkgs\']').click()">去续费</a></div>
        </div>
        <h2>概览</h2><div class="sub" id="dashSub"></div>
        <div class="kpis" id="dashKpis"></div>
        <div class="card"><h3>授权摘要</h3><div id="dashLic" class="muted">加载中…</div></div>
        <div class="card"><h3>最近订单</h3><div id="dashOrders" class="muted">加载中…</div></div>
      </section>
      <section class="panel" id="p-pkgs">
        <h2>套餐购买</h2><div class="sub">购买后自动开通；已有授权会自动续期</div>
        <div class="row" style="margin-bottom:12px"><span class="muted">支付方式</span>
          <select id="payMethod" style="width:160px"><option value="alipay">支付宝</option><option value="wxpay">微信支付</option></select>
          <span class="muted">优惠券可在下单时填写（联系客服获取）</span></div>
        <div class="grid" id="pkgGrid"></div>
      </section>
      <section class="panel" id="p-lic">
        <h2>我的授权</h2><div class="sub">绑定设备后，软件登录会自动拉取授权；每个平台和功能分别计算到期时间</div>
        <div class="card" id="legacyLicCard" hidden></div>
        <div class="card" id="licCard"><div class="muted">加载中…</div></div>
        <div class="card">
          <h3>设备绑定</h3>
          <div class="row"><input id="mcInput" aria-label="软件机器码" placeholder="软件「设置 → 授权」里的机器码" style="width:340px">
            <button class="btn primary small" id="bindBtn">绑定 / 更新</button>
            <button class="btn small" id="unbindBtn">解绑当前设备</button>
            <span class="muted">在设备管理中逐台解绑</span></div>
          <div class="hint" id="bindMsg"></div>
        </div>
        <div class="card"><h3>余额与续费</h3><div class="row"><span id="balanceText">加载中…</span><label class="check-label"><input id="autoRenewChk" type="checkbox">自动续费已购买的套餐</label></div><p class="hint">仅对已支付套餐按周期续费。手动开通的权限不自动扣款。</p></div>
        <div class="card"><h3>授权 Token</h3>
          <div class="row"><button class="btn small" id="copyBtn">复制 Token</button><button class="btn small" id="showBtn">显示 / 隐藏</button></div>
          <code id="tokenBox" style="display:none;margin-top:10px;max-height:150px;overflow:auto"></code>
        </div>
      </section>
      <section class="panel" id="p-logs"><h2>软件日志</h2><div class="sub">仅展示当前账号关联设备上传的日志，支持查看失败原因和处理过程。</div>
        <form id="logSearch" class="row"><label for="logKeyword">文件 / 版本 / 设备</label><input id="logKeyword" name="q" placeholder="输入关键词…"><label class="check-label"><input id="logErrors" type="checkbox">仅有错误</label><button class="btn primary" type="submit">搜索日志</button></form>
        <div class="card" id="userLogList"><div class="empty">加载中…</div></div><div class="row"><button class="btn" id="logPrev">上一页</button><span id="logPageInfo"></span><button class="btn" id="logNext">下一页</button></div><div class="card" id="userLogDetail" hidden><h3 id="userLogTitle"></h3><pre id="userLogContent" class="log-content"></pre></div>
      </section>
      <section class="panel" id="p-status">
        <h2>软件运行状态</h2><div class="sub">软件运行并联网后上报心跳，用于远程了解各平台状态</div>
        <div class="kpis" id="statusKpis"></div>
        <div class="card"><h3>平台明细</h3><div id="statusBox" class="muted">暂无心跳</div></div>
      </section>
      <section class="panel" id="p-products">
        <h2>最近商品摘要</h2><div class="sub">软件最近一次上报的商品摘要，不代表完整刊登历史。完整处理过程可查看软件日志。</div>
        <div id="prodBox"><div class="empty">暂无产品数据，软件上报后会显示</div></div>
      </section>
      <section class="panel" id="p-orders">
        <h2>订单记录</h2><div class="sub">最近 50 笔订单</div>
        <table id="orderTable"><tr><th>订单号</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th></tr></table>
      </section>
      <section class="panel" id="p-devices">
        <h2>设备管理</h2><div class="sub">一个授权可绑定多台设备（按套餐设备数）；解绑后该设备将无法自动拉取授权</div>
        <div class="card"><div id="devList" class="muted">加载中…</div></div>
      </section>
      <section class="panel" id="p-invite">
        <h2>邀请好友</h2><div class="sub">把邀请链接发给朋友，注册时自动记录邀请关系</div>
        <div class="card">
          <div class="row"><b>我的邀请码</b><code id="invCode" style="font-size:16px">-</code>
            <button class="btn small" id="invCopy">复制邀请链接</button>
            <span class="muted">已邀请：<b id="invUses">0</b> 人</span></div>
          <div class="hint">朋友通过你的链接注册后，可在后台统计邀请关系（返佣/奖励规则可后续配置）</div>
        </div>
      </section>
      <section class="panel" id="p-tickets">
        <h2>工单支持</h2><div class="sub">平均 2 小时内回复；紧急问题可联系 QQ 3219904322</div>
        <div class="card">
          <h3>提交工单</h3>
          <div class="row">
            <input id="tkSubject" placeholder="问题标题" style="width:300px">
            <select id="tkCat" style="width:160px">
              <option value="license">授权/激活</option>
              <option value="payment">支付问题</option>
              <option value="software">软件使用</option>
              <option value="other">其他</option>
            </select>
            <input id="tkBody" placeholder="问题描述（可留空）" style="width:420px">
            <button class="btn primary small" id="tkSubmit">提交</button>
          </div>
          <div class="hint" id="tkMsg"></div>
        </div>
        <div class="card"><h3>我的工单</h3><div id="tkList" class="muted">加载中…</div></div>
        <div class="card" id="tkDetailCard" style="display:none"><h3>工单详情</h3><div id="tkDetail"></div></div>
      </section>
      <section class="panel" id="p-help">
        <h2>帮助</h2><div class="sub">常见问题</div>
        <div class="card"><h3>购买后怎么用？</h3><p class="muted">1. 在软件里用本页账号登录（首次启动会弹出登录窗口）；2. 软件自动绑定本机并拉取授权；3. 左侧平台按套餐点亮。</p></div>
        <div class="card"><h3>换电脑怎么办？</h3><p class="muted">在「我的授权 → 解绑当前设备」，再在新电脑登录软件即可（30 天限 1 次）。</p></div>
        <div class="card"><h3>离线能用吗？</h3><p class="muted">可以，授权本地验签，离线 7 天内正常使用；续期后重新登录即可刷新。</p></div>
        <div class="card"><h3>续费怎么算？</h3><p class="muted">购买同平台套餐会自动在现有到期日基础上延长天数。</p></div>
      </section>
    </main>
  </div>
</div>

<footer style="border-top:1px solid var(--line);padding:22px 0;color:var(--muted);font-size:12.5px"><div class="wrap">
  极光上架 AuroraList · 授权中心 · 咨询 QQ 3219904322 ·
  <a href="/terms" style="color:var(--accent)">服务协议</a> ·
  <a href="/privacy" style="color:var(--accent)">隐私政策</a> ·
  <a href="/refund" style="color:var(--accent)">退款政策</a>
</div></footer>

<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
async function api(a,d){
 try{const r=await fetch('/api.php?action='+a,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})});const value=await r.json();if(value.code!==0)notify(value.msg||'操作未完成');return value;}
 catch(error){notify('连接失败，请稍后重试；未确认的操作请先查看结果。');return {code:-1,msg:'连接失败，请重试',data:{}};}
}
function notify(message){const el=document.getElementById('siteMessage');if(el){el.textContent=message;el.hidden=false;}}

const platName={alibaba:'Alibaba 国际站',aliexpress:'速卖通',amazon:'Amazon',tiktok:'TikTok',shopee:'Shopee'};
const featName={listing:'刊登',category:'类目',trade:'价格贸易',pricing:'价格',variants:'变种',ai_text:'AI 文案',ai_image:'AI 图片',audit:'审核',fba:'FBA'};
let ME=null, LIC=null, LEGACY=[], ORDERS=[], AUTH_MODE='login';

/* 站点信息 */
api('site').then(r=>{ if(r.code===0){ const d=r.data||{};
  if(d.name) document.title=d.name.includes('授权中心')?d.name:d.name+' · 授权中心';
  if(d.announcement){ $('#ann').textContent=d.announcement; $('#ann').style.display='inline-block'; } }});

/* 登录/注册 */
function setMode(m){
  AUTH_MODE=m;
  const isLogin=m==='login', isReg=m==='register', isVerify=m==='verify', isReset=m==='reset';
  document.querySelectorAll('.auth-tab').forEach(x=>x.classList.toggle('on',
    (x.dataset.mode==='login'&&isLogin)||(x.dataset.mode==='register'&&isReg)));
  $('#nameField').style.display=isReg?'block':'none';
  $('#codeField').style.display=(isVerify||isReset)?'block':'none';
  $('#newPassField').style.display=isReset?'block':'none';
  $('#aPass').parentElement.style.display=(isVerify||isReset)?'none':'block';
  $('#aSubmit').textContent=isLogin?'登录':isReg?'注册并登录':isVerify?'验证并登录':'重置密码';
  $('#aForgot').style.display=isLogin?'inline':'none';
  $('#aBack').style.display=(isVerify||isReset)?'inline':'none';
  $('#authHint').textContent=isVerify?'验证码已发送到邮箱，10 分钟内有效':
    isReset?'输入邮箱验证码并设置新密码':'登录后查看平台权限、绑定设备和软件日志。原设备授权继续有效，无需重新注册。';
  $('#aErr').textContent='';
}
document.querySelectorAll('.auth-tab').forEach(t=>t.onclick=()=>setMode(t.dataset.mode));
$('#aForgot').onclick=()=>setMode('reset');
$('#aBack').onclick=()=>setMode('login');
$('#aSendCode').onclick=async()=>{
  const email=$('#aEmail').value.trim();
  if(!email){$('#aErr').textContent='先填邮箱';return;}
  const purpose=AUTH_MODE==='reset'?'reset':'verify';
  const r=await api('resend_code',{email,purpose});
  if(r.code!==0){$('#aErr').textContent=r.msg;return;}
  $('#aErr').style.color='var(--accent)';
  $('#aErr').textContent=r.data?.debug_code?('验证码（调试）：'+r.data.debug_code):'验证码已发送，请查收邮件';
};
$('#aSubmit').onclick=async()=>{
  const email=$('#aEmail').value.trim();
  if(!email){$('#aErr').textContent='请填写邮箱';return;}
  let r;
  if(AUTH_MODE==='login'){ r=await api('login',{email,password:$('#aPass').value}); }
  else if(AUTH_MODE==='register'){ const ref=new URLSearchParams(location.search).get('ref')||''; r=await api('register',{email,password:$('#aPass').value,name:$('#aName').value.trim(),invite_code:ref}); }
  else if(AUTH_MODE==='verify'){ r=await api('verify_email',{email,code:$('#aCode').value.trim()}); }
  else { r=await api('reset_password',{email,code:$('#aCode').value.trim(),password:$('#aNewPass').value}); }
  if(r.code!==0){$('#aErr').style.color='var(--danger)';$('#aErr').textContent=r.msg;return;}
  if(r.data?.need_verify){ setMode('verify'); if(r.data.debug_code){$('#aErr').style.color='var(--accent)';$('#aErr').textContent='验证码（调试）：'+r.data.debug_code;} return; }
  if(AUTH_MODE==='reset'){ setMode('login'); $('#aErr').style.color='var(--accent)'; $('#aErr').textContent='密码已重置，请登录'; return; }
  $('#aErr').textContent=''; await boot();
};
setMode('login');
$('#aPass').addEventListener('keydown',e=>{ if(e.key==='Enter') $('#aSubmit').click(); });

$('#navAuth').onclick=()=>{ if(ME){ logout(); } else { $('#landing').scrollIntoView({behavior:'smooth'}); } };
$('#logoutBtn').onclick=()=>logout();
async function logout(){ await api('logout'); ME=null; renderAuth(); }

function renderAuth(){
  const logged=!!ME;
  $('#landing').style.display=logged?'none':'block';
  $('#app').style.display=logged?'block':'none';
  $('#navAuth').textContent=logged?'退出登录':'登录 / 注册';
  $('#navUser').textContent=logged?ME.email:'';
  if(!logged){ loadPublicPkgs(); }
}
/* 未登录预览套餐 */
async function loadPublicPkgs(){
  const r=await api('packages'); const list=r.data||[];
  const g=document.getElementById('landingPkgs'); if(!g) return list;
  g.innerHTML=list.map(p=>{
    const pl=(p.platforms||[]).map(x=>`<span class="tag p">${platName[x]||x}</span>`).join('');
    const ft=(p.features||[]).map(x=>`<span class="tag">${featName[x]||x}</span>`).join('');
    return `<div class="pkg"><div class="top"><h3>${esc(p.name)}</h3><span class="days">${p.days} 天</span></div>
      <div class="price">¥${p.price}<small> / 起</small></div><div class="desc">${esc(p.description||'')}</div>
      <div class="tags">${pl}${ft}</div>
      <button class="btn primary" data-loginbuy="${p.id}" style="margin-top:auto">登录后购买</button></div>`;
  }).join('');
  g.querySelectorAll('[data-loginbuy]').forEach(b=>b.onclick=()=>{
    document.querySelector('.auth-card')?.scrollIntoView({behavior:'smooth',block:'center'});
    document.getElementById('aEmail')?.focus();
  });
  return list;
}

/* ---------- 登录后 ---------- */
async function showPanel(panel,updateURL=true){
 const banner=document.getElementById('siteMessage');if(banner)banner.hidden=true;
 const target=document.querySelector('.snav a[data-p="'+panel+'"]');if(!target)panel='dash';
 document.querySelectorAll('.snav a').forEach(x=>{const on=x.dataset.p===panel;x.classList.toggle('on',on);if(on)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');});
 document.querySelectorAll('.panel').forEach(x=>x.classList.toggle('on',x.id==='p-'+panel));
 if(updateURL)window.scrollTo({top:0,behavior:'instant'});
 if(updateURL){const url=new URL(location.href);url.searchParams.set('page',panel);history.pushState({},'',url);}
 if(panel==='lic'){const r=await api('me');if(r.code===0){LIC=r.data.license;LEGACY=r.data.legacy_licenses||[];renderLicense();}}
 if(panel==='status'){await refreshStatus();}if(panel==='products')renderProducts();if(panel==='orders')renderOrders();
 if(panel==='devices')loadDevices();if(panel==='invite')loadInvite();if(panel==='tickets')loadTickets();if(panel==='logs')loadUserLogs(1);
}
document.querySelectorAll('.snav a').forEach(a=>a.onclick=e=>{e.preventDefault();showPanel(a.dataset.p);});
window.addEventListener('popstate',()=>{if(ME)showPanel(new URLSearchParams(location.search).get('page')||'dash',false);});

async function boot(){
  const me=await api('me'); if(me.code!==0)return; ME=me.data.user; LIC=me.data.license; LEGACY=me.data.legacy_licenses||[];
  const hint=me.data.expiry_hint||'';
  const eb=$('#expiryBanner');
  if(eb){ if(hint){ eb.style.display='block'; $('#expiryText').textContent=hint; } else { eb.style.display='none'; } }
  renderAuth(); if(!ME) return;
  $('#uEmail').textContent=ME.email;
  $('#uSince').textContent='注册于 '+new Date(ME.created_at*1000).toLocaleDateString();
  const pk=await api('packages'); renderPackages(pk.data||[]);
  const od=await api('orders'); ORDERS=od.data||[];
  await refreshStatus();
  renderDash();
  await showPanel(new URLSearchParams(location.search).get('page')||'dash',false);
}

function renderPackages(list){
  const g=$('#pkgGrid'); if(!g) return;
  g.innerHTML=(list||[]).map(p=>{
    const pl=(p.platforms||[]).map(x=>`<span class="tag p">${platName[x]||x}</span>`).join('');
    const ft=(p.features||[]).map(x=>`<span class="tag">${featName[x]||x}</span>`).join('');
    return `<div class="pkg"><div class="top"><h3>${esc(p.name)}</h3><span class="days">${p.days} 天</span></div>
      <div class="price">¥${p.price}<small> / 起</small></div>
      <div class="desc">${esc(p.description||'')}</div>
      <div class="tags">${pl}${ft}</div>
      <button class="btn primary" data-buy="${p.id}" style="margin-top:auto">购买 / 续费</button></div>`;
  }).join('');
  g.querySelectorAll('[data-buy]').forEach(b=>b.onclick=()=>buy(+b.dataset.buy));
}
async function buy(pid){
  if(!ME){ alert('请先登录'); return; }
  const payType=(document.getElementById('payMethod')||{}).value||'alipay';
  const r=await api('order_create',{package_id:pid,pay_type:payType});
  if(r.code!==0){ alert(r.msg); return; }
  const d=r.data;
  if(d.pay_url){ location.href=d.pay_url; return; }
  if(d.mock_pay){
    if(!confirm('当前未配置易支付，使用模拟支付开通？（仅测试）')) return;
    const m=await api('order_mock_pay',{order_id:d.order_id});
    if(m.code!==0){ alert(m.msg); return; }
    alert('已开通'); await boot();
  } else {
    alert('下单成功，但未配置支付网关，请联系管理员处理订单 '+d.trade_no);
  }
}

function daysLeft(exp){
  if(!exp) return 0;
  return Math.ceil((new Date(exp+'T23:59:59')-new Date())/86400000);
}
function renderDash(){
  const exp=LIC?.expires_at, left=daysLeft(exp);
  $('#dashSub').textContent = LIC ? `授权到期 ${exp}，还有 ${left>0?left:0} 天` : '还没有开通套餐，去「套餐购买」选择适合你的服务';
  const prods=(window.__HB?.payload?.products||[]);
  $('#dashKpis').innerHTML=[
    ['剩余天数', left>0?left:0], ['已开通平台', Object.keys(LIC?.platforms||{}).length],
    ['订单数', ORDERS.length], ['刊登产品', prods.length],
  ].map(([k,v])=>`<div class="kpi"><b>${v}</b><span>${k}</span></div>`).join('');
  $('#dashLic').innerHTML=LIC? `平台：${Object.entries(LIC.platforms||{}).map(([k,v])=>`<span class="tag p" style="margin-right:4px">${platName[k]||k}</span>`).join('')}
    <div style="margin-top:8px">机器码：<code>${esc(LIC.machine_code||'未绑定')}</code></div>` : '<span class="muted">未开通</span>';
  $('#dashOrders').innerHTML=ORDERS.slice(0,3).map(o=>`<div class="row" style="border-bottom:1px solid var(--line);padding:6px 0">
    <span>${esc(o.trade_no)}</span><span class="muted">${esc(o.name||'')}</span>
    <span class="st ${o.status==='paid'?'ok':'bad'}">${o.status==='paid'?'已支付':'待支付'}</span>
    <span class="muted" style="margin-left:auto">¥${(o.amount_cents/100).toFixed(2)}</span></div>`).join('')||'<span class="muted">暂无订单</span>';
}
function renderLicense(){
  const old=$('#legacyLicCard');old.hidden=!LEGACY.length;old.innerHTML='<h3>原设备授权（独立保留）</h3>'+LEGACY.map(x=>`<div class="right-line"><code>${esc(x.identity)}</code><span>${esc(x.expires_at)} · ${x.status==='active'?'原设备授权':esc(x.status)}</span></div>`).join('');
  const c=$('#licCard');
  if(!LIC){ c.innerHTML=`<div class="empty">还没有授权。去「套餐购买」选择服务，支付后自动开通。</div>`; $('#tokenBox').textContent=''; return; }
  const left=daysLeft(LIC.expires_at);
  const featureDates=LIC.feature_expiries||{};
  c.innerHTML=`<div class="section-title"><h3>已开通的平台与功能</h3><span class="muted">最近权限变更：${esc(LIC.next_change_at||LIC.expires_at)}</span></div><div class="rights-grid">${Object.entries(LIC.platforms||{}).map(([pid,fs])=>`<article class="right-card"><h3>${esc(platName[pid]||pid)}</h3>${fs.map(f=>`<div class="right-line"><span>${esc(featName[f]||f)}</span><time>${esc(featureDates[pid]?.[f]||LIC.expires_at)}</time></div>`).join('')}</article>`).join('')}</div><p class="hint">已绑定设备：${esc(LIC.machine_code||'未绑定，请在软件中用邮箱登录，或在下方填写机器码。')}</p>`;
  $('#mcInput').value=LIC.machine_code||'';
  api('balance_info').then(r=>{ const d=r.data||{}; $('#balanceText').textContent='¥'+(d.balance||0).toFixed(2);
    $('#autoRenewChk').checked=!!d.auto_renew; });
  $('#autoRenewChk').onchange=async(e)=>{const input=e.target;const r=await api('auto_renew_set',{on:input.checked?'1':'0'});if(r.code!==0)input.checked=!input.checked;};
  api('license').then(r=>{ $('#tokenBox').textContent=r.data?.token||'请先绑定设备，再获取授权 Token。'; });
}
function renderOrders(){
  const t=$('#orderTable');
  t.innerHTML='<tr><th>订单号</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th><th>操作</th></tr>'+
    (ORDERS||[]).map(o=>`<tr><td>${esc(o.trade_no)}</td><td>${esc(o.name||'')}</td>
      <td>¥${(o.amount_cents/100).toFixed(2)}</td>
      <td><span class="st ${o.status==='paid'?'ok':'bad'}">${o.status==='paid'?'已支付':(o.status==='refunded'?'已退款':'待支付')}</span></td>
      <td>${new Date(o.created_at*1000).toLocaleString()}</td>
      <td>${o.status==='pending'?'<a style="color:var(--accent);cursor:pointer" data-pay="'+o.id+'">继续支付</a>':'-'}</td></tr>`).join('');
  document.querySelectorAll('[data-pay]').forEach(a=>a.onclick=async()=>{
    const r=await api('order_pay_url',{order_id:+a.dataset.pay});
    if(r.code!==0){alert(r.msg);return;}
    location.href=r.data.pay_url;
  });
}
async function loadDevices(){
  const r=await api('devices'); const list=r.data||[];
  $('#devList').innerHTML=list.length? `<table><tr><th>授权ID</th><th>平台</th><th>机器码</th><th>到期</th><th>操作</th></tr>`+
    list.map(d=>`<tr><td>${d.id}</td><td>${Object.keys(d.platforms||{}).map(k=>platName[k]||k).join(',')}</td>
      <td><code>${esc(d.machine_code)}</code></td><td>${esc(d.expires_at)}</td>
      <td><a style="color:var(--danger);cursor:pointer" data-dev="${d.id}">解绑</a></td></tr>`).join('')+'</table>'
    : '暂未绑定设备（软件登录后自动绑定）';
  document.querySelectorAll('[data-dev]').forEach(a=>a.onclick=async()=>{
    if(!confirm('解绑该设备？')) return;
    const x=await api('device_unbind',{license_id:+a.dataset.dev});
    if(x.code!==0){alert(x.msg);return;}
    await boot(); loadDevices();
  });
}
async function loadInvite(){
  const r=await api('invite_info'); const d=r.data||{};
  $('#invCode').textContent=d.code||'-'; $('#invUses').textContent=d.uses||0;
}
$('#invCopy') && ($('#invCopy').onclick=async()=>{
  const code=$('#invCode').textContent.trim();
  const link='https://license.moneyfly.club/?ref='+encodeURIComponent(code);
  try{ await navigator.clipboard.writeText(link); alert('邀请链接已复制：'+link); }
  catch(e){ alert('邀请链接：'+link); }
});
async function loadTickets(){
  const r=await api('tickets'); const list=r.data||[];
  $('#tkList').innerHTML=list.length? `<table><tr><th>ID</th><th>标题</th><th>分类</th><th>状态</th><th>时间</th><th>操作</th></tr>`+
    list.map(t=>`<tr><td>${t.id}</td><td>${esc(t.subject)}</td><td>${esc(t.category)}</td>
      <td>${t.status==='open'?'待回复':(t.status==='replied'?'已回复':'已关闭')}</td>
      <td>${new Date(t.created_at*1000).toLocaleDateString()}</td>
      <td><a style="color:var(--accent);cursor:pointer" data-tk="${t.id}">查看</a></td></tr>`).join('')+'</table>'
    : '暂无工单';
  document.querySelectorAll('[data-tk]').forEach(a=>a.onclick=()=>showTicket(+a.dataset.tk));
}
async function showTicket(id){
  const r=await api('ticket_detail',{ticket_id:id}); if(r.code!==0){alert(r.msg);return;}
  const d=r.data; $('#tkDetailCard').style.display='block';
  $('#tkDetail').innerHTML=`<div class="muted">#${d.ticket.id} ${esc(d.ticket.subject)}</div>`+
    (d.replies||[]).map(x=>`<div style="margin:8px 0;padding:10px;border:1px solid var(--line);border-radius:8px">
      <div class="muted">${x.is_admin==1?'客服':'我'} · ${new Date(x.created_at*1000).toLocaleString()}</div>
      <div style="white-space:pre-wrap">${esc(x.body)}</div></div>`).join('');
}
$('#tkSubmit') && ($('#tkSubmit').onclick=async()=>{
  const r=await api('ticket_create',{subject:$('#tkSubject').value.trim(),category:$('#tkCat').value,body:$('#tkBody').value.trim()});
  $('#tkMsg').textContent=r.code===0?'工单已提交':'提交失败：'+r.msg;
  $('#tkMsg').style.color=r.code===0?'var(--accent)':'var(--danger)';
  if(r.code===0){ loadTickets(); }
});
async function refreshStatus(){
  const r=await api('status'); window.__HB=r.data?.heartbeat||null;
  renderStatus(); renderProducts(); renderDash();
}
function renderStatus(){
  const hb=window.__HB; const k=$('#statusKpis'), b=$('#statusBox'); if(!k) return;
  if(!hb){ k.innerHTML=''; b.textContent='暂无心跳数据（软件运行并联网后上报）'; return; }
  const p=hb.payload||{}; const plats=p.platforms||{};
  k.innerHTML=`<div class="kpi"><b class="sm">${new Date(hb.ts*1000).toLocaleString()}</b><span>最近心跳</span></div>`+
    Object.entries(plats).map(([k2,v])=>`<div class="kpi"><b class="sm">${platName[k2]||k2}</b><span>${esc(v.status||'运行中')} · 今日 ${v.today??'-'}</span></div>`).join('');
  b.innerHTML=`<table><tr><th>平台</th><th>状态</th><th>今日处理</th></tr>`+
    Object.entries(plats).map(([k2,v])=>`<tr><td>${platName[k2]||k2}</td><td>${esc(v.status||'-')}</td><td>${v.today??'-'}</td></tr>`).join('')+
    `</table>`;
}
function renderProducts(){
  const hb=window.__HB; const box=$('#prodBox'); if(!box) return;
  const prods=hb?.payload?.products||[];
  box.innerHTML=prods.length? `<table><tr><th>平台</th><th>商品名称</th><th>状态</th><th>更新时间</th></tr>`+
    prods.map(x=>`<tr><td>${platName[x.platform]||esc(x.platform)}</td><td>${esc(x.title)}</td><td>${esc(x.status)}</td><td>${esc(x.updated||'')}</td></tr>`).join('')+`</table>`
    : '<div class="empty">暂无产品数据，软件上报后会显示</div>';
}

/* 授权操作 */
$('#bindBtn') && ($('#bindBtn').onclick=async()=>{
  const r=await api('bind_machine',{machine_code:$('#mcInput').value.trim()});
  $('#bindMsg').textContent=r.code===0?'已绑定':'绑定失败：'+r.msg;
  $('#bindMsg').style.color=r.code===0?'var(--accent)':'var(--danger)';
  await boot();
});
$('#unbindBtn') && ($('#unbindBtn').onclick=async()=>{
  if(!confirm('解绑后当前电脑将无法自动拉取授权，确定？')) return;
  const machine=$('#mcInput').value.trim();if(!machine){notify('请先在设备管理中选择已绑定设备');return;}
  const r=await api('unbind',{machine_code:machine}); if(r.code!==0){alert(r.msg);return;}
  alert('已解绑，可在新电脑登录软件自动绑定'); await boot();
});
$('#copyBtn') && ($('#copyBtn').onclick=async()=>{
  try{ await navigator.clipboard.writeText($('#tokenBox').textContent); alert('Token 已复制'); }
  catch(e){ $('#tokenBox').style.display='block'; alert('请手动选择复制'); }
});
$('#showBtn') && ($('#showBtn').onclick=()=>{
  const t=$('#tokenBox'); t.style.display = t.style.display==='none'?'block':'none';
});

let userLogPage=1,userLogPages=1;
async function loadUserLogs(n){
 const r=await api('logs',{page:n,q:$('#logKeyword').value.trim(),errors:$('#logErrors').checked});if(r.code!==0)return;
 const d=r.data;userLogPage=d.page;userLogPages=d.pages;$('#logPageInfo').textContent=`第 ${d.page} / ${d.pages} 页，共 ${d.total} 条`;
 $('#logPrev').disabled=d.page<=1;$('#logNext').disabled=d.page>=d.pages;
 $('#userLogList').innerHTML=d.rows.length?`<div class="table-scroll"><table><thead><tr><th>上传时间</th><th>文件 / 版本</th><th>错误</th><th>操作</th></tr></thead><tbody>${d.rows.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.filename)}<div class="muted">${esc(x.client_version)}</div></td><td>${x.error_count}</td><td><button class="btn small" data-log="${x.id}">查看日志</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">暂无关联日志。账号绑定设备后，软件成功上传的日志会显示在这里。</div>';
 $('#userLogList').querySelectorAll('[data-log]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=await api('log_detail',{id:+b.dataset.log});if(r.code===0){$('#userLogTitle').textContent=r.data.filename+' · '+r.data.created_at;$('#userLogContent').textContent=r.data.content;$('#userLogDetail').hidden=false;}}finally{b.disabled=false;}});
}
$('#logSearch').onsubmit=e=>{e.preventDefault();loadUserLogs(1);};$('#logPrev').onclick=()=>{if(userLogPage>1)loadUserLogs(userLogPage-1);};$('#logNext').onclick=()=>{if(userLogPage<userLogPages)loadUserLogs(userLogPage+1);};
// Associate visible field labels even in older preserved forms.
document.querySelectorAll('input[id],select[id],textarea[id]').forEach(input=>{const label=input.parentElement.querySelector('label');if(label&&!label.hasAttribute('for')&&!label.contains(input))label.htmlFor=input.id;});
boot();
</script></body></html>
