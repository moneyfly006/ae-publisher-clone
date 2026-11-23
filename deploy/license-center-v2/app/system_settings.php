<?php
declare(strict_types=1);
function lc_settings_sections(): array {return ['license'=>'授权默认值','logs'=>'日志与保留','rules'=>'日志识别效果','payment'=>'支付网关','mail'=>'邮件与验证','portal'=>'用户端设置','access'=>'接口访问','password'=>'登录密码','security'=>'两步验证','platforms'=>'平台与接口'];}
function lc_settings_section(): string {
    $path=parse_url($_SERVER['REQUEST_URI'] ?? '',PHP_URL_PATH);
    $aliases=['/admin/shop-pay'=>'payment','/admin/security'=>'security','/admin/platforms'=>'platforms','/admin/rules'=>'rules'];
    $value=(string)($_GET['section'] ?? ($aliases[$path] ?? 'license'));
    return isset(lc_settings_sections()[$value])?$value:'license';
}
function lc_is_settings_request(): bool {return in_array(parse_url($_SERVER['REQUEST_URI'] ?? '',PHP_URL_PATH),['/admin/settings','/admin/shop-pay','/admin/security','/admin/platforms','/admin/rules'],true);}
