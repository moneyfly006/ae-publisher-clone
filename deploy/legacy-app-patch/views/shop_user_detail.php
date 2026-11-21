<div class="toolbar">
  <a class="btn ghost" href="/admin/shop-users">← 返回用户列表</a>
  <div class="toolbar-right">
    <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/revoke" onsubmit="return confirm('取消该用户全部授权？')">
      <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">取消全部授权</button>
    </form>
    <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/delete" onsubmit="return confirm('删除用户及其全部授权/订单？不可恢复')">
      <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">删除用户</button>
    </form>
  </div>
</div>

<div class="card">
  <h3 style="margin-bottom:10px">账号信息</h3>
  <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/update">
    <?= csrf_field() ?>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px">
      <label style="width:80px">邮箱</label><input name="email" value="<?= e($u['email']) ?>" style="width:320px" required>
      <label>称呼</label><input name="name" value="<?= e($u['name'] ?? '') ?>" style="width:200px">
      <label>标签</label><input name="tags" value="<?= e($u['tags'] ?? '') ?>" placeholder="如：VIP,待续费" style="width:180px">
      <button class="btn ghost sm" type="submit">保存账号</button>
    </div>
    <div class="toolbar" style="margin-top:8px;gap:10px">
      <label style="width:80px">备注</label><textarea name="note" rows="2" style="width:520px"><?= e($u['note'] ?? '') ?></textarea>
    </div>
  </form>
  <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/password" style="margin-top:10px">
    <?= csrf_field() ?>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px">
      <label style="width:80px">新密码</label><input name="password" type="text" placeholder="至少 6 位" style="width:260px" required>
      <button class="btn ghost sm" type="submit">重置密码</button>
    </div>
  </form>
</div>

<div class="card" style="margin-top:16px">
  <h3 style="margin-bottom:10px">手动开通平台全权限</h3>
  <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/grant">
    <?= csrf_field() ?>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px">
      <label style="width:80px">平台</label>
      <select name="platform" style="width:220px">
        <option value="all">全部平台（全功能）</option>
        <?php foreach ($platforms as $pid => $feats): ?>
          <option value="<?= e($pid) ?>"><?= e($pid) ?>（<?= e(implode(',', $feats)) ?>）</option>
        <?php endforeach; ?>
      </select>
      <label>天数</label><input name="days" type="number" value="30" style="width:90px">
      <button class="btn" type="submit">立即开通</button>
    </div>
    <p class="hint">会生成一条新授权（沿用该用户已有机器码）；软件刷新/登录后生效。</p>
  </form>
</div>

<div class="card" style="margin-top:16px;padding:8px 16px">
  <h3 style="margin:10px 8px">授权记录</h3>
  <?php if (!$licenses): ?><div class="empty">暂无授权</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>机器码</th><th>平台/功能</th><th>到期</th><th>状态</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($licenses as $l): $pl = json_decode((string)$l['platforms'], true) ?: []; ?>
      <tr>
        <td><?= (int)$l['id'] ?></td>
        <td class="mono"><?= e($l['machine_code'] ?: '未绑定') ?></td>
        <td><?php foreach ($pl as $pid => $feats): ?><div><?= e($pid) ?>：<?= e(implode(',', $feats)) ?></div><?php endforeach; ?></td>
        <td><?= e($l['expires_at']) ?></td>
        <td><span class="badge <?= (int)$l['revoked'] ? 'bad' : 'ok' ?>"><?= (int)$l['revoked'] ? '已取消' : '有效' ?></span></td>
        <td class="ops">
          <?php if (!(int)$l['revoked']): ?>
          <form method="post" action="/admin/shop-licenses/<?= (int)$l['id'] ?>/revoke" onsubmit="return confirm('取消这条授权？')">
            <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">取消</button>
          </form>
          <?php else: ?>—<?php endif; ?>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>

<div class="card" style="margin-top:16px;padding:8px 16px">
  <h3 style="margin:10px 8px">订单记录</h3>
  <?php if (!$orders): ?><div class="empty">暂无订单</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>订单号</th><th>套餐</th><th>金额</th><th>状态</th><th>时间</th></tr></thead>
    <tbody>
    <?php foreach ($orders as $o): ?>
      <tr>
        <td><?= (int)$o['id'] ?></td><td class="mono"><?= e($o['trade_no']) ?></td>
        <td><?= e($o['package_name'] ?? '') ?></td>
        <td>¥<?= number_format(((int)$o['amount_cents'])/100, 2) ?></td>
        <td><span class="badge <?= $o['status']==='paid'?'ok':'warn' ?>"><?= $o['status']==='paid'?'已支付':'待支付' ?></span></td>
        <td><?= e(date('Y-m-d H:i', (int)$o['created_at'])) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
