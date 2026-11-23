<?php
declare(strict_types=1);

/* ==================== 公共 ==================== */

function page_landing(): void
{
    if (current_admin()) {
        redirect(cfg('admin_path'));
    }
    render('landing', ['title' => '授权中心']);
}

function page_login_form(): void
{
    if (current_admin()) {
        redirect(cfg('admin_path'));
    }
    render('login', ['title' => '管理员登录']);
}

function page_login_post(): void
{
    csrf_check();
    $ip = client_ip();
    $username = trim((string)($_POST['username'] ?? ''));
    $password = (string)($_POST['password'] ?? '');

    if (login_locked($ip)) {
        flash('bad', '尝试次数过多，请 10 分钟后再试。');
        redirect(cfg('admin_path') . '/login');
    }
    if ($username === '' || $password === '') {
        flash('bad', '请输入用户名和密码。');
        redirect(cfg('admin_path') . '/login');
    }

    $st = db()->prepare('SELECT * FROM admins WHERE username = ? AND is_active = 1');
    $st->execute([$username]);
    $admin = $st->fetch();

    if (!$admin || !password_verify($password, $admin['pass_hash'])) {
        login_attempt_record($ip, $username, false);
        flash('bad', '用户名或密码不正确。');
        redirect(cfg('admin_path') . '/login');
    }

    login_attempt_record($ip, $username, true);
    db()->prepare('DELETE FROM login_attempts WHERE ip = ? AND ok = 0')->execute([$ip]);

    if (function_exists('admin_totp_columns')) { admin_totp_columns(); }
    try {
        $tst = db()->prepare('SELECT totp_enabled FROM admins WHERE id=?');
        $tst->execute([(int)$admin['id']]);
        $trow = $tst->fetch();
        if ((int)($trow['totp_enabled'] ?? 0) === 1) {
            $_SESSION['admin_2fa_pending'] = (int)$admin['id'];
            redirect('/admin/2fa');
        }
    } catch (Exception $e) {}

    session_regenerate_id(true);
    $_SESSION['admin_id'] = (int)$admin['id'];
    $_SESSION['csrf'] = bin2hex(random_bytes(32));

    $up = db()->prepare('UPDATE admins SET last_login_at = ?, last_login_ip = ? WHERE id = ?');
    $up->execute([now(), $ip, $admin['id']]);
    audit('登录后台', 'admin:' . $admin['id'], '', $admin['username']);

    redirect(cfg('admin_path'));
}

function page_logout_get(): void
{
    session_boot();
    if (current_admin()) {
        audit('退出后台(GET)', 'admin:' . current_admin()['id']);
    }
    $_SESSION = [];
    session_destroy();
    redirect(cfg('admin_path') . '/login');
}

function page_logout(): void
{
    csrf_check();
    $a = current_admin();
    if ($a) {
        audit('退出后台', 'admin:' . $a['id'], '', $a['username']);
    }
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $p = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], $p['secure'], $p['httponly']);
    }
    session_destroy();
    redirect(cfg('admin_path') . '/login');
}

/* ==================== 仪表盘 ==================== */

function page_dashboard(): void
{
    $admin = require_admin();
    $db = db();

    $total   = (int)$db->query('SELECT COUNT(*) FROM licenses')->fetchColumn();
    $active  = (int)$db->query("SELECT COUNT(*) FROM licenses WHERE status='active' AND expires_at >= date('now','localtime')")->fetchColumn();
    $expired = (int)$db->query("SELECT COUNT(*) FROM licenses WHERE status='active' AND expires_at < date('now','localtime')")->fetchColumn();
    $revoked = (int)$db->query("SELECT COUNT(*) FROM licenses WHERE status='revoked'")->fetchColumn();
    $soon    = (int)$db->query("SELECT COUNT(*) FROM licenses WHERE status='active' AND expires_at BETWEEN date('now','localtime') AND date('now','localtime','+7 day')")->fetchColumn();

    $today = today();
    $todayPub  = (int)$db->query("SELECT COUNT(*) FROM usage_events WHERE event='publish_ok' AND created_at LIKE '$today%'")->fetchColumn();
    $todayDraft= (int)$db->query("SELECT COUNT(*) FROM usage_events WHERE event='draft' AND created_at LIKE '$today%'")->fetchColumn();
    $todayErr  = (int)$db->query("SELECT COUNT(*) FROM usage_events WHERE event='error' AND created_at LIKE '$today%'")->fetchColumn();
    $todayLogs = (int)$db->query("SELECT COUNT(*) FROM app_logs WHERE created_at LIKE '$today%'")->fetchColumn();
    $recent = $db->query('SELECT * FROM licenses ORDER BY id DESC LIMIT 6')->fetchAll();
    $audits = $db->query('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8')->fetchAll();
    $verifies = $db->query('SELECT * FROM verify_logs ORDER BY id DESC LIMIT 8')->fetchAll();
    $lang = $admin;

    render('dashboard', compact('total', 'active', 'expired', 'revoked', 'soon', 'recent', 'audits', 'verifies', 'todayPub', 'todayDraft', 'todayErr', 'todayLogs') + ['title' => '控制台']);
}

/* ==================== 授权列表 ==================== */


function page_rule_diag(): void
{
    require_admin();
    $rules = log_rules();
    $buckets = [];

    $st = db()->query('SELECT content FROM app_logs ORDER BY id DESC LIMIT 10');
    foreach ($st->fetchAll() as $r) {
        $lines = preg_split('/\R/u', (string)$r['content']) ?: [];
        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '' || mb_strlen($line) < 6) {
                continue;
            }
            // 已命中规则 → 跳过
            $hit = false;
            foreach ($rules as $list) {
                foreach ($list as $n) {
                    if (strpos($line, $n) !== false) {
                        $hit = true;
                        break 2;
                    }
                }
            }
            if ($hit) {
                continue;
            }
            // 只保留“像报错”的行：含 ✗ / ERROR / 异常 / 失败 等
            $hasErr = (bool)preg_match('/(✗|×|ERROR|CRITICAL|异常|失败|错误|超时|未授权|denied|cannot|failed|invalid)/iu', $line);
            if (!$hasErr) {
                continue;
            }
            // 排除成功总结行（完成：成功 N / 失败 0、✓ 开头等）
            $isOkLine = (mb_strpos($line, '✓') !== false || (bool)preg_match('/完成[:：]/u', $line));
            $forceErr = (bool)preg_match('/(✗|×(?!\s*API)|ERROR|CRITICAL)/u', $line);
            if ($isOkLine && !$forceErr) {
                continue;
            }
            // 排除“失败 0 / 失败 0 个”
            if (preg_match('/失败\s*0\b/u', $line)) {
                continue;
            }
            // 归一化聚合：去掉时间戳、数字
            $key = preg_replace('/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/u', '', $line);
            $key = preg_replace('/\d+/u', '#', (string)$key);
            $key = mb_substr(trim((string)$key), 0, 90);
            if (!isset($buckets[$key])) {
                $buckets[$key] = ['count' => 0, 'sample' => mb_substr($line, 0, 220)];
            }
            $buckets[$key]['count']++;
        }
    }

    uasort($buckets, static fn($a, $b) => $b['count'] <=> $a['count']);
    $buckets = array_slice($buckets, 0, 25, true);

    render('rules', ['title' => '规则诊断', 'rules' => $rules, 'buckets' => $buckets]);
}

