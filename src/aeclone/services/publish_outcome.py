"""统一的发布结果判定：保存到待发布 / 直接发布使用不同成功标准。"""
from __future__ import annotations

PENDING_STATES = {"editingrequired", "waitpublish", "pending", "saved_to_waiting"}
AUDIT_STATES = {"auditing", "reviewing", "underreview", "in_review"}
ONLINE_STATES = {"online", "onselling", "published", "publishsuccess", "success", "listed"}
PROCESSING_STATES = {"publishing", "processing", "submitted", "pending_review", "in_progress"}
FAIL_STATES = {"publishfail", "auditfail", "editingfailed", "failed", "rejected", "offline_failed"}


def norm(value) -> str:
    return "".join(str(value or "").lower().split())


def judge(action: str, state: str = "", error: str = "") -> dict:
    """Return {ok,state,category,label,success}.

    action:
      - save_to_waiting / 3 / waiting: 只有进入待发布才算成功
      - publish / 2: 只有最终审核中或在线才算成功
    """
    act = norm(action)
    st = norm(state)
    err = str(error or "").strip()
    if act in {"3", "save_to_waiting", "waiting", "pending", "move_pending"}:
        if st in PENDING_STATES:
            return {"ok": True, "state": state, "category": "pending",
                    "label": "已进入待发布", "success": True}
        if st in FAIL_STATES or err:
            return {"ok": False, "state": state, "category": "failed",
                    "label": "保存后未进入待发布", "success": False}
        return {"ok": False, "state": state, "category": "uncertain",
                "label": "待发布状态未确认", "success": False}
    # publish / direct
    if st in AUDIT_STATES:
        return {"ok": True, "state": state, "category": "auditing",
                "label": "已进入平台审核", "success": True}
    if st in ONLINE_STATES:
        return {"ok": True, "state": state, "category": "online",
                "label": "已在线", "success": True}
    if st in FAIL_STATES or err:
        return {"ok": False, "state": state, "category": "failed",
                "label": "发布失败", "success": False}
    if st in PROCESSING_STATES:
        return {"ok": False, "state": state, "category": "processing",
                "label": "平台仍在处理，尚未进入审核", "success": False}
    return {"ok": False, "state": state, "category": "uncertain",
            "label": "发布结果未确认", "success": False}
