<?php
declare(strict_types=1);
$root=dirname(__DIR__,2).'/deploy/license-center-v2';
$folder=sys_get_temp_dir().'/aurora-diag-tests-'.bin2hex(random_bytes(5));mkdir($folder,0700,true);
putenv('SHOP_LICENSE_DB='.$folder.'/shop.db');putenv('LEGACY_LICENSE_DB='.$folder.'/legacy.sqlite');
$registry=json_decode(file_get_contents($root.'/shared/platforms.json'),true);$registry['platforms'][]=['id'=>'future_market','name'=>'后续平台','features'=>['listing','ai_text'],'status'=>'planned'];file_put_contents($folder.'/platforms.json',json_encode($registry));putenv('LICENSE_PLATFORM_REGISTRY='.$folder.'/platforms.json');
require $root.'/shared/diagnostics.php';
$db=lc_diag_db();$db->exec('CREATE TABLE app_logs(id INTEGER PRIMARY KEY,identity TEXT,client_version TEXT,content TEXT,created_at TEXT)');
lc_diag_schema($db);
$count=0;function expect(bool $ok,string $label): void {global $count;if(!$ok)throw new RuntimeException($label);$count++;}
$message="2026-11-22 09:15:20 [ERROR] [amazon] Read timed out product 12345";
$log=['id'=>1,'identity'=>'DEVICE1','client_version'=>'1.0.6','content'=>$message,'created_at'=>'2026-11-22 09:16:00'];
expect(lc_index_log($db,$log)===1,'first event stored');$log['id']=2;expect(lc_index_log($db,$log)===0,'overlapping log does not repeat timestamped line');
$log['id']=3;$log['content']="2026-11-22 09:15:21 [ERROR] [amazon] Read timed out product 99999";expect(lc_index_log($db,$log)===1,'new occurrence retained');
$rows=$db->query('SELECT * FROM lc_diagnostic_events')->fetchAll();expect($rows[0]['fingerprint']===$rows[1]['fingerprint'],'time and product ID do not split identical issues');expect($rows[0]['source']==='log-inferred','old log source labelled inferred');expect($rows[0]['platform']==='amazon','platform inferred');expect($rows[0]['error_type']==='network','error classified');
$log['content']='[ERROR] unknown platform failed';$log['id']=4;expect(lc_index_log($db,$log)===1,'untimed event stored');$row=$db->query('SELECT * FROM lc_diagnostic_events ORDER BY id DESC')->fetch();expect($row['platform']==='unknown'&&$row['occurred_at']===$log['created_at'],'unknown and no timestamp explicitly handled');
$secret='{"password":"secret-one","api_key":"secret-two","email":"client@example.invalid"} Authorization: Bearer abc.def';$safe=lc_redact($secret);expect(strpos($safe,'secret-one')===false&&strpos($safe,'secret-two')===false,'quoted JSON secrets redacted');expect(strpos($safe,'client@example.invalid')===false,'email redacted');expect(strpos($safe,'abc.def')===false,'bearer redacted');
$db->prepare('INSERT INTO app_logs VALUES(?,?,?,?,?)')->execute([10,'DEVICE1','1.0.6',$message,'2026-11-22 09:16:00']);$a=lc_backfill();$b=lc_backfill();expect($a['logs']===1&&$b['logs']===0,'resumable backfill only visits new log ids');expect((int)$db->query('SELECT COUNT(*) FROM app_logs')->fetchColumn()===1,'original logs untouched');
expect(isset(lc_registry()['future_market']),'new platform registered without page changes');expect(lc_platforms()['future_market']===['ai_text','listing'],'new platform capability exposed');
echo json_encode(['passed'=>$count,'original_logs_retained'=>true],JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE),"\n";
