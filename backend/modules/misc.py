# -*- coding: utf-8 -*-
"""B9 工作台/消息/统计域（misc）。
依据：deep/08-消息与全局机制.md、03-登录页与工作台.md、deep/04-报表图表体系.md、deep/01-加工单全流程链路.md
实测样例：api_dump/msg_last_msg_list.json、msg_list_p1.json、msg_big_type_not_read_count.json、
        notice_not_read_count.json、info_notice_list.json、intelli/produce_bill_status_count.json
范围：
- workbench: 待办事项计数（/workbench_config/get_workbench_schedule_status_count）+ 生产状况统计 /workbench/today_*（produce Store 直读，模块间不 import）
- msg: last_msg_list / big_type_not_read_count / list / last_msg(铃铛) / read / read_all / del_all / last_not_read_msg
- notice: list / add / read / not_read_count
- statistics: device_status_statistics 占位空（加工单状态统计 status_count 在 B5 produce.py 名下、
  intelli_report 域（produce_bill_status_count / after_sales_trend_statistics 占位空线）在 stats.py 名下，本文件不重复注册）
- versions: now_push_versions（功能上新）
不在本文件：goods_stock_warning_tip / get_stock_below_low_limit_count（B6 stock.py 名下）。
"""
import core

# ---- 常量（出处：报告 08 §1.5/§1.6 + api_dump 实测） ----
BOOL_YES, BOOL_NO = 1, 2  # has_read：1 已读 / 2 未读

BIG_TYPE_NAMES = {1: "服务工单", 2: "客户反馈", 3: "通知公告", 4: "客户跟单", 5: "维保计划",
                  6: "维保工单", 7: "合同订单", 11: "生产消息", 12: "仓库消息", 15: "服务工单",
                  16: "客户反馈", 17: "办公OA"}
# 实测账号后端只下发 5 类（icon URL 双斜杠为原站原样，照抄）
BIG_TYPE_ICONS = {3: "system_message_blue", 4: "customer_follow_purple", 7: "contract_red",
                  11: "product_yellow", 12: "warehouse_orange"}
ICON_BASE = "https://api.kgd.ltd//weapp/icons/msg/"

NOTICE_TYPE_NAMES = {1: "报警", 2: "待办", 3: "通知"}  # MSG_NOTICE_TYPE：WARING/WAIT/INFO

# 已知 small_type → 跳转 URL（api_dump/msg_small_types.json）
SMALL_TYPE_URLS = {1121: ("/lean_production_todo/list", "/packageLeanProduction/onsiteInspection/Detail"),
                   1205: ("/goods_stock/list", "/packageStock/stock/List")}

# 待办事项卡全字段（chunk 11213 xt 表，前端按 data[fields]||0 取数）
TODO_FIELDS = ["customer_contract", "customer_invoice", "work_order_wait_dispatch",
               "work_order_wait_settle_receive", "work_order_pool", "work_order_wait_settle_order",
               "work_order_wait_settle_commission", "work_order_wait_close",
               "inspection_task_not_start", "inspection_task_wait_receive", "inspection_task_wait_audit"]

# 加工单状态枚举（deep/01 §1.1）：1未开始/2生产中/3已完成/4已取消/5已暂停——today_finish/overdue 计数按 3=已完成、4=已取消 判定

ECUT = lambda: core.now()  # enterprise_config_update_time（原站各响应均随带）


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _user_brief(session):
    return {"id": session.get("id"), "name": session.get("name"), "real_name": session.get("real_name"),
            "mobile": session.get("mobile"), "enterprise_id": session.get("enterprise_id", 16807)}


# ================= msg =================

