<div class="card" style="padding:8px 16px">
<?php if (!$rows): ?>
  <div class="empty">还没有客户端上传日志。</div>
<?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>时间</th><th>授权ID</th><th>文件名</th><th>大小</th><th>版本</th><th></th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td><?= e($r['created_at']) ?></td>
        <td class="mono"><?= e($r['identity']) ?></td>
        <td><?= e($r['filename']) ?></td>
        <td><?= number_format((int)$r['size'] / 1024, 1) ?> KB</td>
        <td><?= e($r['client_version']) ?></td>
        <td style="text-align:right"><a class="btn ghost sm" href="/admin/logs/<?= (int)$r['id'] ?>">查看</a></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
<?php endif; ?>
</div>
<?php if ($pages > 1): ?>
<div class="pager">
  <span>共 <?= (int)$total ?> 条，第 <?= (int)$page ?>/<?= (int)$pages ?> 页</span>
  <?php if ($page > 1): ?><a href="/admin/logs?page=<?= $page - 1 ?>">上一页</a><?php endif; ?>
  <?php if ($page < $pages): ?><a href="/admin/logs?page=<?= $page + 1 ?>">下一页</a><?php endif; ?>
</div>
<?php endif; ?>
