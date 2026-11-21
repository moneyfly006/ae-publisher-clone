"""Run isolated regression tests, never application button automation.

Run from the project root: python3 tools/test_offline.py
Network/process/browser calls are denied. Application configuration points
at a temporary directory. test_api.py is a separate pytest module, not silently
counted as covered by this unittest runner.
"""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "test_audit_safety", "test_sku_write_safety", "test_product_isolation",
    "test_image_job_regression", "test_download_cache", "test_pricing_regression",
    "test_price_write_safety", "test_variants_regression", "test_workbench_safety",
    "test_content_review", "test_weight_reuse", "test_ui_state_regression",
    "test_image_hosting_optimization",
    # Amazon 刊登（task-4 接入；AURORA_TEST=1 下应全绿）
    "test_amazon_contract", "test_amazon_client", "test_amazon_payload",
    "test_amazon_prompts", "test_amazon_pipeline", "test_amazon_ui_page",
    "test_amazon_adapter", "test_amazon_platform_ui_filter",
    # 授权：token 解析 / 授权服务器地址归一化与 308 query 保留
    "test_licensing", "test_license_server",
    # TikTok Shop 刊登（task-5 接入；AURORA_TEST=1 下应全绿）
    "test_tiktok_client", "test_tiktok_payload", "test_tiktok_prompts",
    "test_tiktok_pipeline", "test_tiktok_contract", "test_tiktok_ui_page",
)


def denied(*args, **kwargs):
    raise AssertionError("Offline tests prohibit network connections and child processes")


def main():
    sys.path.insert(0, str(ROOT / "tests"))
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="aurora-offline-") as root:
        env = {"AURORA_CONFIG_DIR": root, "AURORA_INSTANCE": "offline-regression",
               "AURORA_SKIP_LICENSE": "1", "AURORA_TEST": "1"}
        with patch.dict(os.environ, env), patch.object(socket.socket, "connect", denied), \
             patch.object(socket.socket, "connect_ex", denied), \
             patch.object(socket, "create_connection", denied), \
             patch.object(socket, "getaddrinfo", denied), \
             patch.object(subprocess, "Popen", denied), patch.object(os, "system", denied):
            suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
