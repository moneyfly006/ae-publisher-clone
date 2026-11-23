<?php $st = license_effective_status($row); $dl = days_left($row['expires_at']); ?>
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
    <div>
      <div class="mono" style=""><?= e($row['identity']) ?></div>
      <div class="hint" style="margin-top:4px">创建于 <?= e($row['created_at']) ?> · 创建人 <?= e($row['created_by']) ?></div>
    </div>
    <div><span class="badge <?= status_class($st) ?>" style="padding:5px 12px"><?= status_label($st) ?></span></div>
  </div>
</div>

<div class="grid g2">
  <div class="card">
    <h2>原设备授权详情</h2>
    <table>
      <tr><th >套餐</th><td><?= e($row['plan']) ?></td></tr>
      <tr><th>客户端类型</th><td><?= $row['client_type'] === 'shop' ? '店铺验证模式' : '本地验证模式' ?></td></tr>
      <tr><th>到期日</th><td><?= e($row['expires_at']) ?><?= $st === 'active' ? '（剩余 ' . (int)$dl . ' 天）' : '' ?></td></tr>
      <tr><th>签发日</th><td><?= e($row['issued_at']) ?></td></tr>
      <tr><th>最近校验</th><td><?= $row['last_verified_at'] ? e($row['last_verified_at']) : '尚未校验' ?></td></tr>
      <tr><th>校验次数</th><td><?= (int)$row['verify_count'] ?></td></tr>
      <tr><th>备注</th><td><?= $row['note'] !== '' ? nl2br(e($row['note'])) : '—' ?></td></tr>
    </table>
    <div class="actions" style="margin-top:14px">
      <a class="btn ghost" href="/admin/licenses/<?= (int)$row['id'] ?>/edit">编辑</a>
      <a class="btn ghost" href="/admin/licenses">返回列表</a><a class="btn" href="/admin?identity=<?= e(rawurlencode($row['identity'])) ?>">运行问题分析</a><a class="btn ghost" href="/admin/logs?identity=<?= e(rawurlencode($row['identity'])) ?>">该设备日志</a>
    </div>
  </div>

  <div class="card">
    <h2>续期与授权管理</h2>
    <form method="post" action="/admin/licenses/<?= (int)$row['id'] ?>/renew" class="field">
      <?= csrf_field() ?>
      <label>续期（在当前到期日基础上顺延）</label>
      <div class="row">
        <input type="number" name="days" value="365" min="1" max="3650">
        <button class="btn" type="submit" style="flex:0 0 auto">续期</button>
      </div>
    </form>
    <hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
    <div class="actions">
      <form method="post" action="/admin/licenses/<?= (int)$row['id'] ?>/revoke" onsubmit="return confirm('确认吊销该授权？吊销后客户端将无法通过校验。')">
        <?= csrf_field() ?>
        <button class="btn danger" type="submit">吊销授权</button>
      </form>
      <form method="post" action="/admin/licenses/<?= (int)$row['id'] ?>/delete" onsubmit="return confirm('确认删除该记录？此操作不可恢复。')">
        <?= csrf_field() ?>
        <button class="btn danger" type="submit">删除记录</button>
      </form>
    </div>
    <div class="hint" style="margin-top:12px">
      客户端请求：<code>POST /api/v1/license/verify</code>，参数 <code>{"identity":"<?= e($row['identity']) ?>","client_type":"<?= e($row['client_type']) ?>"}</code>
    </div>
  </div>
</div>

<div class="grid g2">
  <div class="card">
    <h2>最近校验记录</h2>
    <?php if (!$logs): ?><div class="empty">暂无校验记录</div><?php else: ?>
    <table>
      <thead><tr><th>时间</th><th>IP</th><th>结果</th></tr></thead>
      <tbody>
      <?php foreach ($logs as $l): ?>
        <tr>
          <td><?= e($l['created_at']) ?></td>
          <td class="mono"><?= e($l['ip']) ?></td>
          <td><?= $l['ok'] ? '<span class="badge ok">通过</span>' : '<span class="badge bad">' . e($l['reason']) . '</span>' ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php endif; ?>
  </div>
  <div class="card">
    <h2>相关操作</h2>
    <?php if (!$audits): ?><div class="empty">暂无操作记录</div><?php else: ?>
    <table>
      <thead><tr><th>时间</th><th>操作</th><th>操作人</th></tr></thead>
      <tbody>
      <?php foreach ($audits as $a): ?>
        <tr>
          <td><?= e($a['created_at']) ?></td>
          <td><?= e($a['action']) ?></td>
          <td><?= e($a['actor']) ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php endif; ?>
  </div>
</div>
