<div class="auth-wrap">
  <div class="auth">
    <h1>管理员登录</h1>
    <p class="sub"><?= e(cfg('brand')) ?> 授权中心</p>
    <form method="post" action="/admin/login">
      <?= csrf_field() ?>
      <div class="field">
        <label for="u">用户名</label>
        <input id="u" type="text" name="username" autocomplete="username" autofocus required>
      </div>
      <div class="field">
        <label for="p">密码</label>
        <input id="p" type="password" name="password" autocomplete="current-password" required>
      </div>
      <button class="btn" type="submit">登录</button>
    </form>
  </div>
</div>
