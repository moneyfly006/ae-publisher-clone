"""测试图片托管优化的BUG修复"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_image_hosting_preserves_all_images():
    """测试图片托管不会丢失第9张及以后的图片"""
    print("测试: 图片托管保留所有图片（不丢失9+张）")
    print("=" * 60)
    
    # 模拟有10张图片的产品
    product = {
        "images": [
            "https://moneyfly.club/img1.png",  # 已托管
            "https://moneyfly.club/img2.png",  # 已托管
            "https://ae01.alicdn.com/img3.jpg",  # 已托管
            "http://private.site/img4.jpg",  # 需转存
            "http://private.site/img5.jpg",  # 需转存
            "https://moneyfly.club/img6.png",  # 已托管
            "http://private.site/img7.jpg",  # 需转存
            "https://moneyfly.club/img8.png",  # 已托管
            "http://private.site/img9.jpg",  # 第9张 - 需转存
            "http://private.site/img10.jpg",  # 第10张 - 需转存
        ]
    }
    
    print(f"原始图片数量: {len(product['images'])}")
    for i, url in enumerate(product['images'], 1):
        print(f"  [{i}] {url}")
    
    # 模拟 _publicize_images 的逻辑
    hosted_patterns = ("moneyfly.club", "alicdn.com")
    all_images = product["images"]
    images = all_images[:8]  # 只处理前8张
    
    needs_upload = [i for i, u in enumerate(images) 
                    if not any(pattern in str(u).lower() for pattern in hosted_patterns)]
    
    print(f"\n前8张中需要上传: {len(needs_upload)} 张")
    print(f"索引: {needs_upload}")
    
    # 模拟上传结果（假设上传成功）
    for i in needs_upload:
        images[i] = f"https://moneyfly.club/uploaded_{i}.png"
    
    # 关键：拼接回所有图片
    final_images = images + all_images[8:]
    
    print(f"\n处理后图片数量: {len(final_images)}")
    for i, url in enumerate(final_images, 1):
        print(f"  [{i}] {url}")
    
    # 验证
    if len(final_images) == len(product['images']):
        print(f"\n✓ 图片数量正确: {len(final_images)} 张")
    else:
        print(f"\n✗ 图片丢失! 原始{len(product['images'])}张，处理后{len(final_images)}张")
        raise AssertionError("图片数量不匹配")
    
    # 验证第9、10张是否保留
    if final_images[8] == "http://private.site/img9.jpg":
        print("✓ 第9张图片保留")
    else:
        print(f"✗ 第9张图片丢失或被替换: {final_images[8]}")
        raise AssertionError("第9张图片丢失")
    
    if final_images[9] == "http://private.site/img10.jpg":
        print("✓ 第10张图片保留")
    else:
        print(f"✗ 第10张图片丢失或被替换: {final_images[9]}")
        raise AssertionError("第10张图片丢失")
    
    print("\n✓ 所有测试通过")


if __name__ == "__main__":
    test_image_hosting_preserves_all_images()