def _norm_msg(r, session=None):
    """把 messages 行归一成 /msg/list dump 形状（兼容 B11 两种种子行：嵌套 msg 原样行 / title-content 平铺简化行）。"""
    m = r.get("msg") if isinstance(r.get("msg"), dict) else None
    big = r.get("big_type", (m or {}).get("big_type"))
    small = r.get("small_type", (m or {}).get("small_type"))
    if m is None:
        web_url, mini_url = r.get("web_url", ""), r.get("miniapp_url", "")
        if not web_url and small in SMALL_TYPE_URLS:
            web_url, mini_url = SMALL_TYPE_URLS[small]
        m = {"id": r.get("msg_id") or r.get("id"), "content": r.get("content") or "",
             "title": r.get("title") or r.get("small_type_name") or "",
             "created_at": r.get("created_at") or "", "updated_at": r.get("updated_at") or r.get("created_at") or "",
             "big_type": big, "small_type": small, "notice_type": r.get("notice_type", 1),
             "busi_id": r.get("busi_id"), "enterprise_id": r.get("enterprise_id", 16807),
             "big_type_name": r.get("big_type_name") or BIG_TYPE_NAMES.get(big, ""),
             "small_type_name": r.get("small_type_name") or "",
             "notice_type_name": NOTICE_TYPE_NAMES.get(r.get("notice_type", 1), "")}
    web_url = r.get("web_url", m.get("web_url", ""))
    mini_url = r.get("miniapp_url", m.get("miniapp_url", ""))
    if not web_url and small in SMALL_TYPE_URLS:
        web_url, mini_url = SMALL_TYPE_URLS[small]
    return {"id": r.get("id"), "msg_id": r.get("msg_id") or m.get("id"),
            "user_id": r.get("user_id") or (session or {}).get("id"),
            "has_read": _int(r.get("has_read"), BOOL_NO),
            "enterprise_id": r.get("enterprise_id", 16807),
            "big_type": big, "small_type": small, "del": r.get("del", BOOL_NO),
            "fieldValueList": [],
            "big_type_name": r.get("big_type_name") or m.get("big_type_name") or BIG_TYPE_NAMES.get(big, ""),
            "miniapp_url": mini_url, "web_url": web_url,
            "small_type_name": r.get("small_type_name") or m.get("small_type_name") or "",
            "msg": m, "user": r.get("user"), "_raw": r}


def _msgs(session=None):
    rows = [x for x in core.Store("messages").all() if _int(x.get("del"), BOOL_NO) != 1]
    rows = [_norm_msg(x, session) for x in rows]
    rows.sort(key=lambda x: (x["msg"].get("created_at") or ""), reverse=True)  # 最新在前
    return rows


def _filter_msgs(rows, body):
    has_read = body.get("has_read")
    if has_read not in (None, ""):
        rows = [x for x in rows if x["has_read"] == _int(has_read)]
    notice_type = body.get("notice_type")
    if notice_type not in (None, ""):
        rows = [x for x in rows if _int(x["msg"].get("notice_type")) == _int(notice_type)]
    big_type = body.get("big_type")
    if big_type not in (None, ""):
        rows = [x for x in rows if _int(x["big_type"]) == _int(big_type)]
    ts, te = str(body.get("time_start") or "")[:10], str(body.get("time_end") or "")[:10]
    if ts:
        rows = [x for x in rows if (x["msg"].get("created_at") or "")[:10] >= ts]
    if te:
        rows = [x for x in rows if (x["msg"].get("created_at") or "")[:10] <= te]
    return rows


def _page(rows, body, default_size):
    try:
        pageNo = max(1, _int(body.get("pageNo"), 1))
    except Exception:
        pageNo = 1
    try:
        pageSize = max(1, _int(body.get("pageSize"), default_size))
    except Exception:
        pageSize = default_size
    return rows[(pageNo - 1) * pageSize: pageNo * pageSize], len(rows)


def _msg_page_response(body, session, default_size):
    """msg/list 与 msg/last_msg 同构：dump 实测 data 为数组 + 顶层 count/code 0（非 {list,total} 包装）。"""
    rows = _filter_msgs(_msgs(session), body)
    page_rows, total = _page(rows, body, default_size)
    data = []
    for x in page_rows:
        d = {k: v for k, v in x.items() if k != "_raw"}
        d["user"] = d.get("user") or _user_brief(session)  # dump 行自带收件人 user，缺省用当前会话
        data.append(d)
    return {"success": True, "count": total, "data": data, "code": 0, "enterprise_config_update_time": ECUT()}


def _msg_list(body, session):
    return _msg_page_response(body, session, 30)  # 消息中心页默认 30


def _msg_last_msg(body, session):
    return _msg_page_response(body, session, 10)  # 顶栏铃铛弹层默认 10


def _msg_last_msg_list(body, session):
    """90 秒轮询：未读总数 + 最新 10 条未读（前端弹桌面通知读 big_type_name/small_type_name/msg.content/web_url）。"""
    unread = [x for x in _msgs(session) if x["has_read"] == BOOL_NO]
    data = [{k: v for k, v in x.items() if k != "_raw"} for x in unread[:10]]
    return {"success": True, "count": len(data), "data": data, "code": 0,
            "not_read_msg_count": len(unread), "enterprise_config_update_time": ECUT()}


