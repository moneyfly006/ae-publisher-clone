<?php
require '/www/server/ae-license/app/shop_ops.php';
$re = cron_reconcile();
$ar = cron_auto_renew();
echo json_encode(['reconcile'=>$re, 'auto_renew'=>$ar], JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT), "\n";
