<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无邀请码</div><?php else: ?>
  <table><thead><tr><th>ID</th><th>邀请码</th><th>邀请人</th><th>已邀请人数</th><th>创建时间</th></tr></thead><tbody>
  <?php foreach ($rows as $r): ?>
    <tr><td><?= (int)$r['id'] ?></td><td class="mono"><?= e($r['code']) ?></td><td><?= e($r['email'] ?? '') ?></td>
      <td><?= (int)$r['uses'] ?></td><td><?= e(date('Y-m-d H:i',(int)$r['created_at'])) ?></td></tr>
  <?php endforeach; ?></tbody></table>
  <?php endif; ?>
</div>
