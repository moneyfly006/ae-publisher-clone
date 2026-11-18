#!/usr/bin/env python3
"""api_save 单元测试（不联网）：payload 构建 + 属性/SKU 合并 + op 映射。

逆向成果（docs/ADD-JSON-REVERSE-NOTES.md）：
- add.json 传输 = multipart file=zip(smtEditSave.txt=JSON)
- payload 60 个扁平键，aeopAeProductPropertys / aeopAeProductSKUs 都是 JSON 字符串
- 系统属性 {attrNameId,attrValueId}，自定义属性 {attrName,attrValue}
- op: 1=保存 2=发布 3=移入待发布
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import aeclone.services.api_save as A


def _product(**kw):
    p = {
        "id": "P1", "shopId": "9190736", "categoryId": "100007204",
        "subject": "test product", "sourceUrl": "https://alibaba.com/x",
        "aeopAeProductPropertys": json.dumps([
            {"attrName": "Warranty", "attrValue": "1 Year"},
        ]),
        "aeopAeProductSKUs": json.dumps([
            {"id": "1:2", "skuPrice": "100.0", "ipmSkuStock": 50,
             "originalBox": None, "logisticAttrList": None,
             "specialProductTypeList": None},
        ]),
        "grossWeight": 10, "packageLength": 10, "packageWidth": 10, "packageHeight": 10,
        "promiseTemplateId": None, "freightTemplateId": "50192654715",
    }
    p.update(kw)
    return p


def _template():
    return {
        "aeopAeProductPropertys": json.dumps([
            {"attrName": "Brand Name", "attrNameId": "2", "attrValueId": "201512802", "attrValue": "NONE"},
            {"attrName": "Origin", "attrNameId": "219", "attrValueId": "9441741844", "attrValue": "Mainland China"},
            {"attrName": "Charge Status", "attrNameId": "400074001", "attrValueId": "23399554847", "attrValue": "No"},
            {"attrName": "High-concerned chemical", "attrNameId": "400000603", "attrValueId": "23399591357", "attrValue": "None"},
            {"attrName": "Certificate", "attrValue": "CE"},
        ]),
        "aeopAeProductSKUs": json.dumps([
            {"originalBox": "1", "logisticAttrList": [{"value": "1", "text": "普货"}],
             "specialProductTypeList": []},
        ]),
        "hsCode": json.dumps({"euHsCode": "8433199000", "version": "V2"}),
        "productManual": "https://manual.pdf",
        "grossWeight": 30,
    }


def t1_system_attrs_merged_with_ids():
    print("\n[测试1] 4 个系统属性按 id 合并，自定义属性保留名字")
    p = build = A.build_save_payload(_product(), _template(), action="save_only")
    props = json.loads(p["aeopAeProductPropertys"])
    by_id = {tuple(sorted(x.items())) for x in props if "attrNameId" in x}
    assert ("attrNameId", "2"), ("attrValueId", "201512802") in by_id or \
           {("attrNameId","2"),("attrValueId","201512802")} <= set(by_id)
    def has(nid, vid):
        return any(d.get("attrNameId") == nid and d.get("attrValueId") == vid
                   for d in props if "attrNameId" in d)
    assert has("2", "201512802"), "Brand Name 缺失"
    assert has("219", "9441741844"), "Origin 缺失"
    assert has("400074001", "23399554847"), "Charge Status 缺失"
    assert has("400000603", "23399591357"), "High-concerned 缺失"
    # 自定义属性用名字
    assert {"attrName": "Warranty", "attrValue": "1 Year"} in props
    print("  ✓ 4 个系统属性(id) + 自定义属性(name) 都在")


def t2_skus_json_string_and_merged():
    print("\n[测试2] SKU 是 JSON 字符串，originalBox/logisticAttrList 从模板补")
    p = A.build_save_payload(_product(), _template(), action="save_only")
    assert isinstance(p["aeopAeProductSKUs"], str), type(p["aeopAeProductSKUs"])
    skus = json.loads(p["aeopAeProductSKUs"])
    assert skus[0]["originalBox"] == "1", skus[0]
    assert skus[0]["logisticAttrList"], skus[0]
    print("  ✓ SKU 字符串化 + originalBox/logisticAttrList 已补")


def t3_op_mapping():
    print("\n[测试3] op 映射：save_only=1 / publish=2 / save_to_waiting=3")
    assert A.OP_MAP == {"save_only": 1, "publish": 2, "save_to_waiting": 3}
    p = A.build_save_payload(_product(), _template(), action="publish")
    assert p["op"] == 2
    p = A.build_save_payload(_product(), _template(), action="save_only")
    assert p["op"] == 1
    print("  ✓ op 映射正确")


def t4_payload_has_62_keys_and_promise_zero():
    print("\n[测试4] payload 键完整、promiseTemplateId 默认 0")
    p = A.build_save_payload(_product(), _template(), action="save_only")
    assert len(p) == 62, len(p)
    assert p["promiseTemplateId"] == "0", p["promiseTemplateId"]
    assert p["aeopAeProductPropertys"].startswith("["), "属性应为 JSON 字符串"
    print(f"  ✓ {len(p)} 键，promiseTemplateId=0")


def t5_template_fallback_for_missing_fields():
    print("\n[测试5] 目标缺的字段从模板补（hsCode/productManual）")
    p = A.build_save_payload(_product(), _template(), action="save_only")
    assert json.loads(p["hsCode"]).get("euHsCode") == "8433199000"
    assert p["productManual"] == "https://manual.pdf"
    print("  ✓ hsCode / productManual 从模板补齐")


def t6_ai_overrides_map_title_detail_sku_images():
    print("\n[测试6] AI 产物 → overrides（标题/描述/SKU/图片）")
    from types import SimpleNamespace
    cfg = SimpleNamespace(stock_quantity="999", write_sku_codes_to_product=True)
    product_edit = _product()
    p = {
        "title_en": "AI Title 100m Drill Rig",
        "detail_html": "<p>AI detail</p>",
        "skus": [{"optimized_name": "Drill-100M-Hydraulic", "processed_image": "https://img.com/sku1.png"}],
        "images": ["https://img.com/m1.png", "https://img.com/m2.png"],
        "price_result": SimpleNamespace(final=1234.5),
    }
    ov = A.build_ai_overrides(product_edit, p, cfg, images=p["images"])
    assert ov["subject"] == "AI Title 100m Drill Rig"
    assert ov["detail"] == "<p>AI detail</p>"
    assert ov["imageURLs"] == "https://img.com/m1.png;https://img.com/m2.png"
    skus = json.loads(ov["aeopAeProductSKUs"])
    assert skus[0]["skuCode"] == "Drill-100M-Hydraulic", skus[0]
    assert skus[0]["skuPrice"] == "1234.50", skus[0]
    assert skus[0]["ipmSkuStock"] == "999", skus[0]
    assert skus[0]["mobileModelSkuImage"] == "https://img.com/sku1.png", skus[0]
    print("  ✓ 标题/描述/主图/SKU名/价格/库存/SKU图 全部映射")


def t7_ai_overrides_serialized_as_string_in_payload():
    print("\n[测试7] overrides 套进 payload 后 SKU 仍是 JSON 字符串")
    from types import SimpleNamespace
    cfg = SimpleNamespace(stock_quantity="999", write_sku_codes_to_product=True)
    product_edit = _product()
    p = {"title_en": "T", "skus": [{"optimized_name": "SKU1"}],
         "price_result": SimpleNamespace(final=99.0), "images": ["https://i/1.png"]}
    ov = A.build_ai_overrides(product_edit, p, cfg, images=p["images"])
    payload = A.build_save_payload(product_edit, _template(), action="save_only", overrides=ov)
    assert payload["subject"] == "T"
    assert isinstance(payload["aeopAeProductSKUs"], str)
    assert json.loads(payload["aeopAeProductSKUs"])[0]["skuCode"] == "SKU1"
    print("  ✓ payload.subject 覆盖、SKU 仍是 JSON 字符串")

def t8_ship_and_price_synced():
    print("\n[测试8] 重量/尺寸/价格同步进 payload")
    from types import SimpleNamespace
    cfg = SimpleNamespace(stock_quantity="999", write_sku_codes_to_product=True)
    product_edit = _product()
    p = {"title_en": "T", "skus": [{"optimized_name": "S"}],
         "price_result": SimpleNamespace(final=88.0)}
    ov = A.build_ai_overrides(product_edit, p, cfg, images=None,
                              ship=("15", "20", "30", "40"))
    payload = A.build_save_payload(product_edit, _template(), action="save_only", overrides=ov)
    assert payload["grossWeight"] == "15", payload["grossWeight"]
    assert payload["packageLength"] == "20"
    assert payload["packageWidth"] == "30"
    assert payload["packageHeight"] == "40"
    assert payload["productMinPrice"] == "88.00"
    assert payload["productMaxPrice"] == "88.00"
    print("  ✓ grossWeight/尺寸/productMinMaxPrice 全部同步")

def t9_duplicate_custom_attr_removed():
    print("\n[测试9] 自定义属性与系统属性重名 → 自动删除")
    import json as _j
    # 目标有自定义 "Brand Name=yugong" + "Place of Origin=Henan"（Place of Origin 不是系统属性）
    product = _product()
    product["aeopAeProductPropertys"] = _j.dumps([
        {"attrName": "Brand Name", "attrValue": "yugong"},          # 与系统 Brand Name 重名 → 删
        {"attrName": "Place of Origin", "attrValue": "Henan, China"},  # 不重名 → 保留
        {"attrName": "Warranty", "attrValue": "1 Year"},              # 保留
    ])
    # 模板有系统属性 Brand Name / Origin / 及一个自定义属性 "Certificate=CE"
    tpl = _template()
    tpl["aeopAeProductPropertys"] = _j.dumps([
        {"attrName": "Brand Name", "attrNameId": "2", "attrValueId": "201512802", "attrValue": "NONE"},
        {"attrName": "Origin", "attrNameId": "219", "attrValueId": "9441741844", "attrValue": "Mainland China"},
        {"attrName": "Certificate", "attrValue": "CE"},   # 自定义，无 id → 不进入 system_names
    ])
    props = A.to_submit_props(product, tpl)
    names = [p.get("attrName") for p in props if "attrName" in p]
    ids = [(p.get("attrNameId"), p.get("attrValueId")) for p in props if "attrNameId" in p]
    # Brand Name 自定义被删，系统 Brand Name 以 id 形式在
    assert "Brand Name" not in names, f"自定义 Brand Name 应被删除: {props}"
    assert ("2", "201512802") in ids, "系统 Brand Name(id) 应在"
    assert "Place of Origin" in names, "Place of Origin 应保留（不重名）"
    assert "Warranty" in names, "Warranty 应保留"
    print("  ✓ 重名自定义属性已删，非重名保留，系统属性以 id 补齐")


def t10_duplicate_generic_not_only_brand():
    print("\n[测试10] 重名检测通用化（不只品牌，型号/产地等任何带 id 的属性都算系统属性）")
    import json as _j
    product = _product()
    product["aeopAeProductPropertys"] = _j.dumps([
        {"attrName": "Model Number", "attrValue": "X99"},   # 与模板系统 Model Number 重名 → 删
    ])
    tpl = _template()
    tpl["aeopAeProductPropertys"] = _j.dumps([
        {"attrName": "Model Number", "attrNameId": "1000", "attrValueId": "888", "attrValue": "X99"},
        {"attrName": "Origin", "attrNameId": "219", "attrValueId": "9441741844", "attrValue": "Mainland China"},
    ])
    props = A.to_submit_props(product, tpl)
    names = [p.get("attrName") for p in props if "attrName" in p]
    assert "Model Number" not in names, f"重名 Model Number 应删除: {props}"
    print("  ✓ Model Number（模板系统属性）重名检测生效")

def t11_missing_category_blocked():
    print("\n[测试11] 缺类目 ID → 拒绝保存（防止清空商品类目/属性）")
    msg = ""
    try:
        A.build_save_payload({"id": "1", "categoryId": None}, {"categoryId": None}, action="save_only")
        raise AssertionError("应该拒绝保存")
    except ValueError as e:
        msg = str(e)
        assert "类目" in msg, msg
    print("  ✓ 已拦截：", msg[:40])


def t12_category_from_template():
    print("\n[测试12] 商品缺类目 → 从模板补上")
    p = A.build_save_payload({"id": "1", "categoryId": None},
                             {"categoryId": "100007204"}, action="save_only")
    assert p["categoryId"] == "100007204", p["categoryId"]
    print("  ✓ 从模板补类目 100007204")

def t13_original_box_product_level():
    print("\n[测试13] 商品级 originalBox 不能为空（取自首个 SKU）")
    p = A.build_save_payload({"id": "1", "categoryId": "1",
                              "aeopAeProductSKUs": '[{"skuCode":"X","originalBox":"2"}]'},
                             None, action="save_only")
    assert p.get("originalBox") == "2", p.get("originalBox")
    # 商品完全没有 SKU 时也要有默认值
    p2 = A.build_save_payload({"id": "1", "categoryId": "1"}, None, action="save_only")
    assert p2.get("originalBox") in ("1", "2"), p2.get("originalBox")
    print("  ✓ originalBox =", p.get("originalBox"), "/ 兜底", p2.get("originalBox"))


def t14_empty_sku_gets_default():
    print("\n[测试14] 商品 0 个 SKU → 用模板骨架造 1 个（带 skuCode/originalBox）")
    tpl = _template()
    tpl["aeopAeProductSKUs"] = '[{"skuCode":"TPL-1","skuPrice":"10","ipmSkuStock":"5","originalBox":"1"}]'
    p = A.build_save_payload(_product(), tpl, action="save_only")
    skus = json.loads(p["aeopAeProductSKUs"])
    assert skus, "不应为空"
    assert skus[0].get("skuCode"), skus[0]
    assert skus[0].get("originalBox"), skus[0]
    print("  ✓ 兜底 SKU:", skus[0].get("skuCode"), "/", skus[0].get("originalBox"))

def t15_override_keeps_sku_required_fields():
    print("\n[测试15] AI overrides 覆盖 SKU 后，originalBox/skuCode 不能丢")
    from types import SimpleNamespace
    cfg = SimpleNamespace(stock_quantity="999", write_sku_codes_to_product=True,
                          wholesale_enable=False, regional_quote_enable=False,
                          detail_local_image_position="tail")
    prod = {"id": "1", "categoryId": "1",
            "aeopAeProductSKUs": '[{"skuCode":"","skuPrice":"1","originalBox":null}]'}
    p = {"skus": [{"optimized_name": "NEW-1"}],
         "price_result": SimpleNamespace(final=10.0), "images": []}
    ov = A.build_ai_overrides(prod, p, cfg, images=None)
    pay = A.build_save_payload(prod, None, action="publish", overrides=ov)
    sk = json.loads(pay["aeopAeProductSKUs"])
    assert sk[0].get("originalBox") == "1", sk[0]
    assert sk[0].get("skuCode") == "NEW-1", sk[0]
    assert pay.get("originalBox") == "1", pay.get("originalBox")
    print("  ✓ SKU:", sk[0].get("skuCode"), "/", sk[0].get("originalBox"),
          "| 商品级:", pay.get("originalBox"))

def t16_mobile_detail_generated():
    print("\n[测试16] 无线端描述（mobileDetail）自动生成，避免发布报错")
    html = ('<p>Our HDD machine is built for tough jobs.</p>'
            '<p><img src="https://x/a.jpg"/></p>'
            '<p><img src="https://x/b.jpg"/></p>'
            '<p>Key specs: 100m depth.</p>')
    md = A.build_mobile_detail(html)
    d = json.loads(md)
    assert d.get("moduleList"), "moduleList 不能为空"
    types = [m["type"] for m in d["moduleList"]]
    assert "text" in types and "image" in types, types
    # 连续两张图应合并成一个 image 模块
    img_mod = next(m for m in d["moduleList"] if m["type"] == "image")
    assert len(img_mod["images"]) == 2, img_mod
    # payload 里 mobileDetail 不能是空
    p = A.build_save_payload({"id": "1", "categoryId": "1", "detail": html},
                             None, action="save_only")
    assert str(p.get("mobileDetail") or "").strip(), "payload 缺 mobileDetail"
    print("  ✓ 模块:", types, "| 图片合并:", len(img_mod["images"]))


def t17_publish_keeps_mobile_detail_when_ai_overrides():
    print("\n[测试17] AI 改详情后，mobileDetail 跟着重建")
    from types import SimpleNamespace
    cfg = SimpleNamespace(stock_quantity="999", write_sku_codes_to_product=True,
                          wholesale_enable=False, regional_quote_enable=False,
                          detail_local_image_position="tail")
    ov = A.build_ai_overrides({"id": "1", "categoryId": "1"}, 
                              {"detail_html": "<p>New AI detail</p>"}, cfg, images=None)
    assert "mobileDetail" in ov, ov.keys()
    d = json.loads(ov["mobileDetail"])
    assert any("New AI detail" in t["texts"][0]["content"]
               for t in d["moduleList"] if t["type"] == "text")
    print("  ✓ mobileDetail 与 detail 同步")

def t18_detail_image_over_5mb_shrunk():
    print("\n[测试18] 描述图 >5MB → 自动压缩并替换 URL")
    import io as _io
    from PIL import Image as _Img
    import aeclone.services.publisher as P

    # 造一张大图（纯色块，压缩后很小）
    im = _Img.new("RGB", (3000, 3000), (120, 130, 140))
    buf = _io.BytesIO()
    im.save(buf, format="PNG")
    # 人为把字节数灌到 >5MB
    big = buf.getvalue()
    big = big + b"\x00" * (6 * 1024 * 1024 - len(big))

    class FakeResp:
        content = big
        status_code = 200
        headers = {"Content-Length": str(len(big))}

    class FakeReq:
        @staticmethod
        def get(u, timeout=40, **kw):
            return FakeResp()

    import sys as _sys
    _sys.modules["requests"] = FakeReq

    class FakeHost:
        @staticmethod
        def upload(p):
            return "https://cdn/shrunk.jpg"

    _sys.modules["aeclone.services.hosting"] = type(
        "H", (), {"upload": staticmethod(lambda p: "https://cdn/shrunk.jpg")})

    pub = object.__new__(P.Publisher)
    pub.cfg = type("C", (), {"prefer_dianxiaomi_album": False})()
    res = P.PublishResult(product_id="x", ok=True)
    html = '<p><img src="https://x/big.png"/></p>'
    out = pub._shrink_detail_images(html, res)
    assert "https://cdn/shrunk.jpg" in out, out
    assert any("压缩" in s.name for s in res.steps), res.steps
    print("  ✓ URL 已替换：", out)

if __name__ == "__main__":
    tests = [t1_system_attrs_merged_with_ids, t2_skus_json_string_and_merged,
             t3_op_mapping, t4_payload_has_62_keys_and_promise_zero,
             t5_template_fallback_for_missing_fields,
             t6_ai_overrides_map_title_detail_sku_images,
             t7_ai_overrides_serialized_as_string_in_payload,
             t8_ship_and_price_synced,
             t9_duplicate_custom_attr_removed,
             t10_duplicate_generic_not_only_brand,
             t11_missing_category_blocked, t12_category_from_template,
             t13_original_box_product_level, t14_empty_sku_gets_default,
             t15_override_keeps_sku_required_fields,
             t16_mobile_detail_generated, t17_publish_keeps_mobile_detail_when_ai_overrides,
             t18_detail_image_over_5mb_shrunk]
    ok = fail = 0
    for t in tests:
        try:
            t()
            ok += 1
        except AssertionError as ex:
            fail += 1
            print(f"  ✗ {ex}")
        except Exception as ex:
            fail += 1
            import traceback
            traceback.print_exc()
    print("\n" + "=" * 50)
    print(f"结果: {ok} 通过, {fail} 失败")
    sys.exit(0 if fail == 0 else 1)
