<?php
declare(strict_types=1);

// 用法: php bin/init.php <用户名> <密码>
require __DIR__ . '/../app/bootstrap.php';

ensure_keypair();
db();

$username = $argv[1] ?? 'admin';
$password = $argv[2] ?? '';
if ($password === '') {
    fwrite(STDERR, "用法: php bin/init.php <用户名> <密码>\n");
    exit(1);
}
if (strlen($password) < 10) {
    fwrite(STDERR, "密码至少 10 位\n");
    exit(1);
}

$hash = password_hash($password, PASSWORD_BCRYPT, ['cost' => 12]);
$st = db()->prepare('SELECT id FROM admins WHERE username = ?');
$st->execute([$username]);
$row = $st->fetch();

if ($row) {
    $up = db()->prepare('UPDATE admins SET pass_hash = ?, is_active = 1 WHERE id = ?');
    $up->execute([$hash, $row['id']]);
    echo "已重置管理员密码: {$username}\n";
} else {
    $ins = db()->prepare('INSERT INTO admins (username, pass_hash, role, is_active, created_at) VALUES (?,?,?,1,?)');
    $ins->execute([$username, $hash, 'owner', now()]);
    echo "已创建管理员: {$username}\n";
}

setting_set('default_days', (string)cfg('default_days'));
echo "数据库: " . cfg('db_path') . "\n";
echo "公钥: " . cfg('key_dir') . "/public.pem\n";
