<div class="actions section-space"><a class="btn" href="/admin/?user_id=<?= (int)$u['id'] ?>">运行与问题分析</a><a class="btn ghost" href="/admin/events?user_id=<?= (int)$u['id'] ?>">该客户运行事件</a></div>
<nav class="detail-nav" aria-label="用户详情分区"><a href="#rights">有效权限</a><a href="#grant">追加权限</a><a href="#account">账号资料</a><a href="#runtime">运行概况</a><a href="#devices">设备</a><a href="#customer-logs">日志</a><a href="#history">历史与订单</a></nav>
<section class="card" id="rights"><div class="section-title"><div><span class="eyebrow">ACCOUNT ENTITLEMENTS</span><h2>当前有效权限</h2></div><span class="badge ok">原设备授权独立保留</span></div>
<?php if(!$effective): ?><div class="empty">暂无有效套餐权限，可在下方追加开通。</div><?php else: ?><div class="rights-grid">
<?php foreach($effective['feature_expiries'] as $pid=>$fs): ?><article class="right-card"><h3><?= e(lc_names()[$pid] ?? $pid) ?></h3><?php foreach($fs as $f=>$exp): ?><div class="right-line"><span><?= e(lc_feature_names()[$f] ?? $f) ?></span><time><?= e($exp) ?></time></div><?php endforeach; ?></article><?php endforeach; ?></div><?php endif; ?>
</section>
<section class="card"><h2>关联原设备授权</h2><p class="muted">仅关联查看权限和软件日志，不复制、续期、吊销或改变原授权。原客户仍可直接使用原软件，无需注册。</p>
<?php foreach($linkedLegacy as $old): ?><div class="device-row"><code><?= e($old['identity']) ?></code><span><?= e($old['expires_at']) ?> 到期</span><span><?= e(status_label(license_effective_status($old))) ?></span></div><?php endforeach; ?>
<?php if((current_admin()['role'] ?? '')==='owner'): ?><form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/link-device" class="form-grid"><?= csrf_field() ?><label>已核实归属的原设备授权 ID<input name="identity" type="text" required placeholder="粘贴原授权 ID…"></label><div class="form-actions"><button class="btn ghost">关联原设备</button></div></form><?php endif; ?></section>
<section class="card" id="runtime"><div class="section-title"><div><h2>客户运行概况</h2><p class="muted">最近 7 天 · 设备校验和日志实际接收数据</p></div><a class="btn ghost sm" href="/admin?user_id=<?= (int)$u['id'] ?>">查看完整分析</a></div><div class="analysis-kpis compact"><article class="metric-card"><span>运行事件</span><strong><?= (int)$runtime['events'] ?></strong></article><article class="metric-card"><span>异常事件</span><strong class="danger"><?= (int)$runtime['errors'] ?></strong></article><article class="metric-card"><span>最近事件</span><strong class="metric-date"><?= e($runtime['latest'] ?: '暂无上报') ?></strong></article></div>
<?php if(!$runtime['devices']): ?><p class="muted">暂无关联设备的环境信息。已开通权限不代表软件已运行或日志已上传。</p><?php else: ?><div class="table-scroll"><table><thead><tr><th>设备</th><th>软件版本</th><th>操作系统</th><th>店铺 / 实例</th><th>最近设备校验</th></tr></thead><tbody><?php foreach($runtime['devices'] as $environment): ?><tr><td><a href="/admin/userdata/<?= e(rawurlencode($environment['identity'])) ?>"><?= e($environment['identity']) ?></a></td><td><?= e($environment['app_version'] ?: '未上报') ?></td><td><?= e(trim($environment['platform'].' '.$environment['os_version']) ?: '未上报') ?></td><td><?= e($environment['store_name'] ?: '未上报') ?><div class="muted"><?= e($environment['instance']) ?></div></td><td><?= e($environment['last_seen']) ?></td></tr><?php endforeach; ?></tbody></table></div><?php endif; ?></section>
<section class="card" id="devices"><div class="section-title"><h2>账号设备</h2><a href="/admin/customer-devices">查看设备列表</a></div>
<?php if(!$devices): ?><p class="muted">尚未绑定。用户在软件中使用邮箱登录后，设备自动关联；不要求旧客户重新注册。</p><?php else: foreach($devices as $device): ?><div class="device-row"><code><?= e($device['machine_code']) ?></code><span class="badge ok">已绑定</span><a href="/admin/customer-logs?user_id=<?= (int)$u['id'] ?>">查看该账号日志</a></div><?php endforeach; endif; ?>
</section>
<section class="card" id="customer-logs"><div class="section-title"><div><h2>软件日志</h2><p class="muted">按已知设备关联历史上传，保留原日志和错误分类。</p></div><a class="btn ghost sm" href="/admin/customer-logs?user_id=<?= (int)$u['id'] ?>">查看全部 <?= (int)$customerLogs['total'] ?> 条</a></div>
<?php if(!$customerLogs['rows']): ?><div class="empty">暂无关联日志。账号绑定设备后，软件成功上传的日志会显示在这里。</div><?php else: ?><div class="table-scroll"><table><thead><tr><th>时间</th><th>文件</th><th>错误</th><th></th></tr></thead><tbody><?php foreach(array_slice($customerLogs['rows'],0,5) as $log): ?><tr><td><?= e($log['created_at']) ?></td><td><?= e($log['filename']) ?></td><td><?= (int)$log['error_count'] ?></td><td><a href="/admin/customer-logs/<?= (int)$u['id'] ?>/<?= (int)$log['id'] ?>">查看</a></td></tr><?php endforeach; ?></tbody></table></div><?php endif; ?></section>
<div class="toolbar">
  <a class="btn ghost" href="/admin/shop-users">← 返回用户列表</a>
  <div class="toolbar-right">
    <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/revoke" onsubmit="return confirm('取消该用户全部授权？')">
      <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">取消全部套餐权限</button>
    </form>
    <form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/delete" onsubmit="return confirm('删除套餐用户及其订单和套餐权限？原设备授权不受影响。不可恢复')">
      <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">删除用户</button>
    </form>
  </div>
