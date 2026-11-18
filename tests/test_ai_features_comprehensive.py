"""全面AI功能验证测试

测试所有AI功能是否正常工作：
1. AI文本生成（标题、描述、SKU）
2. AI图片处理（白底、去水印、编辑、四合一）
3. 内容审查
4. 估重

不调用真实API，使用mock验证逻辑正确性。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import Mock, MagicMock, patch
import tempfile


def test_ai_title_generation():
    """测试AI标题生成逻辑"""
    print("=" * 60)
    print("测试 1: AI标题生成")
    print("=" * 60)
    
    from aeclone.services.text_pipeline import TextPipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.ai_title_enable = True
    cfg.title_prompt = "生成英文标题"
    
    # Mock API客户端
    mock_client = Mock()
    mock_client.chat_complete = Mock(return_value="3 Ton Mini Spider Crane with 20m Boom")
    mock_client.chat = Mock(return_value="3 Ton Mini Spider Crane with 20m Boom")
    
    tp = TextPipeline(cfg, client=mock_client)
    
    product = {
        "title": "3吨蜘蛛吊车 20米臂长",
        "description": "适用于狭窄空间作业"
    }
    
    result = tp.make_title(product.get("title", ""), product.get("description", ""))
    
    print(f"原标题: {product['title']}")
    print(f"生成标题: {result.value if result.ok else result.error}")
    print(f"状态: {'✓ 成功' if result.ok else '✗ 失败'}")
    
    assert result.ok, f"标题生成失败: {result.error}"
    assert len(result.value) > 0, "生成的标题为空"
    print()


def test_ai_description_generation():
    """测试AI描述生成逻辑"""
    print("=" * 60)
    print("测试 2: AI描述生成")
    print("=" * 60)
    
    from aeclone.services.text_pipeline import TextPipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.ai_desc_enable = True
    
    mock_client = Mock()
    mock_client.chat_complete = Mock(return_value="<p>High-quality mini spider crane...</p>")
    mock_client.chat = Mock(return_value="<p>High-quality mini spider crane...</p>")
    
    tp = TextPipeline(cfg, client=mock_client)
    
    product = {"title": "Spider Crane", "description": "原始描述"}
    result = tp.make_detail(product.get("title", ""),
                            {"description": product.get("description", "")})
    
    print(f"原标题: {product['title']}")
    print(f"生成描述: {result.value[:50] if result.ok else result.error}...")
    print(f"状态: {'✓ 成功' if result.ok else '✗ 失败'}")
    
    assert result.ok, f"描述生成失败: {result.error}"
    print()


def test_ai_sku_names():
    """测试AI SKU名称优化"""
    print("=" * 60)
    print("测试 3: AI SKU名称优化")
    print("=" * 60)
    
    from aeclone.services.text_pipeline import TextPipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.enable_ai_sku = True
    
    mock_client = Mock()
    mock_client.chat_complete = Mock(return_value="Red - Small\nRed - Medium\nBlue - Small")
    mock_client.chat = Mock(return_value="Red - Small\nRed - Medium\nBlue - Small")
    
    tp = TextPipeline(cfg, client=mock_client)
    
    sku_names = ["红色-S", "红色-M", "蓝色-S"]
    result = tp.make_skus(sku_names)
    
    print(f"原SKU: {sku_names}")
    print(f"优化后: {result.value.splitlines() if result.ok else result.error}")
    print(f"状态: {'✓ 成功' if result.ok else '✗ 失败'}")
    
    assert result.ok, f"SKU优化失败: {result.error}"
    assert len(result.value.splitlines()) == len(sku_names), "SKU数量不匹配"
    print()


def test_image_white_background():
    """测试白底图处理"""
    print("=" * 60)
    print("测试 4: 白底图处理")
    print("=" * 60)
    
    from aeclone.services.image_pipeline import ImagePipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.white_bg_enable = True
    
    # Mock下载和API
    with patch('aeclone.services.image_pipeline.requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        mock_client = Mock()
        mock_client.images_edit = Mock(return_value="https://result.com/white_bg.png")
        
        pipeline = ImagePipeline(cfg, client=mock_client)
        
        result = pipeline.run("https://example.com/original.jpg", white_bg=True)
        
        print(f"原图: https://example.com/original.jpg")
        print(f"处理后: {result.last_url() if result.ok else '失败'}")
        print(f"状态: {'✓ 成功' if result.ok else '✗ 失败'}")
        print(f"步骤: {[s.name for s in result.steps]}")
        
        assert result.ok, "白底图处理失败"
        assert any(s.name == "白底" for s in result.steps), "缺少白底步骤"
    print()


def test_image_jobs_precise_replacement():
    """测试图片任务精确替换逻辑"""
    print("=" * 60)
    print("测试 5: 图片任务精确替换")
    print("=" * 60)
    
    from aeclone.services.image_jobs import parse_rules, parse_jobs
    
    # 简单规则：主图1-3白底
    rules_json = '[{"group":"main","range":"1-3","ops":["white"]}]'
    rules = parse_rules(rules_json)
    
    print(f"简单规则: 主图1-3白底")
    print(f"解析结果: {rules}")
    
    assert len(rules) == 1, "规则数量错误"
    assert rules[0]["group"] == "main", "分组错误"
    assert rules[0]["range"] == "1-3", "范围错误"
    assert "white" in rules[0]["ops"], "操作缺失"
    
    # 精细任务：第5张图用提示词编辑
    jobs_json = '[{"group":"main","index":5,"refs":"1,2","prompt":"test","mode":"edit","count":1,"insert":"replace"}]'
    jobs = parse_jobs(jobs_json)
    
    print(f"\n精细任务: 第5张图编辑")
    print(f"解析结果: {jobs}")
    
    assert len(jobs) == 1, "任务数量错误"
    assert jobs[0]["index"] == 5, "索引错误"
    assert jobs[0]["insert"] == "replace", "插入模式错误"
    
    print("✓ 图片任务解析正确")
    print()


def test_content_review():
    """测试内容审查"""
    print("=" * 60)
    print("测试 6: 内容审查")
    print("=" * 60)
    
    from aeclone.services.text_pipeline import TextPipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.review_enable = True
    
    mock_client = Mock()
    mock_client.chat = Mock(return_value="PASS")

    tp = TextPipeline(cfg, client=mock_client)

    # 测试通过
    mock_client.chat_complete = Mock(return_value="PASS")
    result = tp.review_text("This is a clean product title")
    
    print(f"测试1 - 合规内容")
    print(f"  输入: 'This is a clean product title'")
    print(f"  审查结果: {result.value}")
    print(f"  状态: {'✓ 通过' if result.ok and 'PASS' in result.value.upper() else '✗ 失败'}")
    
    assert result.ok, "审查调用失败"
    assert "PASS" in result.value.upper(), "应该返回PASS"
    
    # 测试拒绝
    mock_client.chat_complete = Mock(return_value="FAIL|含有联系方式")
    mock_client.chat = Mock(return_value="FAIL|含有联系方式")
    result = tp.review_text("Contact: +86 12345678")
    
    print(f"\n测试2 - 违规内容")
    print(f"  输入: 'Contact: +86 12345678'")
    print(f"  审查结果: {result.value}")
    print(f"  状态: {'✓ 正确拒绝' if result.ok and 'FAIL' in result.value.upper() else '✗ 失败'}")
    
    assert result.ok, "审查调用失败"
    assert "FAIL" in result.value.upper(), "应该返回FAIL"
    print()


def test_weight_estimation():
    """测试重量估算"""
    print("=" * 60)
    print("测试 7: 重量估算")
    print("=" * 60)
    
    from aeclone.services.text_pipeline import TextPipeline
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.estimate_weight_enable = True
    
    mock_client = Mock()
    mock_client.chat_vision = Mock(return_value="2.5 kg")
    mock_client.chat = Mock(return_value="2.5 kg")
    
    tp = TextPipeline(cfg, client=mock_client)
    
    result = tp.estimate_weight(
        "https://example.com/crane.jpg",
        "Mini Spider Crane",
    )
    
    print(f"产品: Mini Spider Crane")
    print(f"估算重量: {result.value if result.ok else result.error}")
    print(f"状态: {'✓ 成功' if result.ok else '✗ 失败'}")
    
    assert result.ok, "重量估算失败"
    assert "kg" in result.value.lower() or "g" in result.value.lower(), "结果应包含重量单位"
    print()


def test_pricing_with_commission():
    """测试佣金定价"""
    print("=" * 60)
    print("测试 8: 佣金定价计算")
    print("=" * 60)
    
    from aeclone.services.pricing import compute_price
    from aeclone.config import AppConfig
    
    cfg = AppConfig()
    cfg.price_mode = "commission"
    cfg.price_multiplier = 1.2  # 20%利润
    cfg.platform_commission = 5.0  # 5%佣金
    
    cost = 100.0
    result = compute_price(cfg, cost)
    
    expected = 126.32  # 100 * 1.2 / 0.95
    
    print(f"成本: ¥{cost}")
    print(f"倍率: {cfg.price_multiplier} (利润20%)")
    print(f"佣金: {cfg.platform_commission}%")
    print(f"预期售价: ¥{expected}")
    print(f"实际售价: ¥{result.final}")
    print(f"差异: {abs(result.final - expected):.2f}")
    print(f"状态: {'✓ 正确' if abs(result.final - expected) < 0.01 else '✗ 错误'}")
    
    assert abs(result.final - expected) < 0.01, f"定价计算错误: {result.final} != {expected}"
    print()


def main():
    print("\n" + "=" * 60)
    print("AuroraList 全面AI功能验证测试")
    print("=" * 60)
    print()
    
    tests = [
        ("AI标题生成", test_ai_title_generation),
        ("AI描述生成", test_ai_description_generation),
        ("AI SKU优化", test_ai_sku_names),
        ("白底图处理", test_image_white_background),
        ("图片任务精确替换", test_image_jobs_precise_replacement),
        ("内容审查", test_content_review),
        ("重量估算", test_weight_estimation),
        ("佣金定价", test_pricing_with_commission),
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
            import traceback
            traceback.print_exc()
    
    print("=" * 60)
    print(f"测试总结: {passed}/{len(tests)} 通过")
    if failed:
        print(f"失败: {', '.join(failed)}")
        return 1
    else:
        print("✓ 所有AI功能测试通过")
        return 0


if __name__ == "__main__":
    exit(main())
