# -*- coding: utf-8 -*-
"""B5 加工单域（状态机核心）：produce_bill + produce_bill_craft。
依据：deep\\01-加工单全流程链路.md（五态状态机/edit_status/batch 族/排单/编号 YYYYMMDDNNN-n）、
      deep\\18-报工与工时工资.md（report_work 载荷、工资=单价×良品数 decimal 截断）。
字段名对齐 api_dump 实测（opb_list.json / craft_list.json 同构实体）。
"""
import core
from decimal import Decimal, ROUND_DOWN

# ---------------- 枚举（deep\01 §1.1 / §2.1，逐字） ----------------
STATUS = {1: "未开始", 2: "生产中", 3: "已完成", 4: "已取消", 5: "已暂停"}
STATUS_COLOR = {1: "#757575", 2: "#fa8c16", 3: "#52c41a", 4: "#f5222d", 5: "#eb2f96"}
STATUS_KEY = {1: "wait", 2: "in_produce", 3: "finish", 4: "cancel", 5: "pause"}
# tag_class 实测仅 未开始="4"（api_dump）；其余为 demo 占位
TAG_CLASS = {1: "4", 2: "1", 3: "2", 4: "5", 5: "3"}
CRAFT_STATUS = {1: "未开始", 2: "进行中", 3: "已完成", 4: "已暂停"}
CRAFT_STATUS_KEY = {1: "wait_produce", 2: "in_produce", 3: "finish", 4: "pause"}
CRAFT_TAG_CLASS = {1: "4", 2: "1", 3: "2", 4: "3"}
WORK_TYPE = {1: "生产工单", 2: "返工工单"}
REQUISITION_STATUS = {1: "无需领料", 2: "未领料", 3: "部分领料", 4: "全部领料"}
PRICE_MODE = {1: "计件", 2: "计时"}
QUALITY_TYPE = {1: "全检", 2: "抽检", 3: "不需检验"}
BOOL = {1: True, 2: False}

# 工资公式（deep\18 §4.2b 实测配置）：计件=良品数×单价；计时=时长(秒)/3600×单价；按 decimal_num 位【截断】
WAGE_FORMULA = {"is_format_decimal": 1, "decimal_num": 0}
MAX_NUM = 99999999

# ---------------- 存取（单例：避免多 Store 实例 flush 互相覆盖） ----------------
_BILLS = _CRAFTS = _RECORDS = _WASTES = None

def _bills():
    global _BILLS
    if _BILLS is None:
        _BILLS = core.Store("produce_bills")
    return _BILLS

def _crafts():
    global _CRAFTS
    if _CRAFTS is None:
        _CRAFTS = core.Store("produce_bill_crafts")
    return _CRAFTS

def _records():
    global _RECORDS
    if _RECORDS is None:
        _RECORDS = core.Store("report_work_records")
    return _RECORDS

def _wastes():
    global _WASTES
    if _WASTES is None:
        _WASTES = core.Store("wastes")
    return _WASTES

def _num(x, w=8):
    try:
        return ("%." + str(w) + "f") % float(x or 0)
    except Exception:
        return ("%.8f" % 0) if w == 8 else ("%.2f" % 0)

def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0

def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name") or str(uid)}
    try:  # users 种子未生成时回退 demo 账号
        from modules.auth import DEMO_USER
        if str(DEMO_USER.get("id")) == str(uid):
            return {"id": DEMO_USER["id"], "real_name": DEMO_USER["real_name"]}
    except Exception:
        pass
    return {"id": uid, "real_name": "用户%s" % uid}

def _wage(price_mode, valid_num, unit_money, working_minutes):
    """工资=单价×良品数（计件）/ 时长秒÷3600×单价（计时），按 decimal_num 位截断（非四舍五入，deep\18 §4.3 实测）。"""
    dn = int(WAGE_FORMULA.get("decimal_num") or 0)
    if str(price_mode) == "2":
        base = (Decimal(str(_f(working_minutes))) * 60 / Decimal(3600)) * Decimal(str(_f(unit_money)))
    else:
        base = Decimal(str(_f(valid_num))) * Decimal(str(_f(unit_money)))
    q = Decimal(1).scaleb(-dn)
    val = base.quantize(q, rounding=ROUND_DOWN)
    return str(int(val)) if dn == 0 else str(val)

# ---------------- 排单版本（sort_status 轮询 / drag_move 乐观锁，deep\01 §1.4） ----------------
def _meta():
    return core._load("produce_bills_meta", {})

def _sort_version():
    return _meta().get("sort_version", 1)

def _bump_sort_version():
    m = _meta()
    m["sort_version"] = int(m.get("sort_version", 1)) + 1
    core._save("produce_bills_meta", m)

def _next_sort_num():
    mx = 0
    for r in _bills().all():
        if r.get("status") == 2 and r.get("sort_num"):
            mx = max(mx, int(r["sort_num"]))
    return mx + 1

