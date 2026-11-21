<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无退款单</div><?php else: ?>
  <table><thead><tr><th>ID</th><th>订单</th><th>用户</th><th>金额</th><th>原因</th><th>状态</th><th>时间</th><th style="text-align:right">操作</th></tr></thead><tbody>
  <?php foreach ($rows as $r): ?>
    <tr><td><?= (int)$r['id'] ?></td><td class="mono"><?= e($r['trade_no'] ?? '') ?></td><td><?= e($r['email'] ?? '') ?></td>
      <td>¥<?= number_format(((int)$r['amount_cents'])/100, 2) ?></td><td><?= e($r['reason']) ?></td>
      <td><span class="badge <?= $r['status']==='done'?'ok':'warn' ?>"><?= $r['status']==='done'?'已完成':'待处理' ?></span></td>
      <td><?= e(date('Y-m-d H:i',(int)$r['created_at'])) ?></td>
      <td class="ops"><?php if ($r['status']!=='done'): ?>
        <form method="post" action="/admin/shop-refunds/<?= (int)$r['id'] ?>/complete" onsubmit="return confirm('确认已在支付渠道退款？将同时取消该用户授权')">
          <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">完成退款</button></form>
      <?php else: ?>—<?php endif; ?></td></tr>
  <?php endforeach; ?></tbody></table>
  <?php endif; ?>
</div>
