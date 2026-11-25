"""图片托管：把本地图片上传到图床，换一个长期可访问的公网 URL。

为什么要图床（而不是直接用 AI 返回的地址，也不直接塞本地）：
  1. 店小秘编辑器写主图，只接受「网络图片 URL」，不认本地文件；
  2. AI 模型返回的是**临时签名 URL**（qwen-image-3.0 约 1 天过期），
     直接写进商品，审核期间就会失效；
  3. 所以要有一个「稳定、公网可访问」的中间地址，编辑器和速卖通随时能取到。

支持两种协议，自动识别：
  A. Cloudflare-ImgBed 风格（image.moneyfly.club）
       POST {base}/rest/upload  multipart 字段 files  Header Authorization: <key>
       → {"code":200,"data":[{"url":"..."}]}
  B. 老自建服务（VPS）
       POST {base}/upload       multipart 字段 file
       → {"url":"..."}
"""
from __future__ import annotations

from pathlib import Path

import requests

# 默认用 Cloudflare 图床；可被配置 image_host_url / image_host_key 覆盖
DEFAULT_HOST = "https://image.moneyfly.club"
DEFAULT_KEY = "120423mm"


class HostError(RuntimeError):
    pass


_CACHE: dict[str, str] = {}

# ---------------------------------------------------------------------------
# 上传台账：记录每张图上传时拿到的 delToken，之后才能按时间清理。
# 图床也会满，必须能删；而删除接口要的是 delToken（不是文件名）。
# ---------------------------------------------------------------------------
_LEDGER_NAME = "host_ledger.json"


def _ledger_path() -> Path:
    from ..config import config_dir
    return Path(config_dir()) / _LEDGER_NAME


def _ledger_load() -> dict:
    import json
    try:
        p = _ledger_path()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _ledger_save(d: dict) -> None:
    import json
    try:
        p = _ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _ledger_add(url: str, token: str, name: str = "") -> None:
    import time
    if not url or not token:
        return
    d = _ledger_load()
    d[url] = {"token": token, "ts": int(time.time()), "name": name,
              "host": url.split("/rest/")[0] if "/rest/" in url else ""}
    # 只留最近 5000 条，避免文件无限增长
    if len(d) > 5000:
        for k in sorted(d, key=lambda x: d[x].get("ts", 0))[: len(d) - 5000]:
            d.pop(k, None)
    _ledger_save(d)


def _hash_of(path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(base: str | None, key: str | None) -> tuple[str, str]:
    """没显式传参时，从配置读图床地址与密钥。"""
    b = base
    k = key
    if not b or k is None:
        try:
            from ..config import get_config
            cfg = get_config()
            if not b:
                b = (getattr(cfg, "image_host_url", "") or "").strip() or DEFAULT_HOST
            if k is None:
                _k = getattr(cfg, "image_host_key", None)
                k = DEFAULT_KEY if _k is None else str(_k)
        except Exception:
            b = b or DEFAULT_HOST
            k = DEFAULT_KEY if k is None else k
    return str(b).rstrip("/"), str(k or "")


def _try_cf_imgbed(path: Path, base: str, key: str, folder: str, timeout: int) -> str | None:
    """Cloudflare-ImgBed 协议；不适用就返回 None。"""
    try:
        headers = {"Authorization": key} if key else {}
        data: dict[str, str] = {"keepName": "false"}   # 让服务端生成唯一名，避免重名覆盖
        if folder:
            data["path"] = folder.strip("/")
        with open(path, "rb") as f:
            r = requests.post(f"{base}/rest/upload", headers=headers,
                              files={"files": (path.name, f, "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png")},
                              data=data, timeout=timeout)
        if r.status_code >= 400:
            return None
        j = r.json()
        items = j.get("data") or []
        if isinstance(items, list) and items:
            it = items[0]
            url = str(it.get("url") or "")
            if url.startswith("http"):
                _ledger_add(url, str(it.get("delToken") or ""), str(it.get("filename") or path.name))
                return url
        url = str(j.get("url") or "")
        return url if url.startswith("http") else None
    except Exception:
        return None


def _try_legacy(path: Path, base: str, timeout: int) -> str | None:
    """老自建服务：POST /upload, 字段 file, 返回 {url}。"""
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{base}/upload",
                              files={"file": (path.name, f, "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png")}, timeout=timeout)
        if r.status_code >= 400:
            return None
        url = str((r.json() or {}).get("url") or "")
        return url if url.startswith("http") else None
    except Exception:
        return None


def _prepare_image(path: Path) -> tuple[Path, bool]:
    """平台图片抓取对 RGBA/CMYK PNG 兼容差；统一转 RGB JPEG 再上传。"""
    try:
        from PIL import Image
        im = Image.open(path)
        if im.mode in ("RGBA", "LA", "P", "CMYK", "LAB"):
            im = im.convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        else:
            return path, False
        tmp = path.with_suffix(".upload.jpg")
        im.save(tmp, "JPEG", quality=92, optimize=True)
        return tmp, True
    except Exception:
        return path, False


