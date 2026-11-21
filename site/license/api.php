<?php
declare(strict_types=1);
require __DIR__.'/lib.php';

function pf(): array {
    return [
      'alibaba'=>['listing','category','trade','ai_text','ai_image','audit'],
      'aliexpress'=>['listing','category','pricing','variants','ai_text','ai_image','audit'],
      'amazon'=>['listing','category','ai_text','ai_image','audit','fba'],
      'tiktok'=>['listing','ai_text','ai_image'],
      'shopee'=>['listing','ai_text','ai_image'],
    ];
}

function issue_for_order(int $orderId): ?array {
    $db = db();
    $st = $db->prepare('SELECT * FROM orders WHERE id=?');
    $st->execute([$orderId]); $o = $st->fetch(PDO::FETCH_ASSOC);
    if (!$o || $o['status'] !== 'paid') return null;
    $st = $db->prepare('SELECT * FROM packages WHERE id=?');
    $st->execute([$o['package_id']]); $p = $st->fetch(PDO::FETCH_ASSOC);
    if (!$p) return null;
    $platforms = json_decode($p['platforms'], true) ?: [];
    $features = json_decode($p['features'], true) ?: [];
    $map = pf(); $payloadPlats = [];
    foreach ($platforms as $pid) {
        $allowed = $map[$pid] ?? [];
        $feat = array_values(array_intersect($features, $allowed));
        if ($feat) $payloadPlats[$pid] = $feat;
    }
    $prev = license_for_user((int)$o['user_id']);
    $expires = spend_days($prev['expires_at'] ?? '', (int)$p['days']);
    $machine = $prev['machine_code'] ?? '';
    $token = issue_token((string)$machine, $payloadPlats, $expires);
    $ins = $db->prepare('INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,?,?,?,?,?,?)');
    $ins->execute([$o['user_id'], $orderId, $machine, $token, $expires, j($payloadPlats), now()]);
    return ['token'=>$token, 'expires_at'=>$expires, 'platforms'=>$payloadPlats];
}

$action = $_GET['action'] ?? ($_POST['action'] ?? '');
switch ($action) {
// ---------------- 用户 ----------------
case 'register': {
    $in = body();
    $email = trim(strtolower((string)($in['email'] ?? '')));
    $pass = (string)($in['password'] ?? '');
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) fail('邮箱格式不正确');
    if (strlen($pass) < 6) fail('密码至少 6 位');
    $st = db()->prepare('SELECT id FROM users WHERE email=?'); $st->execute([$email]);
    if ($st->fetch()) fail('邮箱已注册');
    $st = db()->prepare('INSERT INTO users(email,pass,name,created_at) VALUES(?,?,?,?)');
    $st->execute([$email, password_hash($pass, PASSWORD_DEFAULT), trim((string)($in['name'] ?? '')), now()]);
    session_start(); $_SESSION['uid'] = (int)db()->lastInsertId();
    out(['user'=>current_user()]);
}
case 'login': {
    $in = body(); $email = trim(strtolower((string)($in['email'] ?? '')));
    $st = db()->prepare('SELECT * FROM users WHERE email=?'); $st->execute([$email]);
    $u = $st->fetch(PDO::FETCH_ASSOC);
    if (!$u || !password_verify((string)($in['password'] ?? ''), $u['pass'])) fail('邮箱或密码错误');
    session_start(); $_SESSION['uid'] = (int)$u['id'];
    out(['user'=>['id'=>$u['id'],'email'=>$u['email'],'name'=>$u['name']]]);
}
case 'logout': { session_start(); session_destroy(); out(['ok'=>true]); }
case 'me': {
    $u = current_user();
    $lic = null;
    if ($u) { $l = license_for_user((int)$u['id']); if ($l) $lic = ['expires_at'=>$l['expires_at'],'platforms'=>json_decode($l['platforms'],true),'machine_code'=>$l['machine_code']]; }
    out(['user'=>$u, 'license'=>$lic]);
}
case 'site': { out(['name'=>get_setting('site_name'), 'announcement'=>get_setting('announcement'), 'epay'=> (get_setting('epay_pid')!=='' )]); }
case 'packages': { out(packages()); }

