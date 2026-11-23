<?php
$pub = (int)($counts['publish_ok'] ?? 0);
$draft = (int)($counts['draft'] ?? 0);
$fail = (int)($counts['publish_fail'] ?? 0);
$errs = (int)($counts['error'] ?? 0);
$linksN = (int)($counts['link'] ?? 0);
$starts = (int)($counts['start'] ?? 0);

$syncAt = (string)($device['last_seen'] ?? '');
$syncAgo = '';
if ($syncAt !== '') {
    $t = strtotime($syncAt);
    if ($t) {
        $d = time() - $t;
        if ($d < 0) {
            $syncAgo = '刚刚';
        } elseif ($d < 3600) {
            $syncAgo = max(1, (int)round($d / 60)) . ' 分钟前';
        } elseif ($d < 86400) {
            $syncAgo = (int)round($d / 3600) . ' 小时前';
        } else {
            $syncAgo = (int)round($d / 86400) . ' 天前';
        }
    }
}
$licState = $lic ? license_effective_status($lic) : '';
?>

<div class="card detail-head-card">
  <div class="detail-head">
    <div class="detail-title">
      <div class="mono detail-id"><?= e($identity) ?></div>
      <div class="detail-meta">
        <?php if ($lic): ?>
          <span class="badge <?= status_class($licState) ?>"><?= status_label($licState) ?></span>
          <span><?= e($lic['plan']) ?></span>
          <span>到期 <?= e($lic['expires_at']) ?></span>
          <span><?= (int)$lic['verify_count'] ?> 次校验</span>
          <span>最近校验 <?= e($lic['last_verified_at'] ?: '无') ?></span>
        <?php else: ?>
          <span class="badge muted">未登记</span>
          <span>刊登尝试 <?= $starts ?> 次</span>
        <?php endif; ?>
        <span class="sync-tag">
          <?= $syncAt !== '' ? '设备同步：' . e($syncAt) . '（' . e($syncAgo) . '）' : '设备数据：尚未同步' ?>
        </span>
      </div>
    </div>
    <div class="detail-actions"><a class="btn" href="/admin?identity=<?= e(rawurlencode($identity)) ?>">设备问题分析</a><a class="btn ghost" href="/admin/events?identity=<?= e(rawurlencode($identity)) ?>">运行事件</a>
      <?php if (!$lic): ?>
        <form method="post" action="/admin/userdata/<?= e($identity) ?>/register" class="inline-form">
          <?= csrf_field() ?>
          <input type="number" name="days" value="365" min="1" max="3650"  title="有效天数">
          <input type="text" name="plan" value="standard"  title="套餐">
          <button class="btn" type="submit" onclick="return confirm('为该设备创建授权？')">一键登记</button>
        </form>
      <?php endif; ?>
      <a class="btn ghost" href="/admin/userdata">返回列表</a>
      <a class="btn ghost" href="/admin/logs?identity=<?= e(urlencode($identity)) ?>">查看日志</a>
      <form method="post" action="/admin/userdata/<?= e(urlencode($identity)) ?>/delete"
            onsubmit="return confirm('删除该设备的使用数据？（日志/事件/设备信息，授权记录保留）')" style="display:inline">
        <?= csrf_field() ?>
        <button class="btn danger" type="submit">删除数据</button>
      </form>
    </div>
  </div>
</div>

<p class="data-note">以下为原日志识别累计值，历史重复上传可能重复计数。去重后的事件、时间范围和数据来源请查看「设备问题分析」。</p>
<div class="kpi-strip">
  <div class="kpi-item"><span class="l">刊登成功</span><b class="n" style="color:var(--ok)"><?= $pub ?></b></div>
  <div class="kpi-item"><span class="l">保存草稿</span><b class="n"><?= $draft ?></b></div>
  <div class="kpi-item"><span class="l">刊登失败</span><b class="n" style="color:var(--warn)"><?= $fail ?></b></div>
  <div class="kpi-item"><span class="l">报错</span><b class="n" style="color:var(--bad)"><?= $errs ?></b></div>
  <div class="kpi-item"><span class="l">产品链接</span><b class="n"><?= $linksN ?></b></div>
  <div class="kpi-item"><span class="l">刊登尝试</span><b class="n"><?= $starts ?></b></div>
</div>

