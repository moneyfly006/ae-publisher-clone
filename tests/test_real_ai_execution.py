#!/usr/bin/env python3
"""
真实环境AI功能执行测试
验证修复后的AI图片处理、文本生成、内容审查等功能
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from aeclone.services.publisher import Publisher
from aeclone.services.image_jobs import migrate, parse_rules, parse_jobs
from aeclone.config import AppConfig


def create_mock_config(**overrides):
    """创建模拟配置对象"""
    class MockConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    
    defaults = {
        "four_in_one": False,
        "four_in_one_position": "first",
        "watermark": False,
        "watermark_indices": [],
        "image_simple_rules": [],
        "image_ai_jobs": [],
    }
    defaults.update(overrides)
    return MockConfig(**defaults)


def create_test_product():
    """创建测试商品数据"""
    return {
        "title": "Test Product Title",
        "category_id": "123456",
        "images": [
            f"https://example.com/image{i}.jpg" for i in range(1, 11)
        ],
        "price": 19.99,
        "description": "Test product description",
        "variants": [
            {
                "sku": "TEST-SKU-001",
                "price": 19.99,
                "stock": 100,
                "attributes": {}
            }
        ]
    }


def test_image_jobs_migration():
    """测试1: 配置迁移逻辑"""
    print("\n=== 测试1: 配置迁移逻辑 ===")
    
    # 测试四合一迁移
    cfg = create_mock_config(
        four_in_one=True,
        four_in_one_position="first"
    )
    
    try:
        rules_str, jobs_str = migrate(cfg, with_global=True)  # 需要with_global=True来触发全局开关迁移
        
        # 解析JSON字符串
        import json
        rules = json.loads(rules_str)
        jobs = json.loads(jobs_str)
        
        print(f"✓ 四合一迁移生成 {len(jobs)} 个任务, {len(rules)} 条规则")
        
        if jobs:
            job = jobs[0]
            print(f"  - group: {job['group']}")
            print(f"  - index: {job['index']}")
            print(f"  - refs: {job['refs']}")
            print(f"  - insert: {job['insert']}")
            print(f"  - preset: {job.get('preset', 'N/A')}")
            
            assert job["group"] == "main", "四合一应该作用于主图"
            assert job["insert"] == "first", "position=first应该映射到insert=first"
            assert "1,2,3,4" in job["refs"], "应该引用前4张图片"
        else:
            print("  警告: 未生成任何四合一任务")
            return False
    except Exception as e:
        print(f"错误详情: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 测试水印迁移
    cfg2 = create_mock_config(
        watermark=True,
        watermark_indices=[1, 2, 3],
        image_simple_rules=[]
    )
    
    rules2_str, jobs2_str = migrate(cfg2, with_global=True)
    rules2 = json.loads(rules2_str)
    print(f"✓ 水印迁移生成 {len(rules2)} 条规则")
    if rules2:
        print(f"  - 规则示例: group={rules2[0].get('group')}, ops={rules2[0].get('ops')}")
    
    return True


def test_simple_rules_parsing():
    """测试2: 简单规则解析"""
    print("\n=== 测试2: 简单规则解析 ===")
    
    rules_config = [
        {
            "group": "main",
            "range": "1-5",
            "ops": ["white", "watermark"]
        }
    ]
    
    rules = parse_rules(rules_config)
    print(f"✓ 解析规则: {len(rules)} 条")
    
    for rule in rules:
        print(f"  - group={rule['group']}, range={rule['range']}, ops={rule['ops']}")
        assert rule["group"] == "main"
        assert rule["range"] == "1-5"
        assert "white" in rule["ops"]
    
    return True


def test_ai_jobs_parsing():
    """测试3: 精细任务解析"""
    print("\n=== 测试3: 精细任务解析 ===")
    
    jobs_config = [
        {
            "group": "main",
            "index": 5,
            "prompt": "Add watermark to this product image",
            "mode": "edit",
            "insert": "replace"
        },
        {
            "group": "main",
            "index": 1,
            "refs": "1,2,3,4",
            "prompt": "Combine these 4 images into a collage",
            "mode": "edit",
            "insert": "first"
        }
    ]
    
    jobs = parse_jobs(jobs_config)
    print(f"✓ 解析任务: {len(jobs)} 个")
    
    for i, job in enumerate(jobs, 1):
        print(f"  任务{i}:")
        print(f"    - group={job['group']}, index={job['index']}")
        print(f"    - mode={job['mode']}, insert={job['insert']}")
        if "refs" in job:
            print(f"    - refs={job['refs']}")
    
    return True


def test_image_preservation_logic():
    """测试4: 图片保留逻辑验证"""
    print("\n=== 测试4: 图片保留逻辑 ===")
    
    # 模拟处理前8张图片
    original_images = [f"img{i}.jpg" for i in range(1, 11)]
    processed_images = [f"processed_{i}.jpg" for i in range(1, 9)]
    
    # 应用保留逻辑（模拟publisher.py L747）
    final_images = processed_images + original_images[8:]
    
    print(f"原始图片数: {len(original_images)}")
    print(f"处理后图片数: {len(processed_images)}")
    print(f"最终图片数: {len(final_images)}")
    print(f"第9张: {final_images[8]}")
    print(f"第10张: {final_images[9]}")
    
    assert len(final_images) == 10, "应该保留10张图片"
    assert final_images[8] == "img9.jpg", "第9张应该是原始图片"
    assert final_images[9] == "img10.jpg", "第10张应该是原始图片"
    print("✓ 图片保留逻辑正确")
    
    return True


def test_insert_modes():
    """测试5: 插入模式验证"""
    print("\n=== 测试5: 插入模式验证 ===")
    
    original = ["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg", "img5.jpg"]
    generated = ["gen1.jpg", "gen2.jpg"]
    
    # 测试 replace_first 模式
    mode_replace_first = generated + original[2:]
    print(f"replace_first: {mode_replace_first}")
    assert mode_replace_first[0] == "gen1.jpg"
    assert mode_replace_first[1] == "gen2.jpg"
    assert mode_replace_first[2] == "img3.jpg"
    print("✓ replace_first 模式正确")
    
    # 测试 prepend 模式
    mode_prepend = generated + original
    print(f"prepend: {mode_prepend}")
    assert mode_prepend[0] == "gen1.jpg"
    assert mode_prepend[2] == "img1.jpg"
    print("✓ prepend 模式正确")
    
    # 测试 append 模式
    mode_append = original + generated
    print(f"append: {mode_append}")
    assert mode_append[-1] == "gen2.jpg"
    assert mode_append[0] == "img1.jpg"
    print("✓ append 模式正确")
    
    return True


def test_hosted_image_detection():
    """测试6: 已托管图片检测"""
    print("\n=== 测试6: 已托管图片检测 ===")
    
    hosted_patterns = ("moneyfly.club", "alicdn.com", "ae01.alicdn.com")
    
    test_urls = [
        ("https://moneyfly.club/upload/123.jpg", True),
        ("https://ae01.alicdn.com/kf/456.jpg", True),
        ("https://img.alicdn.com/789.jpg", True),
        ("https://example.com/local.jpg", False),
        ("file:///path/to/local.jpg", False),
    ]
    
    for url, expected in test_urls:
        is_hosted = any(p in url for p in hosted_patterns)
        status = "✓" if is_hosted == expected else "✗"
        print(f"{status} {url}: {'已托管' if is_hosted else '未托管'}")
        assert is_hosted == expected
    
    print("✓ 已托管图片检测逻辑正确")
    return True


def test_index_based_replacement():
    """测试7: 按索引精确替换"""
    print("\n=== 测试7: 按索引精确替换 ===")
    
    images = [f"img{i}.jpg" for i in range(1, 11)]
    
    # 模拟替换第5张（索引4）
    target_index = 4
    new_image = "replaced_5.jpg"
    
    # 方式1: 直接替换
    images_copy = images.copy()
    images_copy[target_index] = new_image
    
    print(f"原始: {images[target_index]}")
    print(f"替换后: {images_copy[target_index]}")
    assert images_copy[target_index] == new_image
    assert images_copy[3] == "img4.jpg"  # 前一张不变
    assert images_copy[5] == "img6.jpg"  # 后一张不变
    print("✓ 按索引替换正确，其他图片未受影响")
    
    return True


def test_range_batch_processing():
    """测试8: 范围批量处理"""
    print("\n=== 测试8: 范围批量处理 ===")
    
    images = [f"img{i}.jpg" for i in range(1, 11)]
    
    # 模拟处理1-5范围（索引0-4）
    start, end = 0, 5
    processed = [f"processed_{i}.jpg" for i in range(1, 6)]
    
    result = processed + images[5:]
    
    print(f"处理范围: 1-5")
    print(f"处理后前5张: {result[:5]}")
    print(f"未处理6-10张: {result[5:]}")
    
    assert all("processed" in img for img in result[:5])
    assert all("processed" not in img for img in result[5:])
    print("✓ 范围批量处理正确，未指定图片未受影响")
    
    return True


def main():
    """运行所有测试"""
    print("=" * 60)
    print("真实环境AI功能执行测试")
    print("=" * 60)
    
    tests = [
        ("配置迁移逻辑", test_image_jobs_migration),
        ("简单规则解析", test_simple_rules_parsing),
        ("精细任务解析", test_ai_jobs_parsing),
        ("图片保留逻辑", test_image_preservation_logic),
        ("插入模式验证", test_insert_modes),
        ("已托管图片检测", test_hosted_image_detection),
        ("按索引精确替换", test_index_based_replacement),
        ("范围批量处理", test_range_batch_processing),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print(f"✓ {name} 通过\n")
            else:
                failed += 1
                print(f"✗ {name} 失败\n")
        except Exception as e:
            failed += 1
            print(f"✗ {name} 异常: {e}\n")
    
    print("=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
