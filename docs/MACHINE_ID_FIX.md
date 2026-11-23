# 机器码重复问题修复（2026-11-23）

## 原因
旧算法只取一个标识：
- Windows `MachineGuid`
- macOS `IOPlatformUUID`
- Linux `/etc/machine-id`
- 取不到时只用 `hostname|arch|system`

克隆虚拟机/系统镜像/命令读取失败时，这些值可能重复；兜底 hostname 更容易重复。
随后机器码只取 SHA256 前 16 位，也会放大重复概率。

## 修复（v1.0.8）
- 机器码改为**多硬件标识组合**：
  - Windows：MachineGuid + BIOS UUID 等；
  - macOS：IOPlatformUUID + IOPlatformSerialNumber + model/board-id；
  - Linux：machine-id + DMI product_uuid / board_serial / product_serial；
  - 全部失败才用 hostname/arch。
- 过滤 `default string`、全 0 UUID 等占位值；
- 仍显示 8-8 格式，保持后台登记习惯；
- **旧机器码继续作为授权兼容候选**：
  - 授权校验依次尝试新码、旧短码、旧完整哈希；
  - 账号 token 刷新失败时自动用旧机器码重试；
  - 已授权设备升级后不会被强制踢出，后台可逐步迁移到新码。
- 不把网卡 MAC 作为主标识，避免换网卡/虚拟网卡导致授权漂移。

## 验证
- 新增 `tests/test_machine_identity.py`：
  - 不同硬件组合机器码不同；
  - 同一硬件稳定；
  - 8-8 格式；
  - 旧机器码仍在兼容候选中。

## 旧客户（v1.0.5 设备授权码）升级说明
- 旧授权码仍有效：服务端旧 `licenses` 表未删除，客户端校验会依次尝试新码/旧短码/旧完整哈希。
- 不需要账号登录：`license_identity` 或旧授权缓存存在时，启动不会弹「登录授权账号」。
- 如自动检测失败：设置 → 授权 → 旧版授权 ID（可选）填原授权码，点「重新检测」。
- 旧授权迁移期临时开通**全部平台**；绑定账号后继续全部平台可用（v1.0.9）。
