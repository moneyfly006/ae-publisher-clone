"""Run actual local pipeline computations inside the packaged interpreter."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import tempfile

def run() -> int:
    from .config import AppConfig, DEFAULTS
    from .services.publisher import Publisher
    from .services.image_ops import add_watermark, four_in_one
    from .services.api_save import build_ai_overrides
    from PIL import Image

    def worker(index):
        data = dict(DEFAULTS)
        data.update({key: False for key, value in data.items() if isinstance(value, bool)})
        data.update(image_edit_mode="fine", image_ai_jobs="[]", image_edit_plans="[]",
                    image_simple_rules="[]", background_gen_rules="[]", stock_quantity="",
                    price_mode="multiplier", price_multiplier="1.5", price_addition="0",
                    write_sku_codes_to_product=True, write_price_to_product=True,
                    api_key="", custom_sku_prefix="", sales_profit="0", max_sku_count=256)
        pub = Publisher.__new__(Publisher)
        pub.cfg = AppConfig(data)
        result = pub.run(dict(id=f"local-{index}", title="Synthetic product", cost=100,
                             skus=[dict(sku="A", stock=3)], images=[]), dry_run=True)
        if not result.ok or not result.preview:
            raise AssertionError(result.error or "Preview result was incorrect")
        price = next(s for s in result.steps if s.name == "价格计算")
        if not price.ok:
            raise AssertionError(price.detail)
        return index

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            completed = list(pool.map(worker, range(24)))
        with tempfile.TemporaryDirectory(prefix="aurora-localcheck-") as folder:
            root = Path(folder)
            sources=[]
            for index,color in enumerate(("red","blue","green","yellow")):
                path=root/f"{index}.png"
                Image.new("RGB",(200,200),color).save(path)
                sources.append(path)
            out=four_in_one(sources,root/"collage.png")
            with Image.open(out) as image:
                if image.size!=(1200,1200):raise AssertionError("Collage output size")
            out=add_watermark(sources[0],sources[1],root/"watermark.png",opacity=1)
            with Image.open(out) as image:
                if image.getpixel((150,150))!=(0,0,255):raise AssertionError("Watermark did not apply")
        source=dict(aeopAeProductSKUs=[dict(id="red",skuCode="RED",ipmSkuStock=3),
                                      dict(id="blue",skuCode="BLUE",ipmSkuStock=7)])
        result=build_ai_overrides(source,dict(skus=[dict(_source_id="blue",sku="Blue new"),
                         dict(_source_id="red",sku="Red new")]),AppConfig(dict(stock_quantity="")))
        rows=json.loads(result['aeopAeProductSKUs'])
        if [r['skuCode'] for r in rows]!=['Red new','Blue new']:raise AssertionError("SKU identity mismatch")
        print(json.dumps(dict(status="PASS",concurrent_previews=len(completed),
                              collage=True,watermark=True,sku_identity=True),ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(status="FAIL",error=f"{type(error).__name__}: {error}"),ensure_ascii=False))
        return 1
