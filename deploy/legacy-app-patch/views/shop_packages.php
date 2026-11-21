<div class="toolbar">
  <div class="toolbar-right">
    <a class="btn" href="#new-package">新增套餐</a>
  </div>
</div>
<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">还没有套餐</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>名称</th><th>平台</th><th>功能</th><th>价格(元)</th><th>天数</th><th>启用</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td><?= e($r['name']) ?></td>
        <td><?= e(implode(',', $r['platforms_arr'])) ?></td>
        <td><?= e(implode(',', $r['features_arr'])) ?></td>
        <td colspan="4">
          <form method="post" action="/admin/shop-packages" style="display:flex;gap:8px;align-items:center;justify-content:flex-end">
            <?= csrf_field() ?>
            <input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
            <input type="hidden" name="name" value="<?= e($r['name']) ?>">
            <input type="hidden" name="platforms" value="<?= e(implode(',', $r['platforms_arr'])) ?>">
            <input type="hidden" name="features" value="<?= e(implode(',', $r['features_arr'])) ?>">
            <input type="number" step="0.01" name="price" value="<?= e((string)$r['price']) ?>" style="width:100px">
            <input type="number" name="days" value="<?= (int)$r['days'] ?>" style="width:80px">
            <select name="active" style="width:90px">
              <option value="1" <?= (int)$r['active'] === 1 ? 'selected' : '' ?>>启用</option>
              <option value="0" <?= (int)$r['active'] !== 1 ? 'selected' : '' ?>>停用</option>
            </select>
            <button class="btn ghost sm" type="submit">保存</button>
          </form>
        </td>
        <td class="ops">
          <form method="post" action="/admin/shop-packages" onsubmit="return confirm('删除该套餐？')">
            <?= csrf_field() ?><input type="hidden" name="delete_id" value="<?= (int)$r['id'] ?>">
            <button class="btn ghost sm danger" type="submit">删除</button>
          </form>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
<div class="card" id="new-package" style="margin-top:16px">
  <h3 style="margin-bottom:10px">新增套餐</h3>
  <form method="post" action="/admin/shop-packages">
    <?= csrf_field() ?>
    <div class="toolbar" style="gap:8px;flex-wrap:wrap">
      <input name="name" placeholder="套餐名" required>
      <input name="price" type="number" step="0.01" placeholder="价格(元)" required style="width:110px">
      <input name="days" type="number" value="365" style="width:90px">
      <input name="platforms" value="alibaba" placeholder="平台: alibaba,amazon" style="width:220px">
      <input name="features" value="listing,category" placeholder="功能: listing,category,ai_text" style="width:260px">
      <button class="btn" type="submit">新增</button>
    </div>
  </form>
</div>