<div class="card">
  <h2>设备信息</h2>
  <?php if (!$device): ?>
    <div class="empty">暂无设备信息（客户端下次授权校验后会自动上报）</div>
  <?php else: ?>
    <div class="info-grid">
      <div class="info-item"><span class="info-label">机器名</span><span class="info-value"><?= e($device['machine_name'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">机器码</span><span class="info-value mono"><?= e($device['machine_code'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">平台</span><span class="info-value"><?= e($device['platform'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">系统版本</span><span class="info-value"><?= e($device['os_version'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">软件版本</span><span class="info-value"><?= e($device['app_version'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">店铺</span><span class="info-value"><?= e($device['store_name'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">实例</span><span class="info-value"><?= e($device['instance'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">最近 IP</span><span class="info-value mono"><?= e($device['last_ip'] ?: '—') ?></span></div>
      <div class="info-item"><span class="info-label">首次在线</span><span class="info-value"><?= e($device['first_seen'] ?: '—') ?></span></div>
    </div>
  <?php endif; ?>
</div>

<div class="detail-row">
  <div class="card">
    <h2>错误类型分布</h2>
    <?php if (!$errTypes): ?>
      <div class="empty">暂无错误记录</div>
    <?php else: ?>
      <?php
        $max = 1;
        foreach ($errTypes as $t) { $max = max($max, (int)$t['c']); }
      ?>
      <div class="bars">
        <?php foreach ($errTypes as $t): ?>
          <div class="bar-row">
            <span class="bar-label" title="<?= e($t['error_type']) ?>"><?= e($t['error_type']) ?></span>
            <span class="bar-track"><span class="bar-fill" style="width:<?= (int)round((int)$t['c'] / $max * 100) ?>%"></span></span>
            <span class="bar-num"><?= (int)$t['c'] ?></span>
          </div>
        <?php endforeach; ?>
      </div>
    <?php endif; ?>
  </div>

  <div class="card">
    <h2>上传的日志 <span class="count-pill"><?= count($logs) ?></span></h2>
    <?php if (!$logs): ?>
      <div class="empty">暂无日志</div>
    <?php else: ?>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>文件</th><th>大小</th><th>事件</th><th>错误</th><th style="text-align:right">操作</th></tr></thead>
          <tbody>
          <?php foreach ($logs as $l): ?>
            <tr>
              <td class="nowrap"><?= e($l['created_at']) ?></td>
              <td><?= e($l['filename']) ?></td>
              <td class="nowrap"><?= number_format((int)$l['size'] / 1024, 1) ?> KB</td>
              <td><?= (int)$l['event_count'] ?></td>
              <td><?= (int)$l['error_count'] > 0 ? '<span class="badge bad">' . (int)$l['error_count'] . '</span>' : '<span class="badge ok">0</span>' ?></td>
              <td class="ops"><a class="btn ghost sm" href="/admin/logs/<?= (int)$l['id'] ?>">查看</a></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>
    <?php endif; ?>
  </div>
</div>

<div class="card">
  <h2>产品链接 <span class="count-pill"><?= $linksN ?></span></h2>
  <?php if (!$links): ?>
    <div class="empty empty-sm">还没有抓到产品链接</div>
  <?php else: ?>
    <div class="table-wrap">
      <table>
        <thead><tr><th >时间</th><th>链接</th></tr></thead>
        <tbody>
        <?php foreach ($links as $k): ?>
          <tr>
            <td class="nowrap"><?= e(substr($k['created_at'], 5, 11)) ?></td>
            <td class="link-cell"><a href="<?= e($k['detail']) ?>" target="_blank" rel="noopener"><?= e($k['detail']) ?></a></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  <?php endif; ?>
</div>

<div class="card events-card">
  <h2>最近事件 <span class="count-pill"><?= count($events) ?></span></h2>
  <?php if (!$events): ?>
    <div class="empty">暂无</div>
  <?php else: ?>
    <div class="table-wrap">
      <table>
        <thead><tr><th >时间</th><th >事件</th><th >类型</th><th>内容</th></tr></thead>
        <tbody>
        <?php foreach ($events as $ev): ?>
          <tr>
            <td class="nowrap"><?= e(substr($ev['created_at'], 5, 11)) ?></td>
            <td><span class="badge <?= $ev['event'] === 'publish_ok' ? 'ok' : (in_array($ev['event'], ['error', 'publish_fail'], true) ? 'bad' : 'muted') ?>"><?= e($ev['event']) ?></span></td>
            <td><?= $ev['error_type'] !== '' ? '<span class="badge warn">' . e($ev['error_type']) . '</span>' : '<span class="hint">—</span>' ?></td>
            <td style="word-break:break-all;font-size:12.5px"><?= e($ev['detail']) ?></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  <?php endif; ?>
</div>
