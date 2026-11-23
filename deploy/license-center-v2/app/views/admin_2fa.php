<div class="card">
  <h3 style="margin-bottom:10px">两步验证</h3>
  <p class="hint">请输入身份验证器 App（Google Authenticator / 微信小程序等）中的 6 位验证码。</p>
  <form method="post" action="/admin/2fa">
    <?= csrf_field() ?>
    <input name="code" inputmode="numeric" maxlength="6" placeholder="6 位验证码" style="margin:12px 0;letter-spacing:6px;text-align:center">
    <button class="btn" type="submit" >验证并登录</button>
  </form>
  <p class="hint" style="margin-top:10px"><a href="/admin/login">返回登录</a></p>
</div>
