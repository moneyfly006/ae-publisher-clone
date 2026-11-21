"""使用已有配置执行只读连接验证；不输出密钥，不保存或发布商品。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import time
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aeclone.config import AppConfig
from aeclone.api import ApiClient
from aeclone.integrations.dianxiaomi import DianxiaomiClient


def main():
    cfg = AppConfig.load()
    state = threading.local()
    def check_response(response, *args, **kwargs):
        try:
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and (data.get('success') is False or
                    str(data.get('code', '0')) not in ('0', '200', 'None')):
                raise ValueError('服务返回业务错误')
        except Exception:
            state.errors.append(f'响应校验失败（HTTP {response.status_code}）')
    def tracked(client):
        client.session.hooks['response'].append(check_response)
        return client
    def api():
        return tracked(ApiClient(cfg.api_base, cfg.api_key, 20))
    def shop():
        return tracked(DianxiaomiClient(cfg.manual_cookies or {}, timeout=15,
                               overrides=cfg.endpoint_overrides or {}))
    def template():
        product = shop()._get('product_detail', {'id': cfg.template_product_id}).get('data', {}).get('product')
        if not isinstance(product, dict) or not product:
            raise ValueError('未返回模板商品')
        return {'field_count': len(product)}
    cases = [
        ('API 模型列表', lambda: {'model_count': len(api().models(force=True))}),
        ('API 余额', lambda: {'available': api().balance().get('remain', 0) > 0}),
        ('API 价格表', lambda: {'model_count': len(api().pricing(force=True))}),
        ('店铺列表', lambda: {'shop_count': len(shop().shops())}),
        ('商品状态统计', lambda: {'counts': shop().counts(cfg.store_id)}),
        ('草稿读取', lambda: {'product_count': len(shop().iter_products(cfg.store_id, 'draft', 2))}),
        ('在线商品读取', lambda: {'product_count': len(shop().online(limit=2, shop_id=cfg.store_id))}),
        ('模板详情读取', template),
        ('相册空间读取', lambda: shop().album_counts()),
        ('区域调价模板读取', lambda: {'template_count': len(shop().search_price_templates())}),
    ]
    def run(case):
        name, action = case
        start = time.monotonic()
        state.errors = []
        try:
            detail = action()
            if state.errors:
                raise ValueError('; '.join(state.errors))
            status = 'PASS'
        except Exception as ex:
            detail = f'{type(ex).__name__}: {str(ex)[:240]}'
            if cfg.api_key:
                detail = detail.replace(cfg.api_key, '[redacted]')
            status = 'FAIL'
        result = dict(name=name, status=status, detail=detail, elapsed=round(time.monotonic()-start, 2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, cases))
    out = Path('docs/connection-verification.json')
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(r['status']=='FAIL' for r in results) else 0

if __name__ == '__main__':
    raise SystemExit(main())
