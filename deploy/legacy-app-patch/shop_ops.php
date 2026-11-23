<?php
declare(strict_types=1);

/* 商用运营：退款 / 发票 / 工单 / 优惠券 / 提醒 */

function ops_pdo(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;
    $path = getenv('SHOP_LICENSE_DB') ?: '/www/wwwroot/license.moneyfly.club/public/shop/data/license.db';
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA busy_timeout = 5000');
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec("CREATE TABLE IF NOT EXISTS refunds(
      id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      reason TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
      note TEXT DEFAULT '', created_at INTEGER NOT NULL, handled_at INTEGER DEFAULT 0)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_id INTEGER DEFAULT 0,
      title TEXT NOT NULL, tax_no TEXT DEFAULT '', email TEXT DEFAULT '',
      amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      created_at INTEGER NOT NULL, issued_at INTEGER DEFAULT 0, file TEXT DEFAULT '')");
    $pdo->exec("CREATE TABLE IF NOT EXISTS tickets(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, subject TEXT NOT NULL,
      category TEXT DEFAULT 'other', status TEXT NOT NULL DEFAULT 'open',
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS ticket_replies(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
      admin_id INTEGER DEFAULT 0, body TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
      created_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS coupons(
      id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
      type TEXT NOT NULL DEFAULT 'fixed', value_cents INTEGER DEFAULT 0,
      percent INTEGER DEFAULT 0, min_amount_cents INTEGER DEFAULT 0,
      expires_at TEXT DEFAULT '', max_uses INTEGER DEFAULT 0, used INTEGER DEFAULT 0,
      active INTEGER DEFAULT 1, created_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS reminders(
      id INTEGER PRIMARY KEY AUTOINCREMENT, license_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL, kind TEXT NOT NULL, sent_at INTEGER NOT NULL,
      UNIQUE(license_id, kind))");
    $pdo->exec("CREATE TABLE IF NOT EXISTS coupon_uses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, coupon_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL, order_id INTEGER NOT NULL, created_at INTEGER NOT NULL)");
    return $pdo;
}

/* ---------- 优惠券 ---------- */
function coupon_apply(string $code, int $amountCents): array
{
    $code = strtoupper(trim($code));
    if ($code === '') return [null, '请输入优惠码'];
    $st = ops_pdo()->prepare('SELECT * FROM coupons WHERE code=? AND active=1');
    $st->execute([$code]);
    $c = $st->fetch();
    if (!$c) return [null, '优惠码不存在或已停用'];
    if ($c['expires_at'] !== '' && strtotime($c['expires_at']) < time()) return [null, '优惠码已过期'];
    if ((int)$c['max_uses'] > 0 && (int)$c['used'] >= (int)$c['max_uses']) return [null, '优惠码已用完'];
    if ($amountCents < (int)$c['min_amount_cents']) return [null, '订单金额未达到优惠门槛'];
    $off = $c['type'] === 'percent'
        ? (int)floor($amountCents * max(0, min(100, (int)$c['percent'])) / 100)
        : (int)$c['value_cents'];
    $off = min($off, $amountCents);
    return [['id' => (int)$c['id'], 'code' => $code, 'off' => $off, 'final' => $amountCents - $off], ''];
}

function coupon_use(int $couponId, int $userId, int $orderId): void
{
    ops_pdo()->prepare('UPDATE coupons SET used=used+1 WHERE id=?')->execute([$couponId]);
    ops_pdo()->prepare('INSERT INTO coupon_uses(coupon_id,user_id,order_id,created_at) VALUES(?,?,?,?)')
        ->execute([$couponId, $userId, $orderId, time()]);
}

/* ---------- 退款 ---------- */
function page_shop_refunds(): void
{
    require_admin(); require_owner();
    $rows = ops_pdo()->query('SELECT r.*, u.email, o.trade_no FROM refunds r
        LEFT JOIN users u ON u.id=r.user_id LEFT JOIN orders o ON o.id=r.order_id
        ORDER BY r.id DESC LIMIT 200')->fetchAll();
    render('shop_refunds', ['title' => '退款管理', 'rows' => $rows]);
}

function page_shop_refund_create(): void
{
    require_admin(); require_owner(); csrf_check();
    $oid = (int)($_POST['order_id'] ?? 0);
    $reason = trim((string)($_POST['reason'] ?? ''));
    $st = ops_pdo()->prepare('SELECT * FROM orders WHERE id=? AND status="paid"');
    $st->execute([$oid]); $o = $st->fetch();
    if (!$o) { flash('bad', '订单不存在或未支付'); redirect('/admin/shop-orders'); }
    ops_pdo()->prepare('INSERT INTO refunds(order_id,user_id,amount_cents,reason,status,created_at) VALUES(?,?,?,?,?,?)')
        ->execute([$oid, $o['user_id'], $o['amount_cents'], $reason, 'pending', time()]);
    audit('创建退款单', 'order:' . $oid, $reason);
    flash('ok', '退款单已创建（待处理）；请在支付渠道完成退款后点「完成退款」');
    redirect('/admin/shop-refunds');
}

function page_shop_refund_complete(int $id): void
{
    require_admin(); require_owner(); csrf_check();
    $db = ops_pdo();
    $st = $db->prepare('SELECT * FROM refunds WHERE id=?'); $st->execute([$id]);
    $r = $st->fetch();
    if (!$r) { flash('bad', '退款单不存在'); redirect('/admin/shop-refunds'); }
    [$refundOk, $refundErr] = function_exists('epay_refund_order') ? epay_refund_order((int)$r['order_id']) : [false, '未实现'];
    $db->beginTransaction();
    $db->prepare("UPDATE refunds SET status='done', handled_at=?, note=? WHERE id=?")
       ->execute([time(), ($refundOk ? '易支付原路退款成功' : '渠道退款失败/需人工：' . $refundErr), $id]);
    $db->prepare("UPDATE orders SET status='refunded' WHERE id=?")->execute([$r['order_id']]);
    $db->prepare('UPDATE licenses SET revoked=1 WHERE user_id=? AND revoked=0')->execute([$r['user_id']]);
    $db->commit();
    audit('完成退款并取消授权', 'refund:' . $id);
    if ($refundOk) {
        flash('ok', '退款完成（易支付原路退款成功），订单已标记退款、授权已取消');
    } else {
        flash('bad', '已完成本地退款登记，但支付渠道退款失败：' . $refundErr . '，请在支付后台人工退款');
    }
    redirect('/admin/shop-refunds');
}

/* ---------- 发票 ---------- */
function page_shop_invoices(): void
{
    require_admin();
    $rows = ops_pdo()->query('SELECT i.*, u.email AS user_email FROM invoices i
        LEFT JOIN users u ON u.id=i.user_id ORDER BY i.id DESC LIMIT 200')->fetchAll();
    render('shop_invoices', ['title' => '发票管理', 'rows' => $rows]);
}

function page_shop_invoice_issue(int $id): void
{
    require_admin(); csrf_check();
    ops_pdo()->prepare("UPDATE invoices SET status='issued', issued_at=? WHERE id=?")->execute([time(), $id]);
    audit('开票', 'invoice:' . $id);
    flash('ok', '已标记开票');
    redirect('/admin/shop-invoices');
}

/* ---------- 工单 ---------- */
function page_shop_tickets(): void
{
    require_admin();
    $rows = ops_pdo()->query('SELECT t.*, u.email FROM tickets t LEFT JOIN users u ON u.id=t.user_id ORDER BY t.status="open" DESC, t.id DESC LIMIT 200')->fetchAll();
    render('shop_tickets', ['title' => '工单支持', 'rows' => $rows]);
}

function page_shop_ticket_detail(int $id): void
{
    require_admin();
    $st = ops_pdo()->prepare('SELECT t.*, u.email FROM tickets t LEFT JOIN users u ON u.id=t.user_id WHERE t.id=?');
    $st->execute([$id]); $t = $st->fetch();
    if (!$t) { flash('bad', '工单不存在'); redirect('/admin/shop-tickets'); }
    $st = ops_pdo()->prepare('SELECT * FROM ticket_replies WHERE ticket_id=? ORDER BY id');
    $st->execute([$id]); $replies = $st->fetchAll();
    render('shop_ticket_detail', ['title' => '工单 #' . $id, 't' => $t, 'replies' => $replies]);
}

function page_shop_ticket_reply(int $id): void
{
    require_admin(); csrf_check();
    $body = trim((string)($_POST['body'] ?? ''));
    if ($body === '') { flash('bad', '回复内容不能为空'); redirect('/admin/shop-tickets/' . $id); }
    $a = current_admin();
    ops_pdo()->prepare('INSERT INTO ticket_replies(ticket_id,admin_id,body,is_admin,created_at) VALUES(?,?,?,?,?)')
        ->execute([$id, (int)$a['id'], $body, 1, time()]);
    ops_pdo()->prepare("UPDATE tickets SET status='replied', updated_at=? WHERE id=?")->execute([time(), $id]);
    audit('回复工单', 'ticket:' . $id);
    flash('ok', '已回复');
    redirect('/admin/shop-tickets/' . $id);
}

/* ---------- 优惠券管理 ---------- */
function page_shop_coupons(): void
{
    require_admin();
    $rows = ops_pdo()->query('SELECT * FROM coupons ORDER BY id DESC LIMIT 200')->fetchAll();
    render('shop_coupons', ['title' => '优惠券', 'rows' => $rows]);
}

function page_shop_coupon_save(): void
{
    require_admin(); csrf_check();
    if (isset($_POST['delete_id'])) {
        ops_pdo()->prepare('DELETE FROM coupons WHERE id=?')->execute([(int)$_POST['delete_id']]);
        flash('ok', '优惠券已删除'); redirect('/admin/shop-coupons');
    }
    $code = strtoupper(trim((string)($_POST['code'] ?? '')));
    if ($code === '') { flash('bad', '请输入优惠码'); redirect('/admin/shop-coupons'); }
    ops_pdo()->prepare('INSERT OR REPLACE INTO coupons(code,type,value_cents,percent,min_amount_cents,expires_at,max_uses,used,active,created_at) VALUES(?,?,?,?,?,?,?,0,?,?)')
        ->execute([$code, (string)($_POST['type'] ?? 'fixed'),
                   (int)round(((float)($_POST['value'] ?? 0)) * 100),
                   (int)($_POST['percent'] ?? 0),
                   (int)round(((float)($_POST['min_amount'] ?? 0)) * 100),
                   (string)($_POST['expires_at'] ?? ''),
                   (int)($_POST['max_uses'] ?? 0),
                   (int)($_POST['active'] ?? 1), time()]);
    audit('保存优惠券', 'coupon:' . $code);
    flash('ok', '优惠券已保存');
    redirect('/admin/shop-coupons');
}

/* ---------- 用户接口（被 shop/api.php 调用） ---------- */
function user_invoice_request(int $uid, array $in): array
{
    $orderId = (int)($in['order_id'] ?? 0);
    $st = ops_pdo()->prepare('SELECT * FROM orders WHERE id=? AND user_id=? AND status="paid"');
    $st->execute([$orderId, $uid]);
    $o = $st->fetch();
    if (!$o) return [false, '订单不存在或未支付'];
    $title = trim((string)($in['title'] ?? ''));
    if ($title === '') return [false, '请填写发票抬头'];
    ops_pdo()->prepare('INSERT INTO invoices(user_id,order_id,title,tax_no,email,amount_cents,status,created_at) VALUES(?,?,?,?,?,?,?,?)')
        ->execute([$uid, $orderId, $title, trim((string)($in['tax_no'] ?? '')),
                   trim((string)($in['email'] ?? '')), (int)$o['amount_cents'], 'pending', time()]);
    if (function_exists('notify_admin_mail')) notify_admin_mail('新的发票申请', "用户ID {$uid} 申请发票：{$title}，金额 ¥" . number_format(((int)$o['amount_cents'])/100,2));
    return [true, ''];
}

function user_ticket_create(int $uid, array $in): array
{
    $subject = trim((string)($in['subject'] ?? ''));
    if ($subject === '') return [false, '请填写工单标题'];
    $db = ops_pdo();
    $db->prepare('INSERT INTO tickets(user_id,subject,category,status,created_at,updated_at) VALUES(?,?,?,?,?,?)')
       ->execute([$uid, $subject, (string)($in['category'] ?? 'other'), 'open', time(), time()]);
    $tid = (int)$db->lastInsertId();
    $body = trim((string)($in['body'] ?? ''));
    if ($body !== '') {
        $db->prepare('INSERT INTO ticket_replies(ticket_id,admin_id,body,is_admin,created_at) VALUES(?,?,?,?,?)')
           ->execute([$tid, 0, $body, 0, time()]);
    }
    if (function_exists('notify_admin_mail')) notify_admin_mail('新的客户工单 #' . $tid, "用户ID {$uid}：{$subject}\n{$body}\n请登录后台处理。");
    return [true, '', $tid];
}

function user_tickets(int $uid): array
{
    $st = ops_pdo()->prepare('SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 50');
    $st->execute([$uid]);
    return $st->fetchAll();
}

/* ---------- 定时任务 ---------- */
function cron_send_expiry_reminders(): array
{
    $sent = [];
    $db = ops_pdo();
    $st = $db->query('SELECT l.id, l.user_id, l.expires_at, u.email FROM licenses l
        JOIN users u ON u.id=l.user_id WHERE l.revoked=0');
    foreach ($st as $l) {
        $days = (int)ceil((strtotime($l['expires_at']) - time()) / 86400);
        $kind = null;
        if ($days <= 1 && $days >= 0) $kind = 'd1';
        elseif ($days <= 7) $kind = 'd7';
        elseif ($days <= 30) $kind = 'd30';
        if (!$kind) continue;
        $chk = $db->prepare('SELECT 1 FROM reminders WHERE license_id=? AND kind=?');
        $chk->execute([$l['id'], $kind]);
        if ($chk->fetch()) continue;
        $subject = 'AuroraList 授权即将到期提醒';
        $body = "你的 AuroraList 授权将于 {$l['expires_at']} 到期（约 {$days} 天）。\n请登录 https://license.moneyfly.club/ 续费，避免影响使用。";
        $ok = false;
        $shopLib = '/www/wwwroot/license.moneyfly.club/public/shop/lib.php';
        if (is_file($shopLib)) {
            require_once $shopLib;
            [$ok, $e] = smtp_send($l['email'], $subject, $body);
        }
        $db->prepare('INSERT OR IGNORE INTO reminders(license_id,user_id,kind,sent_at) VALUES(?,?,?,?)')
           ->execute([$l['id'], $l['user_id'], $kind, time()]);
        $sent[] = ['license' => $l['id'], 'email' => $l['email'], 'kind' => $kind, 'days' => $days, 'ok' => $ok];
    }
    return $sent;
}

function cron_cleanup_orders(): int
{
    $st = ops_pdo()->prepare("UPDATE orders SET status='expired' WHERE status='pending' AND created_at < ?");
    $st->execute([time() - 7 * 86400]);
    return $st->rowCount();
}

/* ---------- 经营报表 ---------- */
function shop_report_data(): array
{
    $db = ops_pdo();
    $total = (int)$db->query("SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE status='paid'")->fetchColumn();
    $monthStart = strtotime(date('Y-m-01'));
    $st = $db->prepare("SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE status='paid' AND paid_at>=?");
    $st->execute([$monthStart]); $month = (int)$st->fetchColumn();
    $refund = (int)$db->query("SELECT COALESCE(SUM(amount_cents),0) FROM refunds WHERE status='done'")->fetchColumn();
    $orders = (int)$db->query("SELECT COUNT(*) FROM orders WHERE status='paid'")->fetchColumn();
    $customers = (int)$db->query('SELECT COUNT(*) FROM users')->fetchColumn();
    $licenses = (int)$db->query('SELECT COUNT(*) FROM licenses WHERE revoked=0')->fetchColumn();
    // 近 6 个月收入
    $months = [];
    for ($i = 5; $i >= 0; $i--) {
        $start = strtotime(date('Y-m-01', strtotime("-$i month")));
        $end = strtotime(date('Y-m-01', strtotime("-" . ($i - 1) . " month")));
        $st = $db->prepare("SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE status='paid' AND paid_at>=? AND paid_at<?");
        $st->execute([$start, $end]);
        $months[] = ['label' => date('Y-m', $start), 'cents' => (int)$st->fetchColumn()];
    }
    // 按套餐
    $byPkg = $db->query("SELECT p.name, COUNT(o.id) AS n, COALESCE(SUM(o.amount_cents),0) AS cents
        FROM orders o LEFT JOIN packages p ON p.id=o.package_id WHERE o.status='paid'
        GROUP BY o.package_id ORDER BY cents DESC LIMIT 10")->fetchAll();
    return ['total'=>$total,'month'=>$month,'refund'=>$refund,'orders'=>$orders,
            'customers'=>$customers,'licenses'=>$licenses,'months'=>$months,'by_pkg'=>$byPkg];
}

function page_shop_reports(): void
{
    require_admin();
    $d = shop_report_data();
    $refunds = ops_pdo()->query("SELECT r.*, u.email FROM refunds r LEFT JOIN users u ON u.id=r.user_id
        WHERE r.status='done' ORDER BY r.id DESC LIMIT 20")->fetchAll();
    render('shop_reports', ['title' => '经营报表', 'd' => $d, 'refunds' => $refunds]);
}

function page_shop_export(): void
{
    require_admin();
    $type = (string)($_GET['type'] ?? 'orders');
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $type . '_' . date('Ymd_His') . '.csv"');
    echo "\xEF\xBB\xBF";
    $out = fopen('php://output', 'w');
    if ($type === 'refunds') {
        fputcsv($out, ['退款ID','订单ID','用户','金额(元)','原因','状态','创建时间','处理时间']);
        foreach (ops_pdo()->query('SELECT r.*, u.email, o.trade_no FROM refunds r LEFT JOIN users u ON u.id=r.user_id LEFT JOIN orders o ON o.id=r.order_id ORDER BY r.id DESC') as $r) {
            fputcsv($out, [$r['id'], $r['order_id'], $r['email'] ?? '', ((int)$r['amount_cents'])/100,
                           $r['reason'], $r['status'], date('Y-m-d H:i', (int)$r['created_at']),
                           $r['handled_at'] ? date('Y-m-d H:i', (int)$r['handled_at']) : '']);
        }
    } else {
        fputcsv($out, ['订单ID','订单号','用户','套餐','金额(元)','状态','创建时间','支付时间']);
        foreach (ops_pdo()->query('SELECT o.*, u.email, p.name AS pkg FROM orders o LEFT JOIN users u ON u.id=o.user_id LEFT JOIN packages p ON p.id=o.package_id ORDER BY o.id DESC') as $r) {
            fputcsv($out, [$r['id'], $r['trade_no'], $r['email'] ?? '', $r['pkg'] ?? '',
                           ((int)$r['amount_cents'])/100, $r['status'],
                           date('Y-m-d H:i', (int)$r['created_at']), $r['paid_at'] ? date('Y-m-d H:i', (int)$r['paid_at']) : '']);
        }
    }
    fclose($out);
    exit;
}

/* ---------- 管理员 2FA（TOTP） ---------- */
function totp_b32decode(string $b32): string
{
    $map = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    $b32 = strtoupper(preg_replace('/[^A-Z2-7]/i', '', $b32));
    $bits = '';
    for ($i = 0; $i < strlen($b32); $i++) {
        $val = strpos($map, $b32[$i]);
        if ($val === false) continue;
        $bits .= str_pad(decbin($val), 5, '0', STR_PAD_LEFT);
    }
    $out = '';
    foreach (str_split($bits, 8) as $byte) {
        if (strlen($byte) === 8) $out .= chr(bindec($byte));
    }
    return $out;
}

function totp_code(string $secret, ?int $slice = null): string
{
    $slice = $slice ?? intdiv(time(), 30);
    $key = totp_b32decode($secret);
    $bin = pack('N*', 0) . pack('N*', $slice);
    $hash = hash_hmac('sha1', $bin, $key, true);
    $offset = ord(substr($hash, -1)) & 0x0F;
    $part = substr($hash, $offset, 4);
    $val = unpack('N', $part)[1] & 0x7FFFFFFF;
    return str_pad((string)($val % 1000000), 6, '0', STR_PAD_LEFT);
}

function totp_verify(string $secret, string $code): bool
{
    $code = preg_replace('/\D/', '', $code);
    if (strlen($code) !== 6) return false;
    $t = intdiv(time(), 30);
    for ($i = -1; $i <= 1; $i++) {
        if (hash_equals(totp_code($secret, $t + $i), $code)) return true;
    }
    return false;
}

function admin_totp_columns(): void
{
    $db = function_exists('db') ? db() : null;
    if (!$db) return;
    try { $db->exec('ALTER TABLE admins ADD COLUMN totp_secret TEXT'); } catch (Exception $e) {}
    try { $db->exec('ALTER TABLE admins ADD COLUMN totp_enabled INTEGER DEFAULT 0'); } catch (Exception $e) {}
}

function page_admin_2fa_form(): void
{
    session_boot();
    if (current_admin()) { redirect('/admin'); }
    $uid = (int)($_SESSION['admin_2fa_pending'] ?? 0);
    if (!$uid) { redirect('/admin/login'); }
    render('admin_2fa', ['title' => '两步验证']);
}

function page_admin_2fa_post(): void
{
    session_boot(); csrf_check();
    $uid = (int)($_SESSION['admin_2fa_pending'] ?? 0);
    if (!$uid) { redirect('/admin/login'); }
    admin_totp_columns();
    $st = db()->prepare('SELECT * FROM admins WHERE id=? AND is_active=1');
    $st->execute([$uid]); $a = $st->fetch();
    if (!$a || !totp_verify((string)$a['totp_secret'], (string)($_POST['code'] ?? ''))) {
        flash('bad', '验证码不正确');
        redirect('/admin/2fa');
    }
    unset($_SESSION['admin_2fa_pending']);
    session_regenerate_id(true);
    $_SESSION['admin_id'] = (int)$a['id'];
    $_SESSION['csrf'] = bin2hex(random_bytes(32));
    audit('登录后台(2FA)', 'admin:' . $a['id'], '', $a['username']);
    redirect('/admin');
}

function page_admin_security(): void
{
    require_admin(); require_owner(); admin_totp_columns();
    $a = current_admin();
    $st = db()->prepare('SELECT totp_secret, totp_enabled FROM admins WHERE id=?');
    $st->execute([$a['id']]); $row = $st->fetch();
    $secret = (string)($row['totp_secret'] ?? '');
    if ($secret === '' && empty($_SESSION['new_totp_secret'])) {
        $chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; $s = '';
        for ($i = 0; $i < 32; $i++) $s .= $chars[random_int(0, 31)];
        $_SESSION['new_totp_secret'] = $s;
        $secret = $s;
    }
    $secret = $secret ?: (string)($_SESSION['new_totp_secret'] ?? '');
    $uri = 'otpauth://totp/AuroraList:' . rawurlencode((string)$a['username'])
        . '?secret=' . $secret . '&issuer=AuroraList&period=30&digits=6';
    render('admin_security', ['title' => '安全设置', 'enabled' => (int)($row['totp_enabled'] ?? 0),
                              'secret' => $secret, 'uri' => $uri]);
}

function page_admin_totp_enable(): void
{
    require_admin(); csrf_check(); admin_totp_columns();
    $a = current_admin();
    $secret = (string)($_SESSION['new_totp_secret'] ?? '');
    if (!$secret || !totp_verify($secret, (string)($_POST['code'] ?? ''))) {
        flash('bad', '验证码不正确，请确认手机时间准确');
        redirect('/admin/security');
    }
    db()->prepare('UPDATE admins SET totp_secret=?, totp_enabled=1 WHERE id=?')->execute([$secret, $a['id']]);
    unset($_SESSION['new_totp_secret']);
    audit('开启 2FA', 'admin:' . $a['id']);
    flash('ok', '两步验证已开启');
    redirect('/admin/security');
}

function page_admin_totp_disable(): void
{
    require_admin(); csrf_check(); admin_totp_columns();
    $a = current_admin();
    db()->prepare('UPDATE admins SET totp_enabled=0 WHERE id=?')->execute([$a['id']]);
    audit('关闭 2FA', 'admin:' . $a['id']);
    flash('ok', '两步验证已关闭');
    redirect('/admin/security');
}

/* ---------- RBAC / 设备数 ---------- */
function shop_device_limit_for_user(int $uid): int
{
    $db = ops_pdo();
    $st = $db->prepare("SELECT MAX(COALESCE(p.device_limit,1)) AS lim FROM licenses l
        LEFT JOIN orders o ON o.id=l.order_id LEFT JOIN packages p ON p.id=o.package_id
        WHERE l.user_id=? AND l.revoked=0");
    $st->execute([$uid]);
    $lim = (int)($st->fetchColumn() ?: 0);
    if ($lim <= 0) {
        $st = $db->prepare('SELECT v FROM settings WHERE k=?'); $st->execute(['device_limit']);
        $v = $st->fetchColumn();
        $lim = max(1, (int)($v ?: 1));
    }
    return $lim;
}

function shop_bound_devices(int $uid): array
{
    $st = ops_pdo()->prepare("SELECT DISTINCT machine_code FROM licenses
        WHERE user_id=? AND revoked=0 AND machine_code<>''");
    $st->execute([$uid]);
    return array_column($st->fetchAll(), 'machine_code');
}

/* ---------- 设备 / 邀请 / 客户标签 ---------- */
function ops_migrate_extra(): void
{
    $db = ops_pdo();
    try { $db->exec('ALTER TABLE users ADD COLUMN tags TEXT DEFAULT \'\''); } catch (Exception $e) {}
    try { $db->exec('ALTER TABLE users ADD COLUMN note TEXT DEFAULT \'\''); } catch (Exception $e) {}
    $db->exec("CREATE TABLE IF NOT EXISTS invites(
      id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
      uses INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 0, created_at INTEGER NOT NULL)");
    $db->exec("CREATE TABLE IF NOT EXISTS invite_uses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, invite_id INTEGER NOT NULL, invitee_id INTEGER NOT NULL,
      created_at INTEGER NOT NULL)");
}

function user_devices(int $uid): array
{
    $st = ops_pdo()->prepare("SELECT id, platforms, machine_code, expires_at, created_at
        FROM licenses WHERE user_id=? AND revoked=0 AND machine_code<>'' ORDER BY id DESC");
    $st->execute([$uid]);
    $rows = $st->fetchAll();
    foreach ($rows as &$r) $r['platforms'] = json_decode((string)$r['platforms'], true) ?: [];
    return $rows;
}

function user_device_unbind(int $uid, int $licenseId): array
{
    $st = ops_pdo()->prepare('SELECT * FROM licenses WHERE id=? AND user_id=? AND revoked=0');
    $st->execute([$licenseId, $uid]);
    $l = $st->fetch();
    if (!$l) return [false, '设备不存在'];
    ops_pdo()->prepare('UPDATE licenses SET machine_code=\'\' WHERE id=?')->execute([$licenseId]);
    return [true, ''];
}

function user_invite_info(int $uid): array
{
    ops_migrate_extra();
    $db = ops_pdo();
    $st = $db->prepare('SELECT * FROM invites WHERE user_id=? ORDER BY id DESC LIMIT 1');
    $st->execute([$uid]);
    $inv = $st->fetch();
    if (!$inv) {
        $code = strtoupper(substr(bin2hex(random_bytes(4)), 0, 8));
        $db->prepare('INSERT INTO invites(code,user_id,uses,max_uses,created_at) VALUES(?,?,0,0,?)')
           ->execute([$code, $uid, time()]);
        $st->execute([$uid]); $inv = $st->fetch();
    }
    return ['code' => $inv['code'], 'uses' => (int)$inv['uses']];
}

function user_invite_bind(string $code, int $inviteeId): void
{
    ops_migrate_extra();
    $st = ops_pdo()->prepare('SELECT * FROM invites WHERE code=?');
    $st->execute([strtoupper(trim($code))]);
    $inv = $st->fetch();
    if (!$inv || (int)$inv['user_id'] === $inviteeId) return;
    if ((int)$inv['max_uses'] > 0 && (int)$inv['uses'] >= (int)$inv['max_uses']) return;
    ops_pdo()->prepare('UPDATE invites SET uses=uses+1 WHERE id=?')->execute([$inv['id']]);
    ops_pdo()->prepare('INSERT INTO invite_uses(invite_id,invitee_id,created_at) VALUES(?,?,?)')
        ->execute([$inv['id'], $inviteeId, time()]);
}

function page_shop_invites(): void
{
    require_admin();
    ops_migrate_extra();
    $rows = ops_pdo()->query('SELECT i.*, u.email FROM invites i LEFT JOIN users u ON u.id=i.user_id ORDER BY i.uses DESC, i.id DESC LIMIT 200')->fetchAll();
    render('shop_invites', ['title' => '邀请码', 'rows' => $rows]);
}

/* 到期提醒横幅数据 */
function user_expiry_hint(array $lic): string
{
    $exp = (string)($lic['expires_at'] ?? '');
    if ($exp === '') return '';
    $days = (int)ceil((strtotime($exp . ' 23:59:59') - time()) / 86400);
    if ($days < 0) return '授权已过期，请立即续费';
    if ($days <= 30) return "授权将在 {$days} 天后到期（{$exp}），建议及时续费";
    return '';
}

/* ---------- 邀请奖励 / 余额 / 自动续费 / 对账 ---------- */
function ops_migrate_billing(): void
{
    $db = ops_pdo();
    try { $db->exec('ALTER TABLE users ADD COLUMN balance_cents INTEGER DEFAULT 0'); } catch (Exception $e) {}
    try { $db->exec('ALTER TABLE users ADD COLUMN auto_renew INTEGER DEFAULT 0'); } catch (Exception $e) {}
    $db->exec("CREATE TABLE IF NOT EXISTS invite_rewards(
      id INTEGER PRIMARY KEY AUTOINCREMENT, invite_id INTEGER NOT NULL, invitee_id INTEGER NOT NULL,
      coupon_code TEXT NOT NULL, amount_cents INTEGER NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(invitee_id))");
    $db->exec("CREATE TABLE IF NOT EXISTS balance_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      reason TEXT DEFAULT '', created_at INTEGER NOT NULL)");
    $db->exec("CREATE TABLE IF NOT EXISTS reconcile_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, checked INTEGER NOT NULL, fixed INTEGER NOT NULL,
      note TEXT DEFAULT '', created_at INTEGER NOT NULL)");
}

function invite_reward_for_order(int $orderId): void
{
    ops_migrate_extra(); ops_migrate_billing();
    $db = ops_pdo();
    $st = $db->prepare('SELECT * FROM orders WHERE id=? AND status="paid"'); $st->execute([$orderId]);
    $o = $st->fetch(); if (!$o) return;
    $uid = (int)$o['user_id'];
    // 是否被邀请，且这是首笔已支付订单
    $st = $db->prepare('SELECT * FROM invite_uses WHERE invitee_id=? LIMIT 1'); $st->execute([$uid]);
    $use = $st->fetch(); if (!$use) return;
    $st = $db->prepare('SELECT COUNT(*) FROM orders WHERE user_id=? AND status="paid"'); $st->execute([$uid]);
    if ((int)$st->fetchColumn() !== 1) return;
    $st = $db->prepare('SELECT 1 FROM invite_rewards WHERE invitee_id=?'); $st->execute([$uid]);
    if ($st->fetch()) return;
    $st = $db->prepare('SELECT * FROM invites WHERE id=?'); $st->execute([$use['invite_id']]);
    $inv = $st->fetch(); if (!$inv) return;
    $code = 'RW' . strtoupper(substr(bin2hex(random_bytes(4)), 0, 8));
    $db->prepare('INSERT INTO coupons(code,type,value_cents,percent,min_amount_cents,expires_at,max_uses,used,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)')
       ->execute([$code, 'fixed', 2000, 0, 0, date('Y-m-d', time() + 90 * 86400), 1, 0, 1, time()]);
    $db->prepare('INSERT INTO invite_rewards(invite_id,invitee_id,coupon_code,amount_cents,created_at) VALUES(?,?,?,?,?)')
       ->execute([$inv['id'], $uid, $code, 2000, time()]);
    $db->prepare('UPDATE invites SET uses=uses WHERE id=?')->execute([$inv['id']]);
    // 邮件通知邀请人
    $shopLib = '/www/wwwroot/license.moneyfly.club/public/shop/lib.php';
    if (is_file($shopLib)) {
        require_once $shopLib;
        $st = $db->prepare('SELECT email FROM users WHERE id=?'); $st->execute([$inv['user_id']]);
        $email = (string)$st->fetchColumn();
        if ($email) smtp_send($email, 'AuroraList 邀请奖励',
            "你邀请的好友已完成首单，赠送你一张 ¥20 优惠券：{$code}（90 天内有效）。\n登录 https://license.moneyfly.club/ 使用。");
    }
}

function user_balance(int $uid): int
{
    ops_migrate_billing();
    $st = ops_pdo()->prepare('SELECT balance_cents FROM users WHERE id=?'); $st->execute([$uid]);
    return (int)$st->fetchColumn();
}

function user_balance_add(int $uid, int $cents, string $reason): void
{
    ops_migrate_billing();
    ops_pdo()->prepare('UPDATE users SET balance_cents=balance_cents+? WHERE id=?')->execute([$cents, $uid]);
    ops_pdo()->prepare('INSERT INTO balance_logs(user_id,amount_cents,reason,created_at) VALUES(?,?,?,?)')
        ->execute([$uid, $cents, $reason, time()]);
}

function user_auto_renew(int $uid, bool $on): void
{
    ops_migrate_billing();
    ops_pdo()->prepare('UPDATE users SET auto_renew=? WHERE id=?')->execute([$on ? 1 : 0, $uid]);
}

function cron_auto_renew(): array
{
    ops_migrate_billing();
    $db = ops_pdo();
    $out = [];
    $st = $db->query('SELECT l.*, u.email, u.balance_cents, u.auto_renew FROM licenses l
        JOIN users u ON u.id=l.user_id
        WHERE l.revoked=0 AND u.auto_renew=1 AND date(l.expires_at) <= date("now", "+3 day")');
    foreach ($st as $l) {
        // 找到该授权对应的套餐（取最近一次订单）
        $q = $db->prepare('SELECT p.* FROM orders o JOIN packages p ON p.id=o.package_id
            WHERE o.user_id=? ORDER BY o.id DESC LIMIT 1');
        $q->execute([$l['user_id']]); $p = $q->fetch();
        if (!$p) { $out[] = ['user'=>$l['user_id'], 'skip'=>'no_package']; continue; }
        $price = (int)$p['price_cents'];
        if ((int)$l['balance_cents'] < $price) {
            $out[] = ['user'=>$l['user_id'], 'skip'=>'balance_low', 'need'=>$price/100, 'have'=>((int)$l['balance_cents'])/100];
            continue;
        }
        $db->beginTransaction();
        $db->prepare('UPDATE users SET balance_cents=balance_cents-? WHERE id=?')->execute([$price, $l['user_id']]);
        $db->prepare('INSERT INTO balance_logs(user_id,amount_cents,reason,created_at) VALUES(?,?,?,?)')
           ->execute([$l['user_id'], -$price, '自动续费：' . $p['name'], time()]);
        $trade = 'AUTO' . date('YmdHis') . random_int(100, 999);
        $db->prepare('INSERT INTO orders(user_id,package_id,amount_cents,status,trade_no,created_at,paid_at) VALUES(?,?,?,?,?,?,?)')
           ->execute([$l['user_id'], $p['id'], $price, 'paid', $trade, time(), time()]);
        $oid = (int)$db->lastInsertId();
        $db->commit();
        $lic = shop_issue_for_order($oid);
        $out[] = ['user'=>$l['user_id'], 'renewed'=>true, 'order'=>$oid, 'expires'=>$lic['expires_at'] ?? null];
    }
    return $out;
}

/* 对账：查询易支付网关，把已支付但本地 pending 的订单补回 */
function epay_query(string $tradeNo): ?array
{
    $shopLib = '/www/wwwroot/license.moneyfly.club/public/shop/lib.php';
    if (!is_file($shopLib)) return null;
    require_once $shopLib;
    $e = effective_epay();
    if (!$e['pid'] || !$e['key']) return null;
    $url = $e['gateway'] . '/api.php?' . http_build_query([
        'act' => 'order', 'pid' => $e['pid'], 'key' => $e['key'], 'out_trade_no' => $tradeNo,
    ]);
    $ch = curl_init($url);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 10]);
    $resp = curl_exec($ch); curl_close($ch);
    $d = json_decode((string)$resp, true);
    return is_array($d) ? $d : null;
}

function cron_reconcile(): array
{
    ops_migrate_extra(); ops_migrate_billing();
    $db = ops_pdo();
    $fixed = 0; $checked = 0; $note = '';
    $st = $db->query("SELECT * FROM orders WHERE status='pending' AND created_at < " . (time() - 600) . " ORDER BY id DESC LIMIT 50");
    foreach ($st as $o) {
        $checked++;
        $res = epay_query((string)$o['trade_no']);
        if ($res && (string)($res['status'] ?? '') === '1' && (float)($res['money'] ?? -1) >= ((int)$o['amount_cents'])/100 - 0.009) {
            $db->prepare("UPDATE orders SET status='paid', paid_at=? WHERE id=?")->execute([time(), $o['id']]);
            shop_issue_for_order((int)$o['id']);
            invite_reward_for_order((int)$o['id']);
            $fixed++;
        }
    }
    $db->prepare('INSERT INTO reconcile_runs(checked,fixed,note,created_at) VALUES(?,?,?,?)')
       ->execute([$checked, $fixed, $note, time()]);
    return ['checked' => $checked, 'fixed' => $fixed];
}

/* ---------- 易支付退款 / 管理员邮件通知 ---------- */
function epay_refund_order(int $orderId): array
{
    $shopLib = '/www/wwwroot/license.moneyfly.club/public/shop/lib.php';
    if (!is_file($shopLib)) return [false, '缺少支付配置'];
    require_once $shopLib;
    $e = effective_epay();
    if (!$e['pid'] || !$e['key']) return [false, '易支付未配置'];
    $st = ops_pdo()->prepare('SELECT * FROM orders WHERE id=?');
    $st->execute([$orderId]); $o = $st->fetch();
    if (!$o) return [false, '订单不存在'];
    $params = ['act'=>'refund','pid'=>$e['pid'],'key'=>$e['key'],
               'out_trade_no'=>$o['trade_no'],'money'=>number_format(((int)$o['amount_cents'])/100,2,'.','')];
    $url = 'https://mzf.mapay.cn/xpay/epay/api.php?' . http_build_query($params);
    $ch = curl_init($url);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER=>true, CURLOPT_TIMEOUT=>15]);
    $resp = curl_exec($ch); $err = curl_error($ch); curl_close($ch);
    if ($resp === false) return [false, '网关请求失败：' . $err];
    $d = json_decode((string)$resp, true);
    if (is_array($d) && ((int)($d['code'] ?? -1) === 1 || (string)($d['status'] ?? '') === '1')) {
        return [true, ''];
    }
    return [false, is_array($d) ? (string)($d['msg'] ?? json_encode($d, JSON_UNESCAPED_UNICODE)) : substr((string)$resp,0,120)];
}

function notify_admin_mail(string $subject, string $body): void
{
    $shopLib = '/www/wwwroot/license.moneyfly.club/public/shop/lib.php';
    if (!is_file($shopLib)) return;
    require_once $shopLib;
    $to = get_setting('admin_notice_email', '3219904322@qq.com');
    @smtp_send($to, $subject, $body);
}
