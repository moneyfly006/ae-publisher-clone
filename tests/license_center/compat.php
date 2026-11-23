<?php
// Complete isolated fixtures. No production code configuration or data is read.
declare(strict_types=1);
$root=dirname(__DIR__,2).'/deploy/license-center-v2';
$folder=getenv('LICENSE_FIXTURE_DIR') ?: sys_get_temp_dir().'/aurora-license-fixture-'.getmypid();
@mkdir($folder,0700,true);
putenv('LICENSE_CENTER_SHARED='.$root.'/shared');putenv('SHOP_LICENSE_DB='.$folder.'/shop.db');putenv('LEGACY_LICENSE_DB='.$folder.'/legacy.sqlite');
// The test harness copies sources so its config files never go into deployment.
$copy=$folder.'/site';
function tree_copy(string $from,string $to): void { @mkdir($to,0700,true);foreach(scandir($from) as $name){if($name==='.'||$name==='..')continue;$s=$from.'/'.$name;$d=$to.'/'.$name;if(is_dir($s))tree_copy($s,$d);else copy($s,$d);} }
tree_copy($root,$copy);
file_put_contents($copy.'/public/shop/config.php','<?php return '.var_export(['db'=>$folder.'/shop.db','sign_key'=>'aurora-list-platform-license-v1','site_url'=>'http://127.0.0.1:18764'],true).';');
file_put_contents($copy.'/app/config.php','<?php return '.var_export(['db_path'=>$folder.'/legacy.sqlite','key_dir'=>$folder.'/keys','timezone'=>'Asia/Shanghai','brand'=>'AuroraList','version'=>'2','admin_path'=>'/admin','base_path'=>'','session_name'=>'aurora_test_admin','default_days'=>30],true).';');
require $copy.'/public/shop/lib.php';db();
require $copy.'/app/shop_ops.php';require $root.'/shared/customer_logs.php';
$db=lc_db();
$db->exec("INSERT INTO users(id,email,pass,name,created_at,verified) VALUES(1,'one@example.invalid','unused','测试客户',0,1),(2,'two@example.invalid','unused','另一客户',0,1)");
$db->prepare('UPDATE users SET pass=?')->execute([password_hash('fixture-user-pass',PASSWORD_DEFAULT)]);
$db->exec('ALTER TABLE packages ADD COLUMN device_limit INTEGER DEFAULT 2');
$db->exec('DELETE FROM packages');$db->exec("INSERT INTO packages(id,name,platforms,features,price_cents,days,created_at) VALUES(1,'Alibaba','[\"alibaba\"]','[\"listing\",\"ai_text\"]',9900,365,0),(2,'Amazon','[\"amazon\"]','[\"listing\"]',9900,30,0)");
$count=0;function check(bool $pass,string $label): void {global $count;if(!$pass)throw new RuntimeException($label);$count++;}
function grant_fixture(int $uid,array $plats,string $date,int $order=0,string $machine=''): int { $db=lc_db();$db->prepare('INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,?,?,?,?,?,?)')->execute([$uid,$order,$machine,'old-token',$date,json_encode($plats),time()]);return (int)$db->lastInsertId(); }
$old=grant_fixture(1,['alibaba'=>['listing']],date('Y-m-d',strtotime('+365 days')));
$short=grant_fixture(1,['amazon'=>['listing']],date('Y-m-d',strtotime('+30 days')));
$historyBefore=$db->query('SELECT * FROM licenses ORDER BY id')->fetchAll();
$e=lc_effective(1);check(isset($e['platforms']['alibaba'],$e['platforms']['amazon']),'separate platform grants retained');check($e['feature_expiries']['alibaba']['listing']>$e['feature_expiries']['amazon']['listing'],'independent expiry');
$future=lc_effective(1,date('Y-m-d',strtotime('+31 days')));check(isset($future['platforms']['alibaba'])&&!isset($future['platforms']['amazon']),'short platform expires alone');
$end=lc_effective(1,date('Y-m-d',strtotime('+30 days')));check(isset($end['platforms']['amazon']),'expiry date inclusive');
check($historyBefore===$db->query('SELECT * FROM licenses ORDER BY id')->fetchAll(),'aggregation does not rewrite original rows');
$device=lc_bind(1,'AAAAAAAA-BBBBBBBB',2);check(isset($device['token']),'bind issues token');
$token=json_decode($device['token'],true);file_put_contents($folder.'/server-token.json',$device['token']);check($token['payload']['expires_at']===$e['next_change_at'],'old token ends at first rights transition');
lc_bind(1,'CCCCCCCC-DDDDDDDD',2);$denied=false;try{lc_bind(1,'EEEEEEEE-FFFFFFFF',2);}catch(RuntimeException $x){$denied=true;}check($denied,'device cap enforced');
$denied=false;try{lc_bind(2,'AAAAAAAA-BBBBBBBB',2);}catch(RuntimeException $x){$denied=true;}check($denied,'other account cannot bind assigned machine');
check(lc_for_machine('aaaaaaaabbbbbbbb')['user_id']===1,'old machine alias lookup');
lc_unbind(1,'CCCCCCCC-DDDDDDDD');check(lc_for_machine('CCCCCCCC-DDDDDDDD')===null,'unbound machine no longer pulls token');check(isset(lc_effective(1)['platforms']['alibaba']),'unbind keeps rights');
$db->prepare('INSERT INTO orders(id,user_id,package_id,amount_cents,status,trade_no,created_at) VALUES(1,1,1,9900,\'pending\',\'TEST1\',0)')->execute();
lc_snapshot(1,$db->query('SELECT * FROM packages WHERE id=1')->fetch());$a=lc_mark_paid(1);$rows=(int)$db->query('SELECT COUNT(*) FROM licenses WHERE order_id=1')->fetchColumn();$b=lc_mark_paid(1);check($rows===(int)$db->query('SELECT COUNT(*) FROM licenses WHERE order_id=1')->fetchColumn(),'payment retry idempotent');check($a['feature_expiries']===$b['feature_expiries'],'retry does not extend twice');
$db->exec("INSERT INTO orders(id,user_id,package_id,amount_cents,status,trade_no,created_at) VALUES(2,2,2,9900,'pending','SNAPSHOT',0)");lc_snapshot(2,$db->query('SELECT * FROM packages WHERE id=2')->fetch());$db->exec("UPDATE packages SET features='[\"ai_image\"]' WHERE id=2");$x=lc_mark_paid(2);check($x['platforms']['amazon']===['listing'],'purchase snapshot retained after package edit');
$db->exec("INSERT INTO orders(id,user_id,package_id,amount_cents,status,trade_no,created_at) VALUES(3,2,999,9900,'pending','FAILURE',0)");try{lc_mark_paid(3);}catch(Throwable $x){}check($db->query('SELECT status FROM orders WHERE id=3')->fetchColumn()==='pending','fulfillment failure rolls back paid state');
ops_migrate_billing();$db->exec('DELETE FROM licenses WHERE user_id=2');$db->exec('UPDATE users SET auto_renew=1,balance_cents=100000 WHERE id=2');grant_fixture(2,['amazon'=>['listing']],date('Y-m-d',strtotime('+1 day')),2);
$a=lc_auto_renew();$balance=(int)$db->query('SELECT balance_cents FROM users WHERE id=2')->fetchColumn();$b=lc_auto_renew();check(count($a)===1&&count($b)===0,'automatic renewal only once');check($balance===(int)$db->query('SELECT balance_cents FROM users WHERE id=2')->fetchColumn(),'second cron no extra charge');
// Prepare original database using its original schema and preserve its license rows.
require $copy.'/app/db.php';$GLOBALS['cfg']=['db_path'=>$folder.'/legacy.sqlite'];$legacy=DB::pdo();
$legacy->exec("INSERT INTO licenses(identity,expires_at,issued_at,created_at,updated_at,status) VALUES('OLD-ACTIVE','2099-01-01','2020-01-01','2020-01-01','2020-01-01','active'),('OLD-REVOKED','2099-01-01','2020-01-01','2020-01-01','2020-01-01','revoked')");
$before=$legacy->query('SELECT * FROM licenses ORDER BY id')->fetchAll();
$legacy->exec("INSERT INTO devices(identity,machine_code) VALUES('OLD-FULL-HASH','AAAAAAAA-BBBBBBBB')");
$legacy->exec("INSERT INTO app_logs(identity,filename,content,created_at,error_count) VALUES('OLD-FULL-HASH','one.log','safe fixture log','2026-01-01',1),('UNRELATED','other.log','must not leak','2026-01-01',1)");
$logs=lc_logs(1);check($logs['total']===1,'historical log associated by known device alias');check(lc_logs(2)['total']===0,'another user sees no other logs');check(lc_log_detail(2,(int)$logs['rows'][0]['id'])===null,'direct log ID access isolated');check(lc_logs(1,999)['page']===1,'page boundary clamps');check(lc_logs(1,1,'no-match')['total']===0,'log search filters');check($before===$legacy->query('SELECT * FROM licenses ORDER BY id')->fetchAll(),'original licenses remain byte-equivalent');
@mkdir($folder.'/keys',0700,true);$key=openssl_pkey_new(['private_key_type'=>OPENSSL_KEYTYPE_EC,'curve_name'=>'prime256v1']);openssl_pkey_export($key,$private);file_put_contents($folder.'/keys/private.pem',$private);file_put_contents($folder.'/keys/public.pem',openssl_pkey_get_details($key)['key']);
$legacy->prepare('INSERT INTO admins(username,pass_hash,role,is_active,created_at) VALUES(?,?,\'owner\',1,?)')->execute(['fixture-admin',password_hash('fixture-pass-only',PASSWORD_DEFAULT),date('Y-m-d H:i:s')]);
echo json_encode(['passed'=>$count,'fixture'=>$folder,'site'=>$copy,'legacy_rows_unchanged'=>true],JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE),"\n";