function page_licenses(): void
{
    require_admin();
    $q      = trim((string)($_GET['q'] ?? ''));
    $status = (string)($_GET['status'] ?? '');
    $page   = max(1, (int)($_GET['page'] ?? 1));
    $per    = 20;

    $where = [];
    $bind  = [];
    if ($q !== '') {
        $where[] = '(identity LIKE ? OR note LIKE ? OR plan LIKE ?)';
        $like = '%' . $q . '%';
        array_push($bind, $like, $like, $like);
    }
    if (in_array($status, ['active', 'expired', 'revoked', 'disabled'], true)) {
        if ($status === 'active') {
            $where[] = "status='active' AND expires_at >= date('now','localtime')";
        } elseif ($status === 'expired') {
            $where[] = "status='active' AND expires_at < date('now','localtime')";
        } else {
            $where[] = 'status = ?';
            $bind[] = $status;
        }
    }
    $sqlWhere = $where ? ('WHERE ' . implode(' AND ', $where)) : '';

    $cnt = db()->prepare("SELECT COUNT(*) FROM licenses $sqlWhere");
    $cnt->execute($bind);
    $totalRows = (int)$cnt->fetchColumn();
    $pages = max(1, (int)ceil($totalRows / $per));
    $page = min($page, $pages);
    $offset = ($page - 1) * $per;

    $st = db()->prepare("SELECT * FROM licenses $sqlWhere ORDER BY id DESC LIMIT $per OFFSET $offset");
    $st->execute($bind);
    $rows = $st->fetchAll();

    render('licenses', compact('rows', 'q', 'status', 'page', 'pages', 'totalRows') + ['title' => '授权管理']);
}

/* ==================== 新建 ==================== */

function page_license_new(): void
{
    require_admin();
    $default = setting_get('default_days', (string)cfg('default_days'));
    $expires = date('Y-m-d', strtotime('+' . max(1, (int)$default) . ' day'));
    render('license_form', [
        'title'  => '新增授权',
        'mode'   => 'create',
        'row'    => ['identity' => '', 'client_type' => 'local', 'plan' => 'standard', 'expires_at' => $expires, 'status' => 'active', 'note' => ''],
        'errors' => [],
    ]);
}

function validate_license(array $in): array
{
    $errors = [];
    $identity = trim((string)($in['identity'] ?? ''));
    $client   = (string)($in['client_type'] ?? 'local');
    $plan     = trim((string)($in['plan'] ?? 'standard'));
    $expires  = trim((string)($in['expires_at'] ?? ''));
    $note     = trim((string)($in['note'] ?? ''));
    $status   = (string)($in['status'] ?? 'active');

    if ($identity === '') {
        $errors[] = '设备授权ID不能为空。';
    } elseif (strlen($identity) > 128) {
        $errors[] = '设备授权ID过长（最多 128 字符）。';
    } elseif (!preg_match('/^[A-Za-z0-9\-_.:]+$/', $identity)) {
        $errors[] = '设备授权ID只能包含字母、数字、- _ . :';
    }
    if (!in_array($client, ['local', 'shop'], true)) {
        $errors[] = '客户端类型不合法。';
    }
    if ($plan === '' || strlen($plan) > 32) {
        $errors[] = '套餐名称需为 1-32 字符。';
    }
    $d = DateTimeImmutable::createFromFormat('Y-m-d', $expires);
    if (!$d || $d->format('Y-m-d') !== $expires) {
        $errors[] = '到期日格式必须为 YYYY-MM-DD。';
    }
    if (!in_array($status, ['active', 'disabled', 'revoked'], true)) {
        $errors[] = '状态不合法。';
    }
    if (mb_strlen($note) > 500) {
        $errors[] = '备注最多 500 字。';
    }

    return [$errors, compact('identity', 'client', 'plan', 'expires', 'note', 'status')];
}

