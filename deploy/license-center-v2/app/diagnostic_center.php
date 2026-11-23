<?php
declare(strict_types=1);
require_once (getenv('LICENSE_CENTER_SHARED') ?: '/www/server/ae-license/shared').'/diagnostics.php';

function lc_diag_rows(string $sql,array $args=[]): array {$q=lc_diag_db()->prepare($sql);$q->execute($args);return $q->fetchAll();}
function lc_diag_context(array $filter): array {
    $platforms=lc_registry();foreach(lc_diag_rows("SELECT DISTINCT platform FROM lc_diagnostic_events WHERE platform<>'unknown' ORDER BY platform") as $row){$id=$row['platform'];if(!isset($platforms[$id]))$platforms[$id]=['id'=>$id,'name'=>$id.'（未注册）','features'=>[],'status'=>'unregistered'];}
    return ['filter'=>$filter,'platforms'=>$platforms,
      'versions'=>lc_diag_rows("SELECT DISTINCT version FROM lc_diagnostic_events WHERE version<>'' ORDER BY version DESC LIMIT 100"),
      'users'=>lc_db()->query('SELECT id,email FROM users ORDER BY id DESC LIMIT 1000')->fetchAll()];
}
function page_diagnostics(): void {
    require_admin();$f=lc_diag_filters();$w=$f['where'];$a=$f['args'];
    $summary=lc_diag_rows("SELECT COUNT(*) events,COUNT(DISTINCT identity) devices,COUNT(DISTINCT NULLIF(version,'')) versions,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors,MAX(occurred_at) latest FROM lc_diagnostic_events WHERE $w",$a)[0];
    $outcomes=lc_diag_rows("SELECT outcome,COUNT(*) n FROM lc_diagnostic_events WHERE $w GROUP BY outcome ORDER BY n DESC",$a);
    $issues=lc_diag_rows("SELECT fingerprint,error_type,platform,COUNT(*) n,COUNT(DISTINCT identity) devices,COUNT(DISTINCT NULLIF(version,'')) versions,MIN(occurred_at) first_at,MAX(occurred_at) latest,MAX(id) sample_id FROM lc_diagnostic_events WHERE error_type<>'' AND $w GROUP BY fingerprint ORDER BY devices DESC,n DESC LIMIT 8",$a);
    $byPlatform=lc_diag_rows("SELECT platform,COUNT(*) events,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors,COUNT(DISTINCT identity) devices FROM lc_diagnostic_events WHERE $w GROUP BY platform ORDER BY errors DESC",$a);
    $byVersion=lc_diag_rows("SELECT version,COUNT(DISTINCT identity) devices,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors,COUNT(*) events FROM lc_diagnostic_events WHERE $w GROUP BY version ORDER BY errors DESC LIMIT 8",$a);
    $trend=lc_diag_rows("SELECT substr(occurred_at,1,10) day,COUNT(*) events,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors FROM lc_diagnostic_events WHERE $w GROUP BY day ORDER BY day",$a);
    $sources=lc_diag_rows("SELECT source,COUNT(*) n FROM lc_diagnostic_events WHERE $w GROUP BY source",$a);
    $coverage=lc_diag_rows("SELECT (SELECT COUNT(*) FROM app_logs) logs,(SELECT MAX(id) FROM app_logs) max_log_id,(SELECT v FROM lc_diagnostic_meta WHERE k='last_log_id') indexed_to")[0];
    render('diagnostics',lc_diag_context($f)+compact('summary','outcomes','issues','byPlatform','byVersion','trend','sources','coverage')+['title'=>'客户运行与问题分析']);
}
function page_diagnostic_events(): void {
    require_admin();$f=lc_diag_filters();$w=$f['where'];$args=$f['args'];$page=max(1,(int)($_GET['page'] ?? 1));$per=30;
    $total=(int)lc_diag_rows("SELECT COUNT(*) n FROM lc_diagnostic_events WHERE $w",$args)[0]['n'];$pages=max(1,(int)ceil($total/$per));$page=min($page,$pages);$off=($page-1)*$per;
    $rows=lc_diag_rows("SELECT * FROM lc_diagnostic_events WHERE $w ORDER BY occurred_at DESC,id DESC LIMIT $per OFFSET $off",$args);
    render('diagnostic_events',lc_diag_context($f)+compact('rows','page','pages','total')+['title'=>'运行事件明细']);
}
function page_diagnostic_issue(string $fingerprint): void {
    require_admin();$f=lc_diag_filters();$w=$f['where'];$args=array_merge([$fingerprint],$f['args']);
    $rows=lc_diag_rows("SELECT * FROM lc_diagnostic_events WHERE fingerprint=? AND $w ORDER BY occurred_at DESC LIMIT 100",$args);
    $summary=lc_diag_rows("SELECT COUNT(*) events,COUNT(DISTINCT identity) devices,COUNT(DISTINCT NULLIF(version,'')) versions,MIN(occurred_at) first_at,MAX(occurred_at) latest FROM lc_diagnostic_events WHERE fingerprint=? AND $w",$args)[0];
    $note=lc_diag_rows('SELECT * FROM lc_issue_notes WHERE fingerprint=?',[$fingerprint])[0] ?? ['status'=>'open','note'=>''];
    render('diagnostic_issue',lc_diag_context($f)+compact('rows','summary','fingerprint','note')+['title'=>'问题详情与排查']);
}
function page_issue_update(string $fingerprint): void {
    require_admin();csrf_check();$status=(string)($_POST['status'] ?? 'open');if(!in_array($status,['open','investigating','planned','resolved','ignored'],true)){http_response_code(400);return;}
    lc_diag_db()->prepare('INSERT INTO lc_issue_notes(fingerprint,status,note,updated_at,actor) VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at,actor=excluded.actor')->execute([$fingerprint,$status,lc_redact((string)($_POST['note'] ?? ''),2000),date('Y-m-d H:i:s'),current_admin()['username']]);
    audit('更新问题排查状态','issue:'.$fingerprint,$status);flash('ok','排查状态已保存；原始日志和事件保持不变');redirect(lc_safe_admin_return((string)($_POST['return_to'] ?? ''),'/admin/issues/'.$fingerprint));
}
function page_customers(): void {
    require_admin();$q=trim((string)($_GET['q'] ?? ''));$scope=(string)($_GET['scope'] ?? 'all');if(!in_array($scope,['all','accounts','legacy'],true))$scope='all';
    $db=lc_diag_db();$attached=false;foreach($db->query('PRAGMA database_list') as $r)if($r['name']==='lc_accounts')$attached=true;
    if(!$attached)$db->exec('ATTACH DATABASE '.$db->quote(lc_path()).' AS lc_accounts');
    $parts=[];
    if($scope!=='legacy')$parts[]="SELECT 'account' kind,id,email name,name description,created_at sort_key,'' identity,'' status,'' expires_at FROM lc_accounts.users";
    if($scope!=='accounts')$parts[]="SELECT 'legacy' kind,id,identity name,note description,strftime('%s',created_at) sort_key,identity,status,expires_at FROM main.licenses";
    $union=implode(' UNION ALL ',$parts);$where=$q!==''?'WHERE (name LIKE ? OR description LIKE ?)':'';$args=$q!==''?['%'.$q.'%','%'.$q.'%']:[];
    $st=$db->prepare('SELECT COUNT(*) FROM ('.$union.') '.$where);$st->execute($args);$total=(int)$st->fetchColumn();$pages=max(1,(int)ceil($total/25));$page=min(max(1,(int)($_GET['page'] ?? 1)),$pages);$off=($page-1)*25;
    $st=$db->prepare('SELECT * FROM ('.$union.') '.$where." ORDER BY sort_key DESC,id DESC LIMIT 25 OFFSET $off");$st->execute($args);$rows=$st->fetchAll();
    foreach($rows as &$row){$ids=$row['kind']==='account'?lc_user_identities((int)$row['id']):[$row['identity']];
        $row['effective']=$row['kind']==='account'?lc_effective((int)$row['id']):null;
        if($ids){$ph=implode(',',array_fill(0,count($ids),'?'));$stats=lc_diag_rows("SELECT COUNT(*) events,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors,MAX(occurred_at) latest FROM lc_diagnostic_events WHERE identity IN ($ph) AND occurred_at>=?",array_merge($ids,[date('Y-m-d',time()-6*86400)]));$row['diagnostics']=$stats[0];}else $row['diagnostics']=['events'=>0,'errors'=>0,'latest'=>null];
    }
    render('customers',compact('rows','q','scope','total','page','pages')+['title'=>'客户目录']);
}
function page_platform_registry(): void {require_admin();render('platform_registry',['title'=>'平台与接口扩展','platforms'=>lc_registry()]);}
function lc_outcome_names(): array{return ['publish_ok'=>'识别到发布成功','published'=>'平台确认上线','draft'=>'保存草稿','draft_saved'=>'草稿已保存','publish_fail'=>'发布失败','error'=>'运行异常','needs_info'=>'需补资料','reviewing'=>'审核中','pending'=>'待发布','processing'=>'处理中','start'=>'开始任务','auth'=>'授权校验','checked'=>'检查通过','observed'=>'其他事件','uncertain'=>'结果待确认','stopped'=>'已停止'];}
function lc_source_name(string $source): string{return ['log-inferred'=>'日志识别（推断）','client-structured'=>'旧版结构化上报','authenticated-v2'=>'凭证验证上报'][$source] ?? $source;}
function lc_diag_url(string $path,array $changes=[]): string {$query=array_merge($_GET,$changes);unset($query['page']);return $path.'?'.http_build_query($query);}
function lc_safe_admin_return(string $path,string $fallback='/admin/logs'): string {
    if(strlen($path)>2000||strpos($path,'\\')!==false||preg_match('/[\r\n]/',$path))return $fallback;
    $parts=parse_url($path);if(!$parts||isset($parts['host'])||isset($parts['scheme'])||!preg_match('#^/admin(?:/|$)#',$parts['path'] ?? ''))return $fallback;
    return $path;
}

