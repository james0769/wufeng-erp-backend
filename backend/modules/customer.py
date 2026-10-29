# -*- coding: utf-8 -*-
"""B7 客户/合同/开票/回款域（demo 级，无权限约束）。
依据：_gen_tmp\\kgd_reverse\\analysis\\deep\\09-客户管理域.md（详情13Tab简化；
合同审核 WAREHOUSE_AUDIT_STATUS 七值码表、≤6级会签/或签简化为单级；INVOICE_STATUS 双码表）。
种子：customers/contacts/contracts/invoices 由 B11 从 api_dump\\deep_m1 真实数据生成；
跟单/地址/收支三个辅助仓（customer_reminds/customer_addresses/customer_incomes）首访时
以 api_dump 实测行落盘（真实数据，字段照抄）。
"""
import os
import core

# ---- 枚举（09 报告 §13 逐字） ----
AUDIT_NAME = {1: "不需审核", 2: "待审", 3: "通过", 4: "退回", 5: "草稿", 6: "撤回", 7: "审核中"}      # WAREHOUSE_AUDIT_STATUS
AUDIT_TAG = {1: "4", 2: "2", 3: "1", 4: "3", 5: "2", 6: "4", 7: "2"}
BIZ_NAME = {1: "未生效", 2: "未开始", 3: "履约中", 4: "已完成", 5: "已终止"}                            # CUSTOMER_CONTRACT_STATUS
BIZ_TAG = {1: "2", 2: "4", 3: "5", 4: "1", 5: "3"}
INVOICE_NAME = {1: "开票完毕", 2: "退回", 3: "待开票"}                                                  # INVOICE_STATUS（WSIT=3 待开票）
INVOICE_TAG = {1: "1", 2: "2", 3: "3"}
DIRECTION_NAME = {1: "收入", 2: "支出"}
DIRECTION_TAG = {1: "5", 2: "2"}
TAX_NAME = {1: "是", 2: "否"}
STOCK_OUT_NAME = {1: "待出库", 2: "部分出库", 3: "已出库"}
FOLLOW_NAME = {946742: "上门", 946743: "电话", 946744: "网络"}                                          # 字典 customer_follow_type 实测
RECONCILIATION_BILL, RECONCILIATION_WARE = 1, 2                                                        # 按合同/按发货

# ---- 工具 ----
def _s(v):
    return str(v).strip() if v is not None else ""

def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _m8(v):
    return "%.8f" % _f(v)

def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def _ids(body):
    v = body.get("ids")
    if isinstance(v, list) and v:
        return v
    i = body.get("id")
    return [i] if i is not None else []

def _in(v, seq):
    return str(v) in [str(x) for x in seq]

def _like(val, key):
    return (not key) or (key.lower() in _s(val).lower())

def _date_range(val, start, end):
    if not val:
        return not start and not end
    if start and val < _s(start):
        return False
    if end:
        e = _s(end)
        if len(e) == 10:
            e += " 23:59:59"
        if val > e:
            return False
    return True

def _paged(rows, body, default_size=30):
    """过滤后分页（契约同 core.Store.page）。"""
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return {"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize], "total": len(rows), "pageNo": pageNo, "pageSize": pageSize}

def _user_ref(uid):
    if uid is None:
        return None
    try:
        u = core.Store("users").get(uid)
        if u:
            return {"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name") or u.get("name"),
                    "avatar": u.get("avatar"), "type_name": u.get("type_name") or ""}
    except Exception:
        pass
    return {"id": uid, "name": _s(uid), "real_name": _s(uid)}

def _customer_ref(cid):
    c = core.Store("customers").get(cid) if cid is not None else None
    if c:
        return {"id": c.get("id"), "name": c.get("name"), "code": c.get("code"), "jianpin": c.get("jianpin")}
    return None

def _fill(r, key, val):
    if r.get(key) in (None, ""):
        r[key] = val
    return r

