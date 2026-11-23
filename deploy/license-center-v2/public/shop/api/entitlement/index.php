<?php
declare(strict_types=1);
require (getenv('LICENSE_CENTER_SHARED') ?: '/www/server/ae-license/shared').'/entitlements.php';
header('Content-Type: application/json; charset=utf-8');header('Cache-Control: no-store');
$in=json_decode(file_get_contents('php://input') ?: '[]',true) ?: $_GET;
$e=lc_for_machine(trim((string)($in['machine_code'] ?? '')));
echo json_encode(['code'=>0,'data'=>['token'=>$e['token'] ?? '']],JSON_UNESCAPED_UNICODE);
