<?php
$path=parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH);
$public=getenv('LICENSE_TEST_PUBLIC');
putenv('SHOP_OPS='.dirname($public).'/app/shop_ops.php');
if(preg_match('/(?:^|\/)data(?:\/|$)|\.bak(?:\.|$)|\.sqlite|\.db(?:-|$)/i',$path)){http_response_code(403);exit;}
if($path==='/'){require $public.'/shop/index.php';exit;}
if($path==='/api.php'){require $public.'/shop/api.php';exit;}
if($path==='/api/license/entitlements'){require $public.'/shop/api/license/entitlements/index.php';exit;}
if(is_file($public.$path))return false;
require $public.'/index.php';
