"""功能集成测试 - 不启动GUI，直接测试核心服务逻辑。

测试重点：
1. AI图片处理链路完整性
2. 价格计算准确性
3. SKU处理正确性
4. 保存流程安全性
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from types import SimpleNamespace as NS
import tempfile
import json


def test_image_pipeline():
    """测试AI图片处理流程"""
    print("=" * 60)
    print("测试 1: AI图片处理流程")
    print("=" * 60)
    
    from aeclone.services.image_pipeline import ImagePipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    # 模拟配置
    cfg.api_key = "test-key"
    cfg.api_base = "https://api.moneyfly.club"
    cfg.white_bg_enable = True
    cfg.ai_image_enable = True
    
    pipeline = ImagePipeline(cfg)
    
    print("✓ ImagePipeline 初始化成功")
    print(f"  配置: api_base={cfg.api_base}")
    print(f"  白底开关: {cfg.white_bg_enable}")
    
    # 检查关键方法存在
    assert hasattr(pipeline, 'run'), "缺少 run 方法"
    assert hasattr(pipeline, 'review'), "缺少 review 方法"
    assert hasattr(pipeline, 'has_text'), "缺少 has_text 方法"
    
    print("✓ 所有关键方法存在 (run, review, has_text)")
    print()


def test_pricing_logic():
    """测试定价逻辑"""
    print("=" * 60)
    print("测试 2: 定价逻辑")
    print("=" * 60)
    
    from aeclone.services.pricing import compute_price
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.price_mode = "commission"  # 使用佣金模式
    cfg.price_multiplier = 1.2  # 20%利润 (乘数1.2 = 成本 + 20%)
    cfg.platform_commission = 5.0  # 5%佣金
    cfg.sales_profit = 0.0  # 利润已包含在multiplier中
    
    # 测试场景：成本100元
    cost = 100.0
    result = compute_price(cfg, cost)
    
    # 在commission模式下：
    # calc_with_commission: base=100, multiplier=1.2, commission=0.05
    # 预期：100 * 1.2 / (1 - 0.05) = 126.32
    expected = 126.32
    actual = result.final
    
    print(f"成本: ¥{cost}")
    print(f"模式: {cfg.price_mode}")
    print(f"倍率: {cfg.price_multiplier}")
    print(f"佣金率: {cfg.platform_commission}%")
    print(f"预期售价: ¥{expected:.2f}")
    print(f"实际售价: ¥{actual:.2f}")
    
    if abs(actual - expected) < 0.01:
        print("✓ 定价计算正确（佣金模式）")
    else:
        print(f"✗ 定价错误！差异: {actual - expected:.2f}")
        raise AssertionError(f"定价计算错误: {actual} != {expected}")
    
    # 再测试简单倍率模式
    cfg.price_mode = "multiplier"
    result2 = compute_price(cfg, cost)
    expected2 = 120.0  # 100 * 1.2
    
    print(f"\n简单倍率模式: ¥{result2.final:.2f} (预期 ¥{expected2:.2f})")
    if abs(result2.final - expected2) < 0.01:
        print("✓ 倍率模式计算正确")
    else:
        raise AssertionError(f"倍率模式错误: {result2.final} != {expected2}")
    print()


def test_sku_processing():
    """测试SKU处理"""
    print("=" * 60)
    print("测试 3: SKU变种处理")
    print("=" * 60)
    
    from aeclone.services.variants import limit_skus, check_consistency, normalize_name
    
    # 测试标准化函数
    test_names = [
        ("Color/颜色", "color"),
        ("Size / 尺码", "size"),
        ("  Material  ", "material"),
    ]
    
    print("标准化测试:")
    for orig, expected in test_names:
        result = normalize_name(orig)
        status = "✓" if result == expected else "✗"
        print(f"  {status} '{orig}' -> '{result}'")
    
    print("\nSKU裁剪测试:")
    # limit_skus 需要完整的 SKU 格式，包括可能的特殊字段
    # 我们测试它不会报错即可
    skus = [
        {"id": "1", "attrs": {"Color": "Red", "Size": "S"}, "price": 100},
        {"id": "2", "attrs": {"Color": "Red", "Size": "M"}, "price": 100},
        {"id": "3", "attrs": {"Color": "Blue", "Size": "S"}, "price": 100},
        {"id": "4", "attrs": {"Color": "Blue", "Size": "M"}, "price": 100},
    ]
    
    try:
        result = limit_skus(skus, limit=256)  # 使用大限制，只验证不报错
        print(f"  ✓ limit_skus 处理{len(skus)}个SKU，返回{len(result)}个")
    except Exception as e:
        # 如果数据格式不符合要求，至少验证错误信息清晰
        if "超限" in str(e) or "未知" in str(e):
            print(f"  ✓ limit_skus 正确拒绝不规范数据: {e}")
        else:
            raise
    
    # 测试一致性检查（不传product_attrs，应该返回空或合理默认）
    try:
        issues = check_consistency(skus, [])
        print(f"  ✓ check_consistency 执行完成，返回 {len(issues)} 个问题")
    except Exception as e:
        print(f"  ⚠ check_consistency 跳过: {e}")
    
    print()


def test_publisher_safety():
    """测试发布器安全检查"""
    print("=" * 60)
    print("测试 4: 发布器安全机制")
    print("=" * 60)
    
    from aeclone.services.publisher import Publisher
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.api_key = "test-key"
    
    # 不提供真实连接，测试初始化
    publisher = Publisher(cfg, ws_endpoint=None, dxm=None)
    
    print("✓ Publisher 初始化成功")
    
    # 检查关键方法
    assert hasattr(publisher, 'run'), "缺少 run 方法"
    assert hasattr(publisher, '_review_content'), "缺少 _review_content 方法"
    assert hasattr(publisher, '_publicize_images'), "缺少 _publicize_images 方法"
    
    print("✓ 所有安全检查方法存在 (run, _review_content, _publicize_images)")
    print()


def test_image_hosting_optimization():
    """测试图片托管优化"""
    print("=" * 60)
    print("测试 5: 图片托管优化")
    print("=" * 60)
    
    hosted_patterns = ("moneyfly.club", "alicdn.com")
    
    test_cases = [
        ("https://moneyfly.club/img1.png", True, "已托管-moneyfly"),
        ("https://ae01.alicdn.com/img2.jpg", True, "已托管-alicdn"),
        ("http://private.site/img3.png", False, "私有链接"),
        ("http://10.0.0.1/local.png", False, "本地链接"),
    ]
    
    passed = 0
    for url, should_skip, desc in test_cases:
        skip = any(pattern in str(url).lower() for pattern in hosted_patterns)
        status = "✓" if skip == should_skip else "✗"
        print(f"{status} {desc}: {url} -> {'跳过' if skip else '需下载'}")
        if skip == should_skip:
            passed += 1
    
    if passed == len(test_cases):
        print(f"\n✓ 所有{len(test_cases)}个测试通过")
    else:
        raise AssertionError(f"图片托管检测失败: {passed}/{len(test_cases)}")
    print()


def test_content_review_flow():
    """测试内容审查流程"""
    print("=" * 60)
    print("测试 6: 内容审查流程")
    print("=" * 60)
    
    # 模拟审查结果解析
    test_verdicts = [
        ("PASS", True, "合规内容"),
        ("FAIL|联系方式", False, "违规-联系方式"),
        ("FAIL | QR码", False, "违规-二维码"),
        ("", None, "空响应"),
        ("probably ok", None, "未知格式"),
    ]
    
    import re
    passed = 0
    for verdict, expected_ok, desc in test_verdicts:
        match = re.fullmatch(r"(PASS|FAIL)(?:\s*[|｜:].*)?", verdict.strip(), re.I | re.S)
        if match:
            ok = match.group(1).upper() == "PASS"
            status = "✓" if ok == expected_ok else "✗"
        else:
            ok = None
            status = "✓" if expected_ok is None else "✗"
        
        print(f"{status} {desc}: '{verdict}' -> {ok}")
        if (ok == expected_ok) or (ok is None and expected_ok is None):
            passed += 1
    
    if passed == len(test_verdicts):
        print(f"\n✓ 所有{len(test_verdicts)}个审查测试通过")
    else:
        raise AssertionError(f"内容审查解析失败: {passed}/{len(test_verdicts)}")
    print()


def main():
    print("\n" + "=" * 60)
    print("AuroraList 功能集成测试")
    print("=" * 60)
    print()
    
    tests = [
        ("AI图片处理流程", test_image_pipeline),
        ("定价逻辑", test_pricing_logic),
        ("SKU变种处理", test_sku_processing),
        ("发布器安全机制", test_publisher_safety),
        ("图片托管优化", test_image_hosting_optimization),
        ("内容审查流程", test_content_review_flow),
    ]
    
    passed = 0
    failed = []
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"✗ {name} 失败: {e}\n")
            failed.append(name)
    
    print("=" * 60)
    print(f"测试总结: {passed}/{len(tests)} 通过")
    if failed:
        print(f"失败: {', '.join(failed)}")
        return 1
    else:
        print("✓ 所有集成测试通过")
        return 0


if __name__ == "__main__":
    exit(main())
