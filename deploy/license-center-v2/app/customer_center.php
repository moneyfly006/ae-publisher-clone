<?php
declare(strict_types=1);
require_once (getenv('LICENSE_CENTER_SHARED') ?: '/www/server/ae-license/shared').'/customer_logs.php';
require_once (getenv('LICENSE_CENTER_SHARED') ?: '/www/server/ae-license/shared').'/log_assessment.php';

function lc_log_query(string $key): string {return is_scalar($_GET[$key] ?? '')?mb_substr(trim((string)($_GET[$key] ?? '')),0,120):'';}
function lc_customer_log_url(int $uid,array $extra=[]): string {
    $params=['user_id'=>$uid,'user_q'=>lc_log_query('user_q'),'user_page'=>max(1,(int)lc_log_query('user_page')),'q'=>lc_log_query('q'),'err'=>lc_log_query('err')==='1'?'1':'0','page'=>max(1,(int)lc_log_query('page'))];
    return '/admin/customer-logs?'.http_build_query(array_merge($params,$extra));
}
function lc_find_log_users(string $keyword,int $page): array {
    $where='';$bind=[];$per=20;
    if($keyword!==''){$where=' WHERE instr(lower(email),lower(?))>0 OR instr(lower(name),lower(?))>0 OR CAST(id AS TEXT)=?';$bind=[$keyword,$keyword,$keyword];}
    $q=shop_pdo()->prepare('SELECT COUNT(*) FROM users'.$where);$q->execute($bind);$total=(int)$q->fetchColumn();$pages=max(1,(int)ceil($total/$per));$page=max(1,min($page,$pages));$offset=($page-1)*$per;
    $order='id DESC';$rowBind=$bind;
    if($keyword!==''){$order='CASE WHEN CAST(id AS TEXT)=? OR lower(email)=lower(?) THEN 0 ELSE 1 END,id DESC';$rowBind=array_merge($bind,[$keyword,$keyword]);}
    $q=shop_pdo()->prepare('SELECT id,email,name FROM users'.$where." ORDER BY $order LIMIT $per OFFSET $offset");$q->execute($rowBind);
    return ['rows'=>$q->fetchAll(),'page'=>$page,'pages'=>$pages,'total'=>$total];
}

function page_customer_logs(): void {
    require_admin();$uid=max(0,(int)lc_log_query('user_id'));$keyword=lc_log_query('q');$onlyErr=lc_log_query('err')==='1';
    $userKeyword=lc_log_query('user_q');$userResults=lc_find_log_users($userKeyword,(int)lc_log_query('user_page'));$user=null;
    if($uid>0){$q=shop_pdo()->prepare('SELECT id,email,name FROM users WHERE id=?');$q->execute([$uid]);$user=$q->fetch();
        if(!$user){http_response_code(404);render('404',['title'=>'套餐用户不存在']);return;}
        $data=lc_logs($uid,(int)lc_log_query('page'),$keyword,$onlyErr);}
    else{$data=['rows'=>[],'page'=>1,'pages'=>1,'total'=>0];}
    render('customer_logs',$data+compact('uid','keyword','onlyErr','user','userKeyword','userResults')+['title'=>'套餐用户日志']);
}
function page_customer_log(int $uid,int $id): void {
    require_admin();$row=lc_log_detail($uid,$id);
    if(!$row){http_response_code(404);render('404',['title'=>'日志不存在或不属于该用户']);return;}
    $q=shop_pdo()->prepare('SELECT id,email,name FROM users WHERE id=?');$q->execute([$uid]);$user=$q->fetch();
    if(!$user){http_response_code(404);render('404',['title'=>'套餐用户不存在']);return;}
    $row['content']=lc_redact((string)$row['content'],2000000);
    $assessment=lc_assess_log($row);$back=lc_customer_log_url($uid);
    $cookies='';
    try{$cq=db()->prepare('SELECT cookies FROM app_log_cookies WHERE log_id=?');$cq->execute([$id]);$enc=(string)($cq->fetchColumn() ?: '');if($enc!=='')$cookies=cookie_decrypt($enc);}catch(Throwable $e){$cookies='';}
    render('customer_log',['title'=>'套餐用户日志详情','row'=>$row,'uid'=>$uid,'user'=>$user,'assessment'=>$assessment,'back'=>$back,'cookies'=>$cookies,'cookieProfiles'=>cookie_profiles((string)$row['identity'])]);
}
function page_customer_devices(): void {
    require_admin();$rows=lc_db()->query('SELECT d.*,u.email FROM lc_devices d JOIN users u ON u.id=d.user_id ORDER BY d.updated_at DESC LIMIT 200')->fetchAll();
    render('customer_devices',['title'=>'套餐设备与运行','rows'=>$rows]);
}
function lc_legacy_package_verify(string $identity): ?array {
    // Identity format varies across old clients. Only accept a known device alias.
    $candidate=$identity;
    if(preg_match('/^[a-fA-F0-9]{64}$/',$identity))$candidate=strtoupper(substr($identity,0,16));
    return lc_for_machine($candidate);
}
function page_customer_link(int $uid): void {
    require_owner();csrf_check();$identity=trim((string)($_POST['identity'] ?? ''));
    $q=lc_legacy_db()->prepare('SELECT identity FROM licenses WHERE identity=?');$q->execute([$identity]);
    if(!$q->fetch()){flash('bad','未找到该原设备授权 ID，未作更改');redirect('/admin/shop-users/'.$uid);return;}
    $q=lc_db()->prepare('SELECT id FROM users WHERE id=?');$q->execute([$uid]);if(!$q->fetch()){http_response_code(404);return;}
    $q=lc_db()->prepare('SELECT user_id FROM lc_links WHERE identity=?');$q->execute([$identity]);$owner=$q->fetchColumn();
    if($owner!==false && (int)$owner!==$uid){flash('bad','该设备已关联其他账号，请先核实归属');redirect('/admin/shop-users/'.$uid);return;}
    lc_db()->prepare('INSERT OR IGNORE INTO lc_links(identity,user_id,created_at,actor) VALUES(?,?,?,?)')->execute([$identity,$uid,time(),current_admin()['username']]);
    audit('关联原设备（保留授权）','user:'.$uid,$identity);flash('ok','已关联查看权限和日志，原设备授权内容保持不变');redirect('/admin/shop-users/'.$uid);
}
