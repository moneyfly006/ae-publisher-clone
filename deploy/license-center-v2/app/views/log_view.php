<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
    <div>
      <h2 style="margin:0 0 4px"><?= e($row['filename']) ?></h2>
      <div class="hint">
        <a href="/admin/userdata/<?= e($row['identity']) ?>" class="mono"><?= e($row['identity']) ?></a>
        · <?= e($row['created_at']) ?>
        · <?= number_format((int)$row['size'] / 1024, 1) ?> KB
        · IP <?= e($row['ip']) ?>
        · 版本 <?= e($row['client_version']) ?>
        <?php if (!empty($row['redacted'])): ?>· 已脱敏 <?= (int)$row['redacted'] ?> 处<?php endif; ?>
      </div>
    </div>
    <div class="actions" style="flex:0 0 auto">
      <a class="btn ghost" href="<?= e(lc_safe_admin_return((string)($_GET['back'] ?? ''))) ?>">返回分析列表</a><a class="btn ghost" href="/admin/events?identity=<?= e(rawurlencode($row['identity'])) ?>">该设备事件</a>
      <button class="btn" type="button" onclick="closeView()">关闭</button>
    </div>
  </div>

  <?php if ($summary): ?>
    <div class="actions" style="margin:14px 0">
      <?php foreach ($summary as $s): ?>
        <span class="badge <?= in_array($s['event'], ['error', 'publish_fail'], true) ? 'bad' : 'muted' ?>">
          <?= e($s['event']) ?><?= $s['error_type'] ? ' / ' . e($s['error_type']) : '' ?> × <?= (int)$s['c'] ?>
        </span>
      <?php endforeach; ?>
    </div>
  <?php endif; ?>

  <pre style="max-height:64vh;overflow:auto;background:#0f1220;color:#d6d9e6;padding:16px;border-radius:10px;line-height:1.6;white-space:pre-wrap"><?= e($row['content']) ?></pre>
</div>
<script>
function closeView() {
  if (document.referrer && document.referrer.indexOf(location.host) !== -1 && history.length > 1) {
    history.back();
  } else {
    location.href = '/admin/logs';
  }
}
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closeView();
});
</script>
