<div class="card">
  <h3>两步验证（2FA）</h3>
  <p class="hint">开启后每次登录后台需要输入身份验证器中的 6 位验证码，可显著降低账号被盗风险。</p>
  <?php if ($enabled): ?>
    <p style="margin:12px 0;color:#0F766E"><b>已开启</b></p>
    <form method="post" action="/admin/security/2fa-disable" onsubmit="return confirm('关闭 2FA？')">
      <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">关闭两步验证</button>
    </form>
  <?php else: ?>
    <p style="margin:12px 0">1. 在身份验证器 App 中添加以下密钥（或扫描/粘贴下方链接）：</p>
    <p class="mono" style="word-break:break-all;background:#F3F6F7;padding:8px;border-radius:6px"><?= e($secret) ?></p>
    <p class="mono" style="word-break:break-all;"><?= e($uri) ?></p>
    <p style="margin:12px 0">2. 输入 App 中生成的 6 位验证码完成绑定：</p>
    <form method="post" action="/admin/security/2fa-enable">
      <?= csrf_field() ?>
      <label for="security-code">身份验证器验证码</label><input id="security-code" name="code" inputmode="numeric" maxlength="6" placeholder="6 位验证码" >
      <button class="btn" type="submit">开启 2FA</button>
    </form>
  <?php endif; ?>
</div>
