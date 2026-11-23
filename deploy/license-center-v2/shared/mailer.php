<?php
declare(strict_types=1);
require_once __DIR__.'/entitlements.php';
function lc_setting(string $key,string $default=''): string {$q=lc_db()->prepare('SELECT v FROM settings WHERE k=?');$q->execute([$key]);$v=$q->fetchColumn();return $v===false?$default:(string)$v;}
function lc_smtp_send(string $to, string $subject, string $body): array {
    if(!filter_var($to,FILTER_VALIDATE_EMAIL))return [false,'邮箱格式不正确'];
    $host = trim(lc_setting('smtp_host'));
    if (!$host) return [false, '未配置 SMTP'];
    $port = (int)lc_setting('smtp_port', '465');
    $user = lc_setting('smtp_user');
    $pass = lc_setting('smtp_pass');
    $secure = lc_setting('smtp_secure', 'ssl');
    $from = lc_setting('smtp_from') ?: $user;
    $remote = ($secure === 'ssl' ? 'ssl://' : '').$host.':'.$port;
    $ctx = stream_context_create(['ssl' => ['verify_peer' => true, 'verify_peer_name' => true]]);
    $fp = @stream_socket_client($remote, $errno, $errstr, 12, STREAM_CLIENT_CONNECT, $ctx);
    if (!$fp) return [false, "连接失败：$errstr"];
    $read = function() use ($fp) {
        $data = '';
        while (($line = fgets($fp, 515)) !== false) {
            $data .= $line;
            if (isset($line[3]) && $line[3] === ' ') break;
        }
        return $data;
    };
    $cmd = function(string $c, string $expect = '2') use ($fp, $read) {
        fwrite($fp, $c."\r\n");
        $r = $read();
        return strpos($r, $expect) === 0 ? [true, $r] : [false, $r];
    };
    $read();
    [$ok, $r] = $cmd('EHLO auroralist', '2');
    if (!$ok) { fclose($fp); return [false, "EHLO 失败"]; }
    if ($secure === 'tls') {
        [$ok] = $cmd('STARTTLS', '2');
        if (!$ok) { fclose($fp); return [false, 'STARTTLS 失败']; }
        stream_socket_enable_crypto($fp, true, STREAM_CRYPTO_METHOD_TLS_CLIENT);
        [$ok] = $cmd('EHLO auroralist', '2');
        if (!$ok) { fclose($fp); return [false, 'EHLO(TLS) 失败']; }
    }
    if ($user) {
        [$ok] = $cmd('AUTH LOGIN', '3');
        if (!$ok) { fclose($fp); return [false, 'AUTH 失败']; }
        [$ok] = $cmd(base64_encode($user), '3');
        if (!$ok) { fclose($fp); return [false, '用户名失败']; }
        [$ok] = $cmd(base64_encode($pass), '2');
        if (!$ok) { fclose($fp); return [false, '密码失败']; }
    }
    [$ok] = $cmd('MAIL FROM:<'.$from.'>', '2');
    if (!$ok) { fclose($fp); return [false, 'MAIL FROM 失败']; }
    [$ok] = $cmd('RCPT TO:<'.$to.'>', '2');
    if (!$ok) { fclose($fp); return [false, 'RCPT TO 失败']; }
    [$ok] = $cmd('DATA', '3');
    if (!$ok) { fclose($fp); return [false, 'DATA 失败']; }
    $headers = 'From: AuroraList <'.$from.'>'."\r\n"
        .'To: <'.$to.'>'."\r\n"
        .'Subject: =?UTF-8?B?'.base64_encode($subject).'?='."\r\n"
        .'MIME-Version: 1.0'."\r\n"
        .'Content-Type: text/plain; charset=UTF-8'."\r\n"
        .'Content-Transfer-Encoding: base64'."\r\n\r\n";
    fwrite($fp, $headers.chunk_split(base64_encode($body))."\r\n.\r\n");
    $response=$read();
    fclose($fp);
    return strpos($response,'250')===0?[true,'']:[false,'邮件服务器未确认接收'];
}
