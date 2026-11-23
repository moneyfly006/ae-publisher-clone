<?php
// Read-only traceability check for the previously uploaded real desktop log.
declare(strict_types=1);
$app='/www/server/ae-license';
require $app.'/app/bootstrap.php';require $app.'/app/pages.php';require $app.'/shared/diagnostics.php';
$log=db()->query('SELECT id,identity,content,created_at FROM app_logs WHERE id=491')->fetch();
if(!$log)throw new RuntimeException('Previously uploaded desktop log missing');
$rules=log_rules();$recognized=0;$matched=0;$sourceIds=[];
$q=db()->prepare('SELECT log_id FROM lc_diagnostic_events WHERE event_key=?');
foreach(array_slice(preg_split('/\R/u',$log['content']) ?: [],-8000) as $lineNo=>$line){
    $line=trim($line);if($line==='')continue;$event='';
    foreach($rules as $kind=>$words)foreach($words as $word)if($word!==''&&strpos($line,$word)!==false){$event=$kind;break 2;}
    if($event===''&&preg_match('/traceback|exception|失败|异常|\[error\]/iu',$line))$event='error';
    if($event==='')continue;$recognized++;
    $timed=preg_match('/\b(20\d\d-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:[.,]\d+)?/',$line);
    $key=hash('sha256',$log['identity'].'|'.($timed?'timestamped|'.$line:'log:'.$log['id'].'|'.$lineNo.'|'.$line));
    $q->execute([$key]);$id=$q->fetchColumn();if($id!==false){$matched++;$sourceIds[(int)$id]=true;}
}
$last=(int)db()->query("SELECT v FROM lc_diagnostic_meta WHERE k='last_log_id'")->fetchColumn();
$max=(int)db()->query('SELECT MAX(id) FROM app_logs')->fetchColumn();
$result=['sample_log_id'=>491,'recognized_lines'=>$recognized,'indexed_or_deduplicated_lines'=>$matched,'source_log_count'=>count($sourceIds),'indexed_to'=>$last,'max_log_id'=>$max,'all_sample_events_covered'=>$recognized>0&&$recognized===$matched,'backfill_complete'=>$last>=$max];
echo json_encode($result,JSON_PRETTY_PRINT),"\n";
if(!$result['all_sample_events_covered']||!$result['backfill_complete'])exit(1);
