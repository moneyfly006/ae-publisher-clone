<?php
declare(strict_types=1);
require_once (getenv('LICENSE_CENTER_SHARED') ?: '/www/server/ae-license/shared') . '/entitlements.php';

/* 新授权中心（套餐/支付/订单/用户端授权）嵌入原后台 */

function shop_pdo(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;
    $path = getenv('SHOP_LICENSE_DB') ?: '/www/wwwroot/license.moneyfly.club/public/shop/data/license.db';
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA busy_timeout = 5000');
    return $pdo;
}

function shop_key(): string { return 'aurora-list-platform-license-v1'; }

function shop_canonical($v): string
{
    if (is_array($v)) {
        $isList = array_keys($v) === range(0, count($v) - 1);
        if ($isList) {
            $parts = [];
            foreach ($v as $x) $parts[] = shop_canonical($x);
            return '[' . implode(',', $parts) . ']';
        }
        ksort($v);
        $parts = [];
        foreach ($v as $k => $x) {
            $parts[] = json_encode((string)$k, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . ':' . shop_canonical($x);
        }
        return '{' . implode(',', $parts) . '}';
    }
    if (is_string($v)) return json_encode($v, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (is_bool($v)) return $v ? 'true' : 'false';
    if ($v === null) return 'null';
    return (string)$v;
}

function shop_sign(array $payload): string
{
    return hash_hmac('sha256', shop_canonical($payload), shop_key());
}

function shop_issue_token(string $machine, array $platforms, string $expires): string
{
    $payload = [
        'machine_code' => $machine,
        'platforms' => array_map(function ($f) { sort($f); return array_values(array_unique($f)); }, $platforms),
        'expires_at' => $expires,
        'issued_at' => time(),
    ];
    return json_encode(['payload' => $payload, 'sig' => shop_sign($payload)], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

function shop_setting(string $k, string $default = ''): string
{
    $st = shop_pdo()->prepare('SELECT v FROM settings WHERE k=?');
    $st->execute([$k]);
    $r = $st->fetch();
    return $r ? (string)$r['v'] : $default;
}

function shop_set_setting(string $k, string $v): void
{
    shop_pdo()->prepare('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v')
        ->execute([$k, $v]);
}

function shop_platform_map(): array { return lc_platforms(); }

function shop_issue_for_order(int $orderId): ?array { return lc_fulfill($orderId); }

function page_shop_packages(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT * FROM packages ORDER BY sort,id')->fetchAll();
    foreach ($rows as &$r) {
        $r['platforms_arr'] = json_decode((string)$r['platforms'], true) ?: [];
        $r['features_arr'] = json_decode((string)$r['features'], true) ?: [];
        $r['price'] = ((int)$r['price_cents']) / 100;
    }
    render('shop_packages', ['title' => '套餐管理', 'rows' => $rows]);
}

function page_shop_packages_save(): void
{
    require_admin(); csrf_check();
    $id = (int)($_POST['id'] ?? 0);
    if (isset($_POST['delete_id'])) {
        shop_pdo()->prepare('DELETE FROM packages WHERE id=?')->execute([(int)$_POST['delete_id']]);
        audit('删除套餐', 'package:' . (int)$_POST['delete_id']);
        flash('ok', '套餐已删除');
        redirect('/admin/shop-packages');
    }
    $name=trim((string)($_POST['name'] ?? ''));
    $platformIds=array_values(array_unique(array_filter(array_map('trim',explode(',',(string)($_POST['platforms'] ?? ''))))));
    $featureIds=array_values(array_unique(array_filter(array_map('trim',explode(',',(string)($_POST['features'] ?? ''))))));
    $days=filter_var($_POST['days'] ?? 365,FILTER_VALIDATE_INT);$devices=filter_var($_POST['device_limit'] ?? 1,FILTER_VALIDATE_INT);
    $price=(string)($_POST['price'] ?? '');$valid=$name!==''&&mb_strlen($name)<=160&&$days>=1&&$days<=36500&&$devices>=1&&$devices<=1000&&is_numeric($price)&&(float)$price>=0&&(float)$price<=1000000&&$platformIds&&$featureIds;
    foreach($platformIds as $pid){if(!isset(lc_platforms()[$pid])||!array_intersect($featureIds,lc_platforms()[$pid]))$valid=false;}
    $allFeatures=array_unique(array_merge(...array_values(lc_platforms())));if(array_diff($featureIds,$allFeatures))$valid=false;
    if(!$valid){flash('bad','请填写有效的套餐名称、平台功能、非负价格、有效天数和设备数');redirect('/admin/shop-packages'.($id>0?'?edit='.$id:'').'#package-editor');return;}
    if ($id > 0) {
        $st = shop_pdo()->prepare('UPDATE packages SET name=?, platforms=?, features=?, price_cents=?, days=?, active=?, device_limit=? WHERE id=?');
        $st->execute([
            trim((string)($_POST['name'] ?? '')),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['platforms'] ?? ''))))), JSON_UNESCAPED_UNICODE),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['features'] ?? ''))))), JSON_UNESCAPED_UNICODE),
            (int)round(((float)($_POST['price'] ?? 0)) * 100),
            (int)($_POST['days'] ?? 365),
            (int)($_POST['active'] ?? 1),
            max(1, (int)($_POST['device_limit'] ?? 1)),
            $id,
        ]);
        audit('修改套餐', 'package:' . $id);
        flash('ok', '套餐已保存');
    } else {
        $st = shop_pdo()->prepare('INSERT INTO packages(name,platforms,features,price_cents,days,active,sort,description,created_at,device_limit) VALUES(?,?,?,?,?,?,?,?,?,?)');
        $st->execute([
            trim((string)($_POST['name'] ?? '新套餐')),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['platforms'] ?? 'alibaba'))))), JSON_UNESCAPED_UNICODE),
            json_encode(array_values(array_filter(array_map('trim', explode(',', (string)($_POST['features'] ?? 'listing,category'))))), JSON_UNESCAPED_UNICODE),
            (int)round(((float)($_POST['price'] ?? 0)) * 100),
            (int)($_POST['days'] ?? 365), (int)($_POST['active'] ?? 1), 99, '', time(), max(1, (int)($_POST['device_limit'] ?? 1)),
        ]);
        audit('新增套餐', 'package:new');
        flash('ok', '套餐已新增');
    }
    redirect('/admin/shop-packages');
}

