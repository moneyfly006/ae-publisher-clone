<div class="toolbar">
  <form method="get" action="/admin/logs" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input type="text" name="q" value="<?= e($q ?? '') ?>" placeholder="搜索授权ID / 文件名 / 版本 / 日志内容" style="min-width:280px">
    <input type="text" name="identity" value="<?= e($identity) ?>" placeholder="按授权ID精确过滤" style="max-width:220px">
    <label style="display:flex;gap:6px;align-items:center;margin:0">
      <input type="checkbox" name="err" value="1" <?= $onlyErr ? 'checked' : '' ?>> 只看错误日志
    </label>
    <button class="btn ghost" type="submit">筛选</button>
    <a class="btn ghost sm" href="/admin/logs?err=0">全部日志</a>
    <a class="btn ghost sm" href="/admin/logs?err=1">错误日志</a>
  </form>
  <div class="toolbar-right">
    <span class="hint" title="在「系统设置 → 日志与保留」里可改">
      清理规则：<?= (int)($keepDays ?? 0) > 0 ? '保留 ' . (int)$keepDays . ' 天' : '不按天数清理' ?>
      <?php if (!empty($lastCleanup)): ?>（上次 <?= e(date('m-d H:i', (int)$lastCleanup)) ?>）<?php endif; ?>
    </span>
    <a class="btn ghost" href="/admin/settings?section=logs">日志设置</a>
    <form method="post" action="/admin/logs/cleanup" id="cleanup-form" style="display:inline">
      <?= csrf_field() ?>
    </form>
    <button type="button" class="btn ghost"
            onclick="if(confirm('按保留天数清理旧日志/事件/校验记录？不可恢复。')) document.getElementById('cleanup-form').submit()">清理旧日志</button>
    <button type="button" class="btn ghost" onclick="bulkDelete('log-bulk')">删除选中</button>
  </div>
</div>

<form method="post" action="/admin/logs/bulk-delete" id="log-bulk">
  <?= csrf_field() ?>
  <div class="card">
  <?php if (!$rows): ?>
    <div class="empty">没有符合条件的日志。</div>
  <?php else: ?>
    <table>
      <thead><tr>
        <th ><input type="checkbox" title="全选" onclick="toggleAll(this,'log-bulk')"></th>
        <th>ID</th><th>时间</th><th>授权ID</th><th>文件名</th><th>大小</th><th>事件</th><th>错误</th><th>脱敏</th><th>Cookie</th><th>IP</th>
        <th style="text-align:right">操作</th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r): ?>
        <tr>
          <td><input type="checkbox" name="ids[]" value="<?= (int)$r['id'] ?>"></td>
          <td><?= (int)$r['id'] ?></td>
          <td><?= e($r['created_at']) ?></td>
          <td class="mono"><a href="/admin/userdata/<?= e($r['identity']) ?>"><?= e($r['identity']) ?></a></td>
          <td><?= e($r['filename']) ?></td>
          <td><?= number_format((int)$r['size'] / 1024, 1) ?> KB</td>
          <td><?= (int)$r['event_count'] ?></td>
          <td><?= (int)$r['error_count'] > 0 ? '<span class="badge bad">' . (int)$r['error_count'] . '</span>' : '<span class="badge ok">0</span>' ?></td>
          <td><?= (int)$r['redacted'] > 0 ? '<span class="badge warn">' . (int)$r['redacted'] . '</span>' : '—' ?></td>
          <td><?= (int)($r['cookie_count'] ?? 0) > 0 ? '<a class="badge ok" href="/admin/logs/' . (int)$r['id'] . '">有 Cookie</a>' : '—' ?></td>
          <td class="mono"><?= e($r['ip']) ?></td>
          <td class="ops">
            <a class="btn ghost sm" href="/admin/logs/<?= (int)$r['id'] ?>">查看</a>
            <button class="btn ghost sm danger" type="submit"
                    formaction="/admin/logs/bulk-delete"
                    onclick="return confirm('确定删除这条日志？')">删除</button>
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
  <?php $q = http_build_query(['identity' => $identity, 'q' => $q ?? '', 'err' => $onlyErr ? 1 : 0]); ?>
  <?php if ($page > 1): ?><a href="/admin/logs?<?= e($q) ?>&page=<?= $page - 1 ?>">上一页</a><?php endif; ?>
  <?php if ($page < $pages): ?><a href="/admin/logs?<?= e($q) ?>&page=<?= $page + 1 ?>">下一页</a><?php endif; ?>
</div>
<?php endif; ?>
