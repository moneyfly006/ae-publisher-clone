<?php
declare(strict_types=1);
require_once __DIR__.'/customer_logs.php';

function lc_diag_db(): PDO {
    static $db;
    if(!$db){$path=getenv('LEGACY_LICENSE_DB') ?: '/www/server/ae-license/storage/license.sqlite';
        $db=new PDO('sqlite:'.$path,null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);$db->exec('PRAGMA busy_timeout=10000');lc_diag_schema($db);}
    return $db;
}
function lc_diag_schema(PDO $db): void {
    $db->exec("CREATE TABLE IF NOT EXISTS lc_diagnostic_events(
      id INTEGER PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,identity TEXT NOT NULL,
      platform TEXT NOT NULL DEFAULT 'unknown',version TEXT NOT NULL DEFAULT '',
      run_id TEXT NOT NULL DEFAULT '',stage TEXT NOT NULL DEFAULT '',outcome TEXT NOT NULL DEFAULT '',
      error_type TEXT NOT NULL DEFAULT '',fingerprint TEXT NOT NULL DEFAULT '',message TEXT NOT NULL DEFAULT '',
      occurred_at TEXT NOT NULL,received_at TEXT NOT NULL,log_id INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL)");
    $db->exec('CREATE INDEX IF NOT EXISTS idx_diag_time ON lc_diagnostic_events(occurred_at,platform,version)');
    $db->exec('CREATE INDEX IF NOT EXISTS idx_diag_device ON lc_diagnostic_events(identity,occurred_at)');
    $db->exec('CREATE INDEX IF NOT EXISTS idx_diag_issue ON lc_diagnostic_events(fingerprint,occurred_at)');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_log_receipts(fingerprint TEXT PRIMARY KEY,log_id INTEGER NOT NULL,received_at TEXT NOT NULL)');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_diagnostic_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL)');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_issue_notes(fingerprint TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT \'open\',note TEXT NOT NULL DEFAULT \'\',updated_at TEXT NOT NULL,actor TEXT NOT NULL)');
    if($db->query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_logs'")->fetchColumn())$db->exec('CREATE TRIGGER IF NOT EXISTS lc_delete_log_projection AFTER DELETE ON app_logs BEGIN DELETE FROM lc_diagnostic_events WHERE log_id=OLD.id; DELETE FROM lc_log_receipts WHERE log_id=OLD.id; END');
}
function lc_redact(string $text,int $limit=1200): string {
    $text=preg_replace('/\bBearer\s+[A-Za-z0-9._~+\/-]+/i','Bearer [已脱敏]',$text);
    $text=preg_replace('/(["\']?(?:cookie|authorization|password|passwd|secret|api[_-]?key|access[_-]?token|sessionid)["\']?\s*[=:]\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;&\r\n]+)/i','$1[已脱敏]',$text);
    $text=preg_replace('/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/i','[邮箱已脱敏]',$text);
    return mb_substr((string)$text,0,$limit);
}
function lc_error_type(string $message): string {
    $s=mb_strtolower($message);$map=[
      'auth'=>['cookie','登录失效','未登录','unauthorized','session expired','授权失效'],
      'network'=>['timeout','timed out','超时','connection','连接失败','网络'],
      'category'=>['category','类目','cat_pub_restrict'],
      'attribute'=>['attribute','必填属性','属性不能为空','variation_theme'],
      'image'=>['image','图片','营销图','watermark'],
      'quota'=>['quota','余额','额度','budget','预算'],
      'identifier'=>['gtin','upc','asin','品牌','brand'],
      'price'=>['price','价格','货值','运费'],
      'validation'=>['不能为空','invalid','校验失败','参数错误'],
      'runtime'=>['traceback','typeerror','attributeerror','exception','fatal error']];
    foreach($map as $key=>$words)foreach($words as $word)if(mb_strpos($s,$word)!==false)return $key;
    return 'other';
}
function lc_error_catalog(): array {return [
 'auth'=>['登录与授权','核对 Cookie 有效期、设备绑定与授权状态；不要把凭据写入工单。'],
 'network'=>['连接与超时','对照发生时间查看平台响应、请求超时及代理；先确认写入结果再重试。'],
 'category'=>['类目与发布权限','检查店铺类目准入及商品目标类目，权限限制通常需要商家处理。'],
 'attribute'=>['属性与变体','定位缺失属性或变体字段，核对当前类目模板和平台枚举。'],
 'image'=>['图片与素材','核对图片地址、尺寸、图床响应及平台合规要求。'],
 'quota'=>['额度与预算','核对服务商余额、任务预算和实际已发生调用，不自动重复付费请求。'],
 'identifier'=>['标识与品牌','核对真实 GTIN、ASIN、品牌及目录信息，不能编造商品标识。'],
 'price'=>['价格与物流','核对售价、币种、货值、物流和包装数据。'],
 'validation'=>['参数与必填资料','查看日志中的具体字段，与平台原编辑页面逐项对照。'],
 'runtime'=>['软件运行异常','对照软件版本、操作步骤和堆栈，复现后修复客户端。'],
 'other'=>['待分析','先查看原始日志、设备与版本，确认是平台限制、资料缺失或软件缺陷。']];}
