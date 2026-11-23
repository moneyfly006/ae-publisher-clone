<?php
declare(strict_types=1);
require_once __DIR__.'/platform_registry.php';

// Shared by the portal, embedded admin and cron. Does not alter legacy licenses.
function lc_path(): string {
    return getenv('SHOP_LICENSE_DB') ?: '/www/wwwroot/license.moneyfly.club/public/shop/data/license.db';
}
function lc_db(): PDO {
    static $db;
    if ($db) return $db;
    $db = new PDO('sqlite:' . lc_path(), null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);
    $db->exec('PRAGMA busy_timeout=10000');
    $db->exec('PRAGMA journal_mode=WAL');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_devices(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, machine_code TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(user_id,machine_code))');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_fulfillments(order_id INTEGER PRIMARY KEY, license_id INTEGER NOT NULL, fulfilled_at INTEGER NOT NULL)');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_renewals(license_id INTEGER NOT NULL, period TEXT NOT NULL, order_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(license_id,period))');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_links(identity TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL, actor TEXT NOT NULL)');
    $db->exec('CREATE TABLE IF NOT EXISTS lc_order_snapshots(order_id INTEGER PRIMARY KEY, package_json TEXT NOT NULL)');
    $db->exec('CREATE INDEX IF NOT EXISTS idx_lc_devices_machine ON lc_devices(machine_code,active)');
    $db->exec('CREATE INDEX IF NOT EXISTS idx_lc_licenses_user ON licenses(user_id,revoked,expires_at)');
    // Bootstrap history exactly once; keep inactive device records inactive.
    $db->exec('CREATE TABLE IF NOT EXISTS lc_migrations(name TEXT PRIMARY KEY,created_at INTEGER NOT NULL)');
    if(!$db->query("SELECT 1 FROM lc_migrations WHERE name='devices-v1'")->fetchColumn()){
    $db->exec("INSERT OR IGNORE INTO lc_devices(user_id,machine_code,active,created_at,updated_at) SELECT user_id,machine_code,1,MIN(created_at),MAX(created_at) FROM licenses WHERE machine_code<>'' AND revoked=0 GROUP BY user_id,machine_code");
    $db->exec("INSERT OR IGNORE INTO lc_migrations(name,created_at) VALUES('devices-v1',".time().")");
    }
    return $db;
}
function lc_platforms(): array {
    return array_map(function($row){return $row['features'];},lc_registry());
}
function lc_names(): array { return array_map(function($row){return $row['name'];},lc_registry()); }
function lc_feature_names(): array { return ['listing'=>'刊登','category'=>'类目','trade'=>'价格贸易','pricing'=>'价格','variants'=>'变种','ai_text'=>'AI 文案','ai_image'=>'AI 图片','audit'=>'审核','fba'=>'FBA']; }
function lc_valid_date(string $value): bool {
    $d=DateTimeImmutable::createFromFormat('!Y-m-d',$value);
    return $d && $d->format('Y-m-d')===$value;
}
function lc_effective(int $uid, ?string $date=null): ?array {
    $date=$date ?: date('Y-m-d');
    $st=lc_db()->prepare('SELECT * FROM licenses WHERE user_id=? AND revoked=0 ORDER BY id'); $st->execute([$uid]);
    $all=$st->fetchAll(); $features=[]; $records=[]; $map=lc_platforms();
    foreach ($all as $row) {
        if (!lc_valid_date((string)$row['expires_at']) || $row['expires_at']<$date) continue;
        foreach ((json_decode($row['platforms'],true) ?: []) as $pid=>$list) {
            foreach (array_intersect((array)$list,$map[$pid] ?? []) as $feature) {
                $features[$pid][$feature]=max($features[$pid][$feature] ?? '',$row['expires_at']);
            }
        }
        $records[]=(int)$row['id'];
    }
    if (!$features) return null;
    $plats=[]; $dates=[]; $platformExpiries=[];
    foreach ($features as $pid=>$items) { ksort($items); $features[$pid]=$items; $plats[$pid]=array_keys($items); $dates=array_merge($dates,array_values($items)); $platformExpiries[$pid]=max($items); }
    return ['user_id'=>$uid,'platforms'=>$plats,'feature_expiries'=>$features,'platform_expiries'=>$platformExpiries,
      'expires_at'=>max($dates),'next_change_at'=>min($dates),'record_ids'=>$records];
}
function lc_sign_token(string $machine,array $effective): string {
    // Old clients understand expires_at only; stop at the next rights transition.
    // New clients use per-feature dates. Never extend shorter rights to a later date.
    $payload=['machine_code'=>$machine,'platforms'=>$effective['platforms'],
      'expires_at'=>$effective['next_change_at'] ?? $effective['expires_at'],
      'feature_expiries'=>$effective['feature_expiries'] ?? [],'issued_at'=>time()];
    $canonical=function($v) use (&$canonical): string {
        if (!is_array($v)) return json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        if ($v===[] || array_keys($v)===range(0,count($v)-1)) return '['.implode(',',array_map($canonical,$v)).']';
        ksort($v); $out=[]; foreach($v as $k=>$x) $out[]=json_encode((string)$k).':'.$canonical($x);
        return '{'.implode(',',$out).'}';
    };
    return json_encode(['payload'=>$payload,'sig'=>hash_hmac('sha256',$canonical($payload),'aurora-list-platform-license-v1')],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
}
function lc_machine(string $machine): string { return strtoupper(str_replace('-','',trim($machine))); }
function lc_bind(int $uid,string $machine,int $limit): array {
    if (!preg_match('/^[A-Za-z0-9-]{6,64}$/',$machine)) throw new RuntimeException('机器码格式不正确');
    if(!lc_effective($uid))throw new RuntimeException('账号没有有效的平台权限');
    $db=lc_db(); $db->exec('BEGIN IMMEDIATE');
    try {
        $other=$db->prepare('SELECT user_id FROM lc_devices WHERE active=1 AND replace(upper(machine_code),\'-\',\'\')=? AND user_id<>?');$other->execute([lc_machine($machine),$uid]);
        if ($other->fetch()) throw new RuntimeException('该设备已绑定其他账号，请联系管理员核实');
        $q=$db->prepare('SELECT machine_code FROM lc_devices WHERE user_id=? AND active=1');$q->execute([$uid]);$bound=$q->fetchAll(PDO::FETCH_COLUMN);
        if (!in_array($machine,$bound,true) && count($bound)>=max(1,$limit)) throw new RuntimeException('设备数已满，请先在设备管理中解绑');
        $db->prepare('INSERT INTO lc_devices(user_id,machine_code,active,created_at,updated_at) VALUES(?,?,1,?,?) ON CONFLICT(user_id,machine_code) DO UPDATE SET active=1,updated_at=excluded.updated_at')->execute([$uid,$machine,time(),time()]);
        $db->exec('COMMIT');
    } catch(Throwable $e) { $db->exec('ROLLBACK'); throw $e; }
    $effective=lc_effective($uid); if (!$effective) throw new RuntimeException('账号没有有效的平台权限');
    $effective['machine_code']=$machine; $effective['token']=lc_sign_token($machine,$effective);return $effective;
}
function lc_unbind(int $uid,string $machine): void {
    $db=lc_db();$db->exec('BEGIN IMMEDIATE');
    try {
        $db->prepare('UPDATE lc_devices SET active=0,updated_at=? WHERE user_id=? AND machine_code=?')->execute([time(),$uid,$machine]);
        $q=$db->prepare('SELECT id,platforms,expires_at FROM licenses WHERE user_id=? AND machine_code=?');$q->execute([$uid,$machine]);
        foreach($q->fetchAll() as $r) {
            $token=lc_sign_token('', ['platforms'=>json_decode($r['platforms'],true) ?: [],'expires_at'=>$r['expires_at']]);
            $db->prepare("UPDATE licenses SET machine_code='',token=?,last_unbind=? WHERE id=?")->execute([$token,time(),$r['id']]);
        }
        $db->exec('COMMIT');
    }catch(Throwable $e){$db->exec('ROLLBACK');throw $e;}
}
function lc_for_machine(string $machine): ?array {
    $q=lc_db()->prepare('SELECT DISTINCT user_id FROM lc_devices WHERE replace(upper(machine_code),\'-\',\'\')=? AND active=1');$q->execute([lc_machine($machine)]);$ids=$q->fetchAll(PDO::FETCH_COLUMN);
    if(count($ids)!==1) return null;
    $e=lc_effective((int)$ids[0]);if(!$e)return null;
    $e['machine_code']=$machine;$e['token']=lc_sign_token($machine,$e);return $e;
}
function lc_epay(): array {
    $q=lc_db()->query("SELECT k,v FROM settings WHERE k IN ('epay_gateway','epay_pid','epay_key','epay_type')");$s=$q->fetchAll(PDO::FETCH_KEY_PAIR);
    return ['gateway'=>rtrim((string)($s['epay_gateway'] ?? ''),'/'),'pid'=>$s['epay_pid'] ?? '','key'=>$s['epay_key'] ?? '','type'=>$s['epay_type'] ?? 'alipay'];
}
function lc_snapshot(int $oid,array $package): void {
    lc_db()->prepare('INSERT OR IGNORE INTO lc_order_snapshots(order_id,package_json) VALUES(?,?)')->execute([$oid,json_encode($package,JSON_UNESCAPED_UNICODE)]);
}
function lc_fulfill(int $oid,bool $outer=false): ?array {
    $db=lc_db();if(!$outer)$db->exec('BEGIN IMMEDIATE');
    try {
        $q=$db->prepare('SELECT * FROM orders WHERE id=?');$q->execute([$oid]);$o=$q->fetch();
        if(!$o || $o['status']!=='paid')throw new RuntimeException('订单尚未支付');
        // Old successful fulfillments are recognized without rewriting them.
        $q=$db->prepare('SELECT * FROM licenses WHERE order_id=? ORDER BY id LIMIT 1');$q->execute([$oid]);$existing=$q->fetch();
        if(!$existing){
            $q=$db->prepare('SELECT package_json FROM lc_order_snapshots WHERE order_id=?');$q->execute([$oid]);$snapshot=$q->fetchColumn();$p=$snapshot?json_decode($snapshot,true):null;
            if(!$p){$q=$db->prepare('SELECT * FROM packages WHERE id=?');$q->execute([$o['package_id']]);$p=$q->fetch();}if(!$p)throw new RuntimeException('订单套餐不存在，请联系管理员处理');
            $plats=[];foreach(json_decode($p['platforms'],true) ?: [] as $pid){$fs=array_values(array_intersect(json_decode($p['features'],true) ?: [],lc_platforms()[$pid] ?? []));if($fs)$plats[$pid]=$fs;}
            if(!$plats)throw new RuntimeException('套餐没有可开通的功能');
            $current=lc_effective((int)$o['user_id']);$groups=[];
            foreach($plats as $pid=>$fs)foreach($fs as $feature){
                $base=max(date('Y-m-d'),$current['feature_expiries'][$pid][$feature] ?? '');
                $exp=date('Y-m-d',strtotime($base.' +'.max(1,(int)$p['days']).' days'));
                $groups[$exp][$pid][]=$feature;
            }
            foreach($groups as $exp=>$ps){$token=lc_sign_token('',['platforms'=>$ps,'expires_at'=>$exp]);$db->prepare('INSERT INTO licenses(user_id,order_id,machine_code,token,expires_at,platforms,created_at) VALUES(?,?,\'\',?,?,?,?)')->execute([$o['user_id'],$oid,$token,$exp,json_encode($ps),time()]);$lid=(int)$db->lastInsertId();}
        }else{$lid=(int)$existing['id'];}
        $db->prepare('INSERT OR IGNORE INTO lc_fulfillments(order_id,license_id,fulfilled_at) VALUES(?,?,?)')->execute([$oid,$lid,time()]);
        if(!$outer)$db->exec('COMMIT');
    }catch(Throwable $e){if(!$outer)$db->exec('ROLLBACK');throw $e;}
    return lc_effective((int)$o['user_id']);
}
function lc_mark_paid(int $oid,string $type=''): ?array {
    $db=lc_db();$db->exec('BEGIN IMMEDIATE');
    try {
        $q=$db->prepare('SELECT status FROM orders WHERE id=?');$q->execute([$oid]);$status=$q->fetchColumn();
        if(!in_array($status,['pending','expired','paid'],true))throw new RuntimeException('订单不存在或已退款，不能开通');
        $db->prepare("UPDATE orders SET status='paid',paid_at=CASE WHEN paid_at>0 THEN paid_at ELSE ? END,pay_type=CASE WHEN ?<>'' THEN ? ELSE pay_type END WHERE id=?")->execute([time(),$type,$type,$oid]);
        $e=lc_fulfill($oid,true);$db->exec('COMMIT');return $e;
    }catch(Throwable $e){$db->exec('ROLLBACK');throw $e;}
}
function lc_auto_renew(): array {
    $db=lc_db();$rows=$db->query("SELECT l.user_id,o.package_id,MAX(l.expires_at) period,MAX(l.id) license_id FROM licenses l JOIN orders o ON o.id=l.order_id JOIN users u ON u.id=l.user_id WHERE l.revoked=0 AND o.status='paid' AND u.auto_renew=1 GROUP BY l.user_id,o.package_id HAVING MAX(l.expires_at)<=date('now','+3 day')")->fetchAll();$results=[];
    foreach($rows as $r){
        $db->exec('BEGIN IMMEDIATE');
        try{
            $q=$db->prepare('SELECT 1 FROM lc_renewals WHERE license_id=? AND period=?');$q->execute([$r['license_id'],$r['period']]);if($q->fetch()){$db->exec('COMMIT');continue;}
            $q=$db->prepare('SELECT * FROM packages WHERE id=? AND active=1');$q->execute([$r['package_id']]);$p=$q->fetch();if(!$p){$db->exec('COMMIT');continue;}
            $q=$db->prepare('UPDATE users SET balance_cents=balance_cents-? WHERE id=? AND auto_renew=1 AND balance_cents>=?');$q->execute([$p['price_cents'],$r['user_id'],$p['price_cents']]);
            if($q->rowCount()!==1){$db->exec('COMMIT');$results[]=['user'=>$r['user_id'],'skip'=>'balance_low'];continue;}
            $db->prepare('INSERT INTO orders(user_id,package_id,amount_cents,status,trade_no,created_at,paid_at) VALUES(?,?,?,\'paid\',?,?,?)')->execute([$r['user_id'],$p['id'],$p['price_cents'],'AUTO'.bin2hex(random_bytes(12)),time(),time()]);$oid=(int)$db->lastInsertId();
            lc_snapshot($oid,$p);$e=lc_fulfill($oid,true);
            $db->prepare('INSERT INTO balance_logs(user_id,amount_cents,reason,created_at) VALUES(?,?,?,?)')->execute([$r['user_id'],-(int)$p['price_cents'],'自动续费订单 '.$oid,time()]);
            $db->prepare('INSERT INTO lc_renewals(license_id,period,order_id) VALUES(?,?,?)')->execute([$r['license_id'],$r['period'],$oid]);
            $db->exec('COMMIT');$results[]=['user'=>$r['user_id'],'renewed'=>true,'order'=>$oid,'expires'=>$e['expires_at'] ?? ''];
        }catch(Throwable $e){$db->exec('ROLLBACK');$results[]=['user'=>$r['user_id'],'error'=>$e->getMessage()];}
    }
    return $results;
}