// ---------------- 订单 / 支付 ----------------
case 'order_create': {
    $u = require_user(); $in = body();
    $pid = (int)($in['package_id'] ?? 0);
    $st = db()->prepare('SELECT * FROM packages WHERE id=? AND active=1'); $st->execute([$pid]);
    $p = $st->fetch(PDO::FETCH_ASSOC);
    if (!$p) fail('套餐不存在');
    $trade = 'AL'.date('YmdHis').random_int(1000,9999);
    $st = db()->prepare('INSERT INTO orders(user_id,package_id,amount_cents,status,trade_no,created_at) VALUES(?,?,?,?,?,?)');
    $st->execute([$u['id'], $pid, $p['price_cents'], 'pending', $trade, now()]);
    $oid = (int)db()->lastInsertId();
    $epay = effective_epay();
    $payUrl = '';
    if ($epay['pid'] && $epay['key']) {
        $params = [
          'pid'=>$epay['pid'], 'type'=>$epay['type'],
          'out_trade_no'=>$trade, 'notify_url'=>$epay['site_url'].'/pay/notify.php',
          'return_url'=>$epay['site_url'].'/index.php?paid=1',
          'name'=>$p['name'], 'money'=>number_format($p['price_cents']/100, 2, '.', ''),
        ];
        ksort($params);
        $params['sign'] = md5(urldecode(http_build_query($params)).$epay['epay_key']);
        $payUrl = rtrim($epay['epay_gateway'], '/').'/submit.php?'.http_build_query($params);
    }
    out(['order_id'=>$oid, 'trade_no'=>$trade, 'amount'=>$p['price_cents']/100, 'pay_url'=>$payUrl,
         'mock_pay'=>(!$payUrl)]);
}
case 'order_mock_pay': { // 仅未配置易支付时用于本地/测试
    $u = require_user(); $in = body();
    if (get_setting('epay_pid') || get_setting('allow_mock_pay') !== '1') fail('未启用模拟支付');
    $oid = (int)($in['order_id'] ?? 0);
    $st = db()->prepare('SELECT * FROM orders WHERE id=? AND user_id=?'); $st->execute([$oid, $u['id']]);
    if (!$st->fetch()) fail('订单不存在');
    db()->prepare("UPDATE orders SET status='paid', paid_at=? WHERE id=?")->execute([now(), $oid]);
    $lic = issue_for_order($oid);
    out(['license'=>$lic]);
}
case 'orders': {
    $u = require_user();
    $st = db()->prepare('SELECT o.*, p.name FROM orders o LEFT JOIN packages p ON p.id=o.package_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 50');
    $st->execute([$u['id']]); out($st->fetchAll(PDO::FETCH_ASSOC));
}
case 'license': {
    $u = require_user(); $l = license_for_user((int)$u['id']);
    if (!$l) out(null);
    out(['machine_code'=>$l['machine_code'],'expires_at'=>$l['expires_at'],
         'platforms'=>json_decode($l['platforms'],true),'token'=>$l['token']]);
}
case 'bind_machine': {
    $u = require_user(); $in = body();
    $mc = trim((string)($in['machine_code'] ?? ''));
    if (!preg_match('/^[A-Za-z0-9\-]{6,64}$/', $mc)) fail('机器码格式不正确');
    $l = license_for_user((int)$u['id']);
    if (!$l) fail('还没有已开通的授权，请先购买套餐');
    $plats = json_decode($l['platforms'], true) ?: [];
    $token = issue_token($mc, $plats, $l['expires_at']);
    db()->prepare('UPDATE licenses SET machine_code=?, token=? WHERE id=?')->execute([$mc, $token, $l['id']]);
    out(['ok'=>true, 'machine_code'=>$mc]);
}

