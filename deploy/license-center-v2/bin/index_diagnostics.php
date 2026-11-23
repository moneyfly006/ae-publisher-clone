<?php
// Safe, resumable projection of existing logs. Never deletes originals.
$app=getenv('AE_LICENSE_DIR') ?: '/www/server/ae-license';
require $app.'/app/bootstrap.php';require $app.'/app/pages.php';
require $app.'/app/shop_admin.php';require $app.'/app/shop_ops.php';require $app.'/app/customer_center.php';require $app.'/app/diagnostic_center.php';require $app.'/app/system_settings.php';
$logs=0;$events=0;$batches=0;
do{$r=lc_backfill(100);$logs+=$r['logs'];$events+=$r['events'];$batches++;}while($r['logs']===100 && $batches<10000);
echo json_encode(['indexed_logs'=>$logs,'added_events'=>$events,'last_log_id'=>$r['last_log_id']]),"\n";
