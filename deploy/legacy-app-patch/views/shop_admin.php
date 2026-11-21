<?php
declare(strict_types=1);

/* 新授权中心（套餐/支付/订单/用户端授权）嵌入原后台 */

function shop_pdo(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;
    $path = getenv('SHOP_LICENSE_DB') ?: '/www/wwwroot/license.moneyfly.club/public/shop/data/license.db';
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA busy_timeout = 5000');
    return $pdo;
}

function shop_key(): string { return 'aurora-list-platform-license-v1'; }

function shop_canonical($v): string
{
    if (is_array($v)) {
        $isList = array_keys($v) === range(0, count($v) - 1);
        if ($isList) {
            $parts = [];
            foreach ($v as $x) $parts[] = shop_canonical($x);
            return '[' . implode(',', $parts) . ']';
        }
        ksort($v);
        $parts = [];
        foreach ($v as $k => $x) {
            $parts[] = json_encode((string)$k, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . ':' . shop_canonical($x);
        }
        return '{' . implode(',', $parts) . '}';
    }
    if (is_string($v)) return json_encode($v, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (is_bool($v)) return $v ? 'true' : 'false';
    if ($v === null) return 'null';
    return (string)$v;
}

function shop_sign(array $payload): string
{
    return hash_hmac('sha256', shop_canonical($payload), shop_key());
}

function shop_issue_token(string $machine, array $platforms, string $expires): string
{
    $payload = [
        'machine_code' => $machine,
        'platforms' => array_map(function ($f) { sort($f); return array_values(array_unique($f)); }, $platforms),
        'expires_at' => $expires,
        'issued_at' => time(),
    ];
    return json_encode(['payload' => $payload, 'sig' => shop_sign($payload)], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

function shop_setting(string $k, string $default = ''): string
{
    $st = shop_pdo()->prepare('SELECT v FROM settings WHERE k=?');
    $st->execute([$k]);
    $r = $st->fetch();
    return $r ? (string)$r['v'] : $default;
}

function shop_set_setting(string $k, string $v): void
{
    shop_pdo()->prepare('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v')
        ->execute([$k, $v]);
}

function shop_platform_map(): array
{
    return [
        'alibaba' => ['listing', 'category', 'trade', 'ai_text', 'ai_image', 'audit'],
        'aliexpress' => ['listing', 'category', 'pricing', 'variants', 'ai_text', 'ai_image', 'audit'],
        'amazon' => ['listing', 'category', 'ai_text', 'ai_image', 'audit', 'fba'],
        'tiktok' => ['listing', 'ai_text', 'ai_image'],
        'shopee' => ['listing', 'ai_text', 'ai_image'],
    ];
}

function shop_issue_for_order(int $orderId): ?array
{
    $db = shop_pdo();
    $st = $db->prepare('SELECT * FROM orders WHERE id=?'); $st->execute([$orderId]);
    $o = $st->fetch(); if (!$o) return null;
    $st = $db->prepare('SELECT * FROM packages WHERE id=?'); $st->execute([$o['package_id']]);
    $p = $st->fetch(); if (!$p) return null;
    $platforms = json_decode((string)$p['platforms'], true) ?: [];
    $features = json_decode((string)$p['features'], true) ?: [];
    $map = shop_platform_map(); $plats = [];
    foreach ($platforms as $pid) {
        $feat = array_values(array_intersect($features, $map[$pid] ?? []));
        if ($feat) $plats[$pid] = $feat;
    }
    $st = $db->prepare('SELECT * FROM licenses WHERE user_id=? AND revoked=0 ORDER BY id DESC LIMIT 1');
    $st->execute([$o['user_id']]); $prev = $st->fetch();
    $base = max(time(), $prev ? (strtotime((string)$prev['expires_at']) ?: time()) : time());
    $expires = date('Y-m-d', $base + ((int)$p['days']) * 86400);
    $machine = $prev ? (string)$prev['machine_code'] : '';
    $token = shop_issue_token($machine, $plats, $expires);
    $db->prepare('INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,?,?,?,?,?,?)')
        ->execute([$o['user_id'], $orderId, $machine, $token, $expires,
                   json_encode($plats, JSON_UNESCAPED_UNICODE), time()]);
    return ['token' => $token, 'expires_at' => $expires];
}

/* ---------- 套餐 ---------- */
function page_shop_packages(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT * FROM packages ORDER BY sort,id')->fetchAll();
    foreach ($rows as &$r) {
        $r['platforms_arr'] = json_decode((string)$r['platforms'], true) ?: [];
        $r['features_arr'] = json_decode((string)$r['features'], true) ?: [];
        $r['price'] = ((int)$r['price_cents']) / 100;
    }
    render('shop_packages', ['title' => '套餐管理', 'rows' => $rows]);
}

function page_shop_packages_save(): void
{
    require_admin(); csrf_check();
    $id = (int)($_POST['id'] ?? 0);
    if (isset($_POST['delete_id'])) {
        shop_pdo()->prepare('DELETE FROM packages WHERE id=?')->execute([(int)$_POST['delete_id']]);
        audit('删除套餐', 'package:' . (int)$_POST['delete_id']);
        flash('ok', '套餐已删除');
        redirect('/admin/shop-packages');
    }
    if ($id > 0) {
        $st = shop_pdo()->prepare('UPDATE packages SET name=?, platforms=?, features=?, price_cents=?, days=?, active=? WHERE id=?');
        $st->execute([
            trim((string)($_POST['name'] ?? '')),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['platforms'] ?? ''))))), JSON_UNESCAPED_UNICODE),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['features'] ?? ''))))), JSON_UNESCAPED_UNICODE),
            (int)round(((float)($_POST['price'] ?? 0)) * 100),
            (int)($_POST['days'] ?? 365),
            (int)($_POST['active'] ?? 1),
            $id,
        ]);
        audit('修改套餐', 'package:' . $id);
        flash('ok', '套餐已保存');
    } else {
        $st = shop_pdo()->prepare('INSERT INTO packages(name,platforms,features,price_cents,days,active,sort,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)');
        $st->execute([
            trim((string)($_POST['name'] ?? '新套餐')),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['platforms'] ?? 'alibaba'))))), JSON_UNESCAPED_UNICODE),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['features'] ?? 'listing,category'))))), JSON_UNESCAPED_UNICODE),
            (int)round(((float)($_POST['price'] ?? 0)) * 100),
            (int)($_POST['days'] ?? 365), 1, 99, '', time(),
        ]);
        audit('新增套餐', 'package:new');
        flash('ok', '套餐已新增');
    }
    redirect('/admin/shop-packages');
}