/* ---------- 支付 / 邮件设置 ---------- */
function page_shop_pay(): void
{
    require_admin(); require_owner();
    $keys = ['site_name', 'epay_gateway', 'epay_pid', 'epay_key', 'epay_type', 'allow_mock_pay',
             'announcement', 'require_verify', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass',
             'smtp_secure', 'smtp_from', 'debug_codes'];
    $s = [];
    foreach ($keys as $k) $s[$k] = shop_setting($k);
    if ($s['site_name'] === '') $s['site_name'] = 'AuroraList 授权中心';
    render('shop_pay', ['title' => '套餐支付设置', 's' => $s]);
}

function page_shop_pay_save(): void
{
    require_admin(); require_owner(); csrf_check();
    $groups = [
        'payment'=>['epay_gateway','epay_pid','epay_key','epay_type','allow_mock_pay'],
        'mail'=>['smtp_host','smtp_port','smtp_user','smtp_pass','smtp_secure','smtp_from','require_verify','debug_codes'],
        'portal'=>['site_name','announcement'],
    ];
    $group=(string)($_POST['settings_group'] ?? 'legacy');
    if($group!=='legacy'&&!isset($groups[$group])){http_response_code(400);return;}
    $keys=$group==='legacy'?array_merge(...array_values($groups)):$groups[$group];
    $changes=[];
    foreach($keys as $key){
        if(array_key_exists($key,$_POST))$changes[$key]=(string)$_POST[$key];
        elseif($group!=='legacy'&&in_array($key,['allow_mock_pay','require_verify','debug_codes'],true))$changes[$key]='0';
    }
    // Validate the entire submission before writing any key.
    $error='';
    foreach(['allow_mock_pay','require_verify','debug_codes'] as $key)if(isset($changes[$key]))$changes[$key]=$changes[$key]==='1'?'1':'0';
    if(isset($changes['smtp_port'])&&(!ctype_digit($changes['smtp_port'])||(int)$changes['smtp_port']<1||(int)$changes['smtp_port']>65535))$error='SMTP 端口必须为 1 至 65535';
    if(isset($changes['smtp_from'])&&$changes['smtp_from']!==''&&!filter_var($changes['smtp_from'],FILTER_VALIDATE_EMAIL))$error='请填写有效的发件人邮箱';
    if(isset($changes['smtp_secure'])&&!in_array($changes['smtp_secure'],['ssl','tls','none'],true))$error='请选择有效的邮件加密方式';
    if(isset($changes['epay_type'])&&!in_array($changes['epay_type'],['alipay','wxpay'],true))$error='请选择有效的支付方式';
    if(isset($changes['epay_gateway'])&&$changes['epay_gateway']!==''&&(!filter_var($changes['epay_gateway'],FILTER_VALIDATE_URL)||!in_array(strtolower((string)parse_url($changes['epay_gateway'],PHP_URL_SCHEME)),['http','https'],true)))$error='支付网关必须为 HTTP 或 HTTPS 地址';
    $section=$group==='legacy'?'payment':$group;
    if($error!==''){flash('bad',$error.'，未保存');redirect('/admin/settings?section='.$section);return;}
    $db=shop_pdo();$db->beginTransaction();
    try{foreach($changes as $key=>$value)shop_set_setting($key,$value);$db->commit();}
    catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    audit('修改服务设置分区','shop:settings:'.$group,implode(',',array_keys($changes)));
    flash('ok','当前分区已保存，其他设置保持不变');
    redirect('/admin/settings?section='.$section);
}

