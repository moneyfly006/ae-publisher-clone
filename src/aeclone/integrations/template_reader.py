"""模板参数读取：打开店小秘编辑器页面，从表单里抽取类目/运费/属性等。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

EDITOR_URL = "https://www.dianxiaomi.com/web/smt/edit?id={pid}"

KEY_LABELS = {
    "category": ["产品分类", "分类"],
    "freight": ["运费模板", "运费"],
    "weight": ["重量", "毛重"],
    "size": ["尺寸", "包装尺寸"],
    "unit": ["单位", "产品单位"],
    "brand": ["品牌"],
    "origin": ["产地"],
    "type": ["商品类型"],
    "description": ["产品描述", "详情描述"],
}


@dataclass
class TemplateSnapshot:
    product_id: str = ""
    title: str = ""
    fields: dict = field(default_factory=dict)
    attributes: list = field(default_factory=list)

    raw_items: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"product_id": self.product_id, "title": self.title,
                           "fields": self.fields, "attributes": self.attributes},
                          ensure_ascii=False)


def read_snapshot(ws_endpoint: str, product_id: str, timeout: int = 60000) -> TemplateSnapshot:
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(ws_endpoint, timeout=30000)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(EDITOR_URL.format(pid=product_id), wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(10000)
        data = page.evaluate("""() => {
            const out = {items: [], attrs: [], title: ''};
            document.querySelectorAll('.ant-form-item').forEach(fi => {
                const lab = (fi.querySelector('.ant-form-item-label')?.innerText || '').trim();
                const ctl = fi.querySelector('input, textarea, .ant-select-selection-item');
                let val = '';
                if (ctl) val = ('value' in ctl && ctl.value) ? ctl.value : (ctl.innerText || '').trim();
                if (lab && val) out.items.push([lab.replace(/ +/g,' '), String(val).slice(0, 200)]);
            });
            document.querySelectorAll('input[placeholder="属性名 - 例如：Color"], input[placeholder^="属性名"]').forEach(inp => {
                const row = inp.closest('div');
                const vals = row ? Array.from(row.querySelectorAll('input')).map(i => i.value).filter(Boolean) : [];
                if (vals.length >= 2) out.attrs.push(vals.slice(0, 2));
            });
            // 下拉类（类目/运费模板等）
            document.querySelectorAll('.ant-select-selection-item, .ant-select-selection-selected-value').forEach(el => {
                const fi = el.closest('.ant-form-item');
                const lab = fi ? (fi.querySelector('.ant-form-item-label')?.innerText || '').trim() : '';
                const val = (el.getAttribute('title') || el.innerText || '').trim();
                if (lab && val) out.items.push([lab.replace(/ +/g,' '), String(val).slice(0, 200)]);
            });
            // 页面里直接显示的类目文本
            const body = document.body.innerText || '';
            const m = body.match(/产品分类[\\s\\S]{0,80}/);
            if (m) out.items.push(["产品分类", m[0].replace(/ +/g,' ').replace('产品分类','').trim().slice(0,120)]);
            const f = body.match(/运费模板[\\s\\S]{0,60}/);
            if (f) out.items.push(["运费模板", f[0].replace(/ +/g,' ').replace('运费模板','').trim().slice(0,80)]);
            const t = document.querySelector('input[type=text]');
            out.title = t ? t.value : '';
            return out;
        }""")
        snap = TemplateSnapshot(product_id=str(product_id), title=(data.get("title") or "")[:200])
        for label, value in data.get("items") or []:
            for key, names in KEY_LABELS.items():
                if any(n in label for n in names) and key not in snap.fields:
                    snap.fields[key] = value
        snap.attributes = [f"{a[0]}={a[1]}" for a in (data.get("attrs") or []) if len(a) >= 2]
        snap.raw_items = data.get("items") or []
        return snap
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()
