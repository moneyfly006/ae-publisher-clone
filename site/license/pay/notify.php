<?php
declare(strict_types=1);
require dirname(__DIR__).'/lib.php';
require dirname(__DIR__).'/pay_func.php';

$in = array_merge($_GET, $_POST);
$sign = (string)($in['sign'] ?? '');
unset($in['sign']);
ksort($in);
$calc = md5(urldecode(http_build_query($in)).cfg()['epay_key']);
if (!$sign || strtolower($calc) !== strtolower($sign)) { echo 'sign error'; exit; }
if (($in['trade_status'] ?? '') !== 'TRADE_SUCCESS') { echo 'fail'; exit; }
$trade = (string)($in['out_trade_no'] ?? '');
$st = db()->prepare('SELECT * FROM orders WHERE trade_no=?');
$st->execute([$trade]); $o = $st->fetch(PDO::FETCH_ASSOC);
if (!$o) { echo 'order not found'; exit; }
if ($o['status'] !== 'paid') {
    db()->prepare("UPDATE orders SET status='paid', paid_at=?, pay_type=? WHERE id=?")
        ->execute([now(), (string)($in['type'] ?? ''), $o['id']]);
    issue_for_order((int)$o['id']);
}
echo 'success';
