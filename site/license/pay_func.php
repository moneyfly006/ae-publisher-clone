<?php
declare(strict_types=1);
// 从 api.php 抽出的公共函数（发授权）
function pf_map(): array {
    return [
      'alibaba'=>['listing','category','trade','ai_text','ai_image','audit'],
      'aliexpress'=>['listing','category','pricing','variants','ai_text','ai_image','audit'],
      'amazon'=>['listing','category','ai_text','ai_image','audit','fba'],
      'tiktok'=>['listing','ai_text','ai_image'],
      'shopee'=>['listing','ai_text','ai_image'],
    ];
}
function issue_for_order(int $orderId): ?array {
    $db = db();
    $st = $db->prepare('SELECT * FROM orders WHERE id=?');
    $st->execute([$orderId]); $o = $st->fetch(PDO::FETCH_ASSOC);
    if (!$o || $o['status'] !== 'paid') return null;
    $st = $db->prepare('SELECT * FROM packages WHERE id=?');
    $st->execute([$o['package_id']]); $p = $st->fetch(PDO::FETCH_ASSOC);
    if (!$p) return null;
    $platforms = json_decode($p['platforms'], true) ?: [];
    $features = json_decode($p['features'], true) ?: [];
    $map = pf_map(); $payloadPlats = [];
    foreach ($platforms as $pid) {
        $feat = array_values(array_intersect($features, $map[$pid] ?? []));
        if ($feat) $payloadPlats[$pid] = $feat;
    }
    $prev = license_for_user((int)$o['user_id']);
    $expires = spend_days($prev['expires_at'] ?? '', (int)$p['days']);
    $machine = $prev['machine_code'] ?? '';
    $token = issue_token((string)$machine, $payloadPlats, $expires);
    $db->prepare('INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,?,?,?,?,?,?)')
       ->execute([$o['user_id'], $orderId, $machine, $token, $expires, j($payloadPlats), now()]);
    return ['token'=>$token, 'expires_at'=>$expires, 'platforms'=>$payloadPlats];
}
