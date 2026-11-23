<div class="toolbar">
  <form method="get" action="/admin/userdata">
    <input type="text" name="q" value="<?= e($q ?? '') ?>" placeholder="搜索授权ID">
    <button class="btn ghost" type="submit">搜索</button>
  </form>
  <div class="toolbar-right">
    <button type="button" class="btn ghost" onclick="bulkDelete('dev-bulk')">删除选中</button>
  </div>
</div>

<form method="post" action="/admin/userdata" id="dev-bulk">
  <?= csrf_field() ?>
  <div class="card">
    <h2 style="margin:12px 0">按用户汇总</h2>
  <?php if (!$rows): ?>
    <div class="empty">还没有采集到使用数据。</div>
  <?php else: ?>
    <table>
      <thead><tr>
        <th ><input type="checkbox" title="全选" onclick="toggleAll(this,'dev-bulk')"></th>
        <th>授权ID</th><th>授权</th><th>刊登成功</th><th>草稿</th><th>刊登失败</th><th>报错</th><th>链接</th><th>日志</th>
        <th>版本</th><th>系统</th><th>店铺</th><th>最近活动</th><th style="text-align:right">操作</th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r): ?>
        <tr>
          <td><input type="checkbox" name="ids[]" value="<?= e($r['identity']) ?>"></td>
          <td class="mono"><a href="/admin/userdata/<?= e($r['identity']) ?>"><?= e($r['identity']) ?></a></td>
          <td><?= (int)$r['licensed'] > 0 ? '<span class="badge ok">已登记</span>' : '<span class="badge muted">未登记</span>' ?></td>
          <td><b style="color:var(--ok)"><?= (int)$r['pub_ok'] ?></b></td>
          <td><?= (int)$r['draft'] ?></td>
          <td style="color:var(--warn)"><?= (int)$r['pub_fail'] ?></td>
          <td style="color:var(--bad)"><?= (int)$r['errs'] ?></td>
          <td><?= (int)$r['links'] ?></td>
          <td><?= (int)$r['log_count'] ?></td>
          <td><?= e($r['device']['app_version'] ?? '—') ?></td>
          <td><?= e(trim(($r['device']['platform'] ?? '') . ' ' . ($r['device']['os_version'] ?? ''))) ?: '—' ?></td>
          <td><?= e($r['device']['store_name'] ?? '—') ?></td>
          <td><?= e($r['last_at']) ?></td>
          <td class="ops">
            <a class="btn ghost sm" href="/admin/userdata/<?= e($r['identity']) ?>">详情</a>
            <button class="btn ghost sm danger" type="submit"
                    formaction="/admin/userdata/<?= e(urlencode($r['identity'])) ?>/delete"
                    onclick="return confirm('删除该设备的使用数据？（日志/事件/设备信息，授权记录保留）')">删除</button>
          </td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  <?php endif; ?>
  </div>
</form>

<?php if (($pages ?? 1) > 1): ?>
<div class="pager">
  <span>共 <?= (int)($totalRows ?? 0) ?> 个设备，第 <?= (int)$page ?>/<?= (int)$pages ?> 页</span>
  <?php $qs = http_build_query(['q' => $q ?? '']); ?>
  <?php if ($page > 1): ?><a href="/admin/userdata?<?= e($qs) ?>&page=<?= $page - 1 ?>">上一页</a><?php endif; ?>
  <?php if ($page < $pages): ?><a href="/admin/userdata?<?= e($qs) ?>&page=<?= $page + 1 ?>">下一页</a><?php endif; ?>
</div>
<?php endif; ?>

<div class="card">
  <h2>最近事件</h2>
  <?php if (!$recent): ?><div class="empty">暂无</div><?php else: ?>
  <table>
    <thead><tr><th>时间</th><th>授权ID</th><th>事件</th><th>类型</th><th>内容</th></tr></thead>
    <tbody>
    <?php foreach ($recent as $e): ?>
      <tr>
        <td><?= e($e['created_at']) ?></td>
        <td class="mono"><?= e($e['identity']) ?></td>
        <td><span class="badge <?= $e['event'] === 'publish_ok' ? 'ok' : (in_array($e['event'], ['error', 'publish_fail'], true) ? 'bad' : 'muted') ?>"><?= e($e['event']) ?></span></td>
        <td><?= $e['error_type'] !== '' ? '<span class="badge warn">' . e($e['error_type']) . '</span>' : '—' ?></td>
        <td style="word-break:break-all"><?= e($e['detail']) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
