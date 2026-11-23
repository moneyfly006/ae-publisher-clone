<section class="card"><h2>新增优惠券</h2><form method="post" action="/admin/shop-coupons"><?= csrf_field() ?><div class="form-grid">
<label>优惠码<input name="code" placeholder="例如 SAVE10…" required></label>
<label>优惠类型<select name="type"><option value="fixed">固定减免金额</option><option value="percent">按比例减免</option></select></label>
<label>固定减免（元）<input name="value" type="number" step="0.01" min="0" placeholder="固定减免时填写…"></label>
<label>减免比例（%）<input name="percent" type="number" min="0" max="100" placeholder="按比例减免时填写…"></label>
<label>最低订单金额（元）<input name="min_amount" type="number" step="0.01" min="0" value="0"></label>
<label>到期日期<input name="expires_at" type="date"></label>
<label>最多使用次数<input name="max_uses" type="number" min="0" value="0"><span class="hint">0 表示不限次数。</span></label>
</div><div class="form-actions"><button class="btn" type="submit">新增优惠券</button></div></form></section>
<div class="card">
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
