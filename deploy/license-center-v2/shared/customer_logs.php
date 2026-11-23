<?php
declare(strict_types=1);
require_once __DIR__.'/entitlements.php';

function lc_legacy_db(): PDO {
    static $db;
    if(!$db){$path=getenv('LEGACY_LICENSE_DB') ?: '/www/server/ae-license/storage/license.sqlite';
      $db=new PDO('sqlite:'.$path,null,null,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
      $db->exec('PRAGMA busy_timeout=5000');$db->exec('PRAGMA query_only=ON');}
    return $db;
}
function lc_user_identities(int $uid): array {
    $db=lc_db();$q=$db->prepare('SELECT machine_code FROM lc_devices WHERE user_id=?');$q->execute([$uid]);$machines=$q->fetchAll(PDO::FETCH_COLUMN);$ids=[];
    $owner=$db->prepare('SELECT COUNT(DISTINCT user_id) FROM lc_devices WHERE replace(upper(machine_code),\'-\',\'\')=?');
    foreach($machines as $mc){
        $owner->execute([lc_machine($mc)]);if((int)$owner->fetchColumn()!==1)continue;
        $ids[]=$mc;$short=lc_machine($mc);$ids[]=$short;if(strlen($short)===16)$ids[]=substr($short,0,8).'-'.substr($short,8);
        $q=lc_legacy_db()->prepare('SELECT identity FROM devices WHERE replace(upper(machine_code),\'-\',\'\')=? OR replace(upper(identity),\'-\',\'\')=?');
        $q->execute([$short,$short]);foreach($q->fetchAll(PDO::FETCH_COLUMN) as $identity)$ids[]=$identity;
    }
    $q=$db->prepare('SELECT identity FROM lc_links WHERE user_id=?');$q->execute([$uid]);
    return array_values(array_unique(array_merge($ids,$q->fetchAll(PDO::FETCH_COLUMN))));
}
function lc_logs(int $uid,int $page=1,string $keyword='',bool $errors=false): array {
    $ids=lc_user_identities($uid);$page=max(1,$page);$per=20;
    if(!$ids)return ['rows'=>[],'page'=>1,'pages'=>1,'total'=>0];
    $where='identity IN ('.implode(',',array_fill(0,count($ids),'?')).')';$bind=$ids;
    if($keyword!==''){$where.=' AND (filename LIKE ? OR client_version LIKE ? OR identity LIKE ?)';$bind=array_merge($bind,array_fill(0,3,'%'.$keyword.'%'));}
    if($errors)$where.=' AND error_count>0';
    $q=lc_legacy_db()->prepare('SELECT COUNT(*) FROM app_logs WHERE '.$where);$q->execute($bind);$total=(int)$q->fetchColumn();$pages=max(1,(int)ceil($total/$per));$page=min($page,$pages);$offset=($page-1)*$per;
    $q=lc_legacy_db()->prepare('SELECT id,identity,filename,size,client_version,event_count,error_count,created_at FROM app_logs WHERE '.$where." ORDER BY id DESC LIMIT $per OFFSET $offset");$q->execute($bind);
    return ['rows'=>$q->fetchAll(),'page'=>$page,'pages'=>$pages,'total'=>$total];
}
function lc_log_detail(int $uid,int $id): ?array {
    $ids=lc_user_identities($uid);if(!$ids)return null;
    $q=lc_legacy_db()->prepare('SELECT id,identity,filename,client_version,content,created_at,error_count,event_count FROM app_logs WHERE id=? AND identity IN ('.implode(',',array_fill(0,count($ids),'?')).')');
    $q->execute(array_merge([$id],$ids));return $q->fetch() ?: null;
}
function lc_linked_legacy(int $uid): array {
    $q=lc_db()->prepare('SELECT identity FROM lc_links WHERE user_id=?');$q->execute([$uid]);$ids=$q->fetchAll(PDO::FETCH_COLUMN);if(!$ids)return [];
    $q=lc_legacy_db()->prepare('SELECT identity,client_type,plan,status,expires_at FROM licenses WHERE identity IN ('.implode(',',array_fill(0,count($ids),'?')).')');$q->execute($ids);return $q->fetchAll();
}
