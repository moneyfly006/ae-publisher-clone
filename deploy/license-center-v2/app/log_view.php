<div class="card">
  <h2><?= e($row['filename']) ?> <span class="hint">· <?= e($row['identity']) ?> · <?= e($row['created_at']) ?></span></h2>
  <pre style="max-height:70vh;overflow:auto;background:#0f1220;color:#d6d9e6;padding:16px;border-radius:10px;font-size:12px;line-height:1.6;white-space:pre-wrap"><?= e($row['content']) ?></pre>
  <div class="actions" style="margin-top:12px"><a class="btn ghost" href="/admin/logs">返回列表</a></div>
</div>
