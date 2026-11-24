"""Durable local task journal. Credentials never enter the database."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import sqlite3
import time
import uuid
from functools import wraps


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


@contextmanager
def exclusive_scope(cfg, platform):
    from ..config import config_dir
    path = config_dir() / "task-locks" / (scope(cfg, platform) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        path.chmod(0o600)
        try:
            import fcntl
        except ImportError:
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("当前店铺/站点已有任务运行，不能重复启动") from None
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("当前店铺/站点已有任务运行，不能重复启动") from None
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def exclusive(operation):
    @wraps(operation)
    def run(cfg, platform, *args, **kwargs):
        with exclusive_scope(cfg, platform):
            return operation(cfg, platform, *args, **kwargs)
    return run


def scope(cfg, platform):
    # Cookies rotate; shop + site + mode identify the listing context. For
    # unbound shops use a non-reversible credential fingerprint as a fallback.
    shop = str(getattr(cfg, platform + "_shop_id", "") or "")
    site = str(getattr(cfg, platform + "_site", "") or "")
    mode = str(getattr(cfg, platform + "_mode", "") or "")
    # 已绑定店铺/站点时不再把 Cookie 指纹算进范围：Cookie 会轮换/刷新，
    # 否则每次切页都会误判成「换了店铺」，把刚保存的批量设置重置掉。
    account = "" if (shop or site or mode) else digest(
        getattr(cfg, platform + "_cookies", "") or getattr(cfg, "manual_cookies", {}))
    return digest([platform, shop, site, mode, account])


def public_settings(cfg, platform):
    return {key: deepcopy(value) for key, value in cfg.as_dict().items()
            if ((key.startswith(platform + "_collect_") and key not in (platform + "_collect_profiles",))
                or (key.startswith(platform + "_") and "prompt" in key and not key.endswith(("_secret", "_key", "_cookies"))))}


@contextmanager
def database():
    from ..config import config_dir
    path = config_dir() / "collection.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, platform TEXT, scope TEXT, started REAL, updated REAL, status TEXT, config TEXT, op TEXT, dry INTEGER, kind TEXT, calls INTEGER DEFAULT 0, cost REAL DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS items (run TEXT, id TEXT, position INTEGER, row TEXT, phase TEXT, prepared TEXT, result TEXT, PRIMARY KEY(run,id))")
        con.execute("CREATE TABLE IF NOT EXISTS ai_cache (key TEXT PRIMARY KEY, value TEXT, created REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS ai_requests (run TEXT, key TEXT, status TEXT, PRIMARY KEY(run,key))")
        path.chmod(0o600)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def create(cfg, platform, rows, *, dry=False, kind="collect"):
    run_id = uuid.uuid4().hex
    now = time.time()
    with database() as con:
        con.execute("INSERT INTO runs(id,platform,scope,started,updated,status,config,op,dry,kind) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (run_id, platform, scope(cfg, platform), now, now, "running", json.dumps(public_settings(cfg, platform), ensure_ascii=False),
                     str(getattr(cfg, platform + "_collect_op", "1")), int(dry), kind))
        for index, row in enumerate(rows):
            pid = str(row.get("idStr") or row.get("id") or "").strip()
            if not pid:
                raise ValueError("任务范围包含空商品ID")
            fields = ("id", "idStr", "title", "subject", "productName", "itemName", "name", "sku")
            if kind == "recovery":
                fields += ("errMsg", "err_msg")
            con.execute("INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?)", (run_id, pid, index,
                json.dumps({k: v for k, v in row.items() if k in fields}, ensure_ascii=False), "queued", "{}", "{}"))
    return run_id


def get(run_id):
    with database() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("任务记录不存在")
        run = dict(row)
        run["config"] = json.loads(run["config"])
        run["items"] = [dict(r) for r in con.execute("SELECT * FROM items WHERE run=? ORDER BY position", (run_id,))]
        for item in run["items"]:
            for key in ("row", "prepared", "result"):
                item[key] = json.loads(item[key])
        return run


def latest(cfg, platform, *, kind=None):
    with database() as con:
        sql = "SELECT id FROM runs WHERE platform=? AND scope=?"
        args = [platform, scope(cfg, platform)]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        row = con.execute(sql + " ORDER BY started DESC LIMIT 1", args).fetchone()
    return get(row[0]) if row else None


def history(cfg, platform, limit=50):
    with database() as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT id,started,status,kind,dry FROM runs WHERE platform=? AND scope=? ORDER BY started DESC LIMIT ?",
            (platform, scope(cfg, platform), limit))]


def item_update(run_id, pid, phase, *, prepared=None, result=None):
    with database() as con:
        con.execute("UPDATE items SET phase=? WHERE run=? AND id=?", (phase, run_id, str(pid)))
        for key, value in (("prepared", prepared), ("result", result)):
            if value is not None:
                con.execute(f"UPDATE items SET {key}=? WHERE run=? AND id=?", (json.dumps(value, ensure_ascii=False), run_id, str(pid)))
        con.execute("UPDATE runs SET updated=? WHERE id=?", (time.time(), run_id))


def finish(run_id, status):
    with database() as con:
        con.execute("UPDATE runs SET status=?,updated=? WHERE id=?", (status, time.time(), run_id))


def reserve(run_id, max_calls, max_cost, estimate, key=None):
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        if key:
            previous = con.execute("SELECT status FROM ai_requests WHERE run=? AND key=?", (run_id, key)).fetchone()
            if previous and previous[0] != "done":
                raise UnknownAIRequest("上次AI请求结果未确认，本次未重复调用；请先核查服务商结果")
        calls, cost = con.execute("SELECT calls,cost FROM runs WHERE id=?", (run_id,)).fetchone()
        if (max_calls and calls >= max_calls) or (max_cost and cost + estimate > max_cost + 1e-9):
            raise BudgetExceeded("AI预算已达到上限，本件未继续生成或提交")
        con.execute("UPDATE runs SET calls=calls+1,cost=cost+? WHERE id=?", (estimate, run_id))
        if key:
            con.execute("INSERT OR REPLACE INTO ai_requests VALUES(?,?,?)", (run_id, key, "pending"))


def request_done(run_id, key):
    with database() as con:
        con.execute("UPDATE ai_requests SET status='done' WHERE run=? AND key=?", (run_id, key))


def update_limits(run_id, limits):
    allowed = {key: value for key, value in limits.items() if key.endswith(("_collect_ai_max_calls", "_collect_ai_budget"))}
    with database() as con:
        row = con.execute("SELECT config FROM runs WHERE id=?", (run_id,)).fetchone()
        config = json.loads(row[0])
        config.update(allowed)
        con.execute("UPDATE runs SET config=? WHERE id=?", (json.dumps(config, ensure_ascii=False), run_id))


class BudgetExceeded(RuntimeError):
    pass


class UnknownAIRequest(BudgetExceeded):
    pass


def cached(key):
    with database() as con:
        row = con.execute("SELECT value FROM ai_cache WHERE key=? AND created>?", (key, time.time() - 30 * 86400)).fetchone()
        return (True, json.loads(row[0])) if row else (False, None)


def cache_put(key, value):
    with database() as con:
        con.execute("INSERT OR REPLACE INTO ai_cache VALUES(?,?,?)", (key, json.dumps(value, ensure_ascii=False), time.time()))
        con.execute("DELETE FROM ai_cache WHERE created<?", (time.time() - 30 * 86400,))


def status(record, op="1"):
    explicit = record.get("outcome")
    if explicit:
        return explicit
    state = str(record.get("state") or "").lower()
    error = str(record.get("error") or "")
    if record.get("checked"):
        return "checked" if record.get("ok") else "needs_info"
    if record.get("status") == "reviewing" or state in ("auditing", "reviewing"):
        return "reviewing"
    if "100521" in error or "正在审查" in error or "审核中" in error:
        from .listing_failures import review_only
        if review_only("amazon", error):
            return "reviewing"
    if state in ("online", "onselling"):
        return "published"
    # 保存到待发布：只有回读到待发布状态才算成功。
    # 直接发布：只有最终进入审核中/在线才算成功。
    if str(op) == "3" and state in ("editingrequired", "waitpublish", "pending"):
        return "pending"
    if str(op) == "2" and state in ("auditing", "reviewing"):
        return "reviewing"
    if str(op) == "2" and state in ("online", "onselling"):
        return "published"
    if state in ("publishing", "processing"):
        return "processing"
    if record.get("saved") and not record.get("ok"):
        return "uncertain" if "回读" in error or "超时" in error else "needs_info"
    if record.get("saved"):
        if str(op) == "1":
            return "draft_saved"
        if str(op) == "3":
            # 已收到保存响应，但没回读到待发布：不能记为成功。
            return "uncertain"
        return "uncertain"
    return "needs_info"


STATUS_LABELS = dict(checked="检查通过", draft_saved="草稿已保存", pending="待发布", reviewing="审核中",
                     published="发布成功", processing="平台处理中", needs_info="需补资料", uncertain="结果待确认",
                     stopped="已停止")
