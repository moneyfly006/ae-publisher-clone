from pathlib import Path
import os, sys, subprocess, tempfile, json, concurrent.futures
root = Path(__file__).resolve().parents[1]
(root / "build/verification").mkdir(parents=True, exist_ok=True)
modules = "test_api_save test_category_map test_config_robustness test_preflight_config test_image_regressions test_image_plan_conflicts test_image_edit_modes test_ai_image_flow test_publisher_pipeline_sim test_image_size_validation test_image_hosting_protocol test_image_hosting_fix test_prompt_plumbing test_api_preflight test_audit_pending_skip test_build_scripts test_packaging_spec test_host_cleanup test_selfcheck_cli test_windows_update test_updater test_preset test_ui_pages test_ai_features_comprehensive test_integration test_real_ai_execution test_ui_redesign test_runtime_verification test_pipeline_result_semantics test_video_settings test_conflict_effectiveness test_image_switch_execution test_sku_write_safety test_price_write_safety test_full_prompt_cache test_detail_content test_schedule_destination test_frozen_localcheck test_missing_price_safety test_customs_metadata test_sku_recovery test_publish_repair test_alibaba_payload test_alibaba_pipeline test_publish_readiness test_alibaba_prompts test_ui_alibaba_actions test_platform_ui_filter test_amazon_contract test_amazon_client test_amazon_payload test_amazon_prompts test_amazon_pipeline test_amazon_ui_page test_amazon_adapter".split()
def run(name):
    with tempfile.TemporaryDirectory(prefix="aurora-regression-") as folder:
        env = dict(os.environ, AURORA_CONFIG_DIR=folder, AURORA_SKIP_LICENSE="1", PYTHONPATH=str(root/"src")+os.pathsep+str(root/"tests"))
        if name.startswith("test_amazon"):
            # Amazon 测试按契约在验收/预演模式跑（禁止真实保存、保存按钮为预演文案）。
            env["AURORA_TEST"] = "1"
        script = """import runpy, socket, sys
from unittest.mock import patch
connect = socket.socket.connect
connect_ex = socket.socket.connect_ex
def guard(fn):
 def call(self, address, *a):
  if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
   raise AssertionError('External networking prohibited in regression test')
  return fn(self, address, *a)
 return call
with patch.object(socket.socket, 'connect', guard(connect)), patch.object(socket.socket, 'connect_ex', guard(connect_ex)):
 target=sys.argv[1]
 sys.argv=[target]
 runpy.run_path(target, run_name='__main__')
"""
        try:
            result=subprocess.run([str(root/'.venv/bin/python'), '-c', script, str(root/'tests'/f'{name}.py')], env=env, capture_output=True, text=True, timeout=100)
            (root/'build/verification'/f'{name}.log').write_text(result.stdout+result.stderr)
            return dict(name=name, status='PASS' if result.returncode==0 else 'FAIL', exit_code=result.returncode)
        except subprocess.TimeoutExpired:
            return dict(name=name,status='TIMEOUT')
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results=list(pool.map(run, modules))
for result in results: print(json.dumps(result), flush=True)
(root/'docs/regression-verification.json').write_text(json.dumps(results, indent=2))
raise SystemExit(int(any(r['status']!='PASS' for r in results)))