function api_platform_registry(): void {json_out(['schema_version'=>2,'platforms'=>array_values(lc_registry()),'event_endpoint'=>'/api/v2/client/events','auth'=>'ecdsa_device_voucher']);}
function lc_raw_to_der(string $raw): string {
    if(strlen($raw)!==64)throw new RuntimeException('invalid_signature');$out='';foreach([substr($raw,0,32),substr($raw,32,32)] as $v){$v=ltrim($v,"\x00");if($v==='')$v="\x00";if(ord($v[0])&128)$v="\x00".$v;$out.="\x02".chr(strlen($v)).$v;}return "\x30".chr(strlen($out)).$out;
}
function api_client_events(): void {
    $raw=file_get_contents('php://input',false,null,0,524289);if(strlen($raw)>524288)json_out(['ok'=>false,'error'=>'payload_too_large'],413);$in=json_decode($raw,true);if(!is_array($in))json_out(['ok'=>false,'error'=>'invalid_json'],400);
    $voucher=(string)($in['voucher'] ?? '');$signature=(string)($in['signature'] ?? '');$decode=function($s){return base64_decode(strtr($s,'-_','+/').str_repeat('=',(4-strlen($s)%4)%4),true);};
    try{$payload=$decode($voucher);$signature=$decode($signature);$parts=explode('|',(string)$payload);if(count($parts)!==4||!lc_valid_date($parts[1])||$parts[1]<date('Y-m-d'))throw new RuntimeException('invalid_voucher');if(openssl_verify($payload,lc_raw_to_der((string)$signature),public_key_pem(),OPENSSL_ALGO_SHA256)!==1)throw new RuntimeException('invalid_signature');
        $identity=$parts[0];$q=db()->prepare('SELECT * FROM licenses WHERE identity=?');$q->execute([$identity]);$license=$q->fetch();if((!$license||license_effective_status($license)!=='active')&&!lc_legacy_package_verify($identity))throw new RuntimeException('inactive_device');
    }catch(Throwable $e){json_out(['ok'=>false,'error'=>'invalid_device_credential'],401);}
    $q=lc_diag_db()->prepare('SELECT COUNT(*) FROM lc_diagnostic_events WHERE identity=? AND source=\'authenticated-v2\' AND received_at>=?');$q->execute([$identity,date('Y-m-d H:i:s',time()-60)]);if((int)$q->fetchColumn()>=2000)json_out(['ok'=>false,'error'=>'rate_limited'],429);
    $events=$in['events'] ?? [];if(!is_array($events)||count($events)>200)json_out(['ok'=>false,'error'=>'events_limit_200'],400);
    $db=lc_diag_db();$stored=0;$db->beginTransaction();try{foreach($events as $event){if(!is_array($event))throw new RuntimeException('invalid_event');$id=(string)($event['event_id'] ?? '');$platform=(string)($event['platform'] ?? 'unknown');$outcome=(string)($event['outcome'] ?? 'observed');
        if(!preg_match('/^[a-zA-Z0-9_.:-]{8,120}$/',$id)||!preg_match('/^[a-z][a-z0-9_]{1,31}$/',$platform)||!isset(lc_outcome_names()[$outcome]))throw new RuntimeException('invalid_event_fields');
        $occurred=(string)($event['occurred_at'] ?? date('c'));$ts=strtotime($occurred);if($ts===false||$ts>time()+600||$ts<time()-90*86400)throw new RuntimeException('invalid_event_time');
        $stored+=(int)lc_diag_event($db,['event_key'=>hash('sha256',$identity.'|v2|'.$id),'identity'=>$identity,'platform'=>$platform,'version'=>$event['version'] ?? '', 'run_id'=>$event['run_id'] ?? '', 'stage'=>$event['stage'] ?? '', 'outcome'=>$outcome,'message'=>(string)($event['message'] ?? ''),'occurred_at'=>date('Y-m-d H:i:s',$ts),'source'=>'authenticated-v2']);
    }$db->commit();}catch(Throwable $e){$db->rollBack();json_out(['ok'=>false,'error'=>$e->getMessage()],400);}
    json_out(['ok'=>true,'schema_version'=>2,'accepted'=>count($events),'inserted'=>$stored,'duplicates'=>count($events)-$stored]);
}