# ---- 辅助仓：首访落盘（api_dump 实测行，字段照抄） ----
_ADDR_SEED = [
    {"id": 1349145, "enterprise_id": 16807, "customer_id": 1687330, "province_code": 110000, "city_code": 110101, "area_code": None, "address": "北京太易德电力抢修中心", "is_default": 1, "location": "116.286617,39.859434", "is_accurate": 1, "region_address": "北京市 东城区 ", "full_address": "北京市 东城区 北京太易德电力抢修中心", "created_at": "2026-10-21 22:51:43", "updated_at": "2026-10-21 22:53:25"},
    {"id": 1339762, "enterprise_id": 16807, "customer_id": 1682415, "province_code": 110000, "city_code": 110101, "area_code": None, "address": "111", "is_default": 1, "location": "116.416334,39.928359", "is_accurate": 2, "region_address": "北京市 东城区 ", "full_address": "北京市 东城区 111", "created_at": "2026-10-07 15:06:29", "updated_at": "2026-10-07 15:06:29"},
    {"id": 1278341, "enterprise_id": 16807, "customer_id": 1580549, "province_code": 440000, "city_code": 440300, "area_code": 440306, "address": "龙华区大浪街道华辉路紫荆一号A栋2层", "is_default": 1, "location": "113.8754349384,22.796625315401", "is_accurate": 2, "region_address": "广东省 深圳市 宝安区 ", "full_address": "广东省 深圳市 宝安区 龙华区大浪街道华辉路紫荆一号A栋2层", "created_at": "2026-07-24 13:11:07", "updated_at": "2026-07-24 13:11:07"},
    {"id": 1263063, "enterprise_id": 16807, "customer_id": 1562367, "province_code": 440000, "city_code": None, "area_code": None, "address": "深圳市宝安区松岗街道松岗工业园B栋3层", "is_default": 1, "location": "113.85777276938,22.766673832822", "is_accurate": 2, "region_address": "广东省 ", "full_address": "广东省 深圳市宝安区松岗街道松岗工业园B栋3层", "created_at": "2026-07-10 13:37:45", "updated_at": "2026-07-10 13:37:45"},
]
_REMIND_SEED = [
    {"id": 109749, "enterprise_id": 16807, "customer_id": 1350596, "user_id": 214396, "content": "测试", "next_time": "2026-08-24 10:13:00", "created_at": "2026-08-23 09:13:50", "updated_at": "2026-08-24 09:45:13", "linkman_id": 1525019, "has_remind": 1, "follow_type": 946742, "location": "116.459049,39.998608", "address": "北京市 朝阳区 望京街道南湖中园222号楼"},
    {"id": 82606, "enterprise_id": 16807, "customer_id": 1455031, "user_id": 196798, "content": "12", "next_time": "2026-04-05 20:59:00", "created_at": "2026-04-05 20:59:00", "updated_at": "2026-04-05 20:59:00", "linkman_id": 1643437, "has_remind": 1, "follow_type": 946744, "location": None, "address": None},
    {"id": 68522, "enterprise_id": 16807, "customer_id": 1350599, "user_id": 171380, "content": "航凯提出刀具管理外包需求，提供“按加工量付费”模式（含库存托管+定期维护）。\n\n测算年成本可降低12%，计划8月启动试点。", "next_time": "2026-01-04 15:19:15", "created_at": "2026-01-04 15:19:15", "updated_at": "2026-01-04 15:19:15", "linkman_id": 1525022, "has_remind": 1, "follow_type": 946743, "location": None, "address": None},
    {"id": 68513, "enterprise_id": 16807, "customer_id": 1350599, "user_id": 171380, "content": "通过行业展会获知河北航凯需求，初步电话沟通其刀具采购痛点（铣刀寿命短、加工效率低）。\n\n发送产品目录及典型客户案例（如汽车零部件加工场景）。", "next_time": "2026-01-04 15:19:15", "created_at": "2026-01-04 15:19:15", "updated_at": "2026-01-04 15:19:15", "linkman_id": 1525022, "has_remind": 1, "follow_type": 946743, "location": None, "address": None},
    {"id": 68512, "enterprise_id": 16807, "customer_id": 1350609, "user_id": 171380, "content": "客户在寻找主轴的供应商，对我们产品有很大的兴趣，但是没用过咱们的产品，缺乏信任感。", "next_time": "2026-01-04 15:19:15", "created_at": "2026-01-04 15:19:15", "updated_at": "2026-01-04 15:19:15", "linkman_id": 1525032, "has_remind": 1, "follow_type": 946743, "location": None, "address": None},
    {"id": 68508, "enterprise_id": 16807, "customer_id": 1350610, "user_id": 171380, "content": "客户对我们刀库有兴趣，想要继续了解", "next_time": "2026-01-04 15:19:15", "created_at": "2026-01-04 15:19:15", "updated_at": "2026-01-04 15:19:15", "linkman_id": 1525033, "has_remind": 1, "follow_type": 946743, "location": None, "address": None},
]
_INCOME_SEED = [
    {"id": 467052, "enterprise_id": 16807, "contract_id": 1121767, "customer_id": 1350596, "money": "99000.00000000", "direction": 1, "is_advance": 2, "remark": None, "audit_user_id": None, "audit_time": None, "audit_remark": None, "audit_status": 1, "type": 1, "created_at": "2026-10-21 13:03:52", "updated_at": "2026-10-21 13:03:52", "create_user_id": 63097},
    {"id": 466692, "enterprise_id": 16807, "contract_id": 1121767, "customer_id": 1350596, "money": "1000.00000000", "direction": 1, "is_advance": 1, "remark": "预付款", "audit_user_id": None, "audit_time": None, "audit_remark": None, "audit_status": 1, "type": 1, "created_at": "2026-10-21 13:03:52", "updated_at": "2026-10-21 13:03:52", "create_user_id": 63097},
    {"id": 431896, "enterprise_id": 16807, "contract_id": None, "customer_id": 1628759, "money": "40000.00000000", "direction": 1, "is_advance": 2, "remark": None, "audit_user_id": None, "audit_time": None, "audit_remark": None, "audit_status": 1, "type": 2, "created_at": "2026-09-02 11:58:07", "updated_at": "2026-09-02 11:58:07", "create_user_id": 63097},
]

def _aux(name, seed):
    if not os.path.exists(os.path.join(core.DATA_DIR, name + ".json")):
        core._save(name, list(reversed([dict(r) for r in seed])))   # 旧→新，add 时插队首
    return core.Store(name)

def _reminds():
    return _aux("customer_reminds", _REMIND_SEED)

def _addresses():
    return _aux("customer_addresses", _ADDR_SEED)

def _incomes():
    return _aux("customer_incomes", _INCOME_SEED)

# ---- 装饰（补缺不改种子里已有值） ----
def _dec_customer(r):
    _fill(r, "is_enable", 1)
    _fill(r, "in_sea", 2)
    _fill(r, "followed", 2)
    _fill(r, "is_shared", 2)
    _fill(r, "location", "-1")
    _fill(r, "jianpin", "")
    _fill(r, "tag_ids", [])
    _fill(r, "tag_names", "")
    _fill(r, "work_order_count", 0)
    _fill(r, "not_finish_work_order_count", 0)
    _fill(r, "fieldValueList", [])
    _fill(r, "code", "")
    _fill(r, "resp_user", _user_ref(r.get("resp_user_id")))
    _fill(r, "create_user", _user_ref(r.get("create_user_id")))
    if not r.get("default_linkman"):
        lm = [c for c in core.Store("contacts").all() if str(c.get("customer_id")) == str(r.get("id")) and c.get("is_default") == 1]
        if lm:
            r["default_linkman"] = {"id": lm[0].get("id"), "name": lm[0].get("name"), "mobile": lm[0].get("mobile"), "is_default": 1}
    if not r.get("default_address"):
        ad = [a for a in _addresses().all() if str(a.get("customer_id")) == str(r.get("id")) and a.get("is_default") == 1]
        if ad:
            r["default_address"] = ad[0]
    _fill(r, "editAccessible", True)
    _fill(r, "delAccessible", True)
    return r

