<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无发票申请</div><?php else: ?>
  <table><thead><tr><th>ID</th><th>用户</th><th>抬头</th><th>税号</th><th>金额</th><th>状态</th><th>时间</th><th style="text-align:right">操作</th></tr></thead><tbody>
  <?php foreach ($rows as $r): ?>
    <tr><td><?= (int)$r['id'] ?></td><td><?= e($r['user_email'] ?? '') ?></td><td><?= e($r['title']) ?></td>
      <td class="mono"><?= e($r['tax_no']) ?></td><td>¥<?= number_format(((int)$r['amount_cents'])/100,2) ?></td>
      <td><span class="badge <?= $r['status']==='issued'?'ok':'warn' ?>"><?= $r['status']==='issued'?'已开票':'待开票' ?></span></td>
      <td><?= e(date('Y-m-d H:i',(int)$r['created_at'])) ?></td>
      <td class="ops"><?php if ($r['status']!=='issued'): ?>
        <form method="post" action="/admin/shop-invoices/<?= (int)$r['id'] ?>/issue"><?= csrf_field() ?>
          <button class="btn ghost sm" type="submit">标记已开票</button></form>
      <?php else: ?>—<?php endif; ?></td></tr>
  <?php endforeach; ?></tbody></table>
  <?php endif; ?>
</div>
