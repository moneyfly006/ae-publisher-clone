<?php /** @var string $content */ ?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= e($title ?? '授权中心') ?> · <?= e(cfg('brand')) ?></title>
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
<?php $admin = current_admin(); ?>
<?php if ($admin): ?>
<div class="shell">
  <aside class="side">
    <div class="brand">
      <span class="logo">AE</span>
      <div>
        <div class="brand-name"><?= e(cfg('brand')) ?></div>
        <div class="brand-sub">授权中心</div>
      </div>
    </div>
    <?php $p = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/'; ?>
    <nav class="nav">
      <a class="<?= $p === '/admin' ? 'on' : '' ?>" href="/admin">控制台</a>
      <a class="<?= strpos($p, '/admin/licenses') === 0 ? 'on' : '' ?>" href="/admin/licenses">授权管理</a>
      <a class="<?= strpos($p, '/admin/userdata') === 0 ? 'on' : '' ?>" href="/admin/userdata">用户数据</a>
      <a class="<?= strpos($p, '/admin/logs') === 0 ? 'on' : '' ?>" href="/admin/logs">错误日志</a>
      <a class="<?= $p === '/admin/audit' ? 'on' : '' ?>" href="/admin/audit">操作日志</a>
      <a class="<?= $p === '/admin/settings' ? 'on' : '' ?>" href="/admin/settings">系统设置</a>
      <a class="<?= strpos($p, '/admin/shop-packages') === 0 ? 'on' : '' ?>" href="/admin/shop-packages">套餐管理</a>
      <a class="<?= strpos($p, '/admin/shop-pay') === 0 ? 'on' : '' ?>" href="/admin/shop-pay">支付设置</a>
      <a class="<?= strpos($p, '/admin/shop-orders') === 0 ? 'on' : '' ?>" href="/admin/shop-orders">套餐订单</a>
      <a class="<?= strpos($p, '/admin/shop-users') === 0 ? 'on' : '' ?>" href="/admin/shop-users">套餐用户</a>
      <a class="<?= strpos($p, '/admin/shop-licenses') === 0 ? 'on' : '' ?>" href="/admin/shop-licenses">套餐授权</a>
      <a href="/" target="_blank">用户端</a>
    </nav>
    <div class="side-foot">
      <div class="who"><?= e($admin['username']) ?></div>
      <form method="post" action="/admin/logout">
        <?= csrf_field() ?>
        <button class="link" type="submit">退出登录</button>
      </form>
    </div>
  </aside>
  <main class="main">
    <header class="top">
      <h1><?= e($title ?? '') ?></h1>
      <div class="top-meta">v<?= e(cfg('version')) ?> · <?= e(date('Y-m-d')) ?></div>
    </header>
    <?php foreach (flash_take() as $f): ?>
      <div class="flash <?= e($f['type']) ?>"><?= e($f['msg']) ?></div>
    <?php endforeach; ?>
    <?= $content ?>
  </main>
</div>
<?php else: ?>
  <?= $content ?>
<?php endif; ?>
</body>
</html>
