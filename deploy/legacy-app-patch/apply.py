import shutil, time, re
A = "/www/server/ae-license/app"
PUB = "/www/wwwroot/license.moneyfly.club/public/index.php"
ts = str(int(time.time()))
# 1) 复制页面与视图
shutil.copy("/tmp/oldapp_patch/shop_admin.php", A + "/shop_admin.php")
for v in ("shop_packages","shop_pay","shop_orders","shop_users","shop_licenses"):
    shutil.copy(f"/tmp/oldapp_patch/{v}.php", f"{A}/views/{v}.php")
# 2) index.php 引入 + 路由
s = open(PUB, encoding="utf-8").read()
if "shop_admin.php" not in s:
    s = s.replace("require $appDir . '/app/pages.php';",
                  "require $appDir . '/app/pages.php';\nrequire $appDir . '/app/shop_admin.php';", 1)
routes = """    ['GET',  '#^/admin/shop-packages$#', 'page_shop_packages'],
    ['POST', '#^/admin/shop-packages$#', 'page_shop_packages_save'],
    ['GET',  '#^/admin/shop-pay$#',      'page_shop_pay'],
    ['POST', '#^/admin/shop-pay$#',      'page_shop_pay_save'],
    ['GET',  '#^/admin/shop-orders$#',   'page_shop_orders'],
    ['POST', '#^/admin/shop-orders/(\\d+)/paid$#', 'page_shop_order_paid'],
    ['GET',  '#^/admin/shop-users$#',    'page_shop_users'],
    ['GET',  '#^/admin/shop-licenses$#', 'page_shop_licenses'],
    ['POST', '#^/admin/shop-licenses/(\\d+)/revoke$#', 'page_shop_license_revoke'],
"""
if "/admin/shop-packages" not in s:
    s = s.replace("    ['POST', '#^/api/v1/license/verify$#',     'api_verify'],",
                  routes + "    ['POST', '#^/api/v1/license/verify$#',     'api_verify'],", 1)
    shutil.copy(PUB, PUB + ".bak." + ts)
    open(PUB, "w", encoding="utf-8").write(s)
    print("index routes added")
else:
    print("routes exist")
# 3) layout 导航：把外链替换为内部页面
L = A + "/layout.php"
s = open(L, encoding="utf-8").read()
if "shop-packages" not in s:
    s = s.replace('      <a href="/shop/admin.php" target="_blank">套餐 / 支付</a>\n', '')
    s = s.replace('      <a href="/shop/" target="_blank">用户端</a>',
'''      <a class="<?= strpos($p, '/admin/shop-packages') === 0 ? 'on' : '' ?>" href="/admin/shop-packages">套餐管理</a>
      <a class="<?= strpos($p, '/admin/shop-pay') === 0 ? 'on' : '' ?>" href="/admin/shop-pay">支付设置</a>
      <a class="<?= strpos($p, '/admin/shop-orders') === 0 ? 'on' : '' ?>" href="/admin/shop-orders">套餐订单</a>
      <a class="<?= strpos($p, '/admin/shop-users') === 0 ? 'on' : '' ?>" href="/admin/shop-users">套餐用户</a>
      <a class="<?= strpos($p, '/admin/shop-licenses') === 0 ? 'on' : '' ?>" href="/admin/shop-licenses">套餐授权</a>
      <a href="/shop/" target="_blank">用户端</a>''', 1)
    shutil.copy(L, L + ".bak." + ts)
    open(L, "w", encoding="utf-8").write(s)
    print("nav updated")
else:
    print("nav exists")