case 'software_login': {
    // 软件登录：账号密码 + 机器码 → 自动绑定并下发 token
    $in = body();
    $email = trim(strtolower((string)($in['email'] ?? '')));
    $pass = (string)($in['password'] ?? '');
    $mc = trim((string)($in['machine_code'] ?? ''));
    if (!$mc) fail('缺少机器码');
    $st = db()->prepare('SELECT * FROM users WHERE email=?'); $st->execute([$email]);
    $u = $st->fetch(PDO::FETCH_ASSOC);
    if (!$u || !password_verify($pass, $u['pass'])) fail('邮箱或密码错误');
    $l = license_for_user((int)$u['id']);
    if (!$l) fail('该账号还没有开通套餐，请先在授权中心购买');
    if (strtotime($l['expires_at']) < time()) fail('授权已过期，请续费');
    $plats = json_decode($l['platforms'], true) ?: [];
    if ($l['machine_code'] && $l['machine_code'] !== $mc) fail('该授权已绑定其他设备，请在用户端解绑后再登录');
    if (!$l['machine_code']) {
        db()->prepare('UPDATE licenses SET machine_code=? WHERE id=?')->execute([$mc, $l['id']]);
    }
    $token = issue_token($mc, $plats, $l['expires_at']);
    db()->prepare('UPDATE licenses SET token=? WHERE id=?')->execute([$token, $l['id']]);
    out(['token'=>$token, 'expires_at'=>$l['expires_at'], 'platforms'=>$plats,
         'user'=>['id'=>$u['id'],'email'=>$u['email'],'name'=>$u['name']]]);
}
case 'unbind': {
    $u = require_user();
    $l = license_for_user((int)$u['id']);
    if (!$l) fail('没有可解绑的授权');
    $last = (int)($l['last_unbind'] ?? 0);
    if ($last && time() - $last < 30*86400) fail('30 天内只能解绑一次');
    db()->prepare("UPDATE licenses SET machine_code='', last_unbind=? WHERE id=?")->execute([now(), $l['id']]);
    out(['ok'=>true]);
}

case 'heartbeat': {
    $in = body();
    $mc = trim((string)($in['machine_code'] ?? ''));
    $payload = $in['payload'] ?? [];
    // 找到该机器的授权用户（没有也记录匿名，便于排查）
    $uid = 0;
    $st = db()->prepare('SELECT user_id FROM licenses WHERE machine_code=? AND revoked=0 ORDER BY id DESC LIMIT 1');
    $st->execute([$mc]); $r = $st->fetch(PDO::FETCH_ASSOC); if ($r) $uid = (int)$r['user_id'];
    db()->prepare('INSERT INTO heartbeats(user_id,machine_code,payload,ts) VALUES(?,?,?,?)')
        ->execute([$uid, $mc, j($payload), now()]);
    out(['ok'=>true]);
}
case 'status': {
    $u = require_user();
    $st = db()->prepare('SELECT * FROM heartbeats WHERE user_id=? ORDER BY id DESC LIMIT 1');
    $st->execute([$u['id']]); $h = $st->fetch(PDO::FETCH_ASSOC);
    out(['heartbeat'=> $h ? ['ts'=>$h['ts'],'payload'=>json_decode($h['payload'],true)] : null]);
}

// ---------------- 管理员 ----------------
case 'admin_login': {
    $in = body();
    if ((string)($in['user'] ?? '') !== cfg()['admin_user'] || (string)($in['password'] ?? '') !== cfg()['admin_pass']) fail('账号或密码错误');
    session_start(); $_SESSION['admin'] = 1; out(['ok'=>true]);
}
case 'admin_logout': { session_start(); unset($_SESSION['admin']); out(['ok'=>true]); }

case 'admin_settings_get': {
    require_admin();
    out(['settings'=>[
      'site_name'=>get_setting('site_name'),
      'epay_gateway'=>get_setting('epay_gateway'),
      'epay_pid'=>get_setting('epay_pid'),
      'epay_key'=>get_setting('epay_key'),
      'epay_type'=>get_setting('epay_type'),
      'allow_mock_pay'=>get_setting('allow_mock_pay'),
      'announcement'=>get_setting('announcement'),
    ]]);
}
case 'admin_settings_save': {
    require_admin();
    $in = body();
    foreach (['site_name','epay_gateway','epay_pid','epay_key','epay_type','allow_mock_pay','announcement'] as $k) {
        if (array_key_exists($k, $in)) set_setting($k, (string)$in[$k]);
    }
    out(['ok'=>true]);
}