def _msg_last_not_read_msg(body, session):
    """工作台"未读消息"卡列表。"""
    unread = [x for x in _msgs(session) if x["has_read"] == BOOL_NO]
    data = [{k: v for k, v in x.items() if k != "_raw"} for x in unread[:10]]
    return {"success": True, "count": len(data), "data": data, "enterprise_config_update_time": ECUT()}


def _msg_big_type_not_read_count(body, session):
    """消息中心左侧分类菜单计数：实测返回 5 类（含 count=0 类），"全部"由前端求和。"""
    rows = _msgs(session)
    unread = [x for x in rows if x["has_read"] == BOOL_NO]
    bigs = []
    for x in unread:
        if x["big_type"] not in bigs:
            bigs.append(x["big_type"])
    for b in sorted(BIG_TYPE_ICONS):  # 实测 5 类恒返回（3/4/7/11/12）
        if b not in bigs:
            bigs.append(b)
    data = []
    for b in bigs:
        group = [x for x in unread if x["big_type"] == b]
        last = dict(group[0]) if group else None
        if last:
            last.pop("_raw", None)
        data.append({"big_type": b, "big_type_name": (last or {}).get("big_type_name") or BIG_TYPE_NAMES.get(b, ""),
                     "icons": ICON_BASE + BIG_TYPE_ICONS[b] if b in BIG_TYPE_ICONS else "",
                     "count": len(group), "last_msg": last})
    return {"success": True, "data": data, "enterprise_config_update_time": ECUT()}


def _msg_read(body, session):
    msg_id = body.get("msg_id", body.get("id"))
    if msg_id in (None, ""):
        return core.fail("参数缺失")
    store = core.Store("messages")
    hit = 0
    for r in store.all():
        if str(r.get("msg_id") or r.get("id")) == str(msg_id):
            r["has_read"] = BOOL_YES
            hit += 1
    store.flush()
    return core.ok() if hit else core.fail("消息不存在")


def _msg_read_all(body, session):
    """全部已读：携带当前筛选参数，只置命中项（报告 08 §1.5）。"""
    rows = _filter_msgs(_msgs(session), body)
    store = core.Store("messages")
    ids = {x["id"] for x in rows}
    for r in store.all():
        if r.get("id") in ids:
            r["has_read"] = BOOL_YES
    store.flush()
    return core.ok()


def _msg_del_all(body, session):
    store = core.Store("messages")
    store.rows = []
    store.flush()
    return core.ok()


# ================= notice =================

def _notice_rows(session):
    """兼容两种种子行：B11 dump 原样行（has_read/has_read_user_list 直存）与本模块 add 生成的行（read_user_ids 推导）。"""
    users = {u.get("id"): u for u in core.Store("users").all()}
    uid = session.get("id")
    out = []
    for r in core.Store("notices").all():
        if _int(r.get("del"), BOOL_NO) == 1:
            continue
        if "has_read" in r:  # dump 原样行：当前用户已读态与已读/未读名单直存，原样透出
            row = {"id": r.get("id"), "title": r.get("title") or "", "content": r.get("content") or "",
                   "create_user_id": r.get("create_user_id"), "created_at": r.get("created_at"),
                   "updated_at": r.get("updated_at"), "del": r.get("del", BOOL_NO), "reason": r.get("reason"),
                   "enterprise_id": r.get("enterprise_id", 16807), "type": r.get("type", 1), "fieldValueList": [],
                   "has_read": _int(r.get("has_read"), BOOL_NO),
                   "has_read_count": r.get("has_read_count", 0), "not_read_count": r.get("not_read_count", 0),
                   "has_read_user_list": r.get("has_read_user_list") or [],
                   "not_read_user_list": r.get("not_read_user_list") or []}
        else:
            read_ids = r.get("read_user_ids") or []
            range_ids = r.get("range_user_ids")
            if range_ids is None:
                range_ids = list(users.keys())  # 无范围记录则默认全员（demo）
            read_set = [i for i in range_ids if i in read_ids]
            not_read_set = [i for i in range_ids if i not in read_ids]
            row = {"id": r.get("id"), "title": r.get("title") or "", "content": r.get("content") or "",
                   "create_user_id": r.get("create_user_id"), "created_at": r.get("created_at"),
                   "updated_at": r.get("updated_at"), "del": r.get("del", BOOL_NO), "reason": r.get("reason"),
                   "enterprise_id": r.get("enterprise_id", 16807), "type": r.get("type", 1), "fieldValueList": [],
                   "has_read": BOOL_YES if uid in read_ids else BOOL_NO,
                   "has_read_count": len(read_set), "not_read_count": len(not_read_set),
                   "has_read_user_list": [_notice_user(i, r, BOOL_YES, users) for i in read_set],
                   "not_read_user_list": [_notice_user(i, r, BOOL_NO, users) for i in not_read_set]}
        out.append(row)
    out.sort(key=lambda x: (x["created_at"] or ""), reverse=True)
    return out


