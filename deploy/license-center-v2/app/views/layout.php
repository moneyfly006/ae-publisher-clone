<?php /** @var string $content */ ?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= e($title ?? '授权中心') ?> · AuroraList 极光上架</title>
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/admin.css?v=6">
</head>
<body>
<a class="skip-link" href="#main-content">跳到主要内容</a>
<?php $admin = current_admin(); ?>
<?php if ($admin): ?>
<div class="shell">
  <aside class="side" id="admin-side">
    <div class="brand">
      <span class="logo">A</span>
      <div>
        <div class="brand-name">AuroraList 极光上架</div>
        <div class="brand-sub">客户运行支持中心</div>
      </div>
    </div>
    <?php $p = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/'; ?>
    <nav class="nav" aria-label="后台功能">
<div class="nav-group"><span class="nav-heading">工作台</span>
<a class="<?= ($p === '/admin' || ('/admin' !== '/admin' && strpos($p, '/admin/') === 0)) ? 'on' : '' ?>" href="/admin"><span class="nav-dot" aria-hidden="true"></span>问题分析</a>
<a class="<?= ($p === '/admin/customers' || ('/admin/customers' !== '/admin' && strpos($p, '/admin/customers/') === 0)) ? 'on' : '' ?>" href="/admin/customers"><span class="nav-dot" aria-hidden="true"></span>客户目录</a>
<a class="<?= ($p === '/admin/events' || ('/admin/events' !== '/admin' && strpos($p, '/admin/events/') === 0)) ? 'on' : '' ?>" href="/admin/events"><span class="nav-dot" aria-hidden="true"></span>运行事件</a>
</div>
<div class="nav-group"><span class="nav-heading">客户与授权</span>
<a class="<?= ($p === '/admin/shop-users' || ('/admin/shop-users' !== '/admin' && strpos($p, '/admin/shop-users/') === 0)) ? 'on' : '' ?>" href="/admin/shop-users"><span class="nav-dot" aria-hidden="true"></span>套餐用户</a>
<a class="<?= ($p === '/admin/shop-licenses' || ('/admin/shop-licenses' !== '/admin' && strpos($p, '/admin/shop-licenses/') === 0)) ? 'on' : '' ?>" href="/admin/shop-licenses"><span class="nav-dot" aria-hidden="true"></span>套餐授权记录</a>
<a class="<?= ($p === '/admin/licenses' || ('/admin/licenses' !== '/admin' && strpos($p, '/admin/licenses/') === 0)) ? 'on' : '' ?>" href="/admin/licenses"><span class="nav-dot" aria-hidden="true"></span>原设备授权码</a>
</div>
<div class="nav-group"><span class="nav-heading">日志与设备</span>
<a class="<?= ($p === '/admin/customer-logs' || ('/admin/customer-logs' !== '/admin' && strpos($p, '/admin/customer-logs/') === 0)) ? 'on' : '' ?>" href="/admin/customer-logs"><span class="nav-dot" aria-hidden="true"></span>套餐客户日志</a>
<a class="<?= ($p === '/admin/logs' || ('/admin/logs' !== '/admin' && strpos($p, '/admin/logs/') === 0)) ? 'on' : '' ?>" href="/admin/logs"><span class="nav-dot" aria-hidden="true"></span>全部软件日志</a>
<a class="<?= ($p === '/admin/customer-devices' || ('/admin/customer-devices' !== '/admin' && strpos($p, '/admin/customer-devices/') === 0)) ? 'on' : '' ?>" href="/admin/customer-devices"><span class="nav-dot" aria-hidden="true"></span>套餐设备</a>
<a class="<?= ($p === '/admin/userdata' || ('/admin/userdata' !== '/admin' && strpos($p, '/admin/userdata/') === 0)) ? 'on' : '' ?>" href="/admin/userdata"><span class="nav-dot" aria-hidden="true"></span>原设备详情</a>
</div>
<div class="nav-group"><span class="nav-heading">交易与服务</span>
<a class="<?= ($p === '/admin/shop-orders' || ('/admin/shop-orders' !== '/admin' && strpos($p, '/admin/shop-orders/') === 0)) ? 'on' : '' ?>" href="/admin/shop-orders"><span class="nav-dot" aria-hidden="true"></span>订单履约</a>
<a class="<?= ($p === '/admin/shop-packages' || ('/admin/shop-packages' !== '/admin' && strpos($p, '/admin/shop-packages/') === 0)) ? 'on' : '' ?>" href="/admin/shop-packages"><span class="nav-dot" aria-hidden="true"></span>套餐配置</a>
<a class="<?= ($p === '/admin/shop-tickets' || ('/admin/shop-tickets' !== '/admin' && strpos($p, '/admin/shop-tickets/') === 0)) ? 'on' : '' ?>" href="/admin/shop-tickets"><span class="nav-dot" aria-hidden="true"></span>客户工单</a>
<a class="<?= ($p === '/admin/shop-reports' || ('/admin/shop-reports' !== '/admin' && strpos($p, '/admin/shop-reports/') === 0)) ? 'on' : '' ?>" href="/admin/shop-reports"><span class="nav-dot" aria-hidden="true"></span>经营报表</a>
<a class="<?= ($p === '/admin/shop-coupons' || ('/admin/shop-coupons' !== '/admin' && strpos($p, '/admin/shop-coupons/') === 0)) ? 'on' : '' ?>" href="/admin/shop-coupons"><span class="nav-dot" aria-hidden="true"></span>优惠券</a>
<a class="<?= ($p === '/admin/shop-invites' || ('/admin/shop-invites' !== '/admin' && strpos($p, '/admin/shop-invites/') === 0)) ? 'on' : '' ?>" href="/admin/shop-invites"><span class="nav-dot" aria-hidden="true"></span>邀请记录</a>
</div>
<div class="nav-group"><span class="nav-heading">系统与扩展</span>
<a class="<?= ($p === '/admin/admins' || ('/admin/admins' !== '/admin' && strpos($p, '/admin/admins/') === 0)) ? 'on' : '' ?>" href="/admin/admins"><span class="nav-dot" aria-hidden="true"></span>管理员</a>
<a class="<?= ($p === '/admin/audit' || ('/admin/audit' !== '/admin' && strpos($p, '/admin/audit/') === 0)) ? 'on' : '' ?>" href="/admin/audit"><span class="nav-dot" aria-hidden="true"></span>操作审计</a>
<a class="<?= lc_is_settings_request() ? 'on' : '' ?>" href="/admin/settings"><span class="nav-dot" aria-hidden="true"></span>系统设置</a>
</div>
<a href="/" target="_blank" rel="noopener">打开用户端 ↗</a><a href="/admin/legacy-dashboard">原统计报表</a>
    </nav>
    <div class="side-foot">
      <div class="who"><?= e($admin['username']) ?></div>
      <form method="post" action="/admin/logout">
        <?= csrf_field() ?>
        <button class="link" type="submit">退出登录</button>
      </form>
    </div>
  </aside>
  <main class="main" id="main-content">
    <?php $section='客户运行支持';$intro='保留原业务入口，统一管理客户、授权与软件问题。';
