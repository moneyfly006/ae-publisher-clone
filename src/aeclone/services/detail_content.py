"""Assemble product detail while keeping original text unless replacement is requested."""
import html
import re

def compose_detail(product: dict, cfg) -> str:
    generated = str(product.get("detail_html") or "")
    original = str(product.get("detail") or (product.get("raw") or {}).get("detail") or "")
    content = generated or original
    images = [str(url) for url in product.get("detail_images") or [] if str(url).startswith(("https://", "http://"))]
    if getattr(cfg, "delete_detail_text", False) and not generated:
        content = "".join(re.findall(r'<img\b[^>]*>', content, re.I))
    if images:
        remaining = iter(images)
        def image_tag(match):
            url = next(remaining, None)
            if url is None:
                return ""
            return f'<img src="{html.escape(url, quote=True)}" style="max-width:100%;" />'
        content = re.sub(r'<img\b[^>]*>', image_tag, content, flags=re.I)
        content += "".join(f'<p><img src="{html.escape(url, quote=True)}" style="max-width:100%;" /></p>' for url in remaining)
    if getattr(cfg, "custom_detail_text_enable", False):
        head = str(getattr(cfg, "detail_text_info", "") or "").strip()
        tail = str(getattr(cfg, "detail_text_info_tail", "") or "").strip()
        position = str(getattr(cfg, "detail_text_position", "head") or "head")
        if getattr(cfg, "move_detail_text", False):
            position = "head"
        if head and position in ("head", "both"):
            content = f"<p>{html.escape(head)}</p>" + content
        if tail and position in ("tail", "both"):
            content += f"<p>{html.escape(tail)}</p>"
        local = str(product.get("detail_local_images_html") or "")
        local_position = str(getattr(cfg, "detail_local_image_position", "tail") or "tail")
        if local and local_position in ("head", "both"):
            content = local + content
        if local and local_position in ("tail", "both"):
            content += local
    if getattr(cfg, "copy_sku_to_detail", False) and product.get("skus"):
        names = " / ".join(str(row.get("optimized_name") or row.get("sku") or "") for row in product["skus"][:20])
        content += f"<p>SKU: {html.escape(names)}</p>"
    return content
