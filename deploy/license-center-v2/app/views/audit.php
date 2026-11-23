<div class="toolbar">
  <form method="get" action="/admin/audit">
    <span class="hint">共 <?= (int)$total ?> 条操作记录</span>
  </form>
  <div class="toolbar-right">
    <button type="button" class="btn ghost" onclick="bulkDelete('audit-bulk')">删除选中</button>
  </div>
</div>

<form method="post" action="/admin/audit/bulk-delete" id="audit-bulk">
  <?= csrf_field() ?>
  <div class="card">
  <?php if (!$rows): ?>
    <div class="empty">暂无操作日志。</div>
  <?php else: ?>
    <table>
      <thead><tr>
        <th ><input type="checkbox" title="全选" onclick="toggleAll(this,'audit-bulk')"></th>
        <th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th>
        <th style="text-align:right">操作</th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r): ?>
        <tr>
          <td><input type="checkbox" name="ids[]" value="<?= (int)$r['id'] ?>"></td>
          <td><?= e($r['created_at']) ?></td>
          <td><?= e($r['actor']) ?></td>
          <td><?= e($r['action']) ?></td>
          <td class="mono"><?= e($r['target']) ?></td>
          <td><?= e($r['detail']) ?></td>
          <td class="mono"><?= e($r['ip']) ?></td>
          <td class="ops">
            <button class="btn ghost sm danger" type="submit"
                    formaction="/admin/audit/bulk-delete"
                    onclick="return confirm('确定删除这条操作日志？')">删除</button>
          </td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  <?php endif; ?>
  </div>
</form>

<?php if ($pages > 1): ?>
<div class="pager">
  <span>共 <?= (int)$total ?> 条，第 <?= (int)$page ?>/<?= (int)$pages ?> 页</span>
  <?php if ($page > 1): ?><a href="/admin/audit?page=<?= $page - 1 ?>">上一页</a><?php endif; ?>
  <?php if ($page < $pages): ?><a href="/admin/audit?page=<?= $page + 1 ?>">下一页</a><?php endif; ?>
</div>
<?php endif; ?>