# ---------------- 编号：YYYYMMDDNNN-n（deep\01 §1.6，日期+3位流水+"-"+同批序号） ----------------
def _next_bill_code(rows):
    import time
    stamp = time.strftime("%Y%m%d")
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(stamp) and len(c) >= len(stamp) + 3:
            try:
                mx = max(mx, int(c[len(stamp):len(stamp) + 3]))
            except ValueError:
                pass
    return stamp + str(mx + 1).zfill(3) + "-1"

# ---------------- 状态机（deep\01 §1.2，严格校验） ----------------
def _visible_actions(st):
    """按钮可见性规则（deep\01 §1.2 表格逐字还原）：
    开始：status ∉ {生产中,已完成,已取消,已暂停}（即仅未开始）；
    撤回：生产中或已完成（回退一步）；完成：未开始/生产中；取消：≠已取消；暂停：生产中；继续：已暂停。"""
    return {
        "start": st == 1,
        "withdraw": st in (2, 3),
        "finish": st in (1, 2),
        "cancel": st != 4,
        "pause": st == 2,
        "cancel_pause": st == 5,
    }

def _check_reason(val, empty_msg):
    reason = str(val or "").strip()
    if not reason:
        return None, core.fail(empty_msg)
    if len(reason) > 200:
        return None, core.fail("长度超出限制")
    return reason, None

def _apply_edit_status(bill, body):
    """单条状态迁移：返回 None=成功 或 fail dict。type 1开始/2撤回(回退一步)/3完成/4取消(带 cancel_reason)。"""
    try:
        t = int(body.get("type"))
    except Exception:
        return core.fail("参数错误")
    st = bill.get("status")
    if t == 1:  # 开始 → 生产中
        if st != 1:
            return core.fail("只有未开始的加工单可以开始生产")
        bill["status"] = 2
        bill["start_time"] = core.now()
        bill.setdefault("sort_num", None)
        if not bill.get("sort_num"):
            bill["sort_num"] = _next_sort_num()
    elif t == 2:  # 撤回=回退一步：生产中→未开始 / 已完成→生产中
        if st == 2:
            bill["status"] = 1
            bill["start_time"] = None
            bill["sort_num"] = None
        elif st == 3:
            bill["status"] = 2
            bill["end_time"] = None
            bill["sort_num"] = _next_sort_num()
        else:
            return core.fail("只有生产中或已完成的加工单可以撤回")
    elif t == 3:  # 完成
        if st not in (1, 2):
            return core.fail("已取消、已完成或已暂停的加工单不能完成")
        if st == 1 and not bill.get("start_time"):
            bill["start_time"] = core.now()
        bill["status"] = 3
        bill["end_time"] = core.now()
    elif t == 4:  # 取消（必填 cancel_reason，max 200）
        if st == 4:
            return core.fail("已经取消的工单无法继续处理")
        reason, err = _check_reason(body.get("cancel_reason"), "请输入取消原因")
        if err:
            return err
        bill["status"] = 4
        bill["cancel_reason"] = reason
        bill["sort_num"] = None
    else:
        return core.fail("参数错误")
    return None

# ---------------- 列表装饰（后端冗余三件套 + 进度列，字段对齐导出表/列配置） ----------------
def _crafts_of(bill_id):
    return [c for c in _crafts().all() if str(c.get("produce_bill_id")) == str(bill_id)]

def _pct(a, b):
    try:
        return int(_f(a) / _f(b) * 100) if _f(b) else 0
    except Exception:
        return 0

def _decorate(bill):
    st = bill.get("status")
    crafts = _crafts_of(bill.get("id"))
    all_c = len(crafts)
    fin_c = len([c for c in crafts if c.get("status") == 3])
    valid_sum = sum(_f(c.get("total_valid_num")) for c in crafts)
    plan_sum = sum(_f(c.get("num")) for c in crafts)
    cur = None
    for c in crafts:  # 当前工序=第一个进行中或第一个未开始（deep\01 §1.5）
        if c.get("status") == 2:
            cur = c.get("craft_name")
            break
    if not cur:
        for c in crafts:
            if c.get("status") == 1:
                cur = c.get("craft_name")
                break
    bill.setdefault("num", "0.00000000")
    bill.setdefault("actual_num", _num(valid_sum))
    bill.setdefault("stock_in_num", "0.00000000")
    bill["status_name"] = STATUS.get(st, "")
    bill["status_tag_class"] = TAG_CLASS.get(st, "")
    bill["status_color"] = STATUS_COLOR.get(st, "")
    bill["status_key"] = STATUS_KEY.get(st, "")
    bill["work_type_name"] = WORK_TYPE.get(bill.get("work_type") or 1, "")
    bill["requisition_status_name"] = REQUISITION_STATUS.get(bill.get("requisition_status") or 2, "")
    bill["create_mode_name"] = "直接创建" if (bill.get("create_mode") or 1) == 1 else "合同创建"
    bill["editAccessible"] = st in (1, 2, 5)  # 修改行标志（chunk 40963）
    bill["visible_status_actions"] = _visible_actions(st)  # 按钮可见性
    bill["current_craft_name"] = cur
    bill["finish_craft_count"] = fin_c
    bill["all_craft_count"] = all_c
    bill["finish_craft_percent"] = "%d%%(%d/%d)" % (_pct(fin_c, all_c), fin_c, all_c) if all_c else ""
    done_plan = sum(_f(c.get("num")) for c in crafts if c.get("status") == 3)
    bill["task_percent"] = "%d%% (%d/%d)" % (_pct(done_plan, plan_sum), int(done_plan), int(plan_sum)) if plan_sum else ""
    bill["stock_in_percent"] = _pct(bill.get("stock_in_num"), bill.get("num"))
    bill["delivery_countdown"] = ""
    if not bill.get("create_user") and bill.get("create_user_id"):
        bill["create_user"] = _user(bill["create_user_id"])
    return bill

