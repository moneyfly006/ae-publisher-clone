<?php
declare(strict_types=1);

$appDir = getenv('AE_LICENSE_DIR') ?: '/www/server/ae-license';
require $appDir . '/app/bootstrap.php';
require $appDir . '/app/pages.php';
require $appDir . '/app/shop_admin.php';
require $appDir . '/app/shop_ops.php';
require $appDir . '/app/customer_center.php';
require $appDir . '/app/diagnostic_center.php';
require $appDir . '/app/system_settings.php';

$base = rtrim((string)cfg('base_path'), '/');
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
if ($base !== '' && strpos($path, $base) === 0) {
    $path = substr($path, strlen($base));
}
$path = '/' . trim($path, '/');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

// 安全响应头
header('X-Frame-Options: SAMEORIGIN');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: same-origin');
header('X-XSS-Protection: 0');

session_boot();

$routes = [
    ['GET',  '#^/$#',                          'page_landing'],
    ['GET',  '#^/admin/login$#',               'page_login_form'],
    ['POST', '#^/admin/login$#',               'page_login_post'],
    ['POST', '#^/admin/logout$#',              'page_logout'],
    ['GET',  '#^/admin/logout$#',              'page_logout_get'],
    ['GET',  '#^/admin$#',                     'page_diagnostics'],
    ['GET',  '#^/admin/licenses$#',            'page_licenses'],
    ['GET',  '#^/admin/licenses/new$#',        'page_license_new'],
    ['POST', '#^/admin/licenses$#',            'page_license_create'],
    ['GET',  '#^/admin/licenses/(\d+)$#',      'page_license_show'],
    ['GET',  '#^/admin/licenses/(\d+)/edit$#', 'page_license_edit'],
    ['POST', '#^/admin/licenses/(\d+)/update$#','page_license_update'],
    ['POST', '#^/admin/licenses/(\d+)/revoke$#','page_license_revoke'],
    ['POST', '#^/admin/licenses/(\d+)/renew$#', 'page_license_renew'],
    ['POST', '#^/admin/licenses/(\d+)/delete$#','page_license_delete'],
    ['GET',  '#^/admin/audit$#',               'page_audit'],
    ['GET',  '#^/admin/settings$#',            'page_settings'],
    ['POST', '#^/admin/settings$#',            'page_settings_save'],
    ['POST', '#^/admin/password$#',            'page_password_change'],
    ['GET',  '#^/admin/publickey$#',           'page_public_key'],
    ['GET',  '#^/admin/logs$#',                'page_logs'],
    ['GET',  '#^/admin/logs/(\d+)$#',           'page_log_view'],
    ['GET',  '#^/admin/userdata$#',            'page_userdata'],
    ['GET',  '#^/admin/userdata/([A-Za-z0-9._-]+)$#', 'page_user_detail'],
    ['POST', '#^/admin/userdata/([A-Za-z0-9._-]+)/register$#', 'page_user_register'],
    ['GET',  '#^/admin/admins$#',               'page_admins'],
    ['POST', '#^/admin/admins/create$#',         'page_admin_create'],
    ['POST', '#^/admin/admins/password-reset$#', 'page_admin_password_reset'],
    ['POST', '#^/admin/admins/(\d+)/toggle$#',   'page_admin_toggle'],
    ['POST', '#^/admin/admins/(\d+)/password$#', 'page_admin_password'],
    ['POST', '#^/admin/admins/(\d+)/delete$#',   'page_admin_delete'],
    ['GET',  '#^/admin/rules$#',                'page_rule_diag'],
    ['POST', '#^/admin/licenses/bulk-delete$#', 'page_licenses_bulk_delete'],
    ['POST', '#^/admin/logs/cleanup$#',         'page_logs_cleanup'],
    ['POST', '#^/admin/logs/bulk-delete$#',     'page_logs_bulk_delete'],
    ['POST', '#^/admin/audit/bulk-delete$#',    'page_audit_bulk_delete'],
    ['POST', '#^/admin/userdata$#',             'page_userdata_bulk_delete'],
    ['POST', '#^/admin/userdata/([A-Za-z0-9._-]+)/delete$#', 'page_userdata_delete'],
    ['POST', '#^/admin/shop-users/(\d+)/link-device$#', 'page_customer_link'],
    ['GET', '#^/admin/legacy-dashboard$#', 'page_dashboard'],
    ['GET', '#^/admin/customers$#', 'page_customers'],
    ['GET', '#^/admin/events$#', 'page_diagnostic_events'],
    ['GET', '#^/admin/issues/([a-f0-9]{64})$#', 'page_diagnostic_issue'],
    ['POST', '#^/admin/issues/([a-f0-9]{64})/update$#', 'page_issue_update'],
    ['GET', '#^/admin/platforms$#', 'page_platform_registry'],
    ['GET', '#^/api/v2/platforms$#', 'api_platform_registry'],
    ['POST', '#^/api/v2/client/events$#', 'api_client_events'],
    ['GET', '#^/admin/customer-logs$#', 'page_customer_logs'],
    ['GET', '#^/admin/customer-logs/(\d+)/(\d+)$#', 'page_customer_log'],
    ['GET', '#^/admin/customer-devices$#', 'page_customer_devices'],
    ['GET',  '#^/admin/shop-packages$#', 'page_shop_packages'],
    ['POST', '#^/admin/shop-packages$#', 'page_shop_packages_save'],
    ['GET',  '#^/admin/shop-pay$#',      'page_shop_pay'],
    ['POST', '#^/admin/shop-pay$#',      'page_shop_pay_save'],
    ['GET',  '#^/admin/shop-orders$#',   'page_shop_orders'],
    ['POST', '#^/admin/shop-orders/(\d+)/paid$#', 'page_shop_order_paid'],
    ['GET',  '#^/admin/shop-tickets$#', 'page_shop_tickets'],
    ['GET',  '#^/admin/shop-tickets/(\d+)$#', 'page_shop_ticket_detail'],
    ['POST', '#^/admin/shop-tickets/(\d+)/reply$#', 'page_shop_ticket_reply'],
    ['GET',  '#^/admin/shop-coupons$#', 'page_shop_coupons'],
    ['POST', '#^/admin/shop-coupons$#', 'page_shop_coupon_save'],
    ['GET',  '#^/terms$#', 'page_legal_terms'],
    ['GET',  '#^/privacy$#', 'page_legal_privacy'],
    ['GET',  '#^/refund$#', 'page_legal_refund'],
    ['GET',  '#^/admin/2fa$#', 'page_admin_2fa_form'],
    ['POST', '#^/admin/2fa$#', 'page_admin_2fa_post'],
    ['GET',  '#^/admin/security$#', 'page_admin_security'],
    ['POST', '#^/admin/security/2fa-enable$#', 'page_admin_totp_enable'],
    ['POST', '#^/admin/security/2fa-disable$#', 'page_admin_totp_disable'],
    ['GET',  '#^/admin/shop-reports$#', 'page_shop_reports'],
    ['GET',  '#^/admin/shop-export$#', 'page_shop_export'],
    ['GET',  '#^/admin/shop-invites$#', 'page_shop_invites'],
    ['GET',  '#^/admin/shop-users$#',    'page_shop_users'],
    ['GET',  '#^/admin/shop-users/(\d+)$#', 'page_shop_user_detail'],
    ['POST', '#^/admin/shop-users/(\d+)/update$#', 'page_shop_user_update'],
    ['POST', '#^/admin/shop-users/(\d+)/password$#', 'page_shop_user_password'],
    ['POST', '#^/admin/shop-users/(\d+)/balance$#', 'page_shop_user_balance'],
    ['POST', '#^/admin/shop-users/(\d+)/grant$#', 'page_shop_user_grant'],
    ['POST', '#^/admin/shop-users/(\d+)/revoke$#', 'page_shop_user_revoke'],
    ['POST', '#^/admin/shop-users/(\d+)/revoke-platform$#', 'page_shop_user_revoke_platform'],
    ['POST', '#^/admin/shop-users/(\d+)/device-limit$#', 'page_shop_user_device_limit'],
    ['POST', '#^/admin/shop-users/(\d+)/bind-device$#', 'page_shop_user_bind_device'],
    ['POST', '#^/admin/shop-users/(\d+)/delete$#', 'page_shop_user_delete'],

    ['GET',  '#^/admin/shop-licenses$#', 'page_shop_licenses'],
    ['POST', '#^/admin/shop-licenses/(\d+)/revoke$#', 'page_shop_license_revoke'],
    ['POST', '#^/api/v1/license/verify$#',     'api_verify'],
    ['POST', '#^/api/v1/log/upload$#',         'api_log_upload'],
    ['GET',  '#^/api/version/check$#',         'api_version_check'],
    ['GET',  '#^/api/v1/health$#',             'api_health'],
];

foreach ($routes as [$m, $re, $fn]) {
    if ($m !== $method) {
        continue;
    }
    if (preg_match($re, $path, $mm)) {
        $args = array_map(static function ($v) {
            return ctype_digit((string)$v) ? (int)$v : (string)$v;
        }, array_slice($mm, 1));
        $fn(...$args);
        exit;
    }
}

http_response_code(404);
if (strpos($path, '/api/') === 0) {
    json_out(['ok' => false, 'error' => 'not_found'], 404);
}
render('404', ['title' => '页面不存在']);
