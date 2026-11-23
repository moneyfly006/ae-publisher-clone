<?php
declare(strict_types=1);
require (getenv('LICENSE_CENTER_SHARED') ?: dirname(__DIR__,2).'/deploy/license-center-v2/shared').'/log_assessment.php';
$count=0;function expect_log(bool $ok,string $label): void {global $count;if(!$ok)throw new RuntimeException($label);$count++;}
$cases=[
 'tls'=>'[ERROR] certificate verify failed', 'rate_limit'=>'[ERROR] HTTP 429 Too many requests',
 'quota'=>'[ERROR] HTTP 429 insufficient_quota: Not enough quota', 'category'=>'[ERROR] category permission denied',
 'auth'=>'[ERROR] HTTP 401 unauthorized Cookie=secret-do-not-show', 'runtime'=>"TypeError: unsupported operand type",
 'attribute'=>'[ERROR] missing required attribute variation_theme', 'identifier'=>'[ERROR] UPC invalid',
 'image'=>'[ERROR] 图片上传失败', 'price'=>'[ERROR] 物流属性不能为空',
 'ai_output'=>'[ERROR] AI 文案解析失败', 'network'=>'[ERROR] Read timed out', 'other'=>'[ERROR] vendor rejected operation xyz',
];
foreach($cases as $type=>$message){$a=lc_assess_log(['content'=>$message,'error_count'=>1]);expect_log(count($a['issues'])===1&&$a['issues'][0]['type']===$type,'classified '.$type);expect_log(count($a['issues'][0]['steps'])>0&&$a['issues'][0]['verify']!==''&&$a['issues'][0]['evidence'][0]['line']===1,'evidence and action '.$type);}
$a=lc_assess_log(['content'=>"[ERROR] Read timed out\n[ERROR] Read timed out\n发布成功",'error_count'=>2]);expect_log(count($a['issues'])===1&&$a['issues'][0]['count']===2&&$a['status']==='needs_attention','later success does not erase historical failure');
$a=lc_assess_log(['content'=>'[INFO] 模型预算设置为 20，product 429','error_count'=>0]);expect_log(!$a['issues']&&$a['status']==='unknown','normal setting and numeric product id not classified as failure');
$a=lc_assess_log(['content'=>'保存草稿成功']);expect_log($a['status']==='draft','draft is not published');
$a=lc_assess_log(['content'=>'产品已提交发布']);expect_log($a['status']==='submitted'&&strpos($a['next'],'最终')!==false,'submission requires platform verification');
$a=lc_assess_log(['content'=>'[INFO] running','error_count'=>3]);expect_log($a['status']==='unknown'&&strpos($a['summary'],'摘要')!==false,'structured errors without body evidence are unknown');
$a=lc_assess_log(['content'=>$cases['auth']]);expect_log(strpos(json_encode($a),'secret-do-not-show')===false,'evidence secrets redacted');
$a=lc_assess_log(['content'=>str_repeat("[INFO] line\n",8100).$cases['network']]);expect_log($a['limited']&&$a['analyzed_lines']===8000&&$a['issues'][0]['evidence'][0]['line']===8101,'large log scan has explicit bounds and original line numbers');
$a=lc_assess_log([]);expect_log($a['status']==='unknown'&&!$a['issues'],'empty log does not claim healthy');
echo json_encode(['passed'=>$count],JSON_PRETTY_PRINT),"\n";