def _dec_contract(r):
    a = _int(r.get("audit_status")) or 1
    b = _int(r.get("business_status")) or 2
    t = _int(r.get("has_tax")) or 1
    _fill(r, "audit_status_name", AUDIT_NAME.get(a, ""))
    _fill(r, "audit_status_tag_class", AUDIT_TAG.get(a, "2"))
    _fill(r, "business_status_name", BIZ_NAME.get(b, ""))
    _fill(r, "business_status_tag_class", BIZ_TAG.get(b, "2"))
    _fill(r, "has_tax_name", TAX_NAME.get(t, ""))
    _fill(r, "has_tax_tag_class", "1" if t == 1 else "4")
    _fill(r, "has_tax_color", "#4caf50" if t == 1 else "")
    _fill(r, "stock_out_status_name", STOCK_OUT_NAME.get(r.get("stock_out_status") or 1, ""))
    _fill(r, "stock_out_status_color", "red" if (r.get("stock_out_status") or 1) == 1 else "")
    _fill(r, "income_money", "0.00000000")
    _fill(r, "expend_money", "0.00000000")
    _fill(r, "advance", "0.00000000")
    _fill(r, "sale_expense_total", "0.0000")
    _fill(r, "finish_invoice_total", "0.0000")
    _fill(r, "sale_total", "0.00000000")
    _fill(r, "item_list", [])
    _fill(r, "produce_project_list", [])
    _fill(r, "fieldValueList", [])
    _fill(r, "item_money_calculate_config", '["${合同明细.单价}*${合同明细.销售数量}","8"]')
    _fill(r, "goods_names", ", ".join([_s((i.get("goods") or {}).get("name")) for i in r["item_list"] if i.get("goods")]))
    _fill(r, "region_address", "")
    _fill(r, "full_address", r.get("region_address"))
    _fill(r, "customer", _customer_ref(r.get("customer_id")))
    _fill(r, "linkman", (lambda c: c and {"id": c.get("id"), "name": c.get("name"), "mobile": c.get("mobile")})(core.Store("contacts").get(r.get("linkman_id")) if r.get("linkman_id") else None))
    _fill(r, "sales_user", _user_ref(r.get("sales_user_id")))
    _fill(r, "create_user", _user_ref(r.get("create_user_id")))
    _fill(r, "audit_user", _user_ref(r.get("audit_user_id")))
    money = _f(r.get("money"))
    income = _f(r.get("income_money"))
    _fill(r, "debt_money", _m8(max(money - income, 0)))
    _fill(r, "income_progress", str(round(income / money * 100)) if money > 0 else "0")
    _fill(r, "auditAccessible", a == 2)
    _fill(r, "allow_withdrawal", 2)
    _fill(r, "editAccessible", True)
    _fill(r, "income_progress_accessible", True)
    return r

def _dec_invoice(r):
    s = _int(r.get("status")) or 3
    _fill(r, "status_name", INVOICE_NAME.get(s, ""))
    _fill(r, "status_tag_class", INVOICE_TAG.get(s, "3"))
    _fill(r, "fieldValueList", [])
    _fill(r, "attachment_list", [])
    _fill(r, "customer", _customer_ref(r.get("customer_id")))
    if not r.get("customer_contract"):
        c = core.Store("contracts").get(r.get("customer_contract_id")) if r.get("customer_contract_id") else None
        if c:
            r["customer_contract"] = {"id": c.get("id"), "code": c.get("code"), "money": c.get("money"),
                                      "finish_invoice_total": c.get("finish_invoice_total") or "0.0000",
                                      "customer": {"id": (c.get("customer") or {}).get("id"), "name": (c.get("customer") or {}).get("name")}}
    _fill(r, "apply_user", _user_ref(r.get("apply_user_id")))
    _fill(r, "audit_user", _user_ref(r.get("audit_user_id")))
    return r

def _dec_income(r):
    a = _int(r.get("audit_status")) or 1
    d = _int(r.get("direction")) or 1
    _fill(r, "audit_status_name", AUDIT_NAME.get(a, ""))
    _fill(r, "audit_status_tag_class", AUDIT_TAG.get(a, "4"))
    _fill(r, "direction_name", DIRECTION_NAME.get(d, ""))
    _fill(r, "direction_tag_class", DIRECTION_TAG.get(d, "5"))
    _fill(r, "is_advance", 2)
    _fill(r, "type", RECONCILIATION_BILL)
    _fill(r, "fieldValueList", [])
    _fill(r, "attachments", [])
    _fill(r, "customer_contract", (lambda c: c and {"id": c.get("id"), "code": c.get("code")})(core.Store("contracts").get(r.get("contract_id")) if r.get("contract_id") else None))
    _fill(r, "customer", _customer_ref(r.get("customer_id")))
    _fill(r, "create_user", _user_ref(r.get("create_user_id")))
    _fill(r, "audit_user", _user_ref(r.get("audit_user_id")))
    _fill(r, "auditAccessible", a == 2)
    _fill(r, "allow_withdrawal", 2)
    return r