$descriptions=[
'/admin'=>['排障工作台','按客户、设备、平台与版本分析运行问题，事件与原始日志互相关联。'],
'/admin/customers'=>['客户与授权','统一查找原设备客户和套餐账号，授权数据各自保留。'],
'/admin/licenses'=>['原设备授权码','原授权码验证与签名规则保留；继续支持新增、编辑、续期、停用、吊销与删除。'],
'/admin/shop-users'=>['套餐客户','集中查看账号的有效权限、设备与日志，支持追加权限和账号管理。'],
'/admin/shop-licenses'=>['套餐授权记录','保留每次历史开通记录。当前有效权限在套餐用户详情中按平台和功能计算。'],
'/admin/logs'=>['软件日志','查看原始软件日志、版本和错误分类，按设备筛选后继续分析具体问题。'],
'/admin/customer-logs'=>['客户软件日志','按已确认归属的设备关联套餐账号日志，保留原上传内容与排查入口。'],
'/admin/events'=>['运行分析','按发生时间检索去重分析事件，明确区分日志识别和客户端上报。'],
'/admin/issues'=>['问题排查','查看受影响设备、版本与日志样本，记录复现步骤和修复进展。'],
'/admin/userdata'=>['原设备运行数据','保留原设备详情、日志、使用事件与管理操作。'],
'/admin/customer-devices'=>['账号设备','查看套餐账号的绑定设备。设备归属与授权开通记录独立保存。'],
'/admin/shop-orders'=>['交易履约','查询订单、支付状态及授权履约记录。重试确认不会重复签发。'],
'/admin/shop-packages'=>['套餐配置','管理平台、功能、价格、有效天数和设备数量；已购买订单保留套餐快照。'],
'/admin/shop-tickets'=>['客户支持','结合客户描述与软件日志处理问题，保留回复和工单状态。'],
'/admin/shop-reports'=>['经营数据','金额与订单统计来自业务数据库，与软件运行事件统计分别展示。'],
'/admin/shop-coupons'=>['营销配置','管理优惠券类型、使用限制、有效期和启用状态。'],
'/admin/shop-invites'=>['营销记录','查询邀请归属和使用记录，不改变客户授权。'],
'/admin/platforms'=>['系统扩展','统一平台能力定义，保留原接口并为新增平台提供版本化接入契约。'],
'/admin/admins'=>['访问管理','保留管理员、角色、密码重置与停用管理。'],
'/admin/audit'=>['操作审计','追溯管理员操作，区分授权变更、客户处理与问题排查记录。'],
'/admin/shop-pay'=>['服务配置','支付与邮件参数集中管理，配置变更与客户授权分开处理。'],
'/admin/settings'=>['系统配置','按用途管理系统参数；每个分区独立保存，可随时切换查看其他设置。'],
'/admin/security'=>['账号安全','管理登录保护与双重验证。'],
'/admin/rules'=>['识别规则','查看原规则与待补充样本；日志推断不等于平台的最终发布结果。'],
'/admin/legacy-dashboard'=>['原统计','保留原统计视图及全部入口；历史识别事件可能含重复上传。']];
foreach($descriptions as $prefix=>$meta){if($p===$prefix || ($prefix!=='/admin' && strpos($p,$prefix.'/')===0)){[$section,$intro]=$meta;}}
?>
    <header class="workspace-header"><div class="header-primary"><button type="button" class="menu-toggle btn ghost sm" aria-controls="admin-side" aria-expanded="false">菜单</button><a class="workspace-home" href="/admin">管理工作台</a><span class="breadcrumb-separator">/</span><span><?= e($section) ?></span></div><form class="global-search" method="get" action="/admin/customers"><label class="sr-only" for="admin-search">搜索客户或设备</label><input id="admin-search" type="search" name="q" placeholder="查找客户或授权码…"><button type="submit" aria-label="搜索客户">搜索</button></form><span class="header-date"><?= e(date('Y.m.d')) ?></span></header>
    <section class="page-heading"><div><h1><?= e($title ?? '') ?></h1><p><?= e($intro) ?></p></div><span class="live-badge"><i aria-hidden="true"></i>旧授权持续兼容</span></section>
    <?php foreach (flash_take() as $f): ?>
      <div class="flash <?= e($f['type']) ?>"><?= e($f['msg']) ?></div>
    <?php endforeach; ?>
    <?php if(function_exists('lc_is_settings_request')&&lc_is_settings_request()): ?><nav class="settings-tabs" aria-label="系统设置分区"><?php foreach(lc_settings_sections() as $key=>$label): if(in_array($key,['payment','mail','portal','security'],true)&&(current_admin()['role'] ?? '')!=='owner')continue; ?><a href="/admin/settings?section=<?= e($key) ?>" class="<?= lc_settings_section()===$key?'active':'' ?>" <?= lc_settings_section()===$key?'aria-current="page"':'' ?>><?= e($label) ?></a><?php endforeach; ?></nav><?php endif; ?>
    <div class="workspace-body">
    <?= $content ?>
    </div>
  </main>
