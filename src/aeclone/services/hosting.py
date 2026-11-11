"""本地图片托管：上传到我们的图像服务，换取公网 URL。"""
from __future__ import annotations

from pathlib import Path

import requests

DEFAULT_HOST = "http://132.226.1.44:8099"


class HostError(RuntimeError):
    pass


def upload(path: Path, base: str = DEFAULT_HOST, timeout: int = 60) -> str:
    path = Path(path)
    if not path.exists():
        raise HostError(f"文件不存在: {path}")
    with open(path, "rb") as f:
        r = requests.post(f"{base.rstrip('/')}/upload",
                          files={"file": (path.name, f, "image/png")}, timeout=timeout)
    if r.status_code >= 400:
        raise HostError(f"上传失败 HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    url = data.get("url")
    if not url:
        raise HostError(f"未返回 URL: {data}")
    return url
