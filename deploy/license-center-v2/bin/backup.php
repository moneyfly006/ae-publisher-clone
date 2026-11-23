<?php
// SQLite snapshots include committed WAL data, unlike copying the live main file.
$dst='/www/backups/license';@mkdir($dst,0700,true);$stamp=date('Ymd_His');
$files=['/www/wwwroot/license.moneyfly.club/public/shop/data/license.db'=>'shop_license',
 '/www/server/ae-license/storage/license.sqlite'=>'legacy_license'];
foreach($files as $src=>$name){
 if(!is_file($src))continue;$output="$dst/{$name}_{$stamp}.db";
 try{$db=new SQLite3($src,SQLITE3_OPEN_READONLY);$target=new SQLite3($output);$ok=$db->backup($target);$integrity=$target->querySingle('PRAGMA quick_check');$target->close();$db->close();chmod($output,0600);if(!$ok||$integrity!=='ok')throw new RuntimeException('快照完整性校验失败');echo 'OK '.basename($output)."\n";}
 catch(Throwable $e){fwrite(STDERR,'FAIL '.$name.' '.$e->getMessage()."\n");exit(1);}
}
foreach(glob("$dst/*.db") as $file){if(filemtime($file)<time()-30*86400)unlink($file);}
