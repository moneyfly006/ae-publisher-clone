<?php
declare(strict_types=1);
function lc_registry(): array {
    static $data;
    if($data!==null)return $data;
    $file=getenv('LICENSE_PLATFORM_REGISTRY') ?: __DIR__.'/platforms.json';
    $raw=json_decode((string)file_get_contents($file),true);$data=[];
    foreach(($raw['platforms'] ?? []) as $row){
        $id=(string)($row['id'] ?? '');if(!preg_match('/^[a-z][a-z0-9_]{1,31}$/',$id))continue;
        $features=array_values(array_unique(array_filter((array)($row['features'] ?? []),function($f){return is_string($f)&&preg_match('/^[a-z][a-z0-9_]{1,31}$/',$f);})));sort($features);
        $data[$id]=['id'=>$id,'name'=>mb_substr((string)($row['name'] ?? $id),0,60),'features'=>$features,'status'=>$row['status'] ?? 'planned'];
    }
    if(!$data)throw new RuntimeException('平台注册表无有效定义');
    return $data;
}