case 'admin_stats': {
    require_admin();
    $db = db();
    out([
      'users'=>(int)$db->query('SELECT COUNT(*) FROM users')->fetchColumn(),
      'orders'=>(int)$db->query('SELECT COUNT(*) FROM orders')->fetchColumn(),
      'paid'=>(int)$db->query("SELECT COUNT(*) FROM orders WHERE status='paid'")->fetchColumn(),
      'revenue'=>(int)$db->query("SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE status='paid'")->fetchColumn()/100,
      'licenses'=>(int)$db->query('SELECT COUNT(*) FROM licenses WHERE revoked=0')->fetchColumn(),
      'heartbeats24'=>(int)$db->query('SELECT COUNT(*) FROM heartbeats WHERE ts>'.(now()-86400))->fetchColumn(),
    ]);
}
case 'admin_packages': {
    require_admin();
    $in = body();
    if (array_key_exists('name', $in)) {
        $id = (int)($in['id'] ?? 0);
        $args = [trim((string)$in['name']), j($in['platforms'] ?? []), j($in['features'] ?? []),
                 (int)round(((float)($in['price'] ?? 0))*100), (int)($in['days'] ?? 365),
                 (int)($in['active'] ?? 1), (int)($in['sort'] ?? 0), (string)($in['description'] ?? '')];
        if ($id) { $args[] = $id; db()->prepare('UPDATE packages SET name=?,platforms=?,features=?,price_cents=?,days=?,active=?,sort=?,description=? WHERE id=?')->execute($args); }
        else { $args[] = now(); db()->prepare('INSERT INTO packages(name,platforms,features,price_cents,days,active,sort,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)')->execute($args); }
        out(['ok'=>true]);
    }
    $rows = db()->query('SELECT * FROM packages ORDER BY sort,id')->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$r) { $r['platforms']=json_decode($r['platforms'],true); $r['features']=json_decode($r['features'],true); $r['price']=$r['price_cents']/100; }
    out($rows);
}
case 'admin_package_delete': {
    require_admin(); $in = body(); $id = (int)($in['id'] ?? 0);
    if ($id <= 0) fail('缺少 id');
    db()->prepare('DELETE FROM packages WHERE id=?')->execute([$id]);
    out(['ok'=>true]);
}
case 'admin_orders': {
    require_admin();
    $rows = db()->query('SELECT o.*, u.email, p.name package FROM orders o LEFT JOIN users u ON u.id=o.user_id LEFT JOIN packages p ON p.id=o.package_id ORDER BY o.id DESC LIMIT 200')->fetchAll(PDO::FETCH_ASSOC);
    out($rows);
}
case 'admin_mark_paid': {
    require_admin(); $in = body(); $oid = (int)($in['order_id'] ?? 0);
    $st = db()->prepare('SELECT * FROM orders WHERE id=?'); $st->execute([$oid]);
    if (!$st->fetch()) fail('订单不存在');
    db()->prepare("UPDATE orders SET status='paid', paid_at=? WHERE id=?")->execute([now(), $oid]);
    out(['license'=>issue_for_order($oid)]);
}
case 'admin_users': {
    require_admin();
    $rows = db()->query('SELECT u.id,u.email,u.name,u.created_at,
        (SELECT COUNT(*) FROM licenses l WHERE l.user_id=u.id AND l.revoked=0) licenses,
        (SELECT expires_at FROM licenses l WHERE l.user_id=u.id AND l.revoked=0 ORDER BY id DESC LIMIT 1) expires_at,
        (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.status=\'paid\') paid_orders
        FROM users u ORDER BY u.id DESC LIMIT 200')->fetchAll(PDO::FETCH_ASSOC);
    out($rows);
}
case 'admin_licenses': {
    require_admin();
    $rows = db()->query('SELECT l.*, u.email FROM licenses l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 200')->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$r) { $r['platforms']=json_decode($r['platforms'],true); }
    out($rows);
}
case 'admin_revoke': {
    require_admin(); $in = body(); $lid = (int)($in['license_id'] ?? 0);
    db()->prepare('UPDATE licenses SET revoked=1 WHERE id=?')->execute([$lid]);
    out(['ok'=>true]);
}
default: fail('未知操作');
}
