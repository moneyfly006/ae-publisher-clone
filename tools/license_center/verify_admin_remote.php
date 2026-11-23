<?php
// Run with VPS root's explicit maintenance access. Render only; never submit forms.
// Do not create a browser session or expose the administrator password/session.
declare(strict_types=1);
$app='/www/server/ae-license';
require $app.'/app/bootstrap.php';require $app.'/app/pages.php';require $app.'/app/shop_admin.php';require $app.'/app/shop_ops.php';require $app.'/app/customer_center.php';require $app.'/app/diagnostic_center.php';require $app.'/app/system_settings.php';
$owner=db()->query("SELECT id FROM admins WHERE role='owner' AND is_active=1 ORDER BY id LIMIT 1")->fetchColumn();if(!$owner)throw new RuntimeException('No active owner available for server-side render check');
$_SESSION=['admin_id'=>(int)$owner,'csrf'=>bin2hex(random_bytes(32))];
$cases=[
 ['/admin','page_diagnostics',[]],['/admin/customers','page_customers',[]],['/admin/events','page_diagnostic_events',[]],['/admin/platforms','page_platform_registry',[]],
 ['/admin/licenses','page_licenses',[]],['/admin/licenses/new','page_license_new',[]],['/admin/audit','page_audit',[]],['/admin/settings','page_settings',[]],['/admin/logs','page_logs',[]],
 ['/admin/userdata','page_userdata',[]],['/admin/admins','page_admins',[]],['/admin/rules','page_rule_diag',[]],['/admin/shop-packages','page_shop_packages',[]],
 ['/admin/shop-pay','page_shop_pay',[]],['/admin/shop-orders','page_shop_orders',[]],['/admin/shop-tickets','page_shop_tickets',[]],
 ['/admin/security','page_admin_security',[]],['/admin/shop-reports','page_shop_reports',[]],['/admin/shop-invites','page_shop_invites',[]],['/admin/shop-users','page_shop_users',[]],
 ['/admin/shop-licenses','page_shop_licenses',[]],['/admin/customer-logs','page_customer_logs',[]],['/admin/customer-devices','page_customer_devices',[]],
 ['/admin/shop-coupons','page_shop_coupons',[]],['/admin/legacy-dashboard','page_dashboard',[]]
];
foreach(array_keys(lc_settings_sections()) as $section)$cases[]=['/admin/settings?section='.$section,'page_settings',[]];
$license=db()->query('SELECT id,identity FROM licenses ORDER BY id LIMIT 1')->fetch();if($license){$cases[]=['/admin/licenses/'.$license['id'],'page_license_show',[(int)$license['id']]];$cases[]=['/admin/licenses/'.$license['id'].'/edit','page_license_edit',[(int)$license['id']]];$cases[]=['/admin/userdata/'.$license['identity'],'page_user_detail',[$license['identity']]];}
$uid=(int)(lc_db()->query('SELECT id FROM users ORDER BY id LIMIT 1')->fetchColumn() ?: 0);if($uid)$cases[]=['/admin/shop-users/'.$uid,'page_shop_user_detail',[$uid]];
$logId=(int)(db()->query('SELECT id FROM app_logs ORDER BY id DESC LIMIT 1')->fetchColumn() ?: 0);if($logId)$cases[]=['/admin/logs/'.$logId,'page_log_view',[$logId]];
$issue=lc_diag_db()->query("SELECT fingerprint FROM lc_diagnostic_events WHERE fingerprint<>'' ORDER BY id DESC LIMIT 1")->fetchColumn();if($issue)$cases[]=['/admin/issues/'.$issue,'page_diagnostic_issue',[$issue]];
$cases[]=['/admin/customer-logs?user_q=no-matching-acceptance-user','page_customer_logs',[]];
if($uid){$cases[]=['/admin/customer-logs?user_id='.$uid,'page_customer_logs',[]];$sample=lc_logs($uid);if($sample['rows']){$lid=(int)$sample['rows'][0]['id'];$cases[]=['/admin/customer-logs/'.$uid.'/'.$lid,'page_customer_log',[$uid,$lid]];}}
$rows=[];$errors=[];
set_error_handler(function($severity,$message,$file,$line) use (&$errors){if(!(error_reporting()&$severity))return false;$errors[]=['message'=>$message,'file'=>basename($file),'line'=>$line];return true;});
foreach($cases as $case){$_GET=[];parse_str((string)parse_url($case[0],PHP_URL_QUERY),$_GET);$_SERVER['REQUEST_URI']=$case[0];$start=microtime(true);$prior=count($errors);ob_start();try{call_user_func_array($case[1],$case[2]);$html=ob_get_clean();$rows[]=['path'=>$case[0],'bytes'=>strlen($html),'milliseconds'=>round((microtime(true)-$start)*1000,2),'h1'=>strpos($html,'<h1>')!==false,'single_stylesheet'=>substr_count($html,'rel="stylesheet"')===1&&strpos($html,'/assets/admin.css')!==false,'errors'=>array_slice($errors,$prior)];}catch(Throwable $e){ob_end_clean();$rows[]=['path'=>$case[0],'error'=>get_class($e).': '.$e->getMessage()];}}
restore_error_handler();$failed=array_filter($rows,function($r){return isset($r['error'])||!$r['h1']||!$r['single_stylesheet']||$r['errors'];});
echo json_encode(['pages'=>count($rows),'failures'=>array_values($failed),'rows'=>$rows],JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT),"\n";
if($failed)exit(1);
