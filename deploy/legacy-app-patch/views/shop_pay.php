<form method="post" action="/admin/shop-pay">
  <?= csrf_field() ?>
  <div class="card">
    <h3 style="margin-bottom:6px">易支付（new-api 同一套参数）</h3>
    <p class="hint">回调地址：<code>https://license.moneyfly.club/shop/pay/notify.php</code></p>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:10px">
      <label style="width:110px">支付网关</label><input name="epay_gateway" value="<?= e($s['epay_gateway']) ?>" style="width:360px" placeholder="https://pay.example.com">
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">商户 PID</label><input name="epay_pid" value="<?= e($s['epay_pid']) ?>" style="width:180px">
      <label>商户密钥</label><input name="epay_key" value="<?= e($s['epay_key']) ?>" style="width:300px">
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">支付方式</label>
      <select name="epay_type" style="width:140px">
        <option value="alipay" <?= $s['epay_type'] === 'alipay' ? 'selected' : '' ?>>支付宝</option>
        <option value="wxpay" <?= $s['epay_type'] === 'wxpay' ? 'selected' : '' ?>>微信</option>
      </select>
      <label><input type="checkbox" name="allow_mock_pay" value="1" <?= $s['allow_mock_pay'] === '1' ? 'checked' : '' ?>> 允许模拟支付（仅测试）</label>
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">用户端公告</label><input name="announcement" value="<?= e($s['announcement']) ?>" style="width:520px">
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">站点名称</label><input name="site_name" value="<?= e($s['site_name']) ?>" style="width:300px">
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h3 style="margin-bottom:6px">邮箱验证 / 找回密码（SMTP）</h3>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px">
      <label><input type="checkbox" name="require_verify" value="1" <?= $s['require_verify'] === '1' ? 'checked' : '' ?>> 注册需要邮箱验证</label>
      <label><input type="checkbox" name="debug_codes" value="1" <?= $s['debug_codes'] === '1' ? 'checked' : '' ?>> 调试：接口返回验证码（仅测试）</label>
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">SMTP 主机</label><input name="smtp_host" value="<?= e($s['smtp_host']) ?>" style="width:240px">
      <label>端口</label><input name="smtp_port" value="<?= e($s['smtp_port'] ?: '465') ?>" style="width:80px">
      <label>加密</label>
      <select name="smtp_secure" style="width:120px">
        <?php foreach (['ssl' => 'SSL', 'tls' => 'STARTTLS', 'none' => '无'] as $k => $v): ?>
          <option value="<?= e($k) ?>" <?= $s['smtp_secure'] === $k ? 'selected' : '' ?>><?= e($v) ?></option>
        <?php endforeach; ?>
      </select>
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">SMTP 账号</label><input name="smtp_user" value="<?= e($s['smtp_user']) ?>" style="width:240px">
      <label>授权码</label><input name="smtp_pass" value="<?= e($s['smtp_pass']) ?>" style="width:240px">
    </div>
    <div class="toolbar" style="flex-wrap:wrap;gap:10px;margin-top:8px">
      <label style="width:110px">发件人</label><input name="smtp_from" value="<?= e($s['smtp_from']) ?>" style="width:280px">
    </div>
  </div>

  <div style="margin-top:16px"><button class="btn" type="submit">保存设置</button></div>
</form>
