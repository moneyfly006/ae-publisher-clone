<div class="card">
  <?php if (!$rows): ?><div class="empty">暂无订单</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>订单号</th><th>用户</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td class="mono"><?= e($r['trade_no']) ?></td>
        <td><?= e($r['email'] ?? '') ?></td>
        <td><?= e($r['package_name'] ?? '') ?></td>
        <td>¥<?= number_format(((int)$r['amount_cents']) / 100, 2) ?></td>
        <td><span class="badge <?= $r['status'] === 'paid' ? 'ok' : 'warn' ?>"><?= e(['pending'=>'待支付','paid'=>'已支付','expired'=>'已关闭','refunded'=>'已退款'][$r['status']] ?? $r['status']) ?></span></td>
        <td><?= e(date('Y-m-d H:i', (int)$r['created_at'])) ?></td>
        <td class="ops">
          <?php if (in_array($r['status'],['pending','expired'],true)): ?>
          <form method="post" action="/admin/shop-orders/<?= (int)$r['id'] ?>/paid" onsubmit="return confirm('标记已支付并开通？')">
            <?= csrf_field() ?><button class="btn ghost sm" type="submit">标记已付并开通</button>
          </form>
          <?php else: ?>—<?php endif; ?>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
