<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无用户</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>邮箱</th><th>称呼</th><th>有效授权</th><th>到期</th><th>已付订单</th><th>注册时间</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td><?= e($r['email']) ?></td>
        <td><?= e($r['name'] ?? '') ?></td>
        <td><?= (int)$r['lic_count'] ?></td>
        <td><?= e($r['expires_at'] ?? '—') ?></td>
        <td><?= (int)$r['paid_orders'] ?></td>
        <td><?= e(date('Y-m-d', (int)$r['created_at'])) ?></td>
        <td class="ops"><a class="btn ghost sm" href="/admin/shop-users/<?= (int)$r['id'] ?>">管理</a></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