def _decorate_craft(c):
    st = c.get("status")
    c.setdefault("num", "0.00000000")
    c.setdefault("valid_num", "0.00000000")
    c.setdefault("total_valid_num", "0.00000000")
    c.setdefault("waste_num", "0.00000000")
    c.setdefault("rework_num", "0.00000000")
    c.setdefault("scrap_num", "0.00000000")
    c.setdefault("produce_bill_craft_num", c.get("num"))
    c.setdefault("report_num", "0.00000000")
    c["wait_produce_num"] = _num(max(0.0, _f(c.get("num")) - _f(c.get("total_valid_num"))), 0)
    c["status_name"] = CRAFT_STATUS.get(st, "")
    c["status_tag_class"] = CRAFT_TAG_CLASS.get(st, "")
    c["price_mode_name"] = PRICE_MODE.get(c.get("price_mode") or 1, "")
    c["quality_type_name"] = QUALITY_TYPE.get(c.get("quality_type") or 3, "")
    wastes = _wastes().all()
    c["waste_item_list"] = [
        {"id": w.get("id"), "name": w.get("name")}
        for w in wastes if str(w.get("id")) in [str(x) for x in (c.get("waste_item_ids") or [])]
    ]
    c["waste_item_names"] = ",".join(x["name"] or "" for x in c["waste_item_list"])
    dl = c.get("dispatch_list") or []
    c["dispatch_user_names"] = ",".join(
        "%s(%s)" % ((d.get("user") or {}).get("real_name", d.get("user_id")), d.get("count", 0)) for d in dl)
    bill = _bills().get(c.get("produce_bill_id"))
    if bill and not c.get("produce_bill"):
        c["produce_bill"] = {
            "id": bill.get("id"), "code": bill.get("code"), "status": bill.get("status"),
            "status_name": STATUS.get(bill.get("status"), ""), "goods_id": bill.get("goods_id"),
            "goods": bill.get("goods") or {"name": bill.get("goods_name"),
                                           "code": bill.get("goods_code"),
                                           "standard": bill.get("goods_standard")},
            "num": bill.get("num"),
        }
    return c

# ---------------- produce_bill：筛选/分页 ----------------
def _filter_bills(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    st_f = body.get("status")
    for r in rows:
        if st_f not in (None, "", "all"):
            sts = st_f if isinstance(st_f, list) else [st_f]
            if str(r.get("status")) not in [str(x) for x in sts]:
                continue
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        g = r.get("goods") or {}
        gk = s("goods_keyword")
        # 兼容 B11 导出转换行：goods 扁平为 goods_name/goods_code/goods_standard
        g_hay = "%s %s %s" % (g.get("name") or r.get("goods_name") or "",
                              g.get("code") or r.get("goods_code") or "",
                              g.get("standard") or r.get("goods_standard") or "")
        if gk and gk not in g_hay:
            continue
        if s("goods_codes"):
            codes = [x.strip() for x in s("goods_codes").split(",") if x.strip()]
            gcode = str(g.get("code") or r.get("goods_code") or "")
            if codes and gcode not in codes:
                continue
        if s("work_type") and str(r.get("work_type") or 1) != s("work_type"):
            continue
        if s("requisition_status") and str(r.get("requisition_status") or 2) != s("requisition_status"):
            continue
        if s("priority_configure_id") and str(r.get("priority_configure_id") or "") != s("priority_configure_id"):
            continue
        if s("pause_reason") and s("pause_reason") not in str(r.get("pause_reason") or ""):
            continue
        if s("cancel_reason") and s("cancel_reason") not in str(r.get("cancel_reason") or ""):
            continue
        if s("produce_project_id"):
            # 项目-工单双链路（project.py 口径）：新单 produce_project_id 直连；
            # B11 种子行无该字段、仅 project_code → 按项目 id 反查编号兜底匹配
            if str(r.get("produce_project_id") or "") != s("produce_project_id"):
                proj = core.Store("production_projects").get(s("produce_project_id"))
                pcode = (proj or {}).get("code")
                if not pcode or str(r.get("project_code") or "") != str(pcode):
                    continue
        if s("contract_code"):
            cc = (r.get("customer_contract") or {}).get("code") or r.get("contract_code") or ""
            if s("contract_code") not in cc:
                continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out

def _paged(store, rows, body):
    """契约分页：筛选在 page() 前对 all() 过滤（简报规则3）。"""
    backup = store.rows
    store.rows = rows
    try:
        return core.paged_ok(store.page(body))
    finally:
        store.rows = backup

def _list(body, session):
    store = _bills()
    rows = _filter_bills(store.all(), body)
    if str(body.get("sortField") or "") == "sort_num":  # 排单弹窗：生产中按 sort_num 升序（deep\01 §1.4）
        rows = sorted(rows, key=lambda r: (r.get("sort_num") is None, r.get("sort_num") or 0))
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate(dict(r)) for r in res["data"]["list"]]
    return res