def upload(path: Path, base: str | None = None, timeout: int = 90,
           key: str | None = None, folder: str = "") -> str:
    """上传一张图，返回公网 URL。同内容重复上传命中进程内缓存。"""
    path = Path(path)
    if not path.exists():
        raise HostError(f"文件不存在: {path}")
    h = _hash_of(path)
    if h in _CACHE:
        return _CACHE[h]

    b, k = _resolve(base, key)
    send_path, _converted = _prepare_image(path)
    url = _try_cf_imgbed(send_path, b, k, folder, timeout) or _try_legacy(send_path, b, timeout)
    # 旧配置可能指向已超时的自建地址；失败后回退官方图床，避免整批营销图失败。
    if not url and b.rstrip("/") != DEFAULT_HOST.rstrip("/"):
        try:
            url = (_try_cf_imgbed(send_path, DEFAULT_HOST, DEFAULT_KEY, folder, timeout)
                   or _try_legacy(send_path, DEFAULT_HOST, timeout))
        except Exception:
            url = None
    if not url:
        raise HostError(f"上传失败：{b}（两种协议都试过了，检查地址/密钥/网络）")

    _CACHE[h] = url
    if _converted:
        try:
            send_path.unlink(missing_ok=True)
        except Exception:
            pass
    return url


def list_files(base: str | None = None, key: str | None = None, page_size: int = 100,
               max_pages: int = 20) -> list[dict]:
    """列出图床上的文件（只读）。接口不可用时返回空列表。"""
    b, k = _resolve(base, key)
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            r = requests.post(f"{b}/rest/list",
                              headers={"Authorization": k} if k else {},
                              json={"page": page, "pageSize": page_size}, timeout=30)
            if r.status_code >= 400:
                break
            j = r.json()
            items = (j.get("data") or {}).get("list") or []
            if not items:
                break
            out.extend(items)
            if len(items) < page_size:
                break
        except Exception:
            break
    return out


def delete_by_token(token: str, base: str | None = None, key: str | None = None) -> bool:
    """按 delToken 删除一张图。"""
    if not token:
        return False
    b, k = _resolve(base, key)
    try:
        r = requests.post(f"{b}/rest/delImage/{token}",
                          headers={"Authorization": k} if k else {}, timeout=30)
        j = r.json() if r.status_code < 400 else {}
        return str(j.get("code")) == "200"
    except Exception:
        return False


def clean_old(days: int | None = None, base: str | None = None, key: str | None = None) -> dict:
    """删除「N 天前上传」的图（按本地台账里的 delToken）。

    days=None 时读配置 host_keep_days（默认 7）。
    Returns: {"ok":bool, "deleted":int, "failed":int, "kept":int, "message":str}
    """
    import time
    if days is None:
        try:
            from ..config import get_config
            days = int(getattr(get_config(), "host_keep_days", 7) or 7)
        except Exception:
            days = 7
    try:
        days = max(0, int(days))
    except Exception:
        days = 7
    cutoff = int(time.time()) - days * 86400
    d = _ledger_load()
    deleted = failed = kept = 0
    for url, meta in list(d.items()):
        ts = int((meta or {}).get("ts") or 0)
        token = str((meta or {}).get("token") or "")
        if not token:
            d.pop(url, None)
            continue
        if ts > cutoff:          # days=0 时 cutoff=now，全部可删
            kept += 1
            continue
        if delete_by_token(token, base, key):
            d.pop(url, None)
            deleted += 1
        else:
            failed += 1
    _ledger_save(d)
    msg = f"删除 {deleted} 张 / 失败 {failed} / 保留 {kept}（{days} 天内）"
    return {"ok": failed == 0, "deleted": deleted, "failed": failed,
            "kept": kept, "message": msg}


def upload_video_file(path: Path) -> str:
    """把本地视频传到自有文件服务，返回公网 URL（店小秘上传接口已停用时兜底）。

    默认用 VPS 上的老服务 http://132.226.1.44:8099/upload（接受任意文件），
    可用配置 video_host_url 覆盖。
    """
    import requests as _rq
    from ..config import get_config

    p = Path(path)
    if not p.exists():
        raise HostError(f"视频不存在: {p}")
    base = "http://132.226.1.44:8099"
    try:
        base = str(getattr(get_config(), "video_host_url", "") or base).rstrip("/")
    except Exception:
        pass
    try:
        with open(p, "rb") as fh:
            r = _rq.post(base + "/upload", files={"file": (p.name, fh, "video/mp4")}, timeout=300)
        r.raise_for_status()
        j = r.json()
        url = str((j or {}).get("url") or "")
        if url.startswith("http"):
            return url
    except Exception as ex:  # noqa: BLE001
        raise HostError(f"视频托管失败：{str(ex)[:120]}")
    raise HostError("视频托管失败：未返回 URL")