function lc_diag_event(PDO $db,array $ev): bool {
    $message=lc_redact((string)($ev['message'] ?? ''));$outcome=(string)($ev['outcome'] ?? 'observed');
    $type=(string)($ev['error_type'] ?? '');if($type===''&&in_array($outcome,['error','publish_fail','needs_info','uncertain'],true))$type=lc_error_type($message);
    $normalized=preg_replace(['/\b20\d\d-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?\b/','/https?:\/\/\S+/i','/\b[0-9a-f]{16,}\b/i','/\b\d{4,}\b/'],['','[URL]','[ID]','[N]'],$message);
    $fingerprint=$type!==''?hash('sha256',($ev['platform'] ?? 'unknown').'|'.$type.'|'.$normalized):'';
    $q=$db->prepare('INSERT OR IGNORE INTO lc_diagnostic_events(event_key,identity,platform,version,run_id,stage,outcome,error_type,fingerprint,message,occurred_at,received_at,log_id,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)');
    $q->execute([$ev['event_key'],$ev['identity'],$ev['platform'] ?? 'unknown',mb_substr((string)($ev['version'] ?? ''),0,40),mb_substr((string)($ev['run_id'] ?? ''),0,80),mb_substr((string)($ev['stage'] ?? ''),0,60),$outcome,$type,$fingerprint,$message,$ev['occurred_at'],date('Y-m-d H:i:s'),(int)($ev['log_id'] ?? 0),$ev['source'] ?? 'log-inferred']);
    return $q->rowCount()===1;
}
function lc_index_log(PDO $db,array $log): int {
    $count=0;$rules=function_exists('log_rules')?log_rules():['publish_ok'=>['刊登成功','发布成功'],'publish_fail'=>['发布失败','刊登失败'],'error'=>['[ERROR]','Traceback','Exception'],'draft'=>['保存草稿']];
    $platforms=lc_registry();$aliases=['alibaba'=>['alibaba','国际站'],'aliexpress'=>['aliexpress','速卖通'],'amazon'=>['amazon','亚马逊'],'tiktok'=>['tiktok'],'shopee'=>['shopee']];
    foreach($platforms as $id=>$p)$aliases[$id]=array_merge($aliases[$id] ?? [],[$id,$p['name']]);
    foreach(array_slice(preg_split('/\R/u',$log['content']) ?: [],-8000) as $lineNo=>$line){
        $line=trim($line);if($line==='')continue;$event='';
        foreach($rules as $kind=>$words){foreach($words as $word)if($word!==''&&strpos($line,$word)!==false){$event=$kind;break 2;}}
        if($event==='' && preg_match('/traceback|exception|失败|异常|\[error\]/iu',$line))$event='error';
        if($event==='')continue;$platform='unknown';$lower=mb_strtolower($line);
        foreach($aliases as $id=>$names)foreach($names as $name)if(mb_strpos($lower,mb_strtolower($name))!==false){$platform=$id;break 2;}
        $time=$log['created_at'];$timed=preg_match('/\b(20\d\d-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:[.,]\d+)?/',$line,$m);
        if($timed && strtotime($m[1].' '.$m[2])!==false)$time=$m[1].' '.$m[2];
        $key=hash('sha256',$log['identity'].'|'.($timed?'timestamped|'.$line:'log:'.$log['id'].'|'.$lineNo.'|'.$line));
        $count+=(int)lc_diag_event($db,['event_key'=>$key,'identity'=>$log['identity'],'platform'=>$platform,'version'=>$log['client_version'],'message'=>$line,'outcome'=>$event,'occurred_at'=>$time,'log_id'=>$log['id'],'source'=>'log-inferred']);
    }
    return $count;
}
function lc_backfill(int $limit=100): array {
    $db=lc_diag_db();$last=(int)($db->query("SELECT v FROM lc_diagnostic_meta WHERE k='last_log_id'")->fetchColumn() ?: 0);
    $q=$db->prepare('SELECT id,identity,client_version,content,created_at FROM app_logs WHERE id>? ORDER BY id LIMIT ?');$q->bindValue(1,$last,PDO::PARAM_INT);$q->bindValue(2,max(1,min(500,$limit)),PDO::PARAM_INT);$q->execute();$rows=$q->fetchAll();$count=0;
    $db->beginTransaction();try{foreach($rows as $row){$count+=lc_index_log($db,$row);$last=$row['id'];}$db->prepare("INSERT INTO lc_diagnostic_meta(k,v) VALUES('last_log_id',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v")->execute([(string)$last]);$db->commit();}catch(Throwable $e){$db->rollBack();throw $e;}
    return ['logs'=>count($rows),'events'=>$count,'last_log_id'=>$last];
}
function lc_diag_filters(): array {
    $days=max(1,min(90,(int)($_GET['days'] ?? 7)));$uid=max(0,(int)($_GET['user_id'] ?? 0));
    $from=date('Y-m-d',time()-($days-1)*86400).' 00:00:00';$where=['occurred_at>=?'];$args=[$from];
    foreach(['platform','version','identity','outcome'] as $key){$value=trim((string)($_GET[$key] ?? ''));if($value!==''){$where[]=$key.'=?';$args[]=mb_substr($value,0,128);}}
    if($uid){$ids=lc_user_identities($uid);if($ids){$where[]='identity IN ('.implode(',',array_fill(0,count($ids),'?')).')';$args=array_merge($args,$ids);}else $where[]='0=1';}
    $keyword=trim((string)($_GET['q'] ?? ''));if($keyword!==''){$where[]='message LIKE ?';$args[]='%'.mb_substr($keyword,0,100).'%';}
    return ['where'=>implode(' AND ',$where),'args'=>$args,'days'=>$days,'from'=>$from,'uid'=>$uid];
}
function lc_customer_runtime(int $uid): array {
    $ids=lc_user_identities($uid);if(!$ids)return ['devices'=>[],'events'=>0,'errors'=>0,'latest'=>null];
    $placeholders=implode(',',array_fill(0,count($ids),'?'));$db=lc_diag_db();
    $q=$db->prepare("SELECT identity,machine_code,platform,os_version,app_version,store_name,instance,last_seen FROM devices WHERE identity IN ($placeholders) ORDER BY last_seen DESC LIMIT 20");$q->execute($ids);$devices=$q->fetchAll();
    $q=$db->prepare("SELECT COUNT(*) events,SUM(CASE WHEN error_type<>'' THEN 1 ELSE 0 END) errors,MAX(occurred_at) latest FROM lc_diagnostic_events WHERE identity IN ($placeholders) AND occurred_at>=?");$q->execute(array_merge($ids,[date('Y-m-d',time()-6*86400)]));
    return $q->fetch()+['devices'=>$devices];
}
