<?php
// ECDSA P-256 凭证签名
// 载荷格式（与客户端约定）：base64url(identity|expiry|issued|client_type)
// 签名格式：base64url(raw r||s 各 32 字节)

function key_dir(): string
{
    $d = (string)cfg('key_dir');
    if (!is_dir($d)) {
        mkdir($d, 0700, true);
    }
    return $d;
}

function ensure_keypair(): void
{
    $priv = key_dir() . '/private.pem';
    $pub  = key_dir() . '/public.pem';
    if (is_file($priv) && is_file($pub)) {
        return;
    }
    if (!function_exists('openssl_pkey_new')) {
        throw new RuntimeException('PHP openssl 扩展未启用');
    }
    $res = openssl_pkey_new([
        'private_key_type' => OPENSSL_KEYTYPE_EC,
        'curve_name'       => 'prime256v1',
    ]);
    if ($res === false) {
        throw new RuntimeException('生成密钥失败: ' . openssl_error_string());
    }
    openssl_pkey_export($res, $privPem);
    $details = openssl_pkey_get_details($res);
    file_put_contents($priv, $privPem, LOCK_EX);
    @chmod($priv, 0600);
    file_put_contents($pub, $details['key'], LOCK_EX);
    @chmod($pub, 0644);
}

function private_key()
{
    ensure_keypair();
    $pem = file_get_contents(key_dir() . '/private.pem');
    $k = openssl_pkey_get_private($pem);
    if ($k === false) {
        throw new RuntimeException('私钥不可用');
    }
    return $k;
}

function public_key_pem(): string
{
    ensure_keypair();
    return (string)file_get_contents(key_dir() . '/public.pem');
}

function b64url(string $bin): string
{
    return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

// DER(SEQUENCE{INTEGER r, INTEGER s}) -> raw r||s (32+32)
function der_to_raw(string $der, int $size = 32): string
{
    $pos = 0;
    if (ord($der[$pos++]) !== 0x30) {
        throw new RuntimeException('bad DER');
    }
    $len = ord($der[$pos++]);
    if ($len & 0x80) {
        $n = $len & 0x7f;
        $len = 0;
        for ($i = 0; $i < $n; $i++) {
            $len = ($len << 8) | ord($der[$pos++]);
        }
    }
    $out = '';
    for ($i = 0; $i < 2; $i++) {
        if (ord($der[$pos++]) !== 0x02) {
            throw new RuntimeException('bad DER int');
        }
        $l = ord($der[$pos++]);
        $v = substr($der, $pos, $l);
        $pos += $l;
        $v = ltrim($v, "\x00");
        $out .= str_pad($v, $size, "\x00", STR_PAD_LEFT);
    }
    return $out;
}

/**
 * 生成授权凭证
 * @return array{voucher:string, signature:string, payload:string}
 */
function issue_voucher(string $identity, string $expires, string $clientType): array
{
    $issued  = today();
    $payload = $identity . '|' . $expires . '|' . $issued . '|' . $clientType;
    $key     = private_key();
    if (!openssl_sign($payload, $der, $key, OPENSSL_ALGO_SHA256)) {
        throw new RuntimeException('签名失败: ' . openssl_error_string());
    }
    return [
        'voucher'   => b64url($payload),
        'signature' => b64url(der_to_raw($der)),
        'payload'   => $payload,
    ];
}
