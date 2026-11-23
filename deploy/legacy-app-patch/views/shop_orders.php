<div class="card" style="padding:8px 16px">
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
        <td><span class="badge <?= $r['status'] === 'paid' ? 'ok' : 'warn' ?>"><?= $r['status'] === 'paid' ? '已支付' : '待支付' ?></span></td>
        <td><?= e(date('Y-m-d H:i', (int)$r['created_at'])) ?></td>
        <td class="ops">
          <?php if ($r['status'] === 'paid'): ?>
          <form method="post" action="/admin/shop-refunds" onsubmit="return confirm('为该订单创建退款单？')" style="display:inline">
            <?= csrf_field() ?><input type="hidden" name="order_id" value="<?= (int)$r['id'] ?>">
            <input type="hidden" name="reason" value="后台发起退款">
            <button class="btn ghost sm danger" type="submit">退款</button>
          </form>
          <?php endif; ?>
          <?php if ($r['status'] !== 'paid'): ?>
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