def _status_count(body, session):
    rows = _filter_bills(_bills().all(), body)
    sc = {v: 0 for v in STATUS}
    rq = {v: 0 for v in REQUISITION_STATUS}
    wt = {v: 0 for v in WORK_TYPE}
    for r in rows:
        if r.get("status") in sc:
            sc[r["status"]] += 1
        rq[r.get("requisition_status") or 2] = rq.get(r.get("requisition_status") or 2, 0) + 1
        wt[r.get("work_type") or 1] = wt.get(r.get("work_type") or 1, 0) + 1
    return core.ok({
        "status_count": {STATUS_KEY[k]: v for k, v in sc.items()},
        "requisition_status_count": rq,
        "work_type_count": wt,
        "total_num_sum": _num(sum(_f(r.get("num")) for r in rows)),
        "actual_num_sum": _num(sum(_f(r.get("actual_num")) for r in rows)),
    })

# ---------------- produce_bill：增删改查 ----------------
def _ref_goods(goods_id):
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(goods_id or ""):
            return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
                    "standard": g.get("standard"), "unit": g.get("unit")}
    return None

def _add(body, session):
    store = _bills()
    code = str(body.get("code") or "").strip() or _next_bill_code(store.all())
    goods = body.get("goods") or _ref_goods(body.get("goods_id")) or {}
    bill = {
        "enterprise_id": session.get("enterprise_id"),
        "code": code,
        "goods_id": goods.get("id") or body.get("goods_id"),
        "goods": goods,
        "goods_name": goods.get("name"), "goods_code": goods.get("code"),
        "goods_standard": goods.get("standard"),
        "num": _num(body.get("num")),
        "status": 1,
        "work_type": body.get("work_type") or 1,
        "produce_type": body.get("produce_type") or 1,
        "create_mode": body.get("create_mode") or 1,
        "requisition_status": body.get("requisition_status") or 2,
        "workshop_id": body.get("workshop_id"),
        "goods_bom": body.get("goods_bom"),
        "delivery_date": body.get("delivery_date"),
        "start_produce_date": body.get("start_produce_date"),
        "end_produce_date": body.get("end_produce_date"),
        "priority_configure_id": body.get("priority_configure_id"),
        "customer_contract_id": body.get("customer_contract_id"),
        "produce_schedule_id": body.get("produce_schedule_id"),
        "produce_project_id": body.get("produce_project_id"),
        "remark": body.get("remark"),
        "pause_reason": None, "cancel_reason": None,
        "start_time": None, "end_time": None, "sort_num": None,
        "actual_num": "0.00000000", "stock_in_num": "0.00000000",
        "create_user_id": session.get("id"),
    }
    bill = store.add(bill)
    for c in body.get("craft_list") or []:
        _new_craft(bill, c)
    return core.ok(_decorate(dict(bill)))