def _notice_user(uid, notice, has_read, users):
    u = users.get(uid) or {}
    return {"id": None, "user_id": uid, "notice_id": notice.get("id"), "has_read": has_read, "in_range": BOOL_YES,
            "user": {"id": uid, "name": u.get("name") or u.get("real_name") or "",
                     "real_name": u.get("real_name") or u.get("name") or "",
                     "avatar": u.get("avatar"), "has_password": False, "type_name": "",
                     "claw_fun_type_name": "", "qr_code_url": None}}


def _notice_list(body, session):
    """工作台公告卡 {has_read:2,pageNo:1,pageSize:10}；通知公告页另传 title 关键词。dump：data 数组 + 顶层 count。"""
    rows = _notice_rows(session)
    has_read = body.get("has_read")
    if has_read not in (None, ""):
        rows = [x for x in rows if x["has_read"] == _int(has_read)]
    kw = str(body.get("title") or "").strip()
    if kw:
        rows = [x for x in rows if kw in x["title"]]
    page_rows, total = _page(rows, body, 10)
    return {"success": True, "count": total, "data": page_rows, "enterprise_config_update_time": ECUT()}


def _notice_add(body, session):
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    if not title:
        return core.fail("请输入标题")
    if not content:
        return core.fail("请输入内容")
    range_ids = body.get("user_ids") or body.get("range_user_ids") or []
    row = {"title": title, "content": content, "type": body.get("type", 1),
           "range_user_ids": list(range_ids), "read_user_ids": [],
           "attachment_list": body.get("attachment_list") or [],
           "create_user_id": session.get("id"), "enterprise_id": session.get("enterprise_id", 16807)}
    core.Store("notices").add(row)
    return core.ok()


def _notice_read(body, session):
    nid = body.get("id")
    store = core.Store("notices")
    r = store.get(nid)
    if not r:
        return core.fail("公告不存在")
    uid = session.get("id")
    if "has_read" in r:  # dump 原样行：置已读并同步已读名单/计数
        patch = {"has_read": BOOL_YES}
        if _int(r.get("has_read"), BOOL_NO) != BOOL_YES:
            patch["has_read_count"] = _int(r.get("has_read_count"), 0) + 1
            patch["not_read_count"] = max(0, _int(r.get("not_read_count"), 0) - 1)
            reads = list(r.get("has_read_user_list") or [])
            reads.append({"user_id": uid, "notice_id": r.get("id"), "has_read": BOOL_YES, "in_range": BOOL_YES})
            patch["has_read_user_list"] = reads
            nr = [x for x in (r.get("not_read_user_list") or []) if x.get("user_id") != uid]
            patch["not_read_user_list"] = nr
        store.update(nid, patch)
    else:
        reads = r.get("read_user_ids") or []
        if uid not in reads:
            reads.append(uid)
        store.update(nid, {"read_user_ids": reads})
    return core.ok()


def _notice_not_read_count(body, session):
    n = len([x for x in _notice_rows(session) if x["has_read"] == BOOL_NO])
    return {"success": True, "data": {"not_read_count": n}, "enterprise_config_update_time": ECUT()}


# ================= workbench =================

def _wb_schedule_status_count(body, session):
    """待办事项卡计数：data[fields]||0（chunk 11213 xt 表 + 报告 03 §3.5）。"""
    cc = len([c for c in core.Store("contracts").all() if _int(c.get("audit_status")) == 2])   # 待审合同
    ci = len([i for i in core.Store("invoices").all() if _int(i.get("status")) == 3])          # 待开票(INVOICE_STATUS.WSIT=3)
    data = {"customer_contract": cc, "customer_invoice": ci,
            "work_order_wait_dispatch": 0, "work_order_wait_settle_receive": 0, "work_order_pool": 0,
            "work_order_wait_settle_order": 0, "work_order_wait_settle_commission": 0, "work_order_wait_close": 0,
            "inspection_task_not_start": 0, "inspection_task_wait_receive": 0, "inspection_task_wait_audit": 0}
    return core.ok(data)


