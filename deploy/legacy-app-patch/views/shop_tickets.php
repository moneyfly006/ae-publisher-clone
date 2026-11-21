<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无工单</div><?php else: ?>
  <table><thead><tr><th>ID</th><th>用户</th><th>标题</th><th>分类</th><th>状态</th><th>更新时间</th><th style="text-align:right">操作</th></tr></thead><tbody>
  <?php foreach ($rows as $r): ?>
    <tr><td><?= (int)$r['id'] ?></td><td><?= e($r['email'] ?? '') ?></td><td><?= e($r['subject']) ?></td>
      <td><?= e($r['category']) ?></td>
      <td><span class="badge <?= $r['status']==='open'?'warn':($r['status']==='replied'?'ok':'') ?>"><?= e($r['status']) ?></span></td>
      <td><?= e(date('Y-m-d H:i',(int)$r['updated_at'])) ?></td>
      <td class="ops"><a class="btn ghost sm" href="/admin/shop-tickets/<?= (int)$r['id'] ?>">处理</a></td></tr>
  <?php endforeach; ?></tbody></table>
  <?php endif; ?>
</div>
