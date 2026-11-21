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
    require_admin();
    $rows = ops_pdo()->query('SELECT r.*, u.email, o.trade_no FROM refunds r
        LEFT JOIN users u ON u.id=r.user_id LEFT JOIN orders o ON o.id=r.order_id
        ORDER BY r.id DESC LIMIT 200')->fetchAll();
    render('shop_refunds', ['title' => '退款管理', 'rows' => $rows]);
}

function page_shop_refund_create(): void
{
    require_admin(); csrf_check();
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
    require_admin(); csrf_check();
    $db = ops_pdo();
    $st = $db->prepare('SELECT * FROM refunds WHERE id=?'); $st->execute([$id]);
    $r = $st->fetch();
    if (!$r) { flash('bad', '退款单不存在'); redirect('/admin/shop-refunds'); }
    $db->beginTransaction();
    $db->prepare("UPDATE refunds SET status='done', handled_at=? WHERE id=?")->execute([time(), $id]);
    $db->prepare("UPDATE orders SET status='refunded' WHERE id=?")->execute([$r['order_id']]);
    $db->prepare('UPDATE licenses SET revoked=1 WHERE user_id=? AND revoked=0')->execute([$r['user_id']]);
    $db->commit();
    audit('完成退款并取消授权', 'refund:' . $id);
    flash('ok', '退款完成，订单已标记退款、该用户授权已取消');
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