def _today_finish_count(rows):
    """当日完成（简化口径）：状态=已完成(3) 且完成时间字段命中今天。
    时间字段链：finish_time→end_time→updated_at→created_at（B11 种子任务行无完工时间戳，末位兜底）。"""
    today = core.now("%Y-%m-%d")
    n = 0
    for r in rows:
        if _int(r.get("status")) != 3:
            continue
        t = str(r.get("finish_time") or r.get("end_time") or r.get("updated_at") or r.get("created_at") or "")[:10]
        if t == today:
            n += 1
    return n


def _wb_today_finish(body, session):
    """今日完成工单数（响应 data.num，chunk 11213 Production Situation Overview）。"""
    return core.ok({"num": _today_finish_count(core.Store("produce_bills").all())})


def _wb_today_finish_produce_craft(body, session):
    """今日完成任务数：produce_bill_craft（任务）status=3 已完成且当日。"""
    return core.ok({"num": _today_finish_count(core.Store("produce_bill_crafts").all())})


def _wb_overdue_produce(body, session):
    """逾期工单数：交期已过且未完成/未取消（status ∉ {3,4}）。"""
    today = core.now("%Y-%m-%d")
    n = 0
    for r in core.Store("produce_bills").all():
        if _int(r.get("status")) in (3, 4):
            continue
        d = str(r.get("delivery_date") or "")[:10]
        if d and d < today:
            n += 1
    return core.ok({"num": n})


def _wb_zero(body, session):
    return core.ok({"num": 0})  # today_report / today_waste / today_waste_ratio：报工与不良数据源未播种，占位 0


# ================= statistics =================

def _device_status_statistics(body, session):
    """设备概况占位：data:[{name,count,color}]，demo 未部署设备域 → 空序列（stats.py 未注册此键，归本文件）。"""
    return core.ok([])


# ================= versions =================

def _now_push_versions(body, session):
    """功能上新卡：前端读 data.v.new_feature / data.v.document_url（11213），版本弹窗读 v.title/v.profile（app chunk）。"""
    feature = "BOM管理支持视图管理！！！"
    return core.ok({"v": {"id": 1, "title": feature, "profile": feature, "new_feature": feature,
                          "document_url": "https://doc.qly.ltd/", "version": "8.10.27.24",
                          "created_at": "2026-10-27 10:00:00"}})


ROUTES = {
    # msg（消息中心/铃铛/工作台未读消息卡）
    ("msg", "list"): _msg_list,
    ("msg", "last_msg"): _msg_last_msg,
    ("msg", "last_msg_list"): _msg_last_msg_list,
    ("msg", "last_not_read_msg"): _msg_last_not_read_msg,
    ("msg", "big_type_not_read_count"): _msg_big_type_not_read_count,
    ("msg", "read"): _msg_read,
    ("msg", "read_all"): _msg_read_all,
    ("msg", "del_all"): _msg_del_all,
    # notice（公告）
    ("notice", "list"): _notice_list,
    ("notice", "add"): _notice_add,
    ("notice", "read"): _notice_read,
    ("notice", "not_read_count"): _notice_not_read_count,
    # workbench（待办计数 + 生产状况统计）
    ("workbench_config", "get_workbench_schedule_status_count"): _wb_schedule_status_count,
    ("workbench", "today_finish"): _wb_today_finish,
    ("workbench", "today_finish_produce_craft"): _wb_today_finish_produce_craft,
    ("workbench", "overdue_produce"): _wb_overdue_produce,
    ("workbench", "today_report"): _wb_zero,
    ("workbench", "today_waste"): _wb_zero,
    ("workbench", "today_waste_ratio"): _wb_zero,
    # statistics（工作台图表占位；produce_bill 的 status_count 见 B5 produce.py、intelli_report 域见 stats.py）
    ("statistics", "device_status_statistics"): _device_status_statistics,
    # versions（功能上新）
    ("versions", "now_push_versions"): _now_push_versions,
}
