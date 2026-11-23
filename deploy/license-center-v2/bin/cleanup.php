<?php
declare(strict_types=1);
// 清理过期数据：日志与事件默认保留 90 天
require __DIR__ . '/../app/bootstrap.php';

$days = (int)(getenv('AE_RETENTION_DAYS') ?: 90);
if ($days < 7) { $days = 7; }
$cut = date('Y-m-d H:i:s', time() - $days * 86400);

$n1 = db()->prepare('DELETE FROM app_logs WHERE created_at < ?');
$n1->execute([$cut]);
$n2 = db()->prepare('DELETE FROM usage_events WHERE created_at < ?');
$n2->execute([$cut]);
$n3 = db()->prepare('DELETE FROM login_attempts WHERE created_at < ?');
$n3->execute([$cut]);
db()->exec('PRAGMA wal_checkpoint(TRUNCATE)');

echo "cleanup done: logs=" . $n1->rowCount() . " events=" . $n2->rowCount() . " logins=" . $n3->rowCount() . " (keep {$days}d)\n";
