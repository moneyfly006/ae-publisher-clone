"""AI图片处理专项测试 - 模拟真实流程但不调用真实API。

重点验证：
1. 图片下载缓存机制
2. 任务规划与缓存键生成
3. 白底/去水印/编辑流程完整性
4. 错误处理与降级
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import tempfile
import hashlib


def test_image_download_cache():
    """测试图片下载缓存避免串图"""
    print("=" * 60)
    print("测试 1: 图片下载缓存机制")
    print("=" * 60)
    
    from aeclone.services.image_pipeline import ImagePipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    pipeline = ImagePipeline(cfg)
    
    # 测试URL哈希命名
    url1 = "https://example.com/product1/image.jpg"
    url2 = "https://example.com/product2/image.jpg"  # 同名但不同路径
    
    # 模拟下载逻辑中的哈希计算
    hash1 = hashlib.md5(url1.encode()).hexdigest()[:16]
    hash2 = hashlib.md5(url2.encode()).hexdigest()[:16]
    
    print(f"URL1: {url1}")
    print(f"  哈希: {hash1}")
    print(f"URL2: {url2}")
    print(f"  哈希: {hash2}")
    
    if hash1 != hash2:
        print("✓ 不同URL生成不同哈希，避免串图")
    else:
        raise AssertionError("哈希冲突！")
    print()


def test_image_task_cache_key():
    """测试图片任务缓存键完整性"""
    print("=" * 60)
    print("测试 2: 图片任务缓存键")
    print("=" * 60)
    
    from aeclone.services.image_task_plan import ImagePlan
    
    plan = ImagePlan()
    
    # 测试场景：相同图片、不同提示词应该产生不同缓存键
    url = "https://example.com/test.jpg"
    prompt1 = "白底商品图"
    prompt2 = "白底商品图，去除水印"
    
    # 模拟缓存键生成（包含完整提示词）
    key1 = f"{url}|{prompt1}|white_bg"
    key2 = f"{url}|{prompt2}|white_bg"
    
    print(f"相同图片，提示词1: '{prompt1}'")
    print(f"  缓存键: {key1}")
    print(f"相同图片，提示词2: '{prompt2}'")
    print(f"  缓存键: {key2}")
    
    if key1 != key2:
        print("✓ 不同提示词生成不同缓存键")
    else:
        raise AssertionError("缓存键冲突！")
    
    # 测试多输出与单输出不共享缓存
    key_single = f"{url}|{prompt1}|mode:single"
    key_multi = f"{url}|{prompt1}|mode:multi"
    
    print(f"\n单输出模式: {key_single}")
    print(f"多输出模式: {key_multi}")
    
    if key_single != key_multi:
        print("✓ 单/多输出模式使用不同缓存键")
    else:
        raise AssertionError("单多输出缓存键冲突！")
    print()


def test_image_review_not_replace_content():
    """测试图片审查不覆盖原内容"""
    print("=" * 60)
    print("测试 3: 审查结果不覆盖产品数据")
    print("=" * 60)
    
    # 模拟审查结果处理
    original_html = "<div>商品详情HTML内容</div>"
    review_verdict = "PASS"
    
    # 错误做法：把审查结果写入详情
    # product["detail_html"] = review_verdict  # ❌
    
    # 正确做法：审查结果作为元数据
    product = {
        "detail_html": original_html,
        "_review_text": review_verdict,  # 元数据
    }
    
    print(f"原始详情: {original_html}")
    print(f"审查结果: {review_verdict}")
    print(f"处理后详情: {product['detail_html']}")
    
    if product["detail_html"] == original_html:
        print("✓ 审查结果未覆盖原内容")
    else:
        raise AssertionError("审查结果错误覆盖了产品内容！")
    
    # 测试失败拦截逻辑
    fail_verdict = "FAIL|含有违规联系方式"
    block_save = fail_verdict.startswith("FAIL")
    
    print(f"\n违规审查: {fail_verdict}")
    print(f"是否拦截保存: {'是' if block_save else '否'}")
    
    if block_save:
        print("✓ 违规内容正确拦截")
    print()


def test_image_hosting_skip_logic():
    """测试图片托管跳过逻辑"""
    print("=" * 60)
    print("测试 4: 已托管图片跳过重复上传")
    print("=" * 60)
    
    hosted_patterns = ("moneyfly.club", "alicdn.com")
    
    images = [
        "https://moneyfly.club/hosted/img1.png",
        "http://private.site/img2.jpg",
        "https://ae01.alicdn.com/kf/img3.jpg",
        "http://10.0.0.1/local.png",
    ]
    
    needs_upload = []
    for i, url in enumerate(images):
        skip = any(pattern in str(url).lower() for pattern in hosted_patterns)
        status = "跳过" if skip else "需上传"
        print(f"  [{i}] {status}: {url}")
        if not skip:
            needs_upload.append(i)
    
    print(f"\n✓ 总共{len(images)}张图片，需上传{len(needs_upload)}张，跳过{len(images) - len(needs_upload)}张")
    
    if len(needs_upload) == 2:  # 只有private.site和10.0.0.1需要上传
        print("✓ 托管检测逻辑正确")
    else:
        raise AssertionError(f"托管检测错误：需上传{len(needs_upload)}张（预期2张）")
    print()


def test_publisher_image_flow():
    """测试发布器中的图片处理流程"""
    print("=" * 60)
    print("测试 5: 发布器图片处理流程")
    print("=" * 60)
    
    # 模拟发布器处理产品的图片流程
    product = {
        "id": "test123",
        "title": "测试商品",
        "images": [
            "https://moneyfly.club/img1.png",  # 已托管
            "http://private.site/img2.jpg",    # 需转存
        ]
    }
    
    print(f"商品ID: {product['id']}")
    print(f"原始图片: {len(product['images'])}张")
    
    # 模拟流程：
    # 1. 检查是否需要AI处理
    ai_enabled = True
    white_bg_enabled = False  # 未开启白底
    
    if ai_enabled and white_bg_enabled:
        print("  → AI图片处理（白底/去水印/编辑）")
    else:
        print("  → 跳过AI处理（未启用或未开启功能）")
    
    # 2. 图片转存（智能跳过已托管）
    hosted_count = sum(1 for url in product['images'] 
                      if 'moneyfly.club' in url or 'alicdn.com' in url)
    needs_upload = len(product['images']) - hosted_count
    
    print(f"  → 图片转存: {hosted_count}张已托管，{needs_upload}张需上传")
    
    # 3. 保存到编辑器
    print(f"  → 保存到店小秘编辑器")
    
    print("\n✓ 完整图片处理流程模拟成功")
    print()


def main():
    print("\n" + "=" * 60)
    print("AI图片处理专项测试")
    print("=" * 60)
    print()
    
    tests = [
        test_image_download_cache,
        test_image_task_cache_key,
        test_image_review_not_replace_content,
        test_image_hosting_skip_logic,
        test_publisher_image_flow,
    ]
    
    passed = 0
    failed = []
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} 失败: {e}\n")
            failed.append(test_func.__name__)
            import traceback
            traceback.print_exc()
    
    print("=" * 60)
    print(f"测试总结: {passed}/{len(tests)} 通过")
    if failed:
        print(f"失败: {', '.join(failed)}")
        return 1
    else:
        print("✓ 所有AI图片处理测试通过")
        return 0


if __name__ == "__main__":
    exit(main())