/* ---------- 支付 / 邮件设置 ---------- */
function page_shop_pay(): void
{
    require_admin();
    $keys = ['site_name', 'epay_gateway', 'epay_pid', 'epay_key', 'epay_type', 'allow_mock_pay',
             'announcement', 'require_verify', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass',
             'smtp_secure', 'smtp_from', 'debug_codes'];
    $s = [];
    foreach ($keys as $k) $s[$k] = shop_setting($k);
    render('shop_pay', ['title' => '套餐支付设置', 's' => $s]);
}

function page_shop_pay_save(): void
{
    require_admin(); csrf_check();
    $keys = ['site_name', 'epay_gateway', 'epay_pid', 'epay_key', 'epay_type', 'allow_mock_pay',
             'announcement', 'require_verify', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass',
             'smtp_secure', 'smtp_from', 'debug_codes'];
    foreach ($keys as $k) {
        if (array_key_exists($k, $_POST)) shop_set_setting($k, (string)$_POST[$k]);
    }
    audit('修改支付/邮件设置', 'shop:settings');
    flash('ok', '设置已保存');
    redirect('/admin/shop-pay');
}

/* ---------- 订单 ---------- */
function page_shop_orders(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT o.*, u.email, p.name AS package_name FROM orders o
        LEFT JOIN users u ON u.id=o.user_id LEFT JOIN packages p ON p.id=o.package_id
        ORDER BY o.id DESC LIMIT 200')->fetchAll();
    render('shop_orders', ['title' => '套餐订单', 'rows' => $rows]);
}

function page_shop_order_paid(int $id): void
{
    require_admin(); csrf_check();
    if ($id <= 0) { flash('bad', '订单不存在'); redirect('/admin/shop-orders'); }
    shop_pdo()->prepare("UPDATE orders SET status='paid', paid_at=? WHERE id=?")->execute([time(), $id]);
    shop_issue_for_order($id);
    audit('手动开通套餐订单', 'order:' . $id);
    flash('ok', '订单已标记支付并开通授权');
    redirect('/admin/shop-orders');
}

/* ---------- 用户 / 授权 ---------- */
function page_shop_users(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT u.id,u.email,u.name,u.created_at,
        (SELECT COUNT(*) FROM licenses l WHERE l.user_id=u.id AND l.revoked=0) AS lic_count,
        (SELECT expires_at FROM licenses l WHERE l.user_id=u.id AND l.revoked=0 ORDER BY l.id DESC LIMIT 1) AS expires_at,
        (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.status=\'paid\') AS paid_orders
        FROM users u ORDER BY u.id DESC LIMIT 200')->fetchAll();
    render('shop_users', ['title' => '套餐用户', 'rows' => $rows]);
}

function page_shop_licenses(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT l.*, u.email FROM licenses l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 200')->fetchAll();
    render('shop_licenses', ['title' => '套餐授权', 'rows' => $rows]);
}

function page_shop_license_revoke(int $id): void
{
    require_admin(); csrf_check();
    shop_pdo()->prepare('UPDATE licenses SET revoked=1 WHERE id=?')->execute([$id]);
    audit('吊销套餐授权', 'license:' . $id);
    flash('ok', '授权已吊销');
    redirect('/admin/shop-licenses');
}
