<div class="card" style="padding:8px 16px">
  <h2 style="margin:12px 0">按用户汇总</h2>
<?php if (!$rows): ?>
  <div class="empty">还没有采集到使用数据。</div>
<?php else: ?>
  <table>
    <thead><tr><th>授权ID</th><th>刊登成功</th><th>保存草稿</th><th>刊登失败</th><th>报错</th><th>产品链接</th><th>最近活动</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td class="mono"><?= e($r['identity']) ?></td>
        <td><b style="color:var(--ok)"><?= (int)$r['pub_ok'] ?></b></td>
        <td><?= (int)$r['draft'] ?></td>
        <td style="color:var(--warn)"><?= (int)$r['pub_fail'] ?></td>
        <td style="color:var(--bad)"><?= (int)$r['errs'] ?></td>
        <td><?= (int)$r['links'] ?></td>
        <td><?= e($r['last_at']) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
<?php endif; ?>
</div>

<div class="card">
  <h2>最近事件</h2>
  <?php if (!$recent): ?><div class="empty">暂无</div><?php else: ?>
  <table>
    <thead><tr><th>时间</th><th>授权ID</th><th>事件</th><th>内容</th></tr></thead>
    <tbody>
    <?php foreach ($recent as $e): ?>
      <tr>
        <td><?= e($e['created_at']) ?></td>
        <td class="mono"><?= e($e['identity']) ?></td>
        <td><span class="badge <?= $e['event'] === 'publish_ok' ? 'ok' : ($e['event'] === 'error' || $e['event'] === 'publish_fail' ? 'bad' : 'muted') ?>"><?= e($e['event']) ?></span></td>
        <td style="word-break:break-all"><?= e($e['detail']) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