/* ---------- 订单 ---------- */
function page_shop_orders(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT o.*, u.email, p.name AS package_name FROM orders o
        LEFT JOIN users u ON u.id=o.user_id LEFT JOIN packages p ON p.id=o.package_id
        ORDER BY o.id DESC LIMIT 200')->fetchAll();
    render('shop_orders', ['title' => '套餐订单', 'rows' => $rows]);
}

function page_shop_order_paid(int $id): void {
    require_admin();csrf_check();
    try { lc_mark_paid($id);audit('确认订单并履约','order:'.$id);flash('ok','订单已确认，重复操作不会重复开通'); }
    catch(Throwable $e){flash('bad',$e->getMessage());}
    redirect('/admin/shop-orders');
}

function page_shop_users(): void {
    require_admin();$keyword=trim((string)($_GET['q'] ?? ''));$page=max(1,(int)($_GET['page'] ?? 1));$per=25;
    $where=$keyword!==''?'WHERE (u.email LIKE ? OR u.name LIKE ?)':'';$bind=$keyword!==''?['%'.$keyword.'%','%'.$keyword.'%']:[];
    $q=shop_pdo()->prepare('SELECT COUNT(*) FROM users u '.$where);$q->execute($bind);$total=(int)$q->fetchColumn();$pages=max(1,(int)ceil($total/$per));$page=min($page,$pages);$offset=($page-1)*$per;
    $q=shop_pdo()->prepare("SELECT u.*,(SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.status='paid') paid_orders FROM users u $where ORDER BY u.id DESC LIMIT $per OFFSET $offset");$q->execute($bind);$rows=$q->fetchAll();
    foreach($rows as &$r){$e=lc_effective((int)$r['id']);$r['effective']=$e;$r['lic_count']=count($e['platforms'] ?? []);$r['expires_at']=$e['expires_at'] ?? '';}
    render('shop_users',compact('rows','keyword','page','pages','total')+['title'=>'套餐用户']);
}

function page_shop_licenses(): void
{
    require_admin();
    $rows = shop_pdo()->query('SELECT l.*, u.email FROM licenses l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 200')->fetchAll();
    render('shop_licenses', ['title' => '套餐授权', 'rows' => $rows]);
}

function page_shop_license_revoke(int $id): void
{
    require_admin(); csrf_check();
    shop_pdo()->prepare('UPDATE licenses SET revoked=1 WHERE id=?')->execute([$id]);
    audit('吊销套餐授权', 'license:' . $id);
    flash('ok', '授权已吊销');
    redirect('/admin/shop-licenses');
}

/* ---------- 套餐用户详情 / 管理 ---------- */
function page_shop_user_detail(int $id): void
{
    require_admin();
    $st = shop_pdo()->prepare('SELECT * FROM users WHERE id=?'); $st->execute([$id]);
    $u = $st->fetch();
    if (!$u) { flash('bad', '用户不存在'); redirect('/admin/shop-users'); }
    $st = shop_pdo()->prepare('SELECT * FROM licenses WHERE user_id=? ORDER BY id DESC'); $st->execute([$id]);
    $licenses = $st->fetchAll();
    $st = shop_pdo()->prepare('SELECT o.*, p.name AS package_name FROM orders o LEFT JOIN packages p ON p.id=o.package_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 50');
    $st->execute([$id]);
    $orders = $st->fetchAll();
    $runtime=lc_customer_runtime($id);
    $linkedLegacy=lc_linked_legacy($id);
    $effective=lc_effective($id);$devices=user_devices($id);
    $customerLogs=lc_logs($id,1);
    render('shop_user_detail', ['runtime'=>$runtime,'linkedLegacy'=>$linkedLegacy,'effective'=>$effective,'devices'=>$devices,'customerLogs'=>$customerLogs,'title' => '套餐用户：' . $u['email'], 'u' => $u, 'licenses' => $licenses,
                                'orders' => $orders, 'platforms' => shop_platform_map()]);
}