def _dec_remind(r):
    _fill(r, "has_remind", 1)
    _fill(r, "fieldValueList", [])
    _fill(r, "next_time_formatter", _s(r.get("next_time"))[:10])
    _fill(r, "customer", _customer_ref(r.get("customer_id")))
    _fill(r, "user", _user_ref(r.get("user_id")))
    _fill(r, "linkman", (lambda c: c and {"id": c.get("id"), "name": c.get("name"), "mobile": c.get("mobile")})(core.Store("contacts").get(r.get("linkman_id")) if r.get("linkman_id") else None))
    ft = _int(r.get("follow_type"))
    _fill(r, "follow_category", {"id": ft, "name": FOLLOW_NAME.get(ft, "")} if ft else None)
    return r

# ================= 客户 =================
def _customer_matches(r, b):
    dl = r.get("default_linkman") or {}
    da = r.get("default_address") or {}
    ru = r.get("resp_user") or {}
    kw = _s(b.get("keyword"))
    if kw:
        hay = [_s(r.get("name")), _s(r.get("code")), _s(r.get("jianpin")), _s(dl.get("name")), _s(dl.get("mobile"))]
        if not any(kw.lower() in h.lower() for h in hay):
            return False
    if not (_like(r.get("code"), _s(b.get("code"))) and _like(r.get("name"), _s(b.get("name")))):
        return False
    if not _like(dl.get("name"), _s(b.get("linkman_name"))):
        return False
    if not _like(dl.get("mobile"), _s(b.get("linkman_mobile"))):
        return False
    if not _like(ru.get("real_name"), _s(b.get("resp_user_name"))):
        return False
    tags = b.get("tag_ids")
    if tags:
        tags = tags if isinstance(tags, list) else [tags]
        if not set(str(t) for t in tags) & set(str(x) for x in (r.get("tag_ids") or [])):
            return False
    region = b.get("region") or []
    p, c2, a2 = (region + [None, None, None])[:3] if isinstance(region, list) else (b.get("province_code"), b.get("city_code"), b.get("area_code"))
    if p and str(da.get("province_code")) != str(p):
        return False
    if c2 and str(da.get("city_code")) != str(c2):
        return False
    if a2 and str(da.get("area_code")) != str(a2):
        return False
    if b.get("is_enable") and _int(r.get("is_enable")) != _int(b.get("is_enable")):
        return False
    if b.get("followed") and _int(r.get("followed")) != _int(b.get("followed")):
        return False
    if not _date_range(r.get("created_at"), b.get("created_at_start"), b.get("created_at_end")):
        return False
    return True

def _customer_list(body, session):
    rows = [_dec_customer(r) for r in core.Store("customers").all()]
    rows = [r for r in rows if _customer_matches(r, body)]
    return core.paged_ok(_paged(rows, body))

def _next_customer_code(rows):
    mx = 0
    for r in rows:
        c = _s(r.get("code"))
        if c.startswith("KH"):
            try:
                mx = max(mx, int(c[2:]))
            except ValueError:
                pass
    return "KH%06d" % (mx + 1)

def _customer_add(body, session):
    name = _s(body.get("name"))
    if not name:
        return core.fail("必须填写")
    store = core.Store("customers")
    row = {
        "name": name,
        "code": _s(body.get("code")) or _next_customer_code(store.all()),
        "jianpin": _s(body.get("jianpin")),
        "resp_user_id": body.get("resp_user_id") or session.get("id"),
        "create_user_id": session.get("id"),
        "enterprise_id": body.get("enterprise_id") or session.get("enterprise_id"),
        "is_enable": _int(body.get("is_enable"), 1),
        "in_sea": 2,
        "location": "-1",
        "tag_ids": body.get("tag_ids") or [],
        "last_log": {"operation_name": "新增客户", "channel": 13, "busi_type": 21,
                     "content": "客户，客户名：%s，客户编号：%s" % (name, _s(body.get("code")) or "自动生成"),
                     "created_at": core.now(), "operator": _user_ref(session.get("id"))},
    }
    row = store.add(row)
    # 首要联系人（同表单提交）
    lm = body.get("linkman") or {}
    lm_name = _s(body.get("linkman_name") or lm.get("name"))
    if lm_name:
        lm_mobile = _s(body.get("linkman_mobile") or lm.get("mobile"))
        lrow = core.Store("contacts").add({"customer_id": row["id"], "name": lm_name, "mobile": lm_mobile, "is_default": 1})
        row["default_linkman"] = {"id": lrow["id"], "name": lm_name, "mobile": lm_mobile, "is_default": 1}
    return core.ok(_dec_customer(row))

def _customer_edit(body, session):
    r = core.Store("customers").get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    patch = {k: body[k] for k in ("name", "code", "jianpin", "tag_ids", "is_enable", "resp_user_id") if k in body}
    if "resp_user_id" in patch:
        patch["resp_user"] = _user_ref(patch["resp_user_id"])
    r = core.Store("customers").update(r["id"], patch)
    lm = body.get("linkman") or {}
    lm_name = _s(body.get("linkman_name") or lm.get("name"))
    lm_mobile = body.get("linkman_mobile", lm.get("mobile"))
    if lm_name or lm_mobile:
        cur = [c for c in core.Store("contacts").all() if str(c.get("customer_id")) == str(r["id"]) and c.get("is_default") == 1]
        if cur:
            p = {}
            if lm_name:
                p["name"] = lm_name
            if lm_mobile is not None:
                p["mobile"] = _s(lm_mobile)
            core.Store("contacts").update(cur[0]["id"], p)
    return core.ok(_dec_customer(r))

def _customer_del(body, session):
    ids = _ids(body)
    if not ids:
        return core.fail("请选择数据")
    cstore, lstore, rstore = core.Store("customers"), core.Store("contacts"), _reminds()
    n = 0
    for i in ids:
        if not cstore.get(i):
            continue
        cstore.remove(i)
        lstore.rows = [x for x in lstore.rows if str(x.get("customer_id")) != str(i)]   # 级联：联系人/跟单（同删除确认弹窗语义）
        rstore.rows = [x for x in rstore.rows if str(x.get("customer_id")) != str(i)]
        n += 1
    lstore.flush()
    rstore.flush()
    if not n:
        return core.fail("数据不存在")
    return core.ok()

