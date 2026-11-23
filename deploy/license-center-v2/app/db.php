<?php
// 数据库（SQLite，WAL 模式）
final class DB
{
    private static ?PDO $pdo = null;

    public static function pdo(): PDO
    {
        if (self::$pdo instanceof PDO) {
            return self::$pdo;
        }
        $path = $GLOBALS['cfg']['db_path'];
        $dir  = dirname($path);
        if (!is_dir($dir)) {
            mkdir($dir, 0770, true);
        }
        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA journal_mode = WAL');
        $pdo->exec('PRAGMA synchronous = NORMAL');
        $pdo->exec('PRAGMA foreign_keys = ON');
        $pdo->exec('PRAGMA busy_timeout = 5000');
        self::$pdo = $pdo;
        self::migrate($pdo);
        return $pdo;
    }

    private static function migrate(PDO $pdo): void
    {
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS admins (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  pass_hash     TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'owner',
  is_active     INTEGER NOT NULL DEFAULT 1,
  last_login_at TEXT,
  last_login_ip TEXT,
  created_at    TEXT NOT NULL
);
SQL);
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS licenses (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  identity         TEXT NOT NULL UNIQUE,
  client_type      TEXT NOT NULL DEFAULT 'local',
  plan             TEXT NOT NULL DEFAULT 'standard',
  status           TEXT NOT NULL DEFAULT 'active',
  expires_at       TEXT NOT NULL,
  issued_at        TEXT NOT NULL,
  note             TEXT NOT NULL DEFAULT '',
  created_by       TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  last_verified_at TEXT,
  verify_count     INTEGER NOT NULL DEFAULT 0,
  revoked_at       TEXT
);
SQL);
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_licenses_status  ON licenses(status)');
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_licenses_expires ON licenses(expires_at)');
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS verify_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  identity   TEXT NOT NULL,
  ip         TEXT NOT NULL DEFAULT '',
  ua         TEXT NOT NULL DEFAULT '',
  ok         INTEGER NOT NULL DEFAULT 0,
  reason     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
SQL);
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_vlogs_created ON verify_logs(created_at)');
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS audit_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor      TEXT NOT NULL DEFAULT '',
  action     TEXT NOT NULL,
  target     TEXT NOT NULL DEFAULT '',
  detail     TEXT NOT NULL DEFAULT '',
  ip         TEXT NOT NULL DEFAULT '',
  ua         TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
SQL);
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS login_attempts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ip         TEXT NOT NULL,
  username   TEXT NOT NULL DEFAULT '',
  ok         INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
SQL);
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_login_ip ON login_attempts(ip, created_at)');
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
SQL);
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS app_logs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  identity       TEXT NOT NULL,
  filename       TEXT NOT NULL DEFAULT '',
  size           INTEGER NOT NULL DEFAULT 0,
  client_version TEXT NOT NULL DEFAULT '',
  content        TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL
);
SQL);
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_applogs_identity ON app_logs(identity, created_at)');
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS usage_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  identity   TEXT NOT NULL,
  event      TEXT NOT NULL,
  detail     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
SQL);
        $pdo->exec('CREATE INDEX IF NOT EXISTS idx_events_identity ON usage_events(identity, event)');
        // 设备信息（每次授权校验刷新）：管理端可看到用户机器/系统/版本/店铺
        $pdo->exec(<<<SQL
CREATE TABLE IF NOT EXISTS devices (
  identity     TEXT PRIMARY KEY,
  machine_code TEXT NOT NULL DEFAULT '',
  machine_name TEXT NOT NULL DEFAULT '',
  platform     TEXT NOT NULL DEFAULT '',
  os_version   TEXT NOT NULL DEFAULT '',
  app_version  TEXT NOT NULL DEFAULT '',
  store_name   TEXT NOT NULL DEFAULT '',
  instance     TEXT NOT NULL DEFAULT '',
  first_seen   TEXT NOT NULL DEFAULT '',
  last_seen    TEXT NOT NULL DEFAULT '',
  last_ip      TEXT NOT NULL DEFAULT ''
);
SQL);
        self::ensureColumn($pdo, 'app_logs', 'ip', "TEXT NOT NULL DEFAULT ''");
        self::ensureColumn($pdo, 'app_logs', 'event_count', 'INTEGER NOT NULL DEFAULT 0');
        self::ensureColumn($pdo, 'app_logs', 'error_count', 'INTEGER NOT NULL DEFAULT 0');
        self::ensureColumn($pdo, 'usage_events', 'error_type', "TEXT NOT NULL DEFAULT ''");
        self::ensureColumn($pdo, 'app_logs', 'redacted', 'INTEGER NOT NULL DEFAULT 0');
        self::ensureColumn($pdo, 'app_logs', 'meta', "TEXT NOT NULL DEFAULT ''");
    }

    private static function ensureColumn(PDO $pdo, string $table, string $column, string $ddl): void
    {
        $cols = [];
        foreach ($pdo->query("PRAGMA table_info($table)") as $r) {
            $cols[] = $r['name'];
        }
        if (!in_array($column, $cols, true)) {
            try {
                $pdo->exec("ALTER TABLE $table ADD COLUMN $column $ddl");
            } catch (Throwable $e) {
                // 并发迁移时忽略
            }
        }
    }
}