def _new_craft(bill, c):
    num = _num(c.get("num") or bill.get("num"))
    row = {
        "produce_bill_id": bill["id"],
        "pub_craft_id": c.get("pub_craft_id"),
        "craft_name": c.get("craft_name") or c.get("name") or "工序",
        "num": num,
        "produce_bill_craft_num": num,
        "status": c.get("status") or 1,
        "price_mode": c.get("price_mode") or 1,
        "unit_money": _num(c.get("unit_money")),
        "quality_type": c.get("quality_type") or 3,
        "waste_item_ids": c.get("waste_item_ids") or [],
        "valid_num": "0.00000000", "total_valid_num": "0.00000000",
        "waste_num": "0.00000000", "rework_num": "0.00000000", "scrap_num": "0.00000000",
        "report_num": "0.00000000",
        "working_minutes": "0.00",
        "dispatch_user_ids": None, "dispatch_list": [],
        "is_open_clock": c.get("is_open_clock") or 2,
        "create_type": c.get("create_type") or 1,
        "is_rework": c.get("is_rework") or 2,
        "unit": c.get("unit") or {"id": None, "name": "PCS"},
        "workshop_id": c.get("workshop_id") or bill.get("workshop_id"),
        "plan_start": c.get("plan_start"), "plan_end": c.get("plan_end"),
        "remark": c.get("remark"),
    }
    return _crafts().add(row)

_EDITABLE = ("code", "num", "remark", "workshop_id", "delivery_date", "start_produce_date",
             "end_produce_date", "priority_configure_id", "requisition_status", "goods_bom")

def _edit(body, session):
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    patch = {k: body[k] for k in _EDITABLE if k in body}
    if "num" in patch:
        patch["num"] = _num(patch["num"])
    if not bill.get("editAccessible", True) and bill.get("status") in (3, 4):
        return core.fail("当前状态不允许修改")
    _bills().update(bill["id"], patch)
    return core.ok()

def _del(body, session):
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    # 删除加工单将删除关联的报工、质检、验收信息（chunk 40963 文案语义）
    crafts = _crafts()
    for c in list(crafts.all()):
        if str(c.get("produce_bill_id")) == str(bill["id"]):
            crafts.remove(c["id"])
    recs = _records()
    for r in list(recs.all()):
        if str(r.get("produce_bill_id")) == str(bill["id"]):
            recs.remove(r["id"])
    _bills().remove(bill["id"])
    _bump_sort_version()
    return core.ok()

def _detail(body, session):
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    d = _decorate(dict(bill))
    d["craft_list"] = [_decorate_craft(dict(c)) for c in _crafts_of(bill["id"])]
    return core.ok(d)

