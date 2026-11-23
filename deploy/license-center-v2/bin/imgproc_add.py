
import base64, time as _time

@app.route("/v1/images/edits", methods=["POST"])
def edits_fast():
    try:
        prompt = request.form.get("prompt", "") or "keep the product unchanged, clean product photo"
        files = request.files.getlist("image")
        if not files:
            return jsonify({"error": {"message": "no image", "type": "invalid_request_error"}}), 400
        content = []
        for f in files[:10]:
            b = f.read()
            mime = f.mimetype or "image/jpeg"
            content.append({"image": "data:%s;base64,%s" % (mime, base64.b64encode(b).decode())})
        content.append({"text": prompt})
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
                    url = c["image"]; break
        except Exception:
            pass
        if not url:
            return jsonify({"error": {"message": str(j)[:300], "type": "upstream_error"}}), 400
        img = requests.get(url, timeout=120)
        fn = uuid.uuid4().hex + ".png"
        with open(os.path.join(STORE, fn), "wb") as f2:
            f2.write(img.content)
        return jsonify({"created": int(_time.time()), "data": [{"url": PUBLIC + "/" + fn, "revised_prompt": prompt}]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": {"message": str(e)[:300], "type": "server_error"}}), 500