def _customer_detail(body, session):
    r = core.Store("customers").get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    cid = r["id"]
    reminds = [_dec_remind(x) for x in _reminds().all() if str(x.get("customer_id")) == str(cid)]
    linkmen = [_fill(dict(x), "fieldValueList", []) for x in core.Store("contacts").all() if str(x.get("customer_id")) == str(cid)]
    addrs = [dict(x) for x in _addresses().all() if str(x.get("customer_id")) == str(cid)]
    contracts = [_dec_contract(x) for x in core.Store("contracts").all() if str(x.get("customer_id")) == str(cid)]
    tab_contract = [{k: c.get(k) for k in ("id", "code", "money", "income_money", "debt_money", "audit_status", "audit_status_name",
                                           "business_status", "business_status_name", "delivery_date", "collection_date",
                                           "sale_total", "finish_invoice_total", "created_at")} for c in contracts]
    d = _dec_customer(r)
    d["tabs"] = {   # 详情 13 Tab 简化：恒显日志 + 跟单/联系人/地址/合同
        "log": [d.get("last_log")] if d.get("last_log") else [],
        "remind": reminds,
        "linkman": linkmen,
        "address": addrs,
        "contract": tab_contract,
        "contract_count": len(contracts),
    }
    return core.ok(d)

# ================= 联系人 / 地址 =================
def _linkman_list(body, session):
    rows = core.Store("contacts").all()
    kw = _s(body.get("keyword"))
    out = []
    for x in rows:
        cust = _customer_ref(x.get("customer_id")) or x.get("customer") or {}
        if body.get("customer_id") and str(x.get("customer_id")) != str(body.get("customer_id")):
            continue
        if not (_like(x.get("name"), _s(body.get("name"))) and _like(x.get("mobile"), _s(body.get("mobile")))):
            continue
        if not _like(cust.get("name"), _s(body.get("customer_name"))):
            continue
        if kw:
            hay = [_s(x.get("name")), _s(x.get("mobile")), _s(cust.get("name")), _s(cust.get("jianpin")), _s(cust.get("code"))]
            if not any(kw.lower() in h.lower() for h in hay):
                continue
        r = dict(x)
        _fill(r, "is_default", 2)
        _fill(r, "fieldValueList", [])
        r["customer"] = cust
        out.append(r)
    return core.paged_ok(_paged(out, body))

def _linkman_add(body, session):
    cid = body.get("customer_id")
    if not cid or not core.Store("customers").get(cid):
        return core.fail("请选择客户")
    name = _s(body.get("name"))
    if not name:
        return core.fail("请输入联系人")
    store = core.Store("contacts")
    has_default = any(str(c.get("customer_id")) == str(cid) and c.get("is_default") == 1 for c in store.all())
    row = store.add({"customer_id": cid, "name": name, "mobile": _s(body.get("mobile")),
                     "is_default": _int(body.get("is_default"), 2 if has_default else 1)})
    _fill(row, "fieldValueList", [])
    row["customer"] = _customer_ref(cid)
    return core.ok(row)

def _address_list(body, session):
    rows = _addresses().all()
    if body.get("customer_id"):
        rows = [x for x in rows if str(x.get("customer_id")) == str(body.get("customer_id"))]
    out = []
    for x in rows:
        r = dict(x)
        _fill(r, "is_default", 2)
        _fill(r, "is_accurate", 2)
        _fill(r, "region_address", "")
        _fill(r, "full_address", _s(r.get("region_address")) + _s(r.get("address")))
        _fill(r, "fieldValueList", [])
        r["customer"] = _customer_ref(r.get("customer_id"))
        out.append(r)
    return core.paged_ok(_paged(out, body))

def _address_add(body, session):
    cid = body.get("customer_id")
    if not cid or not core.Store("customers").get(cid):
        return core.fail("请选择客户")
    names = [n for n in (body.get("province_name"), body.get("city_name"), body.get("area_name")) if _s(n)]
    region = _s(body.get("region_address")) or (" ".join(names) + " " if names else "")
    address = _s(body.get("address"))
    store = _addresses()
    has_default = any(str(a.get("customer_id")) == str(cid) and a.get("is_default") == 1 for a in store.all())
    row = store.add({
        "customer_id": cid,
        "province_code": body.get("province_code"),
        "city_code": body.get("city_code"),
        "area_code": body.get("area_code"),
        "address": address,
        "location": _s(body.get("location")) or None,
        "is_accurate": _int(body.get("is_accurate"), 2),
        "region_address": region,
        "full_address": _s(body.get("full_address")) or (region + address),
        "is_default": _int(body.get("is_default"), 2 if has_default else 1),
    })
    row["customer"] = _customer_ref(cid)
    return core.ok(row)

# ================= 合同订单 =================
def _contract_matches(r, b):
    kw = _s(b.get("keyword"))
    cust = r.get("customer") or {}
    if not _like(r.get("code"), _s(b.get("code"))):
        return False
    if not _like(r.get("goods_names"), _s(b.get("goods_name"))):
        return False
    if not _like(cust.get("name"), kw):
        if kw and not (_like(r.get("code"), kw) or _like(r.get("goods_names"), kw)):
            return False
    if b.get("customer_id"):
        cids = b["customer_id"] if isinstance(b["customer_id"], list) else [b["customer_id"]]
        if str(r.get("customer_id")) not in [str(x) for x in cids]:
            return False
    if b.get("audit_status") != "" and b.get("audit_status") is not None and _int(b.get("audit_status")) != _int(r.get("audit_status")):
        return False
    if b.get("business_status") and _int(b.get("business_status")) != _int(r.get("business_status")):
        return False
    if b.get("is_stock_out_finish") and _int(r.get("is_stock_out_finish")) != _int(b.get("is_stock_out_finish")):
        return False
    if b.get("is_income_finish"):
        done = _f(r.get("debt_money")) <= 0
        if (_int(b.get("is_income_finish")) == 1) != done:
            return False
    if b.get("sales_user_id") and str(r.get("sales_user_id")) != str(b.get("sales_user_id")):
        return False
    if b.get("create_user_id") and str(r.get("create_user_id")) != str(b.get("create_user_id")):
        return False
    if not _date_range(r.get("created_at"), b.get("created_at_start"), b.get("created_at_end")):
        return False
    if not _date_range(r.get("delivery_date"), b.get("delivery_date_start"), b.get("delivery_date_end")):
        return False
    if not _date_range(r.get("collection_date"), b.get("collection_date_start"), b.get("collection_date_end")):
        return False
    return True