# ---------------- produce_bill：状态机入口 ----------------
def _edit_status(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    err = _apply_edit_status(bill, body)
    if err:
        return err
    store.flush()
    _bump_sort_version()
    return core.ok()

def _pause(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") != 2:
        return core.fail("只有生产中的加工单可以暂停")
    reason, err = _check_reason(body.get("pause_reason"), "请填写暂停原因")
    if err:
        return err
    bill["status"] = 5
    bill["pause_reason"] = reason
    bill["sort_num"] = None
    store.flush()
    return core.ok()

def _cancel_pause(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") != 5:
        return core.fail("只有已暂停的加工单可以继续生产")
    bill["status"] = 2
    bill["pause_reason"] = None
    bill["sort_num"] = _next_sort_num()
    store.flush()
    _bump_sort_version()
    return core.ok()

# ---------------- produce_bill：批量族（部分失败语义 {success_count,error_count,error_msg}） ----------------
def _batch_apply(body, kind, reason_key=None):
    ids = body.get("id_array") or []
    if not ids:
        return core.fail("请选择加工单")
    if reason_key:
        empty_msg = "请填写暂停原因" if reason_key == "pause_reason" else "请输入取消原因"
        _, err = _check_reason(body.get(reason_key), empty_msg)
        if err:
            return err
    store = _bills()
    sc = ec = 0
    msgs = []
    for i in ids:
        bill = store.get(i)
        if not bill:
            ec += 1
            msgs.append("数据不存在")
            continue
        before = dict(bill)
        if kind == "pause":
            err = None
            if bill.get("status") != 2:
                err = core.fail("只有生产中的加工单可以暂停")
            else:
                bill["status"] = 5
                bill["pause_reason"] = str(body.get("pause_reason") or "").strip()
                bill["sort_num"] = None
        else:
            payload = {"type": {"start": 1, "finish": 3, "cancel": 4}[kind]}
            if reason_key:
                payload[reason_key] = body.get(reason_key)
            err = _apply_edit_status(bill, payload)
        if err:
            bill.update({k: before[k] for k in ("status", "start_time", "end_time", "sort_num",
                                                "cancel_reason", "pause_reason") if k in before})
            ec += 1
            m = err.get("msg")
            if m not in msgs:
                msgs.append(m)
        else:
            sc += 1
    store.flush()
    if sc:
        _bump_sort_version()
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "；".join(msgs)
    return core.ok(data)

def _batch_start(body, session):
    return _batch_apply(body, "start")

def _batch_finish(body, session):
    return _batch_apply(body, "finish")

def _batch_pause(body, session):
    return _batch_apply(body, "pause", reason_key="pause_reason")

def _batch_cancel(body, session):
    return _batch_apply(body, "cancel", reason_key="cancel_reason")

# ---------------- produce_bill：排单 ----------------
def _sort_status(body, session):
    """排单版本号接口：弹窗 5 秒轮询，version 变化即刷新（deep\01 §1.4）。"""
    return core.ok({"version": _sort_version()})

def _drag_move_sort_num(body, session):
    v = body.get("sort_version")
    if v is not None and str(v) != str(_sort_version()):
        return core.fail("列表已更新，请重新拖动")
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") != 2:
        return core.fail("加工单排单只支持生产中的加工单")
    try:
        target = int(body.get("sort_num") or 0)
    except Exception:
        return core.fail("参数错误")
    old = bill.get("sort_num")
    # 占位交换（deep\01 §1.4）：拖拽行取目标位置原有行的 sort_num，原占位行让位（取拖拽行旧值）
    holder = None
    for r in store.all():
        if str(r.get("id")) != str(bill.get("id")) and r.get("status") == 2 and int(r.get("sort_num") or 0) == target:
            holder = r
            break
    if holder is not None:
        holder["sort_num"] = old
    bill["sort_num"] = target
    store.flush()
    _bump_sort_version()
    return core.ok()

# ---------------- produce_bill_craft ----------------
def _filter_crafts(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        if s("produce_bill_id") and str(r.get("produce_bill_id")) != s("produce_bill_id"):
            continue
        if s("craft_name") and s("craft_name") not in str(r.get("craft_name") or ""):
            continue
        if s("status") and str(r.get("status")) != s("status"):
            continue
        if str(body.get("not_filter_rework") or "") == "1" and str(r.get("is_rework") or "2") == "1":
            continue  # 默认不显示返工任务（chunk 32369）
        if s("produce_bill_code"):
            b = _bills().get(r.get("produce_bill_id"))
            if not b or s("produce_bill_code") not in str(b.get("code") or ""):
                continue
        out.append(r)
    return out

def _craft_list(body, session):
    store = _crafts()
    rows = _filter_crafts(store.all(), body)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate_craft(dict(c)) for c in res["data"]["list"]]
    return res

def _craft_status_count(body, session):
    rows = _filter_crafts(_crafts().all(), body)
    sc = {v: 0 for v in CRAFT_STATUS}
    for r in rows:
        if r.get("status") in sc:
            sc[r["status"]] += 1
    return core.ok({"status_count": {CRAFT_STATUS_KEY[k]: v for k, v in sc.items()}})

def _dispatch(body, session):
    """生产派工：{id, dispatch_array:[{user_id,count}]}（deep\01 §2.2）。"""
    store = _crafts()
    c = store.get(body.get("id"))
    if not c:
        return core.fail("数据不存在")
    arr = body.get("dispatch_array") or []
    if not arr:
        return core.fail("请选择派工人员")
    total = 0.0
    lst = []
    for d in arr:
        cnt = _f(d.get("count"))
        if cnt <= 0:
            return core.fail("派工数量必须大于0")
        total += cnt
        lst.append({"user_id": d.get("user_id"), "count": _num(cnt), "user": _user(d.get("user_id"))})
    if total > _f(c.get("num")):
        return core.fail("派工数量不能超过计划数量")
    c["dispatch_user_ids"] = [d["user_id"] for d in lst]
    c["dispatch_list"] = lst
    store.flush()
    return core.ok()

def _get_dispatch_list(body, session):
    c = _crafts().get(body.get("id"))
    if not c:
        return core.fail("数据不存在")
    return core.ok(c.get("dispatch_list") or [])

def _select_waste_item(body, session):
    """小组报工弹窗的不良品项源（deep\18 §5.1）。"""
    c = _crafts().get(body.get("produce_craft_id") or body.get("id"))
    if not c:
        return core.fail("数据不存在")
    wastes = _wastes().all()
    ids = [str(x) for x in (c.get("waste_item_ids") or [])]
    return core.ok([{"id": w.get("id"), "name": w.get("name")} for w in wastes if str(w.get("id")) in ids])

# ---------------- 报工（deep\01 §2.3 + deep\18 §4.3） ----------------
def _report_one(payload, session):
    """单条报工核心：校验数量 → 落报工记录（含不良品数组、工资截断计算）→ 任务/加工单联动。"""
    store = _crafts()
    c = store.get(payload.get("produce_bill_craft_id") or payload.get("produce_craft_id"))
    if not c:
        return core.fail("数据不存在")
    if c.get("status") == 3:
        return core.fail("工序当前状态为%s" % CRAFT_STATUS.get(3))
    if c.get("status") == 4:
        return core.fail("工序当前状态为%s" % CRAFT_STATUS.get(4))
    bill = _bills().get(c.get("produce_bill_id"))
    if bill:
        if bill.get("status") == 4:
            return core.fail("已经取消的工单无法继续处理")
        if bill.get("status") == 5:
            return core.fail("已暂停的加工单不能报工")
    try:
        valid = _f(payload.get("valid_num"))
        waste = _f(payload.get("waste_num"))
    except Exception:
        return core.fail("数量填写错误")
    if valid <= 0:
        return core.fail("良品数需大于0")
    if valid > MAX_NUM:
        return core.fail("良品数最大为99999999")
    if waste < 0:
        return core.fail("不良品数填写错误")
    if waste > MAX_NUM:
        return core.fail("不良品数最大为99999999")
    rwa = payload.get("report_waste_array") or []
    for it in rwa:
        if _f(it.get("num")) <= 0:
            return core.fail("不良品项的值必须大于0")
    try:
        minutes = int(float(payload.get("working_minutes") or 0))
    except Exception:
        minutes = 0
    if minutes < 0:
        return core.fail("报工时长不能小于0")
    if not str(payload.get("working_minutes") or "").strip():
        return core.fail("请填写报工时长")
    st, et = payload.get("start_time"), payload.get("end_time")
    if st and et and str(st) > str(et):
        return core.fail("开始时间不能大于结束时间")
    # ---- 落报工记录 ----
    waste_names = {str(w.get("id")): w.get("name") for w in _wastes().all()}
    rwl = [{"waste_item": {"id": it.get("waste_item_id"),
                           "name": waste_names.get(str(it.get("waste_item_id")), "")},
            "num": _num(it.get("num"))} for it in rwa]
    unit_money = c.get("unit_money")
    rec = {
        "type": 1, "type_name": "生产报工",
        "produce_bill_craft_id": c["id"], "produce_bill_id": c.get("produce_bill_id"),
        "report_user_id": payload.get("report_user_id") or session.get("id"),
        "report_user": _user(payload.get("report_user_id") or session.get("id")),
        "valid_num": _num(valid), "waste_num": _num(waste),
        "report_num": _num(valid + waste),
        "report_waste_list": rwl, "waste_item_sum": len(rwl),
        "working_minutes": minutes,
        "start_time": st, "end_time": et,
        "price_mode": c.get("price_mode"),
        "craft_unit_money": _num(unit_money),
        "valid_money": _wage(c.get("price_mode") or 1, valid, unit_money, minutes),
        "performance_pay_config_json": {"performance_pay": False,
                                        "formula_config": WAGE_FORMULA,
                                        "unit_money": _num(unit_money),
                                        "standard_working_seconds": c.get("standard_working_seconds") or 0},
        "quality_status": 3 if str(c.get("quality_type") or "3") == "3" else 1,
        "settle_status": 1, "settle_status_name": "未结算",
        "is_finish": payload.get("is_finish") or 2,
        "remark": payload.get("remark"),
    }
    rec = _records().add(rec)
    # ---- 任务联动：累计 + 自动开始 + is_finish 完成（deep\01 §0/§2.1） ----
    c["total_valid_num"] = _num(_f(c.get("total_valid_num")) + valid)
    c["valid_num"] = c["total_valid_num"]
    c["waste_num"] = _num(_f(c.get("waste_num")) + waste)
    c["report_num"] = _num(_f(c.get("report_num")) + valid + waste)
    c["working_minutes"] = "%.2f" % (_f(c.get("working_minutes")) + minutes)
    c["wait_produce_num"] = _num(max(0.0, _f(c.get("num")) - _f(c["total_valid_num"])), 0)
    if c.get("status") == 1:
        c["status"] = 2
        c["start_time"] = core.now()
    if str(payload.get("is_finish") or "2") == "1":
        c["status"] = 3
        c["end_time"] = core.now()
    store.flush()
    # ---- 加工单联动：实际生产数累计 ----
    if bill is not None:
        bill["actual_num"] = _num(_f(bill.get("actual_num")) + valid)
        _bills().flush()
    return core.ok(rec)

def _report_work(body, session):
    return _report_one(body, session)

def _report_work_batch(body, session):
    """批量报工（简化）：{produce_craft_data:[...], is_finish 固定仅报工}。"""
    arr = body.get("produce_craft_data") or []
    if not arr:
        return core.fail("请选择报工数据")
    sc = ec = 0
    msgs = []
    for p in arr:
        if not p.get("report_user_id"):
            ec += 1
            msgs.append("请选择生产人员")
            continue
        r = _report_one(dict(p, is_finish=p.get("is_finish") or body.get("is_finish") or 2), session)
        if r.get("success"):
            sc += 1
        else:
            ec += 1
            if r.get("msg") not in msgs:
                msgs.append(r.get("msg"))
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "；".join(msgs)
    return core.ok(data)

# ---------------- 种子（B11 未生成时自建 3 条样例；生成后本函数跳过） ----------------
_WASTE_PRESET = [  # api_dump/waste_all.json 系统预置 8 条（实测原文）
    ("松动/异响",), ("变形/开裂",), ("氧化/脱碳",), ("开裂/起皱",),
    ("螺纹不良",), ("孔位偏移",), ("烧伤/裂纹",), ("毛刺/飞边",),
]

def _seed():
    # 不良品项 8 条系统预置（api_dump/waste_all.json 实测）独立种子：仅当 wastes 库为空时补
    ws = _wastes()
    if not ws.rows:
        for i, (name,) in enumerate(_WASTE_PRESET):
            ws.add({"id": 31629 + i, "code": name, "name": name, "is_seed": 1})
    # B11 已生成 produce_bills（导出表 431 行）时跳过自建样例
    if _bills().rows:
        return
    u = {"id": 250574, "real_name": "佳H"}
    goods1 = {"id": 9731637, "code": "SP202610150002", "name": "测试11", "standard": None, "unit": None}
    goods2 = {"id": 9728493, "code": "SP202610150001", "name": "刀具", "standard": None, "unit": None}
    base = {"enterprise_id": 16807, "create_user": u, "create_user_id": 250574,
            "work_type": 1, "produce_type": 1, "create_mode": 1,
            "pause_reason": None, "cancel_reason": None,
            "actual_num": "0.00000000", "stock_in_num": "0.00000000",
            "priority_configure_id": None, "customer_contract_id": None,
            "produce_schedule_id": None, "produce_project_id": None, "is_seed": 1}
    # 样例1：未开始（可演示 开始/完成/取消）
    b1 = _bills().add(dict(base, code=_next_bill_code(_bills().all()), goods=goods1, goods_id=goods1["id"],
                           num="20.00000000", status=1, requisition_status=2, start_time=None,
                           end_time=None, sort_num=None, remark="B5样例：未开始"))
    for cn, pm, um in (("钻孔", 1, "80.00000000"), ("热处理", 2, "30.00000000")):
        _new_craft(b1, {"craft_name": cn, "num": 20, "price_mode": pm, "unit_money": um,
                        "quality_type": 1, "waste_item_ids": [31629, 31630]})
    # 样例2：生产中（可演示 撤回/暂停/批量/报工；单价 0.2 验证工资截断 19×0.2→3）
    b2 = _bills().add(dict(base, code=_next_bill_code(_bills().all()), goods=goods2, goods_id=goods2["id"],
                           num="100.00000000", status=2, requisition_status=3, start_time=core.now(),
                           end_time=None, sort_num=1, remark="B5样例：生产中"))
    _new_craft(b2, {"craft_name": "装配", "num": 100, "price_mode": 1, "unit_money": "0.20000000",
                    "quality_type": 3, "waste_item_ids": [31631]})
    # 样例3：已暂停（可演示 继续/完成/取消）
    b3 = _bills().add(dict(base, code=_next_bill_code(_bills().all()), goods=goods1, goods_id=goods1["id"],
                           num="10.00000000", status=5, requisition_status=2, start_time=core.now(),
                           end_time=None, sort_num=None, pause_reason="等待物料到货", remark="B5样例：已暂停"))
    _new_craft(b3, {"craft_name": "质检", "num": 10, "price_mode": 1, "unit_money": "5.00000000",
                    "quality_type": 2, "waste_item_ids": []})

_seed()

ROUTES = {
    # produce_bill（deep\01 §1；列表页用 web_list）
    ("produce_bill", "list"): _list,
    ("produce_bill", "web_list"): _list,
    ("produce_bill", "status_count"): _status_count,
    ("produce_bill", "add"): _add,
    ("produce_bill", "edit"): _edit,
    ("produce_bill", "del"): _del,
    ("produce_bill", "detail"): _detail,
    ("produce_bill", "edit_status"): _edit_status,
    ("produce_bill", "pause"): _pause,
    ("produce_bill", "cancel_pause"): _cancel_pause,
    ("produce_bill", "batch_start_order"): _batch_start,
    ("produce_bill", "batch_finish_order"): _batch_finish,
    ("produce_bill", "batch_pause_order"): _batch_pause,
    ("produce_bill", "batch_cancel_order"): _batch_cancel,
    ("produce_bill", "sort_status"): _sort_status,
    ("produce_bill", "drag_move_sort_num"): _drag_move_sort_num,
    # produce_bill_craft（deep\01 §2 / deep\18 §2）
    ("produce_bill_craft", "list"): _craft_list,
    ("produce_bill_craft", "list_by_filter_view"): _craft_list,
    ("produce_bill_craft", "status_count"): _craft_status_count,
    ("produce_bill_craft", "dispatch"): _dispatch,
    ("produce_bill_craft", "get_dispatch_list"): _get_dispatch_list,
    ("produce_bill_craft", "select_waste_item"): _select_waste_item,
    ("produce_bill_craft", "report_work"): _report_work,
    ("produce_bill_craft", "report_work_batch"): _report_work_batch,
}
