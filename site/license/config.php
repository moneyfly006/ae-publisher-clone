<?php
// 授权网站配置（部署后请修改 ADMIN_PASS / EPAY_*）
return [
  'db' => __DIR__ . '/data/license.db',
  // 与桌面客户端一致的签名密钥；换密钥需要发新版客户端
  'sign_key' => 'aurora-list-platform-license-v1',
  'admin_user' => 'admin',
  'admin_pass' => 'change-me-now',
  // 易支付（new-api 后台使用同一套易支付配置，可直接复用）
  'epay_gateway' => 'https://pay.example.com/',
  'epay_pid' => '',
  'epay_key' => '',
  'epay_type' => 'alipay',
  'site_name' => 'AuroraList 授权中心',
  'site_url' => 'https://ali.moneyfly.club/license',
  'license_grace_days' => 7,
  'allow_mock_pay' => false, // 生产必须 false；本地测试可临时 true
];