def _contract_list(body, session):
    rows = [_dec_contract(r) for r in core.Store("contracts").all()]
    rows = [r for r in rows if _contract_matches(r, body)]
    return core.paged_ok(_paged(rows, body))

def _contract_add(body, session):
    cust = core.Store("customers").get(body.get("customer_id"))
    if not cust:
        return core.fail("请选择客户")
    items = body.get("item_list") or body.get("items") or []
    if not items:
        return core.fail("请选择商品")
    total, sale_total = 0.0, 0.0
    item_list = []
    for it in items:
        num = _f(it.get("num"), 1.0)
        price = _f(it.get("unit_price"))
        discount = _f(it.get("discount"), 10.0)          # 折扣（折）：10.00=不打折（与实测默认一致）
        imoney = num * price
        after = imoney * discount / 10.0
        g = it.get("goods") or {}
        item_list.append({
            "goods_id": it.get("goods_id") or g.get("id"), "num": _m8(num), "unit_price": _m8(price),
            "discount": "%.2f" % discount, "money": _m8(imoney),
            "discount_money": _m8(imoney - after), "after_discount_money": _m8(after),
            "remark": it.get("remark"), "order_number": len(item_list) + 10,
            "stock_out_num": "0.00000000", "return_num": "0.00000000", "stock_out_status": 1,
            "sum_stock_num": _s(it.get("sum_stock_num")) or "0", "purchase_num": "0", "put_into_produce_num": "0.00000000",
            "goods": {"id": it.get("goods_id") or g.get("id"), "name": it.get("name") or g.get("name"),
                      "code": it.get("code") or g.get("code"), "standard": it.get("standard") or g.get("standard"),
                      "unit": it.get("unit") or g.get("unit")},
        })
        total += after
        sale_total += num
    names = [n for n in (body.get("province_name"), body.get("city_name"), body.get("area_name")) if _s(n)]
    region = _s(body.get("region_address")) or (" ".join(names) + " " if names else "")
    linkman_id = body.get("linkman_id") or next((c["id"] for c in core.Store("contacts").all()
                                                 if str(c.get("customer_id")) == str(cust["id"]) and c.get("is_default") == 1), None)
    money = _f(body.get("money"), total)
    advance = _f(body.get("advance"))
    row = {
        "customer_id": cust["id"], "linkman_id": linkman_id,
        "code": _s(body.get("code")) or core.next_code("HT", core.Store("contracts").all(), width=3),
        "money": _m8(money), "advance": _m8(advance),
        "income_money": "0.00000000", "expend_money": "0.00000000",
        "has_tax": _int(body.get("has_tax"), 1),
        "delivery_date": body.get("delivery_date"), "collection_date": body.get("collection_date"),
        "remark": body.get("remark"), "sale_total": _m8(sale_total),
        "stock_out_status": 1, "is_stock_out_finish": 2,
        "audit_status": _int(body.get("audit_status"), 1),      # 无审核流程时默认 不需审核=1（与实测一致）
        "business_status": _int(body.get("business_status"), 2),
        "sales_user_id": body.get("sales_user_id") or session.get("id"),
        "create_user_id": session.get("id"),
        "enterprise_id": body.get("enterprise_id") or session.get("enterprise_id"),
        "province_code": body.get("province_code"), "city_code": body.get("city_code"), "area_code": body.get("area_code"),
        "address": _s(body.get("address")) or None,
        "region_address": region, "full_address": _s(body.get("full_address")) or (region + _s(body.get("address"))),
        "item_list": item_list,
    }
    row = core.Store("contracts").add(row)
    return core.ok(_dec_contract(row))

def _contract_audit_one(store, r, status, remark, session):
    """单级审核（≤6级会签/或签简化）：通过=3 / 退回=4，改 audit_status。"""
    if _int(r.get("audit_status")) == 5:
        return "草稿无法审核"
    if status == 4 and not _s(remark):
        return "请输入备注"
    patch = {"audit_status": status, "audit_status_name": AUDIT_NAME.get(status, ""),
             "audit_status_tag_class": AUDIT_TAG.get(status, "2"),
             "audit_remark": _s(remark) or None,
             "audit_time": core.now(), "audit_user_id": session.get("id")}
    if status == 3:
        patch["audit_pass_time"] = core.now()
    store.update(r["id"], patch)
    return None

def _contract_audit(body, session):
    ids = _ids(body)
    if not ids:
        return core.fail("请选择数据")
    status = _int(body.get("audit_status"))
    if status not in (3, 4):
        return core.fail("请选择状态")
    store = core.Store("contracts")
    for i in ids:
        r = store.get(i)
        if not r:
            return core.fail("数据不存在")
        err = _contract_audit_one(store, r, status, body.get("audit_remark"), session)
        if err:
            return core.fail(err)
    return core.ok()

def _contract_del(body, session):
    ids = _ids(body)
    if not ids:
        return core.fail("请选择数据")
    store = core.Store("contracts")
    n = 0
    for i in ids:
        if store.get(i):
            store.remove(i)
            n += 1
    if not n:
        return core.fail("数据不存在")
    return core.ok()

