<?php
// Existing server maintenance access only. No credentials, user data, or log text emitted.
declare(strict_types=1);
$app='/www/server/ae-license';
foreach(['bootstrap','pages','shop_admin','shop_ops','customer_center','diagnostic_center','system_settings'] as $file)require $app.'/app/'.$file.'.php';
$owner=db()->query("SELECT id FROM admins WHERE role='owner' AND is_active=1 ORDER BY id LIMIT 1")->fetchColumn();
$_SESSION=['admin_id'=>(int)$owner,'csrf'=>bin2hex(random_bytes(32))];
$users=shop_pdo()->query('SELECT id,email,name FROM users ORDER BY id LIMIT 200')->fetchAll();
$searched=0;$details=0;$samples=0;$types=[];
foreach($users as $user){
    foreach([(string)$user['id'],(string)$user['email']] as $keyword){$r=lc_find_log_users($keyword,1);if(!in_array($user['id'],array_column($r['rows'],'id')))throw new RuntimeException('Known user not found by exact identifier');$searched++;}
    $r=lc_logs((int)$user['id']);if(!$r['rows'])continue;
    $_GET=['user_q'=>(string)$user['email'],'q'=>'','page'=>'1'];$_SERVER['REQUEST_URI']='/admin/customer-logs/'.$user['id'].'/'.$r['rows'][0]['id'];
    ob_start();page_customer_log((int)$user['id'],(int)$r['rows'][0]['id']);$html=ob_get_clean();
    if(strpos($html,'id="assessment-title"')===false||strpos($html,'id="raw-log"')===false)throw new RuntimeException('Detail missing diagnosis or raw log');$details++;
}
foreach(db()->query('SELECT content,error_count FROM app_logs ORDER BY id DESC LIMIT 20')->fetchAll() as $row){$a=lc_assess_log($row);$samples++;foreach($a['issues'] as $issue)$types[$issue['type']]=($types[$issue['type']] ?? 0)+1;}
echo json_encode(['users_checked'=>count($users),'exact_user_searches_passed'=>$searched,'associated_user_details_rendered'=>$details,'recent_real_logs_analyzed'=>$samples,'diagnosis_category_counts'=>$types,'no_customer_or_log_text_exposed'=>true],JSON_PRETTY_PRINT),"\n";
