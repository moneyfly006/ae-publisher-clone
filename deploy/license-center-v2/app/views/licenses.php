<div class="toolbar">
  <form method="get" action="/admin/licenses">
    <input type="text" name="q" value="<?= e($q) ?>" placeholder="搜索授权ID / 套餐 / 备注">
    <select name="status" >
      <option value="">全部状态</option>
      <?php foreach (['active' => '正常', 'expired' => '已过期', 'revoked' => '已吊销', 'disabled' => '已停用'] as $k => $v): ?>
        <option value="<?= e($k) ?>" <?= $status === $k ? 'selected' : '' ?>><?= e($v) ?></option>
      <?php endforeach; ?>
    </select>
    <button class="btn ghost" type="submit">筛选</button>
  </form>
  <div class="toolbar-right">
    <button type="button" class="btn ghost" onclick="bulkDelete('lic-bulk')">删除选中</button>
    <a class="btn" href="/admin/licenses/new">新增设备授权码</a>
  </div>
</div>

<form method="post" action="/admin/licenses/bulk-delete" id="lic-bulk">
  <?= csrf_field() ?>
  <div class="card">
  <?php if (!$rows): ?>
    <div class="empty">没有符合条件的授权记录。</div>
  <?php else: ?>
    <table>
      <thead><tr>
        <th ><input type="checkbox" title="全选" onclick="toggleAll(this,'lic-bulk')"></th>
        <th>ID</th><th>设备授权码</th><th>套餐</th><th>备注</th><th>到期日</th><th>剩余</th><th>状态</th><th>最近校验</th>
        <th style="text-align:right">操作</th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r): $st = license_effective_status($r); $dl = days_left($r['expires_at']); ?>
        <tr>
          <td><input type="checkbox" name="ids[]" value="<?= (int)$r['id'] ?>"></td>
          <td><?= (int)$r['id'] ?></td>
          <td class="mono"><a href="/admin/licenses/<?= (int)$r['id'] ?>"><?= e($r['identity']) ?></a></td>
          <td><?= e($r['plan']) ?></td>
          <td title="<?= e($r['note']) ?>"><?= $r['note'] !== '' ? e(mb_substr($r['note'], 0, 18)) . (mb_strlen($r['note']) > 18 ? '…' : '') : '<span class="hint">—</span>' ?></td>
          <td><?= e($r['expires_at']) ?></td>
          <td><?= $st === 'active' ? (int)$dl . ' 天' : '—' ?></td>
          <td><span class="badge <?= status_class($st) ?>"><?= status_label($st) ?></span></td>
          <td><?= $r['last_verified_at'] ? e(substr($r['last_verified_at'], 5)) : '—' ?></td>
          <td class="ops">
            <a class="btn ghost sm" href="/admin/licenses/<?= (int)$r['id'] ?>">详情</a>
            <a class="btn ghost sm" href="/admin/licenses/<?= (int)$r['id'] ?>/edit">编辑</a>
            <button class="btn ghost sm danger" type="submit"
                    formaction="/admin/licenses/<?= (int)$r['id'] ?>/delete"
                    onclick="return confirm('确定删除授权 <?= e($r['identity']) ?> ？删除后不可恢复。')">删除</button>
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
  <span>共 <?= (int)$totalRows ?> 条，第 <?= (int)$page ?>/<?= (int)$pages ?> 页</span>
  <?php $qs = http_build_query(['q' => $q, 'status' => $status]); ?>
  <?php if ($page > 1): ?><a href="/admin/licenses?<?= e($qs) ?>&page=<?= $page - 1 ?>">上一页</a><?php endif; ?>
  <?php if ($page < $pages): ?><a href="/admin/licenses?<?= e($qs) ?>&page=<?= $page + 1 ?>">下一页</a><?php endif; ?>
</div>
<?php endif; ?>