function page_shop_user_update(int $id): void
{
    require_admin(); csrf_check();
    $email = trim(strtolower((string)($_POST['email'] ?? '')));
    $name = trim((string)($_POST['name'] ?? ''));
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) { flash('bad', '邮箱格式不正确'); redirect('/admin/shop-users/' . $id); }
    $st = shop_pdo()->prepare('SELECT id FROM users WHERE email=? AND id<>?'); $st->execute([$email, $id]);
    if ($st->fetch()) { flash('bad', '邮箱已被其他用户使用'); redirect('/admin/shop-users/' . $id); }
    if (function_exists('ops_migrate_extra')) { ops_migrate_extra(); }
    $tags = trim((string)($_POST['tags'] ?? ''));
    $note = trim((string)($_POST['note'] ?? ''));
    shop_pdo()->prepare('UPDATE users SET email=?, name=?, tags=?, note=? WHERE id=?')->execute([$email, $name, $tags, $note, $id]);
    audit('修改套餐用户信息', 'user:' . $id, '', $email);
    flash('ok', '用户信息已保存');
    redirect('/admin/shop-users/' . $id);
}

function page_shop_user_password(int $id): void
{
    require_admin(); csrf_check();
    $pass = (string)($_POST['password'] ?? '');
    if (strlen($pass) < 6) { flash('bad', '密码至少 6 位'); redirect('/admin/shop-users/' . $id); }
    shop_pdo()->prepare('UPDATE users SET pass=? WHERE id=?')
        ->execute([password_hash($pass, PASSWORD_DEFAULT), $id]);
    audit('重置套餐用户密码', 'user:' . $id);
    flash('ok', '密码已重置');
    redirect('/admin/shop-users/' . $id);
}

function page_shop_user_grant(int $id): void {
    require_admin();csrf_check();$q=shop_pdo()->prepare('SELECT id FROM users WHERE id=?');$q->execute([$id]);if(!$q->fetch()){flash('bad','用户不存在');redirect('/admin/shop-users');return;}
    $platform=(string)($_POST['platform'] ?? '');$days=filter_var($_POST['days'] ?? 30,FILTER_VALIDATE_INT);
    if(!$days || $days<1 || $days>36500){flash('bad','有效天数必须为 1 至 36500');redirect('/admin/shop-users/'.$id);return;}
    $map=lc_platforms();$plats=$platform==='all'?$map:($map[$platform] ?? null);
    if(!$plats){flash('bad','请选择平台');redirect('/admin/shop-users/'.$id);return;}
    if($platform!=='all')$plats=[$platform=>$plats];
    $expires=date('Y-m-d',strtotime('+'.$days.' days'));
    $token=lc_sign_token('',['platforms'=>$plats,'expires_at'=>$expires]);
    shop_pdo()->prepare("INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,0,'',?,?,?,?)")->execute([$id,$token,$expires,json_encode($plats,JSON_UNESCAPED_UNICODE),time()]);
    audit('追加平台权限','user:'.$id,$platform.' / '.$expires);flash('ok','已追加开通，原有有效权限继续保留；到期 '.$expires);redirect('/admin/shop-users/'.$id);
}

function page_shop_user_balance(int $id): void
{
    require_admin(); csrf_check();
    $amount = (int)round(((float)($_POST['amount'] ?? 0)) * 100);
    $reason = trim((string)($_POST['reason'] ?? '后台调整'));
    if ($amount !== 0) user_balance_add($id, $amount, $reason);
    if (isset($_POST['auto_renew'])) user_auto_renew($id, (string)$_POST['auto_renew'] === '1');
    audit('调整用户余额', 'user:' . $id, (string)$amount);
    flash('ok', '余额/自动续费已更新');
    redirect('/admin/shop-users/' . $id);
}

function page_shop_user_revoke(int $id): void
{
    require_admin(); csrf_check();
    shop_pdo()->prepare('UPDATE licenses SET revoked=1 WHERE user_id=? AND revoked=0')->execute([$id]);
    audit('取消套餐用户全部授权', 'user:' . $id);
    flash('ok', '该用户的授权已全部取消');
    redirect('/admin/shop-users/' . $id);
}

function page_shop_user_delete(int $id): void
{
    require_admin(); csrf_check();
    foreach (['licenses', 'orders', 'heartbeats'] as $t) {
        shop_pdo()->prepare("DELETE FROM $t WHERE user_id=?")->execute([$id]);
    }
    shop_pdo()->prepare('DELETE FROM users WHERE id=?')->execute([$id]);
    audit('删除套餐用户', 'user:' . $id);
    flash('ok', '用户及其授权/订单已删除');
    redirect('/admin/shop-users');
}
