"""Collection runtime presets; no application or service imports."""
PARAMETERS = {
    "request_timeout": ("平台请求超时（秒）", 30, 5, 120, True),
    "read_retries": ("只读失败重试次数", 2, 0, 3, True),
    "interval": ("商品处理间隔（秒）", 1.0, 0, 30, False),
    "failure_limit": ("连续失败停止阈值（0 = 不限制）", 3, 0, 20, True),
}
PRESETS = {
    "steady": ("稳健（推荐）", dict(request_timeout=30, read_retries=2, interval=1.0, failure_limit=3)),
    "slow": ("低频运行", dict(request_timeout=60, read_retries=2, interval=3.0, failure_limit=3)),
    "fast": ("快速运行", dict(request_timeout=20, read_retries=1, interval=0.0, failure_limit=3)),
}
