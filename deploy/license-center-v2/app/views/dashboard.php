<div class="page-intro"><div><span class="eyebrow">AURORALIST OPERATIONS</span><p>套餐账号与原设备授权统一入口、独立保留。客户问题从账号、设备和日志三个维度定位。</p></div><div class="actions"><a class="btn" href="/admin/shop-users">套餐用户</a><a class="btn ghost" href="/admin/customer-logs">客户日志</a><a class="btn ghost" href="/admin/licenses">原设备授权</a></div></div>
<?php if (function_exists('shop_report_data')): $__d = shop_report_data();
  $__tk = function_exists('ops_pdo') ? (int)ops_pdo()->query("SELECT COUNT(*) FROM tickets WHERE status='open'")->fetchColumn() : 0; ?>
<div class="grid g4" style="margin-bottom:18px">
  <div class="kpi"><div class="n">¥<?= number_format($__d['month']/100,2) ?></div><div class="l">本月套餐收入</div></div>
  <div class="kpi"><div class="n"><?= (int)$__d['customers'] ?></div><div class="l">注册客户</div></div>
  <div class="kpi"><div class="n"><?= (int)$__d['licenses'] ?></div><div class="l">有效套餐授权</div></div>
  <div class="kpi"><div class="n" style="color:var(--warn)"><?= $__tk ?></div><div class="l">待处理工单 <a href="/admin/shop-tickets">去处理</a></div></div>
</div>
<?php endif; ?>
<div class="grid g4" style="margin-bottom:18px">
  <div class="kpi"><div class="n"><?= (int)$total ?></div><div class="l">原设备授权总数</div></div>
  <div class="kpi"><div class="n" style="color:var(--ok)"><?= (int)$active ?></div><div class="l">原设备有效授权</div></div>
  <div class="kpi"><div class="n" style="color:var(--warn)"><?= (int)$expired ?></div><div class="l">已过期</div></div>
  <div class="kpi"><div class="n" style="color:var(--bad)"><?= (int)$revoked ?></div><div class="l">已吊销</div></div>
</div>
<?php if ($soon > 0): ?>
<div class="flash bad">有 <?= (int)$soon ?> 个授权将在 7 天内到期，建议提前联系续期。</div>
<?php endif; ?>

<div class="grid g4" style="margin-bottom:18px">
  <div class="kpi"><div class="n"><?= (int)$todayPub ?></div><div class="l">今日刊登成功</div></div>
  <div class="kpi"><div class="n"><?= (int)$todayDraft ?></div><div class="l">今日保存草稿</div></div>
  <div class="kpi"><div class="n" style="color:var(--bad)"><?= (int)$todayErr ?></div><div class="l">今日报错</div></div>
  <div class="kpi"><div class="n"><?= (int)$todayLogs ?></div><div class="l">今日上传日志</div></div>
</div>

<div class="card">
  <h2>最近原设备授权</h2>
  <?php if (!$recent): ?>
    <div class="empty">还没有任何授权，先<a href="/admin/licenses/new">新增一条</a>。</div>
  <?php else: ?>
  <table>
    <thead><tr><th>授权ID</th><th>套餐</th><th>到期</th><th>状态</th><th>校验次数</th><th></th></tr></thead>
    <tbody>
    <?php foreach ($recent as $r): $st = license_effective_status($r); ?>
      <tr>
        <td class="mono"><?= e($r['identity']) ?></td>
        <td><?= e($r['plan']) ?></td>
        <td><?= e($r['expires_at']) ?></td>
        <td><span class="badge <?= status_class($st) ?>"><?= status_label($st) ?></span></td>
        <td><?= (int)$r['verify_count'] ?></td>
        <td><a class="btn ghost sm" href="/admin/licenses/<?= (int)$r['id'] ?>">查看</a></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>

<div class="grid g2">
  <div class="card">
    <h2>最近校验</h2>
    <?php if (!$verifies): ?><div class="empty">暂无</div><?php else: ?>
    <table>
      <thead><tr><th>时间</th><th>授权ID</th><th>结果</th></tr></thead>
      <tbody>
      <?php foreach ($verifies as $v): ?>
        <tr>
          <td><?= e(substr($v['created_at'], 5)) ?></td>
          <td class="mono"><?= e($v['identity']) ?></td>
          <td><?= $v['ok'] ? '<span class="badge ok">通过</span>' : '<span class="badge bad">' . e($v['reason']) . '</span>' ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php endif; ?>
  </div>
  <div class="card">
    <h2>最近操作</h2>
    <?php if (!$audits): ?><div class="empty">暂无</div><?php else: ?>
    <table>
      <thead><tr><th>时间</th><th>操作人</th><th>动作</th></tr></thead>
      <tbody>
      <?php foreach ($audits as $a): ?>
        <tr>
          <td><?= e(substr($a['created_at'], 5)) ?></td>
          <td><?= e($a['actor']) ?></td>
          <td><?= e($a['action']) ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php endif; ?>
  </div>
</div>