function page_license_create(): void
{
    require_admin();
    csrf_check();
    [$errors, $v] = validate_license($_POST);

    if (!$errors) {
        $st = db()->prepare('SELECT id FROM licenses WHERE identity = ?');
        $st->execute([$v['identity']]);
        if ($st->fetch()) {
            $errors[] = '该设备授权ID已存在，请直接编辑现有记录。';
        }
    }
    if ($errors) {
        render('license_form', [
            'title'  => '新增授权',
            'mode'   => 'create',
            'row'    => [
                'identity' => $v['identity'], 'client_type' => $v['client'], 'plan' => $v['plan'],
                'expires_at' => $v['expires'], 'status' => $v['status'], 'note' => $v['note'],
            ],
            'errors' => $errors,
        ]);
        return;
    }

    $stmt = db()->prepare('INSERT INTO licenses
        (identity, client_type, plan, status, expires_at, issued_at, note, created_by, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)');
    $stmt->execute([
        $v['identity'], $v['client'], $v['plan'], $v['status'], $v['expires'],
        today(), $v['note'], current_admin()['username'], now(), now(),
    ]);
    $id = (int)db()->lastInsertId();
    audit('新增授权', 'license:' . $id, $v['identity'] . ' / ' . $v['plan'] . ' / ' . $v['expires']);
    flash('ok', '授权已创建：' . $v['identity']);
    redirect(cfg('admin_path') . '/licenses/' . $id);
}

/* ==================== 详情 / 编辑 ==================== */

function find_license(int $id): array
{
    $st = db()->prepare('SELECT * FROM licenses WHERE id = ?');
    $st->execute([$id]);
    $row = $st->fetch();
    if (!$row) {
        http_response_code(404);
        render('404', ['title' => '授权不存在']);
        exit;
    }
    return $row;
}

function page_license_show(int $id): void
{
    require_admin();
    $row = find_license($id);
    $st = db()->prepare('SELECT * FROM verify_logs WHERE identity = ? ORDER BY id DESC LIMIT 20');
    $st->execute([$row['identity']]);
    $logs = $st->fetchAll();
    $st = db()->prepare('SELECT * FROM audit_logs WHERE target = ? ORDER BY id DESC LIMIT 20');
    $st->execute(['license:' . $id]);
    $audits = $st->fetchAll();
    render('license_show', ['title' => '授权详情', 'row' => $row, 'logs' => $logs, 'audits' => $audits]);
}

function page_license_edit(int $id): void
{
    require_admin();
    $row = find_license($id);
    render('license_form', ['title' => '编辑授权', 'mode' => 'edit', 'row' => $row, 'errors' => []]);
}

function page_license_update(int $id): void
{
    require_admin();
    csrf_check();
    $row = find_license($id);
    [$errors, $v] = validate_license($_POST);

    if (!$errors && $v['identity'] !== $row['identity']) {
        $st = db()->prepare('SELECT id FROM licenses WHERE identity = ? AND id <> ?');
        $st->execute([$v['identity'], $id]);
        if ($st->fetch()) {
            $errors[] = '该设备授权ID已被其他记录占用。';
        }
    }
    if ($errors) {
        $row = array_merge($row, [
            'identity' => $v['identity'], 'client_type' => $v['client'], 'plan' => $v['plan'],
            'expires_at' => $v['expires'], 'status' => $v['status'], 'note' => $v['note'],
        ]);
        render('license_form', ['title' => '编辑授权', 'mode' => 'edit', 'row' => $row, 'errors' => $errors]);
        return;
    }

    $stmt = db()->prepare('UPDATE licenses SET identity=?, client_type=?, plan=?, status=?, expires_at=?, note=?, updated_at=? WHERE id=?');
    $stmt->execute([$v['identity'], $v['client'], $v['plan'], $v['status'], $v['expires'], $v['note'], now(), $id]);
    audit('修改授权', 'license:' . $id, $v['identity'] . ' / ' . $v['expires'] . ' / ' . $v['status']);
    flash('ok', '已保存修改。');
    redirect(cfg('admin_path') . '/licenses/' . $id);
}

/* ==================== 操作 ==================== */

function page_license_revoke(int $id): void
{
    require_admin();
    csrf_check();
    $row = find_license($id);
    $stmt = db()->prepare("UPDATE licenses SET status='revoked', revoked_at=?, updated_at=? WHERE id=?");
    $stmt->execute([now(), now(), $id]);
    audit('吊销授权', 'license:' . $id, $row['identity']);
    flash('ok', '已吊销该授权。');
    redirect(cfg('admin_path') . '/licenses/' . $id);
}

function page_license_renew(int $id): void
{
    require_admin();
    csrf_check();
    $row = find_license($id);
    $days = (int)($_POST['days'] ?? 0);
    if ($days < 1 || $days > 3650) {
        flash('bad', '续期天数需在 1-3650 之间。');
        redirect(cfg('admin_path') . '/licenses/' . $id);
    }
    $base = max($row['expires_at'], today());
    $new = date('Y-m-d', strtotime($base . ' +' . $days . ' day'));
    $stmt = db()->prepare("UPDATE licenses SET expires_at=?, status='active', revoked_at=NULL, updated_at=? WHERE id=?");
    $stmt->execute([$new, now(), $id]);
    audit('续期授权', 'license:' . $id, $row['identity'] . ' → ' . $new);
    flash('ok', '已续期至 ' . $new);
    redirect(cfg('admin_path') . '/licenses/' . $id);
}

function page_license_delete(int $id): void
{
    require_admin();
    csrf_check();
    $row = find_license($id);
    $stmt = db()->prepare('DELETE FROM licenses WHERE id = ?');
    $stmt->execute([$id]);
    audit('删除授权', 'license:' . $id, $row['identity']);
    flash('ok', '已删除该授权记录。');
    redirect(cfg('admin_path') . '/licenses');
}

/* ==================== 审计 / 设置 ==================== */

function page_audit(): void
{
    require_admin();
    $page = max(1, (int)($_GET['page'] ?? 1));
    $per = 30;
    $total = (int)db()->query('SELECT COUNT(*) FROM audit_logs')->fetchColumn();
    $pages = max(1, (int)ceil($total / $per));
    $page = min($page, $pages);
    $offset = ($page - 1) * $per;
    $rows = db()->query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT $per OFFSET $offset")->fetchAll();
    render('audit', compact('rows', 'page', 'pages', 'total') + ['title' => '操作日志']);
}

function page_settings(): void
{
    $admin = require_admin();
    $section=lc_settings_section();
    if(in_array($section,['payment','mail','portal'],true)){page_shop_pay();return;}
    if($section==='security'){page_admin_security();return;}
    if($section==='platforms'){page_platform_registry();return;}
    if($section==='rules'){page_rule_diag();return;}
    render('settings', [
        'title' => '系统设置',
        'default_days' => setting_get('default_days', (string)cfg('default_days')),
        'device_limit' => (string)(ops_pdo()->query("SELECT v FROM settings WHERE k='device_limit'")->fetchColumn() ?: '2'),
        'api_key_enabled' => setting_get('api_key_enabled', '0'),
        'api_key' => setting_get('api_key', ''),
        'log_rules' => setting_get('log_rules', ''),
        'log_keep_days' => setting_get('log_keep_days', '30'),
        'log_last_cleanup' => setting_get('log_last_cleanup', ''),
        'default_rules' => json_encode(log_rules(), JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT),
    ]);
}

function page_settings_save(): void {
    require_admin();csrf_check();$group=(string)($_POST['settings_group'] ?? 'legacy');
    if(!in_array($group,['license','logs','access','legacy'],true)){http_response_code(400);return;}
    $changes=[];
    if(in_array($group,['license','legacy'],true)&&isset($_POST['default_days'])){$days=filter_var($_POST['default_days'],FILTER_VALIDATE_INT);if(!$days||$days<1||$days>3650){flash('bad','有效天数必须为 1 至 3650');redirect('/admin/settings?section=license');return;}$changes['default_days']=(string)$days;}
    $deviceLimitSave = null;
    if(in_array($group,['license','legacy'],true)&&isset($_POST['device_limit'])){$limit=filter_var($_POST['device_limit'],FILTER_VALIDATE_INT);if($limit===false||$limit<1||$limit>1000){flash('bad','默认设备数必须为 1 至 1000');redirect('/admin/settings?section=license');return;}$deviceLimitSave=(string)$limit;}
    if(in_array($group,['logs','legacy'],true)){
        if(isset($_POST['log_keep_days'])){$days=filter_var($_POST['log_keep_days'],FILTER_VALIDATE_INT);if($days===false||$days<0||$days>3650){flash('bad','日志保留天数必须为 0 至 3650');redirect('/admin/settings?section=logs');return;}$changes['log_keep_days']=(string)$days;}
        if(array_key_exists('log_rules',$_POST)){$rules=trim((string)$_POST['log_rules']);if($rules!==''){$decoded=json_decode($rules,true);$valid=is_array($decoded);if($valid)foreach($decoded as $event=>$words){if(!is_string($event)||!is_array($words)){$valid=false;break;}foreach($words as $word)if(!is_string($word))$valid=false;}if(!$valid){flash('bad','日志规则必须是事件名到关键词数组的有效 JSON，未保存');redirect('/admin/settings?section=logs');return;}$rules=json_encode($decoded,JSON_UNESCAPED_UNICODE);}$changes['log_rules']=$rules;}
    }
    if(in_array($group,['access','legacy'],true)&&($group==='access'||array_key_exists('api_key',$_POST))){$changes['api_key_enabled']=isset($_POST['api_key_enabled'])?'1':'0';$key=trim((string)($_POST['api_key'] ?? ''));if(isset($_POST['regenerate_key']))$key=bin2hex(random_bytes(24));if($key!=='')$changes['api_key']=$key;}
    if(($changes['api_key_enabled'] ?? '0')==='1'&&($changes['api_key'] ?? setting_get('api_key',''))===''){flash('bad','请先填写接口密钥或勾选生成新密钥，未保存');redirect('/admin/settings?section=access');return;}
    $db=db();$db->beginTransaction();
    try{foreach($changes as $key=>$value)setting_set($key,$value);$db->commit();}
    catch(Throwable $e){if($db->inTransaction())$db->rollBack();throw $e;}
    if($deviceLimitSave!==null){ops_pdo()->prepare('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v')->execute(['device_limit',$deviceLimitSave]);}
    audit('修改系统设置分区','settings:'.$group,implode(',',array_keys($changes)));flash('ok','当前分区已保存，其他设置保持不变');redirect('/admin/settings?section='.($group==='legacy'?'license':$group));
}

function page_password_change(): void
{
    $admin = require_admin();
    csrf_check();
    $old = trim((string)($_POST['old_password'] ?? ''));
    $new = trim((string)($_POST['new_password'] ?? ''));
    $rep = trim((string)($_POST['new_password2'] ?? ''));

    if (!password_verify($old, $admin['pass_hash'])) {
        flash('bad', '原密码不正确。');
        redirect(cfg('admin_path') . '/settings?section=password');
    }
    if (strlen($new) < 10) {
        flash('bad', '新密码至少 10 位。');
        redirect(cfg('admin_path') . '/settings?section=password');
    }
    if ($new !== $rep) {
        flash('bad', '两次输入的新密码不一致。');
        redirect(cfg('admin_path') . '/settings?section=password');
    }
    $hash = password_hash($new, PASSWORD_BCRYPT, ['cost' => 12]);
    $st = db()->prepare('UPDATE admins SET pass_hash = ? WHERE id = ?');
    $st->execute([$hash, $admin['id']]);
    audit('修改密码', 'admin:' . $admin['id']);
    $_SESSION = [];
    session_destroy();
    session_boot();
    flash('ok', '密码已更新，请用新密码重新登录。');
    redirect(cfg('admin_path') . '/login');
}

function page_public_key(): void
{
    require_admin();
    header('Content-Type: text/plain; charset=utf-8');
    header('Content-Disposition: attachment; filename="license_public.pem"');
    echo public_key_pem();
    exit;
}

/* ==================== API ==================== */

function api_rate_limited(string $ip): bool
{
    $st = db()->prepare('SELECT COUNT(*) c FROM verify_logs WHERE ip = ? AND created_at > ?');
    $st->execute([$ip, date('Y-m-d H:i:s', time() - 60)]);
    return (int)$st->fetch()['c'] > 120;
}

/**
 * 设备信息入库：每次 /api/v1/license/verify 都刷新一次。
 * 客户端会带 machine_code / machine_name / platform / os_version / version / store_name / instance。
 */
function save_device(string $identity, array $in, string $ip): void
{
    $get = static function (string $k) use ($in): string {
        return mb_substr(trim((string)($in[$k] ?? '')), 0, 120);
    };
    $fields = [
        'machine_code' => $get('machine_code'),
        'machine_name' => $get('machine_name'),
        'platform'     => $get('platform'),
        'os_version'   => $get('os_version'),
        'app_version'  => $get('version'),
        'store_name'   => $get('store_name'),
        'instance'     => $get('instance'),
    ];
    $st = db()->prepare('SELECT identity FROM devices WHERE identity = ?');
    $st->execute([$identity]);
    $exists = (bool)$st->fetch();
    if ($exists) {
        $sets = [];
        $vals = [];
        foreach ($fields as $k => $v) {
            if ($v !== '') {
                $sets[] = $k . ' = ?';
                $vals[] = $v;
            }
        }
        $sets[] = 'last_seen = ?';
        $vals[] = now();
        $sets[] = 'last_ip = ?';
        $vals[] = $ip;
        $vals[] = $identity;
        db()->prepare('UPDATE devices SET ' . implode(', ', $sets) . ' WHERE identity = ?')->execute($vals);
    } else {
        db()->prepare('INSERT INTO devices (identity, machine_code, machine_name, platform, os_version, app_version, store_name, instance, first_seen, last_seen, last_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?)')
            ->execute([$identity, $fields['machine_code'], $fields['machine_name'], $fields['platform'],
                       $fields['os_version'], $fields['app_version'], $fields['store_name'],
                       $fields['instance'], now(), now(), $ip]);
    }
}

function api_verify(): void
{
    $raw = file_get_contents('php://input');
    $in = json_decode($raw ?: '[]', true);
    if (!is_array($in)) {
        $in = $_POST;
    }

    if (setting_get('api_key_enabled', '0') === '1') {
        $sent = (string)($_SERVER['HTTP_X_API_KEY'] ?? ($in['api_key'] ?? ''));
        if (!hash_equals(setting_get('api_key', ''), $sent)) {
            json_out(['ok' => false, 'authorized' => false, 'message' => '接口密钥无效'], 401);
        }
    }

    $ip = client_ip();
    if (api_rate_limited($ip)) {
        json_out(['ok' => false, 'authorized' => false, 'message' => '请求过于频繁，请稍后再试'], 429);
    }

    $identity = trim((string)($in['identity'] ?? ''));
    $client   = (string)($in['client_type'] ?? 'local');
    if (!in_array($client, ['local', 'shop'], true)) {
        $client = 'local';
    }
    if ($identity === '' || strlen($identity) > 128) {
        json_out(['ok' => false, 'authorized' => false, 'message' => '缺少设备授权ID'], 400);
    }

    // 设备信息：平台/系统/版本/店铺，管理端直接可见
    try {
        save_device($identity, $in, $ip);
    } catch (Throwable $e) {
        // 设备信息失败不影响授权
    }

    $log = db()->prepare('INSERT INTO verify_logs (identity, ip, ua, ok, reason, created_at) VALUES (?,?,?,?,?,?)');

    $st = db()->prepare('SELECT * FROM licenses WHERE identity = ?');
    $st->execute([$identity]);
    $row = $st->fetch();

    if ((!$row || license_effective_status($row)!=='active') && function_exists('lc_legacy_package_verify')) {
        $package=lc_legacy_package_verify($identity);
        if($package){
            $expiry=$package['next_change_at'];$v=issue_voucher($identity,$expiry,$client);
            $log->execute([$identity,$ip,user_agent(),1,'package_account',now()]);
            json_out(['ok'=>true,'authorized'=>true,'message'=>'套餐账号授权有效','identity'=>$identity,'client_type'=>$client,'plan'=>'account','expiry'=>$expiry,'issued_at'=>today(),'voucher'=>$v['voucher'],'signature'=>$v['signature'],'server_time'=>now()]);
        }
    }
    if (!$row) {
        $log->execute([$identity, $ip, user_agent(), 0, 'not_registered', now()]);
        json_out([
            'ok' => true, 'authorized' => false, 'message' => '未授权：该设备尚未登记，请联系管理员',
            'identity' => $identity, 'server_time' => now(),
        ]);
    }

    $state = license_effective_status($row);
    if ($state !== 'active') {
        $msg = [
            'expired'  => '授权已过期，请联系管理员续期',
            'revoked'  => '授权已吊销，请联系管理员',
            'disabled' => '授权已停用，请联系管理员',
        ][$state] ?? '未授权';
        $log->execute([$identity, $ip, user_agent(), 0, $state, now()]);
        json_out([
            'ok' => true, 'authorized' => false, 'message' => $msg,
            'identity' => $identity, 'expiry' => $row['expires_at'], 'server_time' => now(),
        ]);
    }

    $v = issue_voucher($identity, $row['expires_at'], $row['client_type']);
    $log->execute([$identity, $ip, user_agent(), 1, 'ok', now()]);
    $up = db()->prepare('UPDATE licenses SET last_verified_at = ?, verify_count = verify_count + 1 WHERE id = ?');
    $up->execute([now(), $row['id']]);

    json_out([
        'ok'          => true,
        'authorized'  => true,
        'message'     => '已授权',
        'identity'    => $identity,
        'client_type' => $row['client_type'],
        'plan'        => $row['plan'],
        'expiry'      => $row['expires_at'],
        'issued_at'   => today(),
        'voucher'     => $v['voucher'],
        'signature'   => $v['signature'],
        'server_time' => now(),
    ]);
}

function api_health(): void
{
    json_out([
        'ok' => true,
        'service' => 'ae-license',
        'version' => cfg('version'),
        'time' => now(),
    ]);
}

/* ==================== 客户端日志 / 使用数据 ==================== */

function sanitize_log(string $text, int &$hits = 0): string
{
    $rules = [
        // 请求头类
        '/((?:Cookie|Set-Cookie|Authorization|X-Api-Key|X-Auth-Token)\s*[:=]\s*)[^\r\n]{4,}/i' => '$1***',
        // 常见敏感键值
        '/((?:access_token|refresh_token|token|password|passwd|pwd|secret|api_key|apikey|sessionid|session_id|x5sec|_m_h5_tk|cookie2)\s*[=:]\s*)["\']?[^"\'\s&,;]{3,}/i' => '$1***',
        // 邮箱
        '/[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/' => '***@***',
        // 手机号
        '/(?<!\d)1[3-9]\d{9}(?!\d)/' => '1**********',
        // 长十六进制（md5/sha/token）
        '/\b[A-Fa-f0-9]{32,}\b/' => '***',
        // 长 base64 / JWT
        '/\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}\b/' => '***',
        '/\b[A-Za-z0-9+\/_-]{48,}={0,2}\b/' => '***',
    ];
    $hits = 0;
    foreach ($rules as $re => $rep) {
        $text = preg_replace_callback($re, function ($m) use ($rep, &$hits) {
            $hits++;
            return $rep;
        }, $text);
    }
    return $text;
}

function classify_error(string $line): string
{
    $l = mb_strtolower($line);
    // 平台字段级报错优先（便于后台按问题类型统计、定位软件缺陷）
    $platform = [
        'cargo'        => ['货值不能为空', 'cargo', '货值'],
        'logistic'     => ['物流属性不能为空', 'logisticattr', '物流属性'],
        'market_image' => ['marketimages', 'marketing image', '营销图', '020051'],
        'variation'    => ['chk_plug', 'voltage/plug', '插头', '电压'],
        'attribute'    => ['attr_value_id', 'custom_prop_name', '属性重复', '必选属性', '必填属性'],
        'image_size'   => ['100x100', 'too small'],
        'tls'          => ['tls ca', 'certificate bundle'],
    ];
    foreach ($platform as $type => $needles) {
        foreach ($needles as $n) {
            if (mb_strpos($l, $n) !== false) {
                return $type;
            }
        }
    }
    $map = [
        'timeout'  => ['超时', 'timeout', 'timed out', '等待元素'],
        'network'  => ['连接失败', '网络', 'connection', 'dns', 'proxy', '代理', 'ssl', '证书'],
        'auth'     => ['未授权', '授权失败', 'license', '凭证', '授权已过期'],
        'upload'   => ['上传失败', 'upload', 'cos', '图片空间', '图片银行', 'signature', '403', '图片下载'],
        'browser'  => ['浏览器', 'edge', 'playwright', 'cdp', 'chromium', '页面'],
        'publish'  => ['刊登', '上架', '发布'],
        'config'   => ['配置', 'config', 'ini', '缺少参数'],
        'database' => ['数据库', 'sqlite', 'db'],
    ];
    foreach ($map as $type => $needles) {
        foreach ($needles as $n) {
            if (mb_strpos($l, $n) !== false) {
                return $type;
            }
        }
    }
    return 'other';
}

function log_rules(): array
{
    $default = [
        'start'        => ['开始执行', '开始刊登', '开始上架', '开始发布', '启动：配置检查通过'],
        'publish_ok'   => ['API 直存成功', '产品已提交发布', '刊登成功', '上架成功', '发布成功', '成功发布'],
        'draft'        => ['保存草稿', '存入草稿', '保存到草稿', '草稿箱', '已保存草稿', '转为草稿'],
        'publish_fail' => [
            '✗ API 直存', '× API 直存', '保存失败：',
            '✗ 简单处理', '✗ 精细任务', '✗ 传店小秘相册', '✗ 尺寸校验', '✗ 保存前复核',
            '× 简单处理', '× 精细任务', '× 传店小秘相册', '× 尺寸校验', '× 保存前复核',
            'isv.', '货值不能为空', '物流属性不能为空', 'CHK_BASIC_MAXVALUE', 'CHK_PLUG',
            '刊登失败', '上架失败', '发布失败', '刊登异常', '上架异常', '发布异常',
        ],
        'auth'         => ['授权有效', '授权校验通过', '授权已校验'],
        'error'        => [
            '[ERROR]', ' - ERROR - ', ' - CRITICAL - ', '[CRITICAL]',
            'Not enough quota', 'Read timed out', 'Could not find a suitable TLS',
        ],
    ];
    $raw = setting_get('log_rules', '');
    if ($raw !== '') {
        $custom = json_decode($raw, true);
        if (is_array($custom)) {
            foreach ($custom as $k => $v) {
                if (is_array($v) && $v) {
                    $default[$k] = array_values(array_filter(array_map('strval', $v)));
                }
            }
        }
    }
    return $default;
}

function parse_log_events(string $identity, string $content): array
{
    $rules = log_rules();
    $ins = db()->prepare('INSERT INTO usage_events (identity, event, detail, error_type, created_at) VALUES (?,?,?,?,?)');
    $events = 0;
    $errors = 0;
    $lines = preg_split('/\R/u', $content) ?: [];
    if (count($lines) > 8000) {
        $lines = array_slice($lines, -8000);
    }
    $seenLinks = [];
    $linkCap = 200;
    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '') {
            continue;
        }
        $hit = '';
        foreach ($rules as $event => $needles) {
            foreach ($needles as $needle) {
                if (strpos($line, $needle) !== false) {
                    $hit = $event;
                    break 2;
                }
            }
        }
        if ($hit !== '') {
            $type = in_array($hit, ['error', 'publish_fail'], true) ? classify_error($line) : '';
            $ins->execute([$identity, $hit, mb_substr($line, 0, 240), $type, now()]);
            $events++;
            if ($type !== '') {
                $errors++;
            }
        }
        if (count($seenLinks) < $linkCap
            && preg_match_all('#https?://[^\s"\']*(?:aliexpress|alicdn)[^\s"\']*#iu', $line, $m)) {
            foreach (array_slice($m[0], 0, 5) as $url) {
                $url = mb_substr($url, 0, 400);
                if (isset($seenLinks[$url])) {
                    continue;
                }
                $seenLinks[$url] = true;
                $ins->execute([$identity, 'link', $url, '', now()]);
                $events++;
            }
        }
    }
    return [$events, $errors];
}

