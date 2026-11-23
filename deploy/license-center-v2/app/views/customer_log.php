<div class="page-intro"><div><p><?= e($user['email']) ?> · 用户 #<?= (int)$uid ?> · <?= e($row['created_at']) ?></p><p class="muted">设备 <?= e($row['identity']) ?> · 软件版本 <?= e($row['client_version'] ?: '未上报') ?></p></div><a class="btn ghost" href="<?= e($back) ?>">返回用户日志</a></div>
<?php if (!empty($cookieProfiles)): ?>
<section class="card"><details open><summary style="cursor:pointer;color:#eac078">该账号各平台 Cookie（敏感，仅管理员可见）</summary><p class="muted">按平台分别保存，同一平台只更新最新值，不会覆盖其他平台；用于复现该账号问题，请勿转发。</p>
<?php foreach ($cookieProfiles as $profile): ?><div style="margin:8px 0"><span class="badge muted"><?= e($profile['platform']) ?></span> <span class="muted"><?= e($profile['updated_at']) ?></span><pre class="log-content" style="max-height:24vh;overflow:auto"><?= e($profile['cookies']) ?></pre></div><?php endforeach; ?>
</details></section>
<?php elseif (!empty($cookies)): ?>
<section class="card"><details><summary style="cursor:pointer;color:#eac078">设备 Cookie（敏感，仅管理员可见，点击展开）</summary><p class="muted">用于复现该账号问题；请勿转发、截图或写入工单。</p><pre class="log-content" style="max-height:40vh;overflow:auto"><?= e($cookies) ?></pre></details></section>
<?php endif; ?>
<section class="card log-assessment" aria-labelledby="assessment-title">
<div class="section-title"><div><span class="eyebrow">LOG DIAGNOSIS</span><h2 id="assessment-title"><?= e($assessment['summary']) ?></h2></div><span class="badge <?= $assessment['issues']?'warn':'muted' ?>">日志证据分析</span></div>
<p><?= e($assessment['next']) ?></p><p class="muted assessment-note">分析<?= $assessment['limited']?'最近':'' ?> <?= (int)$assessment['analyzed_lines'] ?> 行 / 共 <?= (int)$assessment['total_lines'] ?> 行。判断基于本份日志中的错误信息，可能原因需结合店铺响应核实；不修改商品或客户配置。</p>
<?php foreach($assessment['issues'] as $issue): ?>
<article class="assessment-issue"><div class="section-title"><h3><?= e($issue['title']) ?></h3><span class="badge <?= $issue['priority']==='高'?'bad':'warn' ?>"><?= e($issue['priority']) ?>优先级 · <?= (int)$issue['count'] ?> 条记录</span></div>
<dl class="assessment-detail"><dt>问题判断</dt><dd><?= e($issue['cause']) ?></dd><dt>日志证据</dt><dd><?php foreach($issue['evidence'] as $evidence): ?><div class="assessment-evidence"><span>第 <?= (int)$evidence['line'] ?> 行</span><code><?= e($evidence['text']) ?></code></div><?php endforeach; ?></dd><dt>解决步骤</dt><dd><ol><?php foreach($issue['steps'] as $step): ?><li><?= e($step) ?></li><?php endforeach; ?></ol></dd><dt>验证方法</dt><dd><?= e($issue['verify']) ?></dd></dl>
</article>
<?php endforeach; ?>
</section>
<section class="card"><div class="section-title"><div><h2><?= e($row['filename']) ?></h2><p class="muted">原始日志正文（展示时再次脱敏），用于核对完整操作过程。</p></div><a class="btn ghost" href="#raw-log">查看日志正文</a></div><pre id="raw-log" class="log-content" tabindex="0"><?= e($row['content']) ?></pre></section>