# ================= 发票 =================
def _invoice_apply_one(body, contract, session):
    """申请开票守卫（逐字）：只有审核通过的合同方可申请开票 / 当前合同不含税，无法申请开票。"""
    if _int(contract.get("audit_status")) not in (1, 3):
        return None, "只有审核通过的合同方可申请开票"
    if _int(contract.get("has_tax")) != 1:
        return None, "当前合同不含税，无法申请开票。"
    money = body.get("money") if _s(body.get("money")) else max(_f(contract.get("money")) - _f(contract.get("finish_invoice_total")), 0)
    if _f(money) <= 0:
        return None, "开票金额不能为0"
    row = {
        "customer_id": contract.get("customer_id"), "customer_contract_id": contract["id"],
        "money": _m8(money), "status": 3, "apply_user_id": session.get("id"),
        "apply_remark": _s(body.get("apply_remark")) or None,
        "attachment_list": body.get("attachment_list") or [],
        "enterprise_id": body.get("enterprise_id") or session.get("enterprise_id"),
    }
    return core.Store("invoices").add(row), None

def _invoice_add(body, session):
    contract = core.Store("contracts").get(body.get("customer_contract_id") or body.get("contract_id"))
    if not contract:
        return core.fail("请选择需要开票的合同")
    row, err = _invoice_apply_one(body, contract, session)
    if err:
        return core.fail(err)
    return core.ok(_dec_invoice(row))

def _invoice_batch_add(body, session):
    cids = body.get("customer_contract_ids") or body.get("ids") or []
    if not cids:
        return core.fail("请选择需要开票的合同")
    store = core.Store("contracts")
    ok_n, errs = 0, []
    for cid in cids:
        c = store.get(cid)
        if not c:
            errs.append("%s:数据不存在" % cid)
            continue
        _, err = _invoice_apply_one({}, c, session)
        if err:
            errs.append("%s:%s" % (_s(c.get("code")), err))
        else:
            ok_n += 1
    return dict(core.ok({"success_num": ok_n, "fail_num": len(errs), "fail_reasons": errs}), msg="批量开票成功")

def _invoice_list(body, session):
    kw_c = _s(body.get("customer_name"))
    kw_code = _s(body.get("customer_contract_code"))
    kw_apply = _s(body.get("apply_user_name"))
    kw_audit = _s(body.get("audit_user_name"))
    rows = []
    for r in core.Store("invoices").all():
        r = _dec_invoice(r)
        c = r.get("customer") or {}
        cc = r.get("customer_contract") or {}
        if not _like(c.get("name"), kw_c):
            continue
        if not _like(cc.get("code"), kw_code):
            continue
        if not _like((r.get("apply_user") or {}).get("real_name"), kw_apply):
            continue
        if not _like((r.get("audit_user") or {}).get("real_name"), kw_audit):
            continue
        if body.get("status") != "" and body.get("status") is not None and _int(body.get("status")) != _int(r.get("status")):
            continue
        if not _date_range(r.get("created_at"), b_start(body, "created_at"), b_end(body, "created_at")):
            continue
        if not _date_range(r.get("audit_time"), b_start(body, "invoice_time"), b_end(body, "invoice_time")):
            continue
        rows.append(r)
    return core.paged_ok(_paged(rows, body))

def b_start(body, field):
    v = body.get(field)
    if isinstance(v, list):
        return v[0] if v else None
    return v if v and not isinstance(v, dict) else None

def b_end(body, field):
    v = body.get(field)
    if isinstance(v, list):
        return v[1] if len(v) > 1 else None
    return body.get(field + "_end")

# 跳过原因 5 类文案（09 报告 §9.2 逐字；采购单 3 类在本域恒为 0，由采购域复用同一弹窗）
SKIP_TEXTS = ["条开票完毕的申请单", "条退回的申请单", "条开票完毕的采购单", "条无供应商的采购单", "条待审核或已退回的采购单"]

def _invoice_batch_finish(body, session):
    ids = _ids(body)
    if not ids:
        return core.fail("请选择数据")
    store = core.Store("invoices")
    cstore = core.Store("contracts")
    invoice_time = _s(body.get("invoice_time")) or _s(body.get("audit_time")) or core.now()
    success = fin_sk = rej_sk = 0
    for i in ids:
        r = store.get(i)
        if not r:
            continue
        st = _int(r.get("status"))
        if st == 1:
            fin_sk += 1
            continue
        if st == 2:
            rej_sk += 1
            continue
        store.update(r["id"], {"status": 1, "status_name": INVOICE_NAME[1], "status_tag_class": INVOICE_TAG[1],
                               "audit_time": invoice_time, "audit_user_id": session.get("id"),
                               "handler_remark": _s(body.get("handler_remark")) or None,
                               "attachment_list": body.get("attachment_list") or r.get("attachment_list") or []})
        c = cstore.get(r.get("customer_contract_id"))
        if c:
            cstore.update(c["id"], {"finish_invoice_total": "%.4f" % (_f(c.get("finish_invoice_total")) + _f(r.get("money")))})
        success += 1
    nums = [fin_sk, rej_sk, 0, 0, 0]
    skips = [{"num": n, "text": t} for n, t in zip(nums, SKIP_TEXTS)]
    parts = ["%d%s" % (n, t) for n, t in zip(nums, SKIP_TEXTS) if n]
    msg = " 批量开票完成 共开票 %d 条，成功开票 %d 条" % (len(ids), success)
    if parts:
        msg += "，已跳过 " + "、".join(parts) + "。"
    return dict(core.ok({"total": len(ids), "success_num": success,
                         "already_finish_num": fin_sk, "already_reject_num": rej_sk, "skip_list": skips}), msg=msg)