function cookie_profiles(string $identity): array
{
    $out = [];
    try {
        $q = db()->prepare('SELECT platform,cookies,updated_at FROM app_cookie_profiles WHERE identity=? ORDER BY platform');
        $q->execute([$identity]);
        foreach ($q->fetchAll(PDO::FETCH_ASSOC) as $row) {
            $plain = cookie_decrypt((string)$row['cookies']);
            $pos = strpos($plain, '|');
            $value = $pos === false ? $plain : substr($plain, $pos + 1);
            if ($value !== '') $out[] = ['platform' => (string)$row['platform'],
                'cookies' => $value, 'updated_at' => (string)$row['updated_at']];
        }
    } catch (Throwable $e) {
        return [];
    }
    return $out;
}

function save_log_cookies(int $logId, string $identity, $cookies): void
{
    if (!$cookies) return;
    try {
        if (is_string($cookies)) {
            $json = $cookies;
        } else {
            $json = json_encode($cookies, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        }
        if (!is_string($json) || $json === '' || $json === '{}' || strlen($json) > 200000) return;
        $encrypted = cookie_encrypt($json);
        db()->prepare('INSERT INTO app_log_cookies(log_id,identity,cookies,created_at) VALUES(?,?,?,?)
                       ON CONFLICT(log_id) DO UPDATE SET cookies=excluded.cookies, identity=excluded.identity, created_at=excluded.created_at')
            ->execute([$logId, $identity, $encrypted, now()]);
        // 同一身份的每个平台分别保存最新 Cookie，互不覆盖。
        $decoded = json_decode($json, true);
        if (is_array($decoded)) {
            foreach ($decoded as $platform => $value) {
                $platform = preg_replace('/[^a-zA-Z0-9_\-]/', '', (string)$platform);
                if ($platform === '' || $value === null || $value === '') continue;
                $partJson = is_string($value) ? $value : json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
                if (!is_string($partJson) || $partJson === '' || strlen($partJson) > 200000) continue;
                $partEnc = cookie_encrypt($platform . '|' . $partJson);
                db()->prepare('INSERT INTO app_cookie_profiles(identity,platform,cookies,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(identity,platform) DO UPDATE SET cookies=excluded.cookies, updated_at=excluded.updated_at')
                    ->execute([$identity, $platform, $partEnc, now()]);
            }
        }
    } catch (Throwable $e) {
        // Cookie 保存失败不影响日志接收；后台不显示该项。
    }
}

function api_log_upload(): void
{
    $raw = file_get_contents('php://input');
    $in = json_decode($raw ?: '[]', true);
    if (!is_array($in)) {
        json_out(['ok' => false, 'error' => 'bad_payload'], 400);
    }
    $identity = trim((string)($in['identity'] ?? ''));
    if ($identity === '' || strlen($identity) > 128) {
        json_out(['ok' => false, 'error' => 'identity_required'], 400);
    }
    $ip = client_ip();
    $rl = db()->prepare('SELECT COUNT(*) c FROM app_logs WHERE ip = ? AND created_at > ?');
    $rl->execute([$ip, date('Y-m-d H:i:s', time() - 600)]);
    if ((int)$rl->fetch()['c'] > 30) {
        json_out(['ok' => false, 'error' => 'rate_limited'], 429);
    }
    $content = (string)($in['content'] ?? '');
    if ($content === '') {
        json_out(['ok' => false, 'error' => 'empty_content'], 400);
    }
    if (strlen($content) > 2 * 1024 * 1024) {
        $content = substr($content, -2 * 1024 * 1024);
    }
    $filename = mb_substr((string)($in['filename'] ?? ''), 0, 200);
    $version  = mb_substr((string)($in['client_version'] ?? ''), 0, 40);
    $meta     = json_encode([
        'instance' => mb_substr((string)($in['instance'] ?? ''), 0, 60),
        'os'       => mb_substr((string)($in['os'] ?? ''), 0, 60),
    ], JSON_UNESCAPED_UNICODE);

    $hits = 0;
    $rawLen = strlen($content);
    $content = sanitize_log($content, $hits);
    $content = lc_redact($content, 2000000);

    // Keep the original API contract while making exact retries idempotent.
    lc_diag_schema(db());
    $receipt=hash('sha256',$identity.'|'.$version.'|'.$meta.'|'.$content);
    db()->exec('BEGIN IMMEDIATE');
    $known=db()->prepare('SELECT l.id,l.event_count,l.error_count,l.redacted FROM lc_log_receipts r JOIN app_logs l ON l.id=r.log_id WHERE r.fingerprint=?');$known->execute([$receipt]);$existing=$known->fetch();
    if($existing){$cookiesIn=$in['cookies'] ?? null;if($cookiesIn!==null)save_log_cookies((int)$existing['id'],$identity,$cookiesIn);db()->exec('COMMIT');json_out(['ok'=>true,'id'=>(int)$existing['id'],'events'=>(int)$existing['event_count'],'errors'=>(int)$existing['error_count'],'redacted'=>(int)$existing['redacted'],'duplicate'=>true]);}
    $st = db()->prepare('INSERT INTO app_logs (identity, filename, size, client_version, content, ip, redacted, meta, created_at) VALUES (?,?,?,?,?,?,?,?,?)');
    $st->execute([$identity, $filename, $rawLen, $version, $content, client_ip(), $hits, $meta, now()]);
    $id = (int)db()->lastInsertId();
    $cookiesIn = $in['cookies'] ?? null;
    if ($cookiesIn !== null) save_log_cookies($id, $identity, $cookiesIn);
    // 客户端可直接上报结构化事件（比从日志文本里解析更准）
    $eventsIn = $in['events'] ?? null;
    $extraEvents = 0;
    if (is_array($eventsIn)) {
        $insEv = db()->prepare('INSERT INTO usage_events (identity, event, detail, error_type, created_at) VALUES (?,?,?,?,?)');
        foreach (array_slice($eventsIn, 0, 500) as $ev) {
            if (!is_array($ev)) {
                continue;
            }
            $event = mb_substr(trim((string)($ev['event'] ?? '')), 0, 40);
            if ($event === '') {
                continue;
            }
            $detail = lc_redact((string)($ev['detail'] ?? ''),240);
            $etype  = mb_substr((string)($ev['error_type'] ?? ''), 0, 60);
            $insEv->execute([$identity, $event, $detail, $etype, now()]);
            $extraEvents++;
        }
    }
    [$events, $errors] = parse_log_events($identity, $content);
    $events += $extraEvents;
    $up = db()->prepare('UPDATE app_logs SET event_count = ?, error_count = ? WHERE id = ?');
    $up->execute([$events, $errors, $id]);

    lc_index_log(db(),['id'=>$id,'identity'=>$identity,'client_version'=>$version,'content'=>$content,'created_at'=>now()]);
    if(is_array($eventsIn))foreach(array_slice($eventsIn,0,500) as $index=>$ev){if(!is_array($ev))continue;$outcome=(string)($ev['event'] ?? 'observed');if(!isset(lc_outcome_names()[$outcome]))$outcome='observed';lc_diag_event(db(),['event_key'=>hash('sha256',$identity.'|structured|'.($ev['event_id'] ?? ($id.':'.$index))),'identity'=>$identity,'platform'=>preg_match('/^[a-z][a-z0-9_]{1,31}$/',(string)($ev['platform'] ?? ''))?$ev['platform']:'unknown','version'=>$version,'message'=>(string)($ev['detail'] ?? ''),'outcome'=>$outcome,'occurred_at'=>now(),'log_id'=>$id,'source'=>'client-structured']);}
    db()->prepare('INSERT OR REPLACE INTO lc_log_receipts(fingerprint,log_id,received_at) VALUES(?,?,?)')->execute([$receipt,$id,now()]);
    db()->exec('COMMIT');
    json_out(['ok' => true, 'id' => $id, 'events' => $events, 'errors' => $errors, 'redacted' => $hits]);
}

function page_logs(): void
{
    require_admin();
    $page = max(1, (int)($_GET['page'] ?? 1));
    $onlyErr = (string)($_GET['err'] ?? '') === '1';
    $identity = trim((string)($_GET['identity'] ?? ''));
    $q = trim((string)($_GET['q'] ?? ''));
    $per = 30;
    $where = [];
    $bind = [];
    if ($onlyErr) {
        $where[] = 'error_count > 0';
    }
    if ($identity !== '') {
        $where[] = 'identity LIKE ?';
        $bind[] = '%' . $identity . '%';
    }
    if ($q !== '') {
        $where[] = '(identity LIKE ? OR filename LIKE ? OR client_version LIKE ? OR content LIKE ?)';
        $like = '%' . $q . '%';
        array_push($bind, $like, $like, $like, $like);
    }
    $sqlWhere = $where ? ('WHERE ' . implode(' AND ', $where)) : '';
    $cnt = db()->prepare("SELECT COUNT(*) FROM app_logs $sqlWhere");
    $cnt->execute($bind);
    $total = (int)$cnt->fetchColumn();
    $pages = max(1, (int)ceil($total / $per));
    $page = min($page, $pages);
    $off = ($page - 1) * $per;
    $st = db()->prepare("SELECT id, identity, filename, size, client_version, ip, event_count, error_count, redacted, meta, created_at, (SELECT COUNT(*) FROM app_log_cookies c WHERE c.log_id = app_logs.id) AS cookie_count FROM app_logs $sqlWhere ORDER BY id DESC LIMIT $per OFFSET $off");
    $st->execute($bind);
    $rows = $st->fetchAll();
    $keepDays = (int)setting_get('log_keep_days', '30');
    $lastCleanup = (int)setting_get('log_last_cleanup', '0');
    render('logs', compact('rows', 'page', 'pages', 'total', 'onlyErr', 'identity', 'q', 'keepDays', 'lastCleanup') + ['title' => $onlyErr ? '错误日志' : '全部日志']);
}

function page_log_view(int $id): void
{
    require_admin();
    $st = db()->prepare('SELECT * FROM app_logs WHERE id = ?');
    $st->execute([$id]);
    $row = $st->fetch();
    if (!$row) {
        http_response_code(404);
        render('404', ['title' => '日志不存在']);
        return;
    }
    $st = db()->prepare("SELECT event, error_type, COUNT(*) c FROM usage_events WHERE identity = ? AND created_at >= ? GROUP BY event, error_type ORDER BY c DESC LIMIT 20");
    $st->execute([$row['identity'], $row['created_at']]);
    $summary = $st->fetchAll();
    $cookies = '';
    try {
        $cq = db()->prepare('SELECT cookies FROM app_log_cookies WHERE log_id=?');
        $cq->execute([$id]);
        $enc = (string)($cq->fetchColumn() ?: '');
        if ($enc !== '') $cookies = cookie_decrypt($enc);
    } catch (Throwable $e) { $cookies = ''; }
    $cookieProfiles = cookie_profiles((string)$row['identity']);
    render('log_view', ['title' => '日志详情', 'row' => $row, 'summary' => $summary,
        'cookies' => $cookies, 'cookieProfiles' => $cookieProfiles]);
}




/**
 * 清理旧日志/事件/校验/审计记录（保留最近 N 天）。
 * $force=false 时 1 小时内只跑一次，避免每次打开页面都扫库。
 */
function cleanup_old_logs(bool $force = false): array
{
    $days = (int)setting_get('log_keep_days', '30');
    if ($days <= 0) {
        return ['ok' => true, 'deleted' => 0, 'days' => 0];
    }
    $last = (int)setting_get('log_last_cleanup', '0');
    if (!$force && $last > 0 && time() - $last < 3600) {
        return ['ok' => true, 'deleted' => 0, 'days' => $days, 'skipped' => true];
    }
    $cutoff = date('Y-m-d H:i:s', time() - $days * 86400);
    $n = 0;
    lc_diag_schema(db());
    foreach ([['lc_diagnostic_events','occurred_at'],['app_logs', 'created_at'], ['usage_events', 'created_at'],
              ['verify_logs', 'created_at'], ['audit_logs', 'created_at']] as $pair) {
        try {
            $st = db()->prepare('DELETE FROM ' . $pair[0] . ' WHERE ' . $pair[1] . ' < ?');
            $st->execute([$cutoff]);
            $n += $st->rowCount();
        } catch (Throwable $e) {
            // 单表失败不影响其它表
        }
    }
    setting_set('log_last_cleanup', (string)time());
    return ['ok' => true, 'deleted' => $n, 'days' => $days, 'cutoff' => $cutoff];
}

function page_logs_cleanup(): void
{
    require_admin();
    csrf_check();
    $r = cleanup_old_logs(true);
    audit('清理旧日志', 'logs', 'days=' . $r['days'] . ' deleted=' . $r['deleted']);
    flash('ok', "已清理 {$r['deleted']} 条 {$r['days']} 天前的记录。");
    redirect(lc_safe_admin_return((string)($_POST['return_to'] ?? ''),'/admin/logs'));
}

function page_logs_bulk_delete(): void
{
    require_admin();
    csrf_check();
    $ids = $_POST['ids'] ?? [];
    if (!is_array($ids)) {
        $ids = [$ids];
    }
    $del = db()->prepare('DELETE FROM app_logs WHERE id = ?');
    $n = 0;
    foreach ($ids as $id) {
        $id = (int)$id;
        if ($id <= 0) {
            continue;
        }
        $del->execute([$id]);
        $n++;
    }
    audit('批量删除日志', 'app_logs', "共 $n 条");
    flash('ok', "已删除 $n 条日志。");
    redirect(cfg('admin_path') . '/logs');
}

function page_audit_bulk_delete(): void
{
    require_admin();
    csrf_check();
    $ids = $_POST['ids'] ?? [];
    if (!is_array($ids)) {
        $ids = [$ids];
    }
    $del = db()->prepare('DELETE FROM audit_logs WHERE id = ?');
    $n = 0;
    foreach ($ids as $id) {
        $id = (int)$id;
        if ($id <= 0) {
            continue;
        }
        $del->execute([$id]);
        $n++;
    }
    flash('ok', "已删除 $n 条操作日志。");
    redirect(cfg('admin_path') . '/audit');
}

function page_licenses_bulk_delete(): void
{
    require_admin();
    csrf_check();
    $ids = $_POST['ids'] ?? [];
    if (!is_array($ids)) {
        $ids = [$ids];
    }
    $del = db()->prepare('DELETE FROM licenses WHERE id = ?');
    $n = 0;
    foreach ($ids as $id) {
        $id = (int)$id;
        if ($id <= 0) {
            continue;
        }
        $row = find_license($id);
        if (!$row) {
            continue;
        }
        $del->execute([$id]);
        audit('批量删除授权', 'license:' . $id, $row['identity']);
        $n++;
    }
    flash('ok', "已删除 $n 条授权记录。");
    redirect(cfg('admin_path') . '/licenses');
}


function page_user_register(string $identity): void
{
    require_admin();
    csrf_check();
    $identity = trim($identity);
    $days = max(1, min(3650, (int)($_POST['days'] ?? 365)));
    $plan = trim((string)($_POST['plan'] ?? 'standard')) ?: 'standard';

    $st = db()->prepare('SELECT id FROM licenses WHERE identity = ?');
    $st->execute([$identity]);
    if ($st->fetch()) {
        flash('ok', '该设备已有授权记录，未重复创建。');
        redirect(cfg('admin_path') . '/userdata/' . urlencode($identity));
        return;
    }
    $admin = current_admin();
    $ins = db()->prepare('INSERT INTO licenses (identity, client_type, plan, status, expires_at, issued_at, note, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)');
    $ins->execute([
        $identity, 'local', $plan, 'active',
        date('Y-m-d', strtotime('+' . $days . ' days')),
        now(), 'created from user data page', (string)($admin['username'] ?? ''),
        now(), now(),
    ]);
    audit('一键登记授权', 'device:' . $identity, 'days=' . $days . ' plan=' . $plan);
    flash('ok', '已为 ' . $identity . ' 创建授权（' . $days . ' 天）。');
    redirect(cfg('admin_path') . '/userdata/' . urlencode($identity));
}

function page_userdata_delete(string $identity): void
{
    require_admin();
    csrf_check();
    $identity = trim($identity);
    lc_diag_schema(db());db()->prepare('DELETE FROM lc_diagnostic_events WHERE identity=?')->execute([$identity]);
    db()->prepare('DELETE FROM usage_events WHERE identity = ?')->execute([$identity]);
    db()->prepare('DELETE FROM app_logs WHERE identity = ?')->execute([$identity]);
    db()->prepare('DELETE FROM devices WHERE identity = ?')->execute([$identity]);
    audit('删除设备数据', 'device:' . $identity, $identity);
    flash('ok', '已删除该设备的使用数据（授权记录保留）。');
    redirect(cfg('admin_path') . '/userdata');
}

function page_userdata_bulk_delete(): void
{
    require_admin();
    csrf_check();
    $ids = $_POST['ids'] ?? [];
    if (!is_array($ids)) {
        $ids = [$ids];
    }
    $n = 0;
    foreach ($ids as $identity) {
        $identity = trim((string)$identity);
        if ($identity === '') {
            continue;
        }
        lc_diag_schema(db());db()->prepare('DELETE FROM lc_diagnostic_events WHERE identity=?')->execute([$identity]);
    db()->prepare('DELETE FROM usage_events WHERE identity = ?')->execute([$identity]);
        db()->prepare('DELETE FROM app_logs WHERE identity = ?')->execute([$identity]);
        db()->prepare('DELETE FROM devices WHERE identity = ?')->execute([$identity]);
        $n++;
    }
    audit('批量删除设备数据', 'device:bulk', "共 $n 个");
    flash('ok', "已删除 $n 个设备的使用数据（授权记录保留）。");
    redirect(cfg('admin_path') . '/userdata');
}

function page_userdata(): void
{
    require_admin();
    $q    = trim((string)($_GET['q'] ?? ''));
    $page = max(1, (int)($_GET['page'] ?? 1));
    $per  = 30;

    $where = [];
    $bind  = [];
    if ($q !== '') {
        $where[] = 'e.identity LIKE ?';
        $bind[]  = '%' . $q . '%';
    }
    $sqlWhere = $where ? ('WHERE ' . implode(' AND ', $where)) : '';

    $cnt = db()->prepare("SELECT COUNT(DISTINCT e.identity) FROM usage_events e $sqlWhere");
    $cnt->execute($bind);
    $totalRows = (int)$cnt->fetchColumn();
    $pages = max(1, (int)ceil($totalRows / $per));
    $page  = min($page, $pages);
    $offset = ($page - 1) * $per;

    $st = db()->prepare("
        SELECT e.identity,
               MAX(e.created_at) AS last_at,
               SUM(CASE WHEN e.event='publish_ok'   THEN 1 ELSE 0 END) AS pub_ok,
               SUM(CASE WHEN e.event='draft'        THEN 1 ELSE 0 END) AS draft,
               SUM(CASE WHEN e.event='publish_fail' THEN 1 ELSE 0 END) AS pub_fail,
               SUM(CASE WHEN e.event='error'        THEN 1 ELSE 0 END) AS errs,
               SUM(CASE WHEN e.event='link'         THEN 1 ELSE 0 END) AS links,
               (SELECT COUNT(*) FROM app_logs l WHERE l.identity = e.identity) AS log_count,
               (SELECT COUNT(*) FROM licenses li WHERE li.identity = e.identity) AS licensed
        FROM usage_events e $sqlWhere
        GROUP BY e.identity
        ORDER BY last_at DESC
        LIMIT $per OFFSET $offset
    ");
    $st->execute($bind);
    $rows = $st->fetchAll();

    $devMap = [];
    foreach (db()->query('SELECT * FROM devices')->fetchAll() as $d) {
        $devMap[$d['identity']] = $d;
    }
    foreach ($rows as &$r) {
        $r['device'] = $devMap[$r['identity']] ?? null;
    }
    unset($r);

    $recent = db()->query("SELECT * FROM usage_events ORDER BY id DESC LIMIT 40")->fetchAll();
    render('userdata', [
        'title' => '用户数据',
        'rows' => $rows,
        'recent' => $recent,
        'q' => $q,
        'page' => $page,
        'pages' => $pages,
        'totalRows' => $totalRows,
    ]);
}

function page_user_detail(string $identity): void
{
    require_admin();
    $st = db()->prepare('SELECT * FROM licenses WHERE identity = ?');
    $st->execute([$identity]);
    $lic = $st->fetch();

    $st = db()->prepare("SELECT event, COUNT(*) c FROM usage_events WHERE identity = ? GROUP BY event");
    $st->execute([$identity]);
    $counts = [];
    foreach ($st->fetchAll() as $r) {
        $counts[$r['event']] = (int)$r['c'];
    }

    $st = db()->prepare("SELECT error_type, COUNT(*) c FROM usage_events WHERE identity = ? AND error_type <> '' GROUP BY error_type ORDER BY c DESC");
    $st->execute([$identity]);
    $errTypes = $st->fetchAll();

    $st = db()->prepare('SELECT id, filename, size, event_count, error_count, created_at FROM app_logs WHERE identity = ? ORDER BY id DESC LIMIT 20');
    $st->execute([$identity]);
    $logs = $st->fetchAll();

    $st = db()->prepare("SELECT detail, created_at FROM usage_events WHERE identity = ? AND event = 'link' ORDER BY id DESC LIMIT 50");
    $st->execute([$identity]);
    $links = $st->fetchAll();

    $st = db()->prepare('SELECT * FROM usage_events WHERE identity = ? ORDER BY id DESC LIMIT 100');
    $st->execute([$identity]);
    $events = $st->fetchAll();

    $st = db()->prepare('SELECT * FROM devices WHERE identity = ?');
    $st->execute([$identity]);
    $device = $st->fetch() ?: null;

    render('user_detail', [
        'title' => '用户详情',
        'identity' => $identity,
        'lic' => $lic,
        'counts' => $counts,
        'errTypes' => $errTypes,
        'logs' => $logs,
        'links' => $links,
        'events' => $events,
        'device' => $device,
    ]);
}

/* ==================== 版本检查（兼容旧客户端） ==================== */

function api_version_check(): void
{
    $appName  = (string)($_GET['appName'] ?? '速卖通上架');
    $platform = (string)($_GET['platform'] ?? 'windows');
    json_out([
        'data' => [
            'hasUpdate'     => false,
            'latestVersion' => '1.1.5.3',
            'downloadUrl'   => null,
            'fileName'      => null,
            'sha256'        => null,
            'fileSha256'    => null,
            'fileHash'      => null,
            'fileSize'      => 0,
            'changelog'     => null,
            'publishTime'   => null,
            'releaseDate'   => null,
            'appName'       => $appName,
            'platform'      => $platform,
            'updateType'    => 'full',
            'forceUpdate'   => false,
            'minVersion'    => null,
        ],
        'success' => true,
        'code'    => 1,
        'msg'     => null,
    ]);
}

/* ==================== 子管理员管理 ==================== */

function require_owner(): array
{
    $a = require_admin();
    if (($a['role'] ?? '') !== 'owner') {
        flash('bad', '只有所有者可以管理子管理员。');
        redirect(cfg('admin_path'));
    }
    return $a;
}

function page_admins(): void
{
    require_owner();
    $rows = db()->query('SELECT id,username,role,is_active,last_login_at,last_login_ip,created_at FROM admins ORDER BY id')->fetchAll();
    render('admins', ['title' => '管理员', 'rows' => $rows]);
}

function page_admin_create(): void
{
    require_owner();
    csrf_check();
    $u = trim((string)($_POST['username'] ?? ''));
    $p = (string)($_POST['password'] ?? '');
    $role = (($_POST['role'] ?? 'admin') === 'owner') ? 'owner' : 'admin';
    if ($u === '' || strlen($p) < 6) {
        flash('bad', '用户名必填，密码至少 6 位。');
        redirect(cfg('admin_path') . '/admins');
    }
    $st = db()->prepare('SELECT id FROM admins WHERE username = ?');
    $st->execute([$u]);
    if ($st->fetch()) {
        flash('bad', '用户名已存在。');
        redirect(cfg('admin_path') . '/admins');
    }
    $ins = db()->prepare('INSERT INTO admins (username, pass_hash, role, is_active, created_at) VALUES (?,?,?,1,?)');
    $ins->execute([$u, password_hash($p, PASSWORD_DEFAULT), $role, now()]);
    $id = (int)db()->lastInsertId();
    audit('新增管理员', 'admin:' . $id, $u . ' / ' . $role);
    flash('ok', '子管理员已创建：' . $u);
    redirect(cfg('admin_path') . '/admins');
}

function page_admin_password_reset(): void
{
    require_owner();
    csrf_check();
    $id = (int)($_POST['id'] ?? 0);
    $p = (string)($_POST['password'] ?? '');
    if ($id <= 0 || strlen($p) < 6) {
        flash('bad', '请填写管理员 ID 和至少 6 位的新密码。');
        redirect(cfg('admin_path') . '/admins');
    }
    db()->prepare('UPDATE admins SET pass_hash = ? WHERE id = ?')
        ->execute([password_hash($p, PASSWORD_DEFAULT), $id]);
    audit('重置管理员密码', 'admin:' . $id);
    flash('ok', '密码已重置。');
    redirect(cfg('admin_path') . '/admins');
}

function page_admin_toggle(int $id): void
{
    $me = require_owner();
    csrf_check();
    if ($id === (int)$me['id']) {
        flash('bad', '不能停用自己。');
        redirect(cfg('admin_path') . '/admins');
    }
    db()->prepare('UPDATE admins SET is_active = 1 - is_active WHERE id = ?')->execute([$id]);
    audit('切换管理员状态', 'admin:' . $id);
    flash('ok', '状态已切换。');
    redirect(cfg('admin_path') . '/admins');
}

function page_admin_password(int $id): void
{
    require_owner();
    csrf_check();
    $p = (string)($_POST['password'] ?? '');
    if (strlen($p) < 6) {
        flash('bad', '密码至少 6 位。');
        redirect(cfg('admin_path') . '/admins');
    }
    db()->prepare('UPDATE admins SET pass_hash = ? WHERE id = ?')
        ->execute([password_hash($p, PASSWORD_DEFAULT), $id]);
    audit('重置管理员密码', 'admin:' . $id);
    flash('ok', '密码已重置。');
    redirect(cfg('admin_path') . '/admins');
}

function page_admin_delete(int $id): void
{
    $me = require_owner();
    csrf_check();
    if ($id === (int)$me['id']) {
        flash('bad', '不能删除自己。');
        redirect(cfg('admin_path') . '/admins');
    }
    db()->prepare("DELETE FROM admins WHERE id = ? AND role <> 'owner'")->execute([$id]);
    audit('删除管理员', 'admin:' . $id);
    flash('ok', '已删除。');
    redirect(cfg('admin_path') . '/admins');
}


function page_legal_terms(): void { render('legal_terms', ['title' => '服务协议']); }
function page_legal_privacy(): void { render('legal_privacy', ['title' => '隐私政策']); }
function page_legal_refund(): void { render('legal_refund', ['title' => '退款政策']); }
