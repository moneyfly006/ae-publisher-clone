<?php $section=lc_settings_section(); if(!in_array($section,['payment','mail','portal'],true))$section='payment'; ?>
<form method="post" action="/admin/shop-pay" class="settings-form">
<?= csrf_field() ?>
<input type="hidden" name="settings_group" value="<?= e($section) ?>">
<?php if($section==='payment'): ?>
<section class="card" id="payment"><div class="section-title"><div><span class="eyebrow">PAYMENT GATEWAY</span><h2>支付网关</h2><p class="muted">用于订单收款与支付结果确认；保存参数不会改变已有客户授权。</p></div><span class="type-label">仅所有者可管理</span></div>
<div class="form-grid">
<label>支付网关地址<input type="url" name="epay_gateway" value="<?= e($s['epay_gateway']) ?>" placeholder="https://pay.example.com" autocomplete="off" spellcheck="false"></label>
<label>商户 PID<input type="text" name="epay_pid" value="<?= e($s['epay_pid']) ?>" autocomplete="off" spellcheck="false"></label>
<label>商户密钥<span class="secret-field"><input id="payment-secret" type="password" name="epay_key" value="<?= e($s['epay_key']) ?>" autocomplete="new-password"><button type="button" data-reveal="payment-secret" aria-label="显示商户密钥">显示</button></span></label>
<label>默认支付方式<select name="epay_type"><option value="alipay" <?= $s['epay_type']==='alipay'?'selected':'' ?>>支付宝</option><option value="wxpay" <?= $s['epay_type']==='wxpay'?'selected':'' ?>>微信支付</option></select></label>
</div><div class="endpoint-box"><span>支付通知地址</span><code>https://license.moneyfly.club/pay/notify.php</code><small>原 /shop/pay/notify.php 通知地址继续兼容。</small></div>
<label class="setting-check"><input type="checkbox" name="allow_mock_pay" value="1" <?= $s['allow_mock_pay']==='1'?'checked':'' ?>><span><b>允许模拟支付</b><small>仅用于测试；正式收款不应依赖模拟支付。</small></span></label>
</section>
<?php elseif($section==='mail'): ?>
<section class="card" id="mail"><div class="section-title"><div><span class="eyebrow">EMAIL DELIVERY</span><h2>邮件服务</h2><p class="muted">用于注册验证、密码找回和授权提醒。</p></div></div>
<div class="form-grid">
<label>SMTP 主机<input type="text" name="smtp_host" value="<?= e($s['smtp_host']) ?>" placeholder="smtp.example.com" autocomplete="off" spellcheck="false"></label>
<label>端口<input type="number" name="smtp_port" value="<?= e($s['smtp_port'] ?: '465') ?>" min="1" max="65535"></label>
<label>连接加密<select name="smtp_secure"><?php foreach(['ssl'=>'SSL','tls'=>'STARTTLS','none'=>'无加密'] as $key=>$label): ?><option value="<?= e($key) ?>" <?= $s['smtp_secure']===$key?'selected':'' ?>><?= e($label) ?></option><?php endforeach; ?></select></label>
<label>SMTP 账号<input type="text" name="smtp_user" value="<?= e($s['smtp_user']) ?>" autocomplete="off" spellcheck="false"></label>
<label>SMTP 授权码<span class="secret-field"><input id="smtp-secret" type="password" name="smtp_pass" value="<?= e($s['smtp_pass']) ?>" autocomplete="new-password"><button type="button" data-reveal="smtp-secret" aria-label="显示 SMTP 授权码">显示</button></span></label>
<label>发件人邮箱<input type="email" name="smtp_from" value="<?= e($s['smtp_from']) ?>" autocomplete="off" spellcheck="false"></label>
</div>
<div class="setting-options"><label class="setting-check"><input type="checkbox" name="require_verify" value="1" <?= $s['require_verify']==='1'?'checked':'' ?>><span><b>注册需要邮箱验证</b><small>客户通过邮件验证码验证后完成注册。</small></span></label><label class="setting-check"><input type="checkbox" name="debug_codes" value="1" <?= $s['debug_codes']==='1'?'checked':'' ?>><span><b>调试时返回验证码</b><small>仅用于测试，不应在正式注册流程中启用。</small></span></label></div>
</section>
<?php else: ?>
<section class="card" id="portal"><div class="section-title"><div><span class="eyebrow">CUSTOMER PORTAL</span><h2>用户端设置</h2></div></div><div class="form-grid"><label>站点名称<input type="text" name="site_name" value="<?= e($s['site_name']) ?>"></label><label>用户端公告<input type="text" name="announcement" value="<?= e($s['announcement']) ?>"></label></div></section>
<?php endif; ?>
<div class="save-bar"><span>仅保存当前分区，其他服务配置保持不变。</span><button class="btn" type="submit">保存<?= e(lc_settings_sections()[$section]) ?></button></div>
</form>