def _invoice_re_apply(body, session):
    r = core.Store("invoices").get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if _int(r.get("status")) != 2:
        return core.fail("仅退回状态可重新申请")
    core.Store("invoices").update(r["id"], {"status": 3, "status_name": INVOICE_NAME[3], "status_tag_class": INVOICE_TAG[3]})
    return core.ok()

# ================= 收支记录 / 回款 =================
def _income_list(body, session):
    kw_code = _s(body.get("customer_contract_code"))
    kw_name = _s(body.get("customer_name"))
    kw_user = _s(body.get("create_user_name"))
    rows = []
    for r in _incomes().all():
        r = _dec_income(r)
        c = r.get("customer") or {}
        cc = r.get("customer_contract") or {}
        if not _like(cc.get("code"), kw_code):
            continue
        if not _like(c.get("name"), kw_name):
            continue
        if not _like((r.get("create_user") or {}).get("real_name"), kw_user):
            continue
        if body.get("direction") and _int(r.get("direction")) != _int(body.get("direction")):
            continue
        if body.get("audit_status") != "" and body.get("audit_status") is not None and _int(body.get("audit_status")) != _int(r.get("audit_status")):
            continue
        if not _date_range(r.get("created_at"), b_start(body, "created_at"), b_end(body, "created_at")):
            continue
        rows.append(r)
    return core.paged_ok(_paged(rows, body))

def _income(body, session):
    """合同收款（type=按合同1）：登记收入并回写合同累计收款/欠款/收款进度。"""
    cstore = core.Store("contracts")
    c = cstore.get(body.get("customer_contract_id") or body.get("contract_id"))
    if not c:
        return core.fail("请选择合同")
    money = _f(body.get("money"))
    if money <= 0:
        return core.fail("合同收款金额需大于0")
    if money > 99999999:
        return core.fail("合同收款金额最大不能超过99999999")
    remark = _s(body.get("remark"))
    if not remark:
        return core.fail("请输入备注")
    is_advance = _int(body.get("is_advance"), 2)
    _incomes().add({
        "contract_id": c["id"], "customer_id": c.get("customer_id"), "money": _m8(money),
        "direction": 1, "type": RECONCILIATION_BILL, "is_advance": is_advance, "remark": remark,
        "audit_status": 1, "create_user_id": session.get("id"),
        "enterprise_id": body.get("enterprise_id") or session.get("enterprise_id"),
        "attachments": body.get("attachment_list") or [],
    })
    income = _f(c.get("income_money")) + money
    total = _f(c.get("money"))
    cstore.update(c["id"], {
        "income_money": _m8(income),
        "debt_money": _m8(max(total - income, 0)),
        "income_progress": str(round(income / total * 100)) if total > 0 else "0",
        "advance": _m8(_f(c.get("advance")) + (money if is_advance == 1 else 0)),
    })
    return {"success": True, "msg": "收款成功"}

def _manual_create_income(body, session):
    """非合同发货收款（type=按发货2）。校验文案逐字（09 报告 §8.2）。"""
    items = body.get("items") or body.get("stock_out_bill_items") or []
    if not items:
        ids = body.get("stock_out_bill_item_ids") or []
        items = [{"stock_out_bill_item_id": i, "money": body.get("money")} for i in ids]
    if not items:
        return core.fail("请选择发货明细")
    for it in items:
        if _f(it.get("money")) <= 0:
            return core.fail("发货明细的收款金额必须大于0")
    money = _f(body.get("money"))
    if money <= 0:
        return core.fail("收款金额必须大于0")
    if abs(sum(_f(it.get("money")) for it in items) - money) > 0.005:
        return core.fail("收款金额与发货明细的收款金额不一致")
    cid = body.get("customer_id")
    if not cid:
        for it in items:
            if it.get("customer_id"):
                cid = it["customer_id"]
                break
    _incomes().add({
        "contract_id": None, "customer_id": cid, "money": _m8(money),
        "direction": 1, "type": RECONCILIATION_WARE, "is_advance": 2,
        "remark": _s(body.get("remark")) or None, "audit_status": 1,
        "create_user_id": session.get("id"),
        "enterprise_id": body.get("enterprise_id") or session.get("enterprise_id"),
        "attachments": body.get("attachment_list") or [],
        "income_stock_out_bill_items": items,
    })
    return {"success": True, "msg": "收款成功"}

ROUTES = {
    # 客户
    ("customer", "list"): _customer_list,
    ("customer", "list_by_filter_view"): _customer_list,
    ("customer", "add"): _customer_add,
    ("customer", "edit"): _customer_edit,
    ("customer", "del"): _customer_del,
    ("customer", "batch_del"): _customer_del,
    ("customer", "detail"): _customer_detail,
    # 联系人 / 地址
    ("linkman", "list"): _linkman_list,
    ("linkman", "add"): _linkman_add,
    ("address", "list"): _address_list,
    ("address", "add"): _address_add,
    # 合同订单
    ("customer_contract", "list"): _contract_list,
    ("customer_contract", "list_by_filter_view"): _contract_list,
    ("customer_contract", "add"): _contract_add,
    ("customer_contract", "audit"): _contract_audit,
    ("customer_contract", "batch_audit"): _contract_audit,
    ("customer_contract", "del"): _contract_del,
    # 发票
    ("customer_invoice", "add"): _invoice_add,
    ("customer_invoice", "batch_add"): _invoice_batch_add,
    ("customer_invoice", "list"): _invoice_list,
    ("customer_invoice", "batch_finish"): _invoice_batch_finish,
    ("customer_invoice", "re_apply"): _invoice_re_apply,
    # 收支记录
    ("customer_financier", "list"): _income_list,
    ("customer_financier", "income"): _income,
    ("customer_financier", "manual_create_income"): _manual_create_income,
}
