<?php
declare(strict_types=1);
require dirname(__DIR__).'/lib.php';
require dirname(__DIR__).'/pay_func.php';

$in = array_merge($_GET, $_POST);
$sign = (string)($in['sign'] ?? '');
unset($in['sign']);
unset($in['sign_type']);   // 易支付回调会带 sign_type，不参与签名
ksort($in);
$epay = effective_epay();
$calc = md5(urldecode(http_build_query($in)).$epay['key']);
if (!$sign || strtolower($calc) !== strtolower($sign)) { echo 'sign error'; exit; }
if (($in['trade_status'] ?? '') !== 'TRADE_SUCCESS') { echo 'fail'; exit; }
$trade = (string)($in['out_trade_no'] ?? '');
$st = db()->prepare('SELECT * FROM orders WHERE trade_no=?');
$st->execute([$trade]); $o = $st->fetch(PDO::FETCH_ASSOC);
if (!$o) { echo 'order not found'; exit; }
$money = (float)($in['money'] ?? 0);
if (abs($money - ((int)$o['amount_cents']) / 100) > 0.009) {
    echo 'amount mismatch';
    exit;
}
if ($o['status'] === 'refunded') { echo 'refunded'; exit; }
try { lc_mark_paid((int)$o['id'],(string)($in['type'] ?? '')); } catch(Throwable $e) { http_response_code(503);echo 'retry';exit; }
echo 'success';
