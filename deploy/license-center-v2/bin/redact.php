<?php
declare(strict_types=1);
// 对已上传的历史日志做脱敏，并重建事件统计
require __DIR__ . '/../app/bootstrap.php';
require __DIR__ . '/../app/pages.php';

db()->exec('DELETE FROM usage_events');
$logs = db()->query('SELECT id, identity, content FROM app_logs ORDER BY id')->fetchAll();
$totalHits = 0; $n = 0;
foreach ($logs as $row) {
    $hits = 0;
    $clean = sanitize_log($row['content'], $hits);
    $totalHits += $hits;
    $up = db()->prepare('UPDATE app_logs SET content = ?, redacted = ? WHERE id = ?');
    $up->execute([$clean, $hits, $row['id']]);
    [$events, $errors] = parse_log_events($row['identity'], $clean);
    $up = db()->prepare('UPDATE app_logs SET event_count = ?, error_count = ? WHERE id = ?');
    $up->execute([$events, $errors, $row['id']]);
    $n += $events;
}
echo "redacted logs=" . count($logs) . " hits=" . $totalHits . " events=" . $n . "\n";
