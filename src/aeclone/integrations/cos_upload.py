"""店小秘图片上传：取签名 → PUT 到 COS → 回调登记。

协议来自逆向规格：
  1) 向店小秘申请上传签名（sign / url / fileId）
  2) 以 PUT 方式上传文件到 COS，带签名头
  3) 调用 cos_dxm_call_back 通知平台完成关联
"""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass
class UploadResult:
    file_id: str
    url: str
    size: int
    name: str


class UploadError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_image(client, path: Path, full_cid: str = "", bucket: str = "wxalbum",
                 timeout: int = 120) -> UploadResult:
    """client 需实现 upload_sign(file_name, file_size) -> {sign,url,fileId,...}"""
    path = Path(path)
    if not path.exists():
        raise UploadError(f"文件不存在: {path}")
    data = path.read_bytes()
    size = len(data)
    name = path.name

    sign_info = client.upload_sign(name, size)
    url = sign_info.get("url")
    file_id = sign_info.get("fileId") or sign_info.get("file_id") or ""
    if not url:
        raise UploadError(f"未获取到上传地址: {str(sign_info)[:200]}")

    headers = {"Content-Type": mimetypes.guess_type(name)[0] or "application/octet-stream"}
    for k, v in (sign_info.get("headers") or {}).items():
        headers[k] = v
    r = requests.put(url, data=data, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        raise UploadError(f"COS 上传失败 HTTP {r.status_code}: {r.text[:200]}")

    try:
        client.upload_callback(file_id=file_id, file_name=name, file_size=size,
                               full_cid=full_cid, bucket=bucket)
    except Exception as e:  # 回调失败不影响文件本身
        raise UploadError(f"上传成功但回调登记失败: {e}") from e

    public = sign_info.get("publicUrl") or sign_info.get("cdn") or url.split("?")[0]
    return UploadResult(file_id=file_id, url=public, size=size, name=name)


def upload_many(client, paths: list[Path], full_cid: str = "", on_done=None) -> list[UploadResult]:
    results = []
    for p in paths:
        try:
            res = upload_image(client, Path(p), full_cid=full_cid)
        except Exception as e:  # noqa: BLE001
            if on_done:
                on_done(Path(p), None, str(e))
            continue
        results.append(res)
        if on_done:
            on_done(Path(p), res, "")
    return results
