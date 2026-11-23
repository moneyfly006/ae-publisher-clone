<div class="toolbar"><a class="btn ghost" href="/admin/shop-tickets">← 返回工单列表</a></div>
<div class="card">
  <h3>#<?= (int)$t['id'] ?> <?= e($t['subject']) ?></h3>
  <p class="hint">用户 <?= e($t['email'] ?? '') ?> · 分类 <?= e($t['category']) ?> · 状态 <?= e($t['status']) ?></p>
  <div style="margin-top:12px">
  <?php foreach ($replies as $r): ?>
    <div style="padding:10px 12px;margin:8px 0;border-radius:8px;background:<?= (int)$r['is_admin'] ? '#EEF7F5' : '#F7F9FA' ?>">
      <div class="hint"><?= (int)$r['is_admin'] ? '客服' : '用户' ?> · <?= e(date('Y-m-d H:i',(int)$r['created_at'])) ?></div>
      <div style="white-space:pre-wrap;margin-top:4px"><?= e($r['body']) ?></div>
    </div>
  <?php endforeach; ?>
  </div>
  <form method="post" action="/admin/shop-tickets/<?= (int)$t['id'] ?>/reply" style="margin-top:14px">
    <?= csrf_field() ?>
    <textarea name="body" rows="4" style="width:100%" placeholder="输入回复内容"></textarea>
    <div style="margin-top:8px"><button class="btn" type="submit">回复用户</button></div>
  </form>
</div>
