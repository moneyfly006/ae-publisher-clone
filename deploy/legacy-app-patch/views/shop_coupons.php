<div class="card">
  <h3 style="margin-bottom:10px">新建优惠券</h3>
  <form method="post" action="/admin/shop-coupons">
    <?= csrf_field() ?>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px">
      <input name="code" placeholder="优惠码（如 SAVE10）" style="width:160px" required>
      <select name="type" style="width:120px"><option value="fixed">固定减免</option><option value="percent">按比例</option></select>
      <input name="value" type="number" step="0.01" placeholder="减免金额(元)" style="width:130px">
      <input name="percent" type="number" placeholder="百分比(如10)" style="width:120px">
      <input name="min_amount" type="number" step="0.01" placeholder="门槛(元)" style="width:110px">
      <input name="expires_at" type="date" style="width:150px">
      <input name="max_uses" type="number" placeholder="可用次数(0不限)" style="width:140px">
      <button class="btn" type="submit">保存</button>
    </div>
  </form>
</div>
<div class="card" style="padding:8px 16px;margin-top:16px">
  <?php if (!$rows): ?><div class="empty">暂无优惠券</div><?php else: ?>
  <table><thead><tr><th>ID</th><th>优惠码</th><th>类型</th><th>优惠</th><th>门槛</th><th>过期</th><th>已用/上限</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>
  <?php foreach ($rows as $r): ?>
    <tr><td><?= (int)$r['id'] ?></td><td class="mono"><?= e($r['code']) ?></td>
      <td><?= $r['type']==='percent'?'比例':'固定' ?></td>
      <td><?= $r['type']==='percent' ? ((int)$r['percent']).'%' : '¥'.number_format(((int)$r['value_cents'])/100,2) ?></td>
      <td>¥<?= number_format(((int)$r['min_amount_cents'])/100,2) ?></td>
      <td><?= e($r['expires_at'] ?: '—') ?></td>
      <td><?= (int)$r['used'] ?> / <?= (int)$r['max_uses'] ?: '∞' ?></td>
      <td><span class="badge <?= (int)$r['active']?'ok':'bad' ?>"><?= (int)$r['active']?'启用':'停用' ?></span></td>
      <td class="ops"><form method="post" action="/admin/shop-coupons" onsubmit="return confirm('删除优惠券？')">
        <?= csrf_field() ?><input type="hidden" name="delete_id" value="<?= (int)$r['id'] ?>">
        <button class="btn ghost sm danger" type="submit">删除</button></form></td></tr>
  <?php endforeach; ?></tbody></table>
  <?php endif; ?>
</div>
