<?php
declare(strict_types=1);
// 重新解析已上传日志，重建事件统计
require __DIR__ . '/../app/bootstrap.php';
require __DIR__ . '/../app/pages.php';

db()->exec('DELETE FROM usage_events');
$logs = db()->query('SELECT id, identity, content FROM app_logs ORDER BY id')->fetchAll();
$n = 0;
foreach ($logs as $row) {
    [$events, $errors] = parse_log_events($row['identity'], $row['content']);
    $up = db()->prepare('UPDATE app_logs SET event_count = ?, error_count = ? WHERE id = ?');
    $up->execute([$events, $errors, $row['id']]);
    $n += $events;
}
echo "reparsed logs=" . count($logs) . " events=" . $n . "\n";
