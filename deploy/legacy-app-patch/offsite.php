<?php
$dir = '/www/backups/license';
$log = '/www/wwwlogs/license_offsite.log';
$line = date('c') . ' ';
$rclone = trim((string)@shell_exec('command -v rclone 2>/dev/null'));
$remote = getenv('LICENSE_BACKUP_REMOTE') ?: 'oss:auroralist-backup/license-backups';
if ($rclone && $remote) {
    $out = []; $code = 0;
    exec("$rclone copy $dir $remote --max-age 48h 2>&1", $out, $code);
    file_put_contents($log, $line . ($code === 0 ? 'RCLONE_OK ' : 'RCLONE_FAIL ') . implode(' | ', array_slice($out, -3)) . "\n", FILE_APPEND);
    exit;
}
// 没有对象存储凭据：尝试挂载点
$mount = getenv('LICENSE_BACKUP_MOUNT') ?: '/mnt/oss/license';
if (is_dir('/mnt/oss') || is_dir($mount)) {
    @mkdir($mount, 0775, true);
    $ok = true;
    foreach (glob("$dir/*.db") as $f) { if (!@copy($f, $mount . '/' . basename($f))) $ok = false; }
    file_put_contents($log, $line . ($ok ? 'MOUNT_COPY_OK ' : 'MOUNT_COPY_FAIL ') . $mount . "\n", FILE_APPEND);
    exit;
}
file_put_contents($log, $line . "SKIP 未配置对象存储（设置 LICENSE_BACKUP_REMOTE + rclone，或挂载 /mnt/oss）\n", FILE_APPEND);