</div>

<section class="card" id="account"><h2>账号资料</h2><form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/update"><?= csrf_field() ?><div class="form-grid">
<label>邮箱<input type="email" name="email" value="<?= e($u['email']) ?>" required></label><label>称呼<input name="name" value="<?= e($u['name'] ?? '') ?>"></label><label>客户标签<input name="tags" value="<?= e($u['tags'] ?? '') ?>" placeholder="例如 VIP、待续费…"></label><label class="field-wide">备注<textarea name="note" rows="3"><?= e($u['note'] ?? '') ?></textarea></label>
</div><div class="form-actions"><button class="btn" type="submit">保存账号资料</button></div></form>
<div class="form-section"><h3>重置登录密码</h3><form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/password"><?= csrf_field() ?><div class="form-grid"><label>新密码<input name="password" type="password" autocomplete="new-password" minlength="6" required><span class="hint">至少 6 位，重置后立即生效。</span></label></div><div class="form-actions"><button class="btn ghost" type="submit">重置密码</button></div></form></div></section>
<section class="card" id="grant"><h2>追加平台权限</h2><p class="muted">只追加所选平台，保留现有有效权限；每个平台及功能分别计算到期。</p><form method="post" action="/admin/shop-users/<?= (int)$u['id'] ?>/grant"><?= csrf_field() ?><div class="form-grid"><label>开通平台<select name="platform" id="grantPlatform" required><option value="">请选择平台</option><option value="all">全部平台（全功能）</option><?php foreach($platforms as $pid=>$features): ?><option value="<?= e($pid) ?>"><?= e(lc_names()[$pid] ?? $pid) ?>（全功能）</option><?php endforeach; ?></select></label><label>有效天数<input name="days" type="number" value="30" min="1" max="36500" required></label></div><div class="form-actions"><button class="btn" type="submit">追加开通</button></div></form></section>
<div class="card" id="history">
  <h3 style="margin:10px 8px">历史开通记录</h3>
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
        <td><span class="badge <?= (int)$l['revoked'] ? 'bad' : 'ok' ?>"><?= (int)$l['revoked'] ? '已取消' : ($l['expires_at']<date('Y-m-d')?'已过期':'有效') ?></span></td>
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

<div class="card">
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
