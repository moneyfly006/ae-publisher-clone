#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图像处理服务（白底/去水印）：直连阿里百炼 qwen-image-2.1-turbo，单张约 8 秒"""
import time
MAX_EDIT_IMAGES = 4
MAX_EDIT_SIDE = 1024
# 结果缓存：同一输入图 10 分钟内重复请求不再调用阿里（省钱）
_CACHE_TTL = 600
_result_cache = {}   # key: sha256(图片字节 + 归一化提示词) -> (url, ts)
_output_index = {}   # key: sha256(结果图字节) -> (url, ts)
import os, uuid, threading, datetime, traceback, base64, hashlib, time
import requests
from flask import Flask, request, jsonify, send_from_directory

API = "https://api.moneyfly.club"
DS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DS_KEY = "sk-ws-H.YPHIXMP.mcy4.MEUCIQDi1DvBPyX1fEHobvA6h4ELctDxGRRFOpG7awyFpeP-lgIgdy-kt6hktAykUnI5GGFDJYmFuaQCO1jVodAzkdwaxI8"
MODEL = "qwen-image-2.1-turbo"
STORE = "/www/wwwroot/api.moneyfly.club/imgres"
PUBLIC = "http://132.226.1.44:8099/imgres"

app = Flask(__name__)
TASKS = {}

