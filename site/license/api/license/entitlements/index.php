<?php
declare(strict_types=1);
require dirname(__DIR__, 3).'/lib.php';
$in = body();
$mc = trim((string)($in['machine_code'] ?? ''));
if (!$mc) out(['token'=>''], 0, '缺 machine_code');
$st = db()->prepare('SELECT * FROM licenses WHERE machine_code=? AND revoked=0 ORDER BY id DESC LIMIT 1');
$st->execute([$mc]); $l = $st->fetch(PDO::FETCH_ASSOC);
if (!$l) out(['token'=>'']);
if (strtotime($l['expires_at']) < time()) out(['token'=>'']);
out(['token'=>$l['token']]);
