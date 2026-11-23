<?php
require '/www/server/ae-license/app/shop_ops.php';
$sent = cron_send_expiry_reminders();
$cleaned = cron_cleanup_orders();
echo json_encode(['reminders'=>$sent, 'expired_orders'=>$cleaned], JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT), "\n";