PROMPTS = {
    1: ("Remove the background completely and place the product on a pure white background (#FFFFFF). "
        "Keep the product's original shape, color, texture and details unchanged. "
        "Do not add any text, shadow or decoration. Output a clean e-commerce product photo."),
    2: ("Remove all watermarks, logos, QR codes, promotional text and labels from this product image. "
        "Keep the product itself unchanged, preserve its original details and colors. "
        "Output a clean product photo without any overlay."),
}

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def worker(tid):
    t = TASKS[tid]
    try:
        t["status"] = 1
        t["submittedAt"] = now()

        key = t.get("apiKey") or ""
        if key:
            # 走客户站点计费：new-api -> 本地 turbo 渠道
            files = [("image", ("in.jpg", requests.get(t["imageUrl"], timeout=90).content, "image/jpeg"))]
            r = requests.post(API + "/v1/images/edits",
                              headers={"Authorization": "Bearer " + key},
                              data={"model": "qwen-image-2.1-turbo", "prompt": PROMPTS.get(t["type"], PROMPTS[1])},
                              files=files, timeout=180)
            j = r.json()
            url = (j.get("data") or [{}])[0].get("url")
            if not url:
                raise RuntimeError(str(j)[:300])
            t["status"] = 3
            t["completedAt"] = now()
            t["resultImageUrl"] = url
            return
        payload = {
            "model": MODEL,
            "input": {"messages": [{"role": "user", "content": [
                {"image": t["imageUrl"]},
                {"text": PROMPTS.get(t["type"], PROMPTS[1])},
            ]}]},
            "parameters": {"size": "1024*1024", "n": 1},
        }
        r = requests.post(DS_URL, headers={"Authorization": "Bearer " + DS_KEY,
                                           "Content-Type": "application/json"},
                          json=payload, timeout=180)
        j = r.json()
        content = (((j.get("output") or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or []
        url = None
        for c in content:
            if isinstance(c, dict) and c.get("image"):
                url = c["image"]; break
        if not url:
            raise RuntimeError(str(j)[:300])
        img = requests.get(url, timeout=120)
        fn = tid + ".png"
        with open(os.path.join(STORE, fn), "wb") as f:
            f.write(img.content)
        t["status"] = 3
        t["completedAt"] = now()
        t["resultImageUrl"] = PUBLIC + "/" + fn
    except Exception as e:
        t["status"] = 4
        t["completedAt"] = now()
        t["errorMessage"] = str(e)[:300]
        traceback.print_exc()

@app.route("/api/image-processing/enqueue", methods=["POST"])
def enqueue():
    b = request.get_json(force=True, silent=True) or {}
    tid = uuid.uuid4().hex
    t = {"taskId": tid, "userId": 0, "type": b.get("type", 1), "model": MODEL,
         "messages": None, "input": None, "imageUrl": b.get("imageUrl"),
         "returnForm": b.get("returnForm", "url"), "status": 0,
         "createdAt": now(), "submittedAt": None, "completedAt": None,
         "result": None, "resultImageUrl": None, "errorMessage": None,
         "retryCount": 0, "queryRetryCount": 0, "apiKey": b.get("apiKey")}
    TASKS[tid] = t
    threading.Thread(target=worker, args=(tid,), daemon=True).start()
    return jsonify({"data": {"taskId": tid, "message": "任务已加入队列", "createdAt": t["createdAt"],
                             "traceId": uuid.uuid4().hex}, "success": True, "code": 1, "msg": None})

@app.route("/api/image-processing/tasks/<tid>", methods=["GET"])
def get_task(tid):
    t = TASKS.get(tid)
    if not t:
        return jsonify({"data": None, "success": True, "code": 1, "msg": "task not found"})
    return jsonify({"data": t, "success": True, "code": 1, "msg": None})

@app.route("/imgres/<fn>", methods=["GET"])
def imgres(fn):
    return send_from_directory(STORE, fn)


import base64, time as _time

@app.route("/v1/images/generations", methods=["POST"])
def generations():
    try:
        j = request.get_json(silent=True) or {}
        prompt = (j.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": {"message": "prompt required", "type": "invalid_request_error"}}), 400
        model = j.get("model") or "z-image-turbo"
        if model not in ("qwen-image-3.0", "qwen-image-2.1-turbo", "qwen-image-2.0", "z-image-turbo", "wan2.7-image"):
            model = "z-image-turbo"
        size = j.get("size") or "1024*1024"
        if "x" in str(size):
            size = str(size).replace("x", "*")
        payload = {"model": model,
                   "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                   "parameters": {"size": size, "n": 1}}
        r = requests.post(DS_URL, headers={"Authorization": "Bearer " + DS_KEY,
                                           "Content-Type": "application/json"},
                          json=payload, timeout=240)
        jj = r.json()
        content = (((jj.get("output") or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or []
        url = next((c["image"] for c in content if isinstance(c, dict) and c.get("image")), None)
        if not url:
            return jsonify({"error": {"message": str(jj)[:300], "type": "upstream_error"}}), 502
        img = requests.get(url, timeout=120)
        fn = uuid.uuid4().hex + ".png"
        with open(os.path.join(STORE, fn), "wb") as f:
            f.write(img.content)
        return jsonify({"created": int(time.time()), "data": [{"url": PUBLIC + "/" + fn}]})
    except Exception as e:
        return jsonify({"error": {"message": str(e)[:300], "type": "server_error"}}), 500


@app.route("/v1/images/edits", methods=["POST"])
def edits_fast():
    try:
        prompt = request.form.get("prompt", "") or "keep the product unchanged, clean product photo"
        low = prompt.lower()
        if ("white background" in low or "白底" in prompt) and "watermark" not in low:
            prompt = prompt + " Also remove all watermarks, logos, QR codes and promotional text."
        elif ("watermark" in low or "去水印" in prompt) and "white background" not in low:
            prompt = prompt + " Also place the product on a pure white background."

        content = []
        files = []
        for k in ("image", "image[]", "images", "images[]", "file", "files", "img"):
            files.extend(request.files.getlist(k))

        if files:
            for f in files[:MAX_EDIT_IMAGES]:
                b = f.read()
                mime = f.mimetype or "image/jpeg"
                try:
                    from PIL import Image as _Img
                    import io as _io
                    im = _Img.open(_io.BytesIO(b)).convert("RGB")
                    if max(im.size) > MAX_EDIT_SIDE:
                        im.thumbnail((MAX_EDIT_SIDE, MAX_EDIT_SIDE))
                        buf = _io.BytesIO()
                        im.save(buf, format="JPEG", quality=88)
                        b = buf.getvalue()
                        mime = "image/jpeg"
                except Exception:
                    pass
                content.append({"image": "data:%s;base64,%s" % (mime, base64.b64encode(b).decode())})
        elif request.is_json:
            j0 = request.get_json(silent=True) or {}
            imgs = j0.get("images") or j0.get("image") or []
            if isinstance(imgs, str):
                imgs = [imgs]
            if j0.get("prompt"):
                prompt = j0["prompt"]
            for u in imgs[:MAX_EDIT_IMAGES]:
                content.append({"image": u})

        if not content:
            return jsonify({"error": {"message": "no image", "type": "invalid_request_error",
                                      "ct": request.content_type,
                                      "form": list(request.form.keys()),
                                      "files": list(request.files.keys())}}), 400

        content.append({"text": prompt})

        # 缓存键 = 图片内容 + 提示词
        h = hashlib.sha256()
        for c in content:
            v = c.get("image", "")
            h.update(v.encode("utf-8", "ignore") if isinstance(v, str) else bytes(v))
        key = h.hexdigest()
        ts = time.time()

        hit = _result_cache.get(key)
        if hit and ts - hit[1] < _CACHE_TTL:
            return jsonify({"created": int(ts), "data": [{"url": hit[0], "cached": True}]})

        # 输入图 == 我们最近的输出（白底 → 去水印这种链式调用）→ 直接返回，不再调用阿里
        raw = None
        try:
            first = content[0].get("image", "")
            if isinstance(first, str) and first.startswith("data:"):
                raw = base64.b64decode(first.split(",", 1)[1])
        except Exception:
            raw = None
        if raw:
            okey = hashlib.sha256(raw).hexdigest()
            ohit = _output_index.get(okey)
            if ohit and ts - ohit[1] < _CACHE_TTL:
                return jsonify({"created": int(ts), "data": [{"url": ohit[0], "cached": True, "chained": True}]})

        payload = {"model": "qwen-image-2.1-turbo",
                   "input": {"messages": [{"role": "user", "content": content}]},
                   "parameters": {"size": "1024*1024", "n": 1}}
        r = requests.post(DS_URL, headers={"Authorization": "Bearer " + DS_KEY,
                                           "Content-Type": "application/json"},
                          json=payload, timeout=180)
        j = r.json()
        url = None
        try:
            for c in j["output"]["choices"][0]["message"]["content"]:
                if isinstance(c, dict) and c.get("image"):
                    url = c["image"]
                    break
        except Exception:
            pass
        if not url:
            return jsonify({"error": {"message": str(j)[:300], "type": "upstream_error"}}), 502

        img = requests.get(url, timeout=120)
        fn = uuid.uuid4().hex + ".png"
        with open(os.path.join(STORE, fn), "wb") as f2:
            f2.write(img.content)
        out_url = PUBLIC + "/" + fn

        _result_cache[key] = (out_url, ts)
        try:
            _output_index[hashlib.sha256(img.content).hexdigest()] = (out_url, ts)
        except Exception:
            pass
        return jsonify({"created": int(time.time()), "data": [{"url": out_url, "revised_prompt": prompt}]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": {"message": str(e)[:300], "type": "server_error"}}), 500


@app.route("/upload", methods=["POST"])
def upload_file():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    import uuid as _u
    ext = (f.filename or "x.png").rsplit(".", 1)[-1].lower()
    name = _u.uuid4().hex + "." + ext
    path = os.path.join(STORE, name)
    f.save(path)
    return jsonify({"url": PUBLIC + "/" + name, "name": name})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099, threaded=True)
