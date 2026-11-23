<?php
declare(strict_types=1);

define('APP_ROOT', dirname(__DIR__));
$GLOBALS['cfg'] = require APP_ROOT . '/app/config.php';
date_default_timezone_set($GLOBALS['cfg']['timezone']);

require APP_ROOT . '/app/db.php';
require APP_ROOT . '/app/crypto.php';

function cfg(string $key)
{
    return $GLOBALS['cfg'][$key] ?? null;
}

function db(): PDO
{
    return DB::pdo();
}

function e($s): string
{
    return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function now(): string
{
    return date('Y-m-d H:i:s');
}

function today(): string
{
    return date('Y-m-d');
}

function client_ip(): string
{
    foreach (['HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR'] as $k) {
        if (!empty($_SERVER[$k])) {
            $ip = trim(explode(',', $_SERVER[$k])[0]);
            if (filter_var($ip, FILTER_VALIDATE_IP)) {
                return $ip;
            }
        }
    }
    return $_SERVER['REMOTE_ADDR'] ?? '';
}

function user_agent(): string
{
    return substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 250);
}

/* ---------------- session ---------------- */

function session_boot(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $secure = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_name((string)cfg('session_name'));
    session_set_cookie_params([
        'lifetime' => 0,
        'path'     => '/',
        'httponly' => true,
        'secure'   => $secure,
        'samesite' => 'Lax',
    ]);
    session_start();
}

/* ---------------- csrf ---------------- */

function csrf_token(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf'];
}

function csrf_field(): string
{
    return '<input type="hidden" name="_csrf" value="' . e(csrf_token()) . '">';
}

function csrf_check(): void
{
    $ok = isset($_POST['_csrf']) && hash_equals($_SESSION['csrf'] ?? '', (string)$_POST['_csrf']);
    if (!$ok) {
        http_response_code(419);
        exit('安全令牌校验失败，请返回刷新页面后重试。');
    }
}

/* ---------------- flash ---------------- */

function flash(string $type, string $msg): void
{
    $_SESSION['flash'][] = ['type' => $type, 'msg' => $msg];
}

function flash_take(): array
{
    $f = $_SESSION['flash'] ?? [];
    unset($_SESSION['flash']);
    return $f;
}

/* ---------------- auth ---------------- */

function current_admin(): ?array
{
    if (empty($_SESSION['admin_id'])) {
        return null;
    }
    static $cache = null;
    if ($cache !== null && (int)$cache['id'] === (int)$_SESSION['admin_id']) {
        return $cache;
    }
    $st = db()->prepare('SELECT * FROM admins WHERE id = ? AND is_active = 1');
    $st->execute([$_SESSION['admin_id']]);
    $row = $st->fetch();
    $cache = $row ?: null;
    if (!$row) {
        unset($_SESSION['admin_id']);
    }
    return $cache;
}

function require_admin(): array
{
    $a = current_admin();
    if (!$a) {
        header('Location: ' . cfg('admin_path') . '/login');
        exit;
    }
    return $a;
}

function login_attempt_record(string $ip, string $username, bool $ok): void
{
    $st = db()->prepare('INSERT INTO login_attempts (ip, username, ok, created_at) VALUES (?,?,?,?)');
    $st->execute([$ip, $username, $ok ? 1 : 0, now()]);
}

function login_locked(string $ip): bool
{
    $st = db()->prepare('SELECT COUNT(*) c FROM login_attempts WHERE ip = ? AND ok = 0 AND created_at > ?');
    $st->execute([$ip, date('Y-m-d H:i:s', time() - 600)]);
    return (int)$st->fetch()['c'] >= 20;
}

function audit(string $action, string $target = '', string $detail = '', string $actor = ''): void
{
    $admin = $actor !== '' ? $actor : (current_admin()['username'] ?? 'api');
    $st = db()->prepare('INSERT INTO audit_logs (actor, action, target, detail, ip, ua, created_at) VALUES (?,?,?,?,?,?,?)');
    $st->execute([$admin, $action, $target, $detail, client_ip(), user_agent(), now()]);
}

/* ---------------- license helpers ---------------- */

function license_effective_status(array $row): string
{
    if (($row['status'] ?? '') === 'revoked') {
        return 'revoked';
    }
    if (($row['status'] ?? '') === 'disabled') {
        return 'disabled';
    }
    if (($row['expires_at'] ?? '') < today()) {
        return 'expired';
    }
    return 'active';
}

function status_label(string $s): string
{
    return [
        'active'   => '正常',
        'expired'  => '已过期',
        'revoked'  => '已吊销',
        'disabled' => '已停用',
    ][$s] ?? $s;
}

function status_class(string $s): string
{
    return [
        'active'   => 'ok',
        'expired'  => 'warn',
        'revoked'  => 'bad',
        'disabled' => 'muted',
    ][$s] ?? 'muted';
}

function days_left(string $expires): int
{
    $a = new DateTimeImmutable(today());
    $b = new DateTimeImmutable($expires);
    return (int)$a->diff($b)->format('%r%a');
}

function setting_get(string $key, string $default = ''): string
{
    $st = db()->prepare('SELECT value FROM settings WHERE key = ?');
    $st->execute([$key]);
    $v = $st->fetchColumn();
    return $v === false ? $default : (string)$v;
}

function setting_set(string $key, string $value): void
{
    $st = db()->prepare('INSERT INTO settings (key, value) VALUES (?,?)
                         ON CONFLICT(key) DO UPDATE SET value = excluded.value');
    $st->execute([$key, $value]);
}

/* ---------------- output ---------------- */

function json_out($data, int $code = 200): void
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function render(string $view, array $data = []): void
{
    if(function_exists('lc_is_settings_request')&&lc_is_settings_request())$data['title']='系统设置 · '.lc_settings_sections()[lc_settings_section()];
    extract($data, EXTR_SKIP);
    $viewFile = APP_ROOT . '/app/views/' . $view . '.php';
    if (!is_file($viewFile)) {
        http_response_code(500);
        exit('view not found: ' . e($view));
    }
    ob_start();
    require $viewFile;
    $content = ob_get_clean();
    require APP_ROOT . '/app/views/layout.php';
}

function redirect(string $path): void
{
    header('Location: ' . $path);
    exit;
}
