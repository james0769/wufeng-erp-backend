# -*- coding: utf-8 -*-
"""B1 认证与用户系统（参考模块——其余模块照此模式编写）。
ROUTES = {("模块名", "动作名"): handler}
handler(body, session) -> dict（core.ok/fail/core.paged_ok 包装）
"""
import core

# demo 账号（与原站实拍一致）
DEMO_USER = {
    "id": 250574, "name": "佳H", "real_name": "佳H", "mobile": "15871772084",
    "enterprise_id": 16807, "department": "生产一部", "role": "系统管理员",
    "fun_ids": "1,2,3"
}

def _login(body, session):
    mobile = str(body.get("mobile") or "")
    pwd = str(body.get("pwd") or "")
    if not mobile or not pwd:
        return core.fail("请输入手机号和密码")
    user = dict(DEMO_USER, mobile=mobile)
    token = core.make_session(user)
    return core.ok({
        "id": user["id"], "name": user["name"], "real_name": user["real_name"],
        "mobile": user["mobile"], "web_token": token, "wechat_token": "",
        "enterprise_id": user["enterprise_id"]
    })

def _logout(body, session):
    core.SESSIONS.pop(str(body.get("token") or ""), None)
    return core.ok()

def _user_info(body, session):
    if not session:
        return core.fail("请登录", error_code="001")
    return core.ok(dict(session, fun_ids=DEMO_USER["fun_ids"]))

def _user_home(body, session):
    if not session:
        return core.fail("请登录", error_code="001")
    return core.ok({
        "id": session["id"], "name": session["name"], "mobile": session["mobile"],
        "enterprise": {"id": 16807, "name": "山东德萨快工单王曼曼", "enable_new_work_order": 1},
        "not_read_msg_count": 3, "untreated_feedback_count": 0,
        "sale_modules": "1|1,2|1,3|1,4|1"
    })

ROUTES = {
    ("user", "login_by_pwd"): _login,
    ("user", "logout"): _logout,
    ("user", "info"): _user_info,
    ("user", "home"): _user_home,
    ("user", "nav"): lambda b, s: core.ok([]),
}
