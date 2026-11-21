<?php
declare(strict_types=1);

function cfg(): array { static $c = null; if ($c === null) { $c = require __DIR__.'/config.php'; } return $c; }

function db(): PDO {
    static $pdo = null;
    if ($pdo) return $pdo;
    $path = cfg()['db'];
    @mkdir(dirname($path), 0775, true);
    $pdo = new PDO('sqlite:'.$path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode=WAL');
    $pdo->exec("CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL, pass TEXT NOT NULL, name TEXT DEFAULT '',
      created_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS packages(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      platforms TEXT NOT NULL, features TEXT NOT NULL,
      price_cents INTEGER NOT NULL, days INTEGER NOT NULL DEFAULT 365,
      active INTEGER NOT NULL DEFAULT 1, sort INTEGER DEFAULT 0,
      description TEXT DEFAULT '', created_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS orders(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      package_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', trade_no TEXT UNIQUE,
      pay_type TEXT DEFAULT '', created_at INTEGER NOT NULL, paid_at INTEGER DEFAULT 0)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS licenses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      order_id INTEGER NOT NULL, machine_code TEXT DEFAULT '',
      token TEXT NOT NULL, expires_at TEXT NOT NULL, platforms TEXT NOT NULL,
      revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS heartbeats(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      machine_code TEXT DEFAULT '', payload TEXT NOT NULL, ts INTEGER NOT NULL)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)");
    // 兼容旧库：设备解绑时间字段
    try { $pdo->exec('ALTER TABLE licenses ADD COLUMN last_unbind INTEGER DEFAULT 0'); } catch (Exception $e) {}
    try { $pdo->exec('ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 1'); } catch (Exception $e) {}
    $pdo->exec("CREATE TABLE IF NOT EXISTS email_codes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, code TEXT NOT NULL,
      purpose TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER DEFAULT 0, created_at INTEGER NOT NULL)");
    $n = (int)$pdo->query('SELECT COUNT(*) FROM packages')->fetchColumn();
    if ($n === 0) {
        $now = time();
        $st = $pdo->prepare('INSERT INTO packages(name,platforms,features,price_cents,days,active,sort,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)');
        $st->execute(['Alibaba 基础', '["alibaba"]', '["listing","category"]', 9900, 365, 1, 1, 'Alibaba 国际站刊登与类目', $now]);
        $st->execute(['Alibaba + AI', '["alibaba"]', '["listing","category","ai_text","ai_image"]', 19900, 365, 1, 2, '含 AI 文案与图片', $now]);
        $st->execute(['Alibaba 全功能', '["alibaba"]', '["listing","category","trade","ai_text","ai_image","audit"]', 29900, 365, 1, 3, '含价格贸易与审核', $now]);
        $st->execute(['Amazon 基础', '["amazon"]', '["listing","category"]', 9900, 365, 1, 4, 'Amazon 刊登与类目', $now]);
        $st->execute(['双平台版', '["alibaba","amazon"]', '["listing","category","ai_text","ai_image","trade"]', 39900, 365, 1, 5, 'Alibaba + Amazon', $now]);
    }
    return $pdo;
}

function j($v): string { return json_encode($v, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES); }

function out($data, $code = 0, $msg = 'ok') {
    header('Content-Type: application/json; charset=utf-8');
    echo j(['code'=>$code, 'msg'=>$msg, 'data'=>$data]);
    exit;
}

function fail($msg, $code = 1) { out(null, $code, $msg); }

function body(): array {
    $raw = file_get_contents('php://input');
    $d = json_decode($raw ?: '[]', true);
    if (!is_array($d)) $d = [];
    return array_merge($_GET, $_POST, $d);
}

function now(): int { return time(); }

function spend_days(string $expires_at, int $days): string {
    $base = max(now(), strtotime($expires_at) ?: now());
    return date('Y-m-d', $base + $days * 86400);
}

// 与 Python 客户端一致的 canonical JSON（递归排序 + 紧凑 + 中文不转义）
function canonical($v): string {
    if (is_array($v)) {
        $isList = array_keys($v) === range(0, count($v) - 1);
        if ($isList) {
            $parts = [];
            foreach ($v as $vv) { $parts[] = canonical($vv); }
            return '['.implode(',', $parts).']';
        }
        ksort($v);
        $parts = [];
        foreach ($v as $k => $vv) {
            $parts[] = json_encode((string)$k, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).':'.canonical($vv);
        }
        return '{'.implode(',', $parts).'}';
    }
    if (is_string($v)) return json_encode($v, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
    if (is_bool($v)) return $v ? 'true' : 'false';
    if ($v === null) return 'null';
    return (string)$v;
}

function sign_payload(array $payload): string {
    return hash_hmac('sha256', canonical($payload), cfg()['sign_key']);
}

function issue_token(string $machine, array $platforms, string $expires_at): string {
    $payload = [
        'machine_code' => $machine,
        'platforms' => array_map(function($f){ sort($f); return array_values(array_unique($f)); }, $platforms),
        'expires_at' => $expires_at,
        'issued_at' => now(),
    ];
    return j(['payload'=>$payload, 'sig'=>sign_payload($payload)]);
}

// ---------- 用户 ----------
function current_user(): ?array {
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    $uid = (int)($_SESSION['uid'] ?? 0);
    if (!$uid) return null;
    $st = db()->prepare('SELECT id,email,name,created_at FROM users WHERE id=?');
    $st->execute([$uid]);
    $u = $st->fetch(PDO::FETCH_ASSOC);
    return $u ?: null;
}

function require_user(): array {
    $u = current_user();
    if (!$u) fail('请先登录', 401);
    return $u;
}

function is_admin(): bool {
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    return !empty($_SESSION['admin']);
}

function require_admin() { if (!is_admin()) fail('需要管理员登录', 403); }

function packages(): array {
    $rows = db()->query('SELECT * FROM packages WHERE active=1 ORDER BY sort,id')->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$r) {
        $r['platforms'] = json_decode($r['platforms'], true) ?: [];
        $r['features'] = json_decode($r['features'], true) ?: [];
        $r['price'] = $r['price_cents'] / 100;
    }
    return $rows;
}

function license_for_user(int $uid): ?array {
    $st = db()->prepare('SELECT * FROM licenses WHERE user_id=? AND revoked=0 ORDER BY id DESC LIMIT 1');
    $st->execute([$uid]);
    $l = $st->fetch(PDO::FETCH_ASSOC);
    return $l ?: null;
}

// ---------- 可在线修改的设置（管理后台「支付设置」等） ----------
function setting_defaults(): array {
    $c = cfg();
    return [
      'site_name' => $c['site_name'],
      'epay_gateway' => $c['epay_gateway'],
      'epay_pid' => $c['epay_pid'],
      'epay_key' => $c['epay_key'],
      'epay_type' => $c['epay_type'],
      'allow_mock_pay' => $c['allow_mock_pay'] ? '1' : '0',
      'announcement' => '',
      'require_verify' => '0',
      'smtp_host' => '', 'smtp_port' => '465', 'smtp_user' => '',
      'smtp_pass' => '', 'smtp_secure' => 'ssl', 'smtp_from' => '',
      'debug_codes' => '0',
    ];
}

function get_setting(string $k, ?string $default = null): string {
    $st = db()->prepare('SELECT v FROM settings WHERE k=?');
    $st->execute([$k]);
    $r = $st->fetch(PDO::FETCH_ASSOC);
    if ($r !== false) return (string)$r['v'];
    $d = setting_defaults();
    return $default ?? ($d[$k] ?? '');
}

function set_setting(string $k, string $v): void {
    $st = db()->prepare('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v');
    $st->execute([$k, $v]);
}

function effective_epay(): array {
    return [
      'gateway' => rtrim(get_setting('epay_gateway'), '/'),
      'pid' => get_setting('epay_pid'),
      'key' => get_setting('epay_key'),
      'type' => get_setting('epay_type', 'alipay'),
    ];
}

// ---------- 邮件 ----------
function smtp_send(string $to, string $subject, string $body): array {
    $host = trim(get_setting('smtp_host'));
    if (!$host) return [false, '未配置 SMTP'];
    $port = (int)get_setting('smtp_port', '465');
    $user = get_setting('smtp_user');
    $pass = get_setting('smtp_pass');
    $secure = get_setting('smtp_secure', 'ssl');
    $from = get_setting('smtp_from') ?: $user;
    $remote = ($secure === 'ssl' ? 'ssl://' : '').$host.':'.$port;
    $ctx = stream_context_create(['ssl' => ['verify_peer' => false, 'verify_peer_name' => false]]);
    $fp = @stream_socket_client($remote, $errno, $errstr, 12, STREAM_CLIENT_CONNECT, $ctx);
    if (!$fp) return [false, "连接失败：$errstr"];
    $read = function() use ($fp) {
        $data = '';
        while (($line = fgets($fp, 515)) !== false) {
            $data .= $line;
            if (isset($line[3]) && $line[3] === ' ') break;
        }
        return $data;
    };
    $cmd = function(string $c, string $expect = '2') use ($fp, $read) {
        fwrite($fp, $c."\r\n");
        $r = $read();
        return strpos($r, $expect) === 0 ? [true, $r] : [false, $r];
    };
    $read();
    [$ok, $r] = $cmd('EHLO auroralist', '2');
    if (!$ok) { fclose($fp); return [false, "EHLO 失败"]; }
    if ($secure === 'tls') {
        [$ok] = $cmd('STARTTLS', '2');
        if (!$ok) { fclose($fp); return [false, 'STARTTLS 失败']; }
        stream_socket_enable_crypto($fp, true, STREAM_CRYPTO_METHOD_TLS_CLIENT);
        [$ok] = $cmd('EHLO auroralist', '2');
        if (!$ok) { fclose($fp); return [false, 'EHLO(TLS) 失败']; }
    }
    if ($user) {
        [$ok] = $cmd('AUTH LOGIN', '3');
        if (!$ok) { fclose($fp); return [false, 'AUTH 失败']; }
        [$ok] = $cmd(base64_encode($user), '3');
        if (!$ok) { fclose($fp); return [false, '用户名失败']; }
        [$ok] = $cmd(base64_encode($pass), '2');
        if (!$ok) { fclose($fp); return [false, '密码失败']; }
    }
    [$ok] = $cmd('MAIL FROM:<'.$from.'>', '2');
    if (!$ok) { fclose($fp); return [false, 'MAIL FROM 失败']; }
    [$ok] = $cmd('RCPT TO:<'.$to.'>', '2');
    if (!$ok) { fclose($fp); return [false, 'RCPT TO 失败']; }
    [$ok] = $cmd('DATA', '3');
    if (!$ok) { fclose($fp); return [false, 'DATA 失败']; }
    $headers = 'From: AuroraList <'.$from.'>'."\r\n"
        .'To: <'.$to.'>'."\r\n"
        .'Subject: =?UTF-8?B?'.base64_encode($subject).'?='."\r\n"
        .'MIME-Version: 1.0'."\r\n"
        .'Content-Type: text/plain; charset=UTF-8'."\r\n"
        .'Content-Transfer-Encoding: base64'."\r\n\r\n";
    fwrite($fp, $headers.chunk_split(base64_encode($body))."\r\n.\r\n");
    $read();
    fclose($fp);
    return [true, ''];
}

function send_code_email(string $email, string $code, string $purpose): array {
    $titles = ['verify'=>'注册验证码', 'reset'=>'找回密码验证码'];
    $subject = 'AuroraList 授权中心 · '.($titles[$purpose] ?? '验证码');
    $body = "你的验证码是：$code\n有效期 10 分钟。如果不是本人操作请忽略。";
    [$ok, $err] = smtp_send($email, $subject, $body);
    if (!$ok) {
        // 兜底：PHP mail()
        $ok = @mail($email, $subject, $body,
            'From: '.get_setting('smtp_from','no-reply@auroralist.local')."\r\nContent-Type: text/plain; charset=UTF-8");
        if (!$ok) return [false, $err ?: '邮件发送失败'];
    }
    return [true, ''];
}

function make_code(): string { return (string)random_int(100000, 999999); }

function save_code(string $email, string $purpose): string {
    $code = make_code();
    db()->prepare('UPDATE email_codes SET used=1 WHERE email=? AND purpose=? AND used=0')->execute([$email, $purpose]);
    db()->prepare('INSERT INTO email_codes(email,code,purpose,expires,created_at) VALUES(?,?,?,?,?)')
        ->execute([$email, $code, $purpose, now()+600, now()]);
    return $code;
}

function check_code(string $email, string $code, string $purpose): bool {
    $st = db()->prepare('SELECT * FROM email_codes WHERE email=? AND code=? AND purpose=? AND used=0 ORDER BY id DESC LIMIT 1');
    $st->execute([$email, $code, $purpose]);
    $r = $st->fetch(PDO::FETCH_ASSOC);
    if (!$r || (int)$r['expires'] < now()) return false;
    db()->prepare('UPDATE email_codes SET used=1 WHERE id=?')->execute([$r['id']]);
    return true;
}
