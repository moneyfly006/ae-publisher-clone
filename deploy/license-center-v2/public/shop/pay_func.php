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
function issue_for_order(int $orderId): ?array { return lc_fulfill($orderId); }