</div>
<?php else: ?>
  <?= $content ?>
<?php endif; ?>
<script>
document.querySelector('.menu-toggle')?.addEventListener('click',function(){ const on=document.body.classList.toggle('menu-open');this.setAttribute('aria-expanded',String(on)); });
document.querySelectorAll('[data-reveal]').forEach(button=>button.addEventListener('click',()=>{const input=document.getElementById(button.dataset.reveal);if(!input)return;const show=input.type==='password';input.type=show?'text':'password';button.textContent=show?'隐藏':'显示';button.setAttribute('aria-pressed',String(show));}));
document.querySelectorAll('label').forEach((label,i)=>{const control=label.nextElementSibling;if(control&&['INPUT','SELECT','TEXTAREA'].includes(control.tagName)&&!label.htmlFor){control.id=control.id||'field-'+i;label.htmlFor=control.id;}});
document.querySelectorAll('table').forEach(t=>{if(!t.parentElement.classList.contains('table-scroll')){const wrap=document.createElement('div');wrap.className='table-scroll';t.parentNode.insertBefore(wrap,t);wrap.appendChild(t);}});

function toggleAll(el, formId) {
  var f = document.getElementById(formId);
  if (!f) return;
  f.querySelectorAll('input[name="ids[]"]').forEach(function (c) { c.checked = el.checked; });
}
function bulkDelete(formId) {
  var f = document.getElementById(formId);
  if (!f) return;
  var n = f.querySelectorAll('input[name="ids[]"]:checked').length;
  if (!n) { alert('请先勾选要删除的行'); return; }
  if (!confirm('确定删除选中的 ' + n + ' 条？删除后不可恢复。')) return;
  f.submit();
}
</script>
</body>
</html>
