# -*- coding: utf-8 -*-
r"""B12 外协加工域（依据 deep\21-外协加工体系.md + api_dump\deep_m13\{opb_list,acceptance_list,settle_list,
settle_stats,content_list,content_detail,opb_materials}.json 实测）。

覆盖四个模块：
- outsource_produce_bill：list / list_by_filter_view / detail / add / edit / del / batch_del /
  batch_edit / edit_status(1开始|2撤回|3完成|4取消) / pause / cancel_pause / batch_start|pause|finish|cancel_order /
  edit_priority / status_count / outsource_material_list / edit_materials_used_list /
  goods_selector(固定自产) / stock_in_outsource_produce_bill_selector / get_import_titles
  （download_import_template / import_template_header / web_import 三个动作由 io_xlsx 持有，本模块不得重定义；
    原站无 /outsource_produce_bill/export，保持不注册=404 语义）
- outsource_acceptance：list / detail / add / edit / del / batch_del（验收，登记合格数/不合格数）
- outsource_settle：list / statistics / detail / settle / batch_settle / set_unit_money / batch_set_unit_money
- outsource_acceptance_content：list / detail / add / edit / del（验收内容=检查项模板）

核心业务规则（deep\21 逐字）：
- 状态机与加工单同构平行（PRODUCE_BILL_STATUS 五态 1未开始/2生产中/3已完成/4已取消/5已暂停）。
- 三级单价链：
  ① 商品档案价 goods.outsource_money（仅 来源=外协加工 的商品有；创建外协工单时默认带出，可改）
  ② 工单外协价 outsource_produce_bill.outsource_money（type=1 工单外协验收时默认取此值）
    / 工序外协价 produce_bill_craft.unit_money（type=2 任务外协验收时默认取此值；工序外协不建实体，
      验收记录直接落 produce_bill_id+produce_bill_craft_id，outsource_produce_bill_id=null）
  ③ 验收/结算价 outsource_acceptance.unit_money（验收默认取②，可被 set_unit_money/batch_set_unit_money 覆盖）
  结算金额 settle_money = unit_money × qualified_num（单价×合格数量；实测 10 行全部满足该式，
  含未结算行 → 单价/合格数变动时同步重算）。
- 验收数量 num = 合格数量 + 不合格数量（8 位小数字符串）；合格/不合格不能同时为 0；
  剩余可验收数量：工单型 = num - qualified_num，任务型 = produce_bill_craft_num - valid_num，负数取 0。
- 批量族响应 {success_count, error_count, error_msg} 三段式；批量结算跳过已结算单
  （返回 total / already_finish_num / already_reject_num）。
- 编号：WX + YYMMDD - 序号（实测 WX261015-1 / WX260811-2）。
- 本域无审核码（不走供应采购域 7 值 WAREHOUSE_AUDIT_STATUS；单据生命周期用五态 status + 验收 SETTLE_STATUS）。
"""
import time
from decimal import Decimal, ROUND_HALF_UP

import core

# ---------------- 枚举（app.1ca764f3.js 逐字，deep\21 §0.3） ----------------
STATUS = {1: "未开始", 2: "生产中", 3: "已完成", 4: "已取消", 5: "已暂停"}
STATUS_KEY = {1: "wait", 2: "in_produce", 3: "finish", 4: "cancel", 5: "pause"}
STATUS_COLOR = {1: "#757575", 2: "#fa8c16", 3: "#52c41a", 4: "#f5222d", 5: "#eb2f96"}
TAG_CLASS = {1: "4", 2: "1", 3: "2", 4: "5", 5: "3"}   # 实测仅 未开始="4"
SETTLE_STATUS_NAME = {1: "未结算", 2: "已结算"}
SETTLE_STATUS_KEY = {1: "not_settle", 2: "already_settle"}
SETTLE_TAG = {1: "4", 2: "5"}   # 实测 settle_status_tag_class
OUTSOURCE_TYPE = {1: "工单外协", 2: "任务外协"}
CREATE_MODE_NAME = {1: "直接创建", 2: "合同创建"}
CREATE_MODE_COLOR = {1: "green", 2: "blue"}
MAX_NUM = 99999999
MAX_UNIT_MONEY = 1000000        # 修改单价 max 1e6（ShtEditNum）
Q8 = Decimal("0.00000001")

# 导入表头（deep\21 §9：13 列逐字；download/header/web_import 由 io_xlsx 持有）
IMPORT_TITLES = ["外协工单号", "商品编号", "供应商编号", "供应商名称", "计划数", "是否使用默认BOM", "BOM名称",
                 "交货日期", "优先级", "外协单价", "计划开始", "计划结束", "备注"]
IMPORT_REQUIRED = ["商品编号", "计划数"]

# ---------------- 存取（单例） ----------------
_BILLS = _ACCS = _CONTENTS = None


def _bills():
    global _BILLS
    if _BILLS is None:
        _BILLS = core.Store("outsource_produce_bills")
    return _BILLS


def _accs():
    global _ACCS
    if _ACCS is None:
        _ACCS = core.Store("outsource_acceptances")
    return _ACCS


def _contents():
    global _CONTENTS
    if _CONTENTS is None:
        _CONTENTS = core.Store("outsource_acceptance_contents")
    return _CONTENTS


# ---------------- 基础工具 ----------------
def _dec(v):
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except Exception:
        return Decimal(0)


def _d8(v):
    """8 位小数字符串（原站数值契约；format f 避免 Decimal 科学计数）。"""
    return format(_dec(v).quantize(Q8, rounding=ROUND_HALF_UP), "f")


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _truthy(v):
    return v in (True, 1, "1", "true", "True", "yes")


def _user_brief(uid):
    if uid is None:
        return None
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name"),
                    "type_name": "普通用户", "has_password": False, "claw_fun_type_name": "",
                    "qr_code_url": None}
    return {"id": uid, "real_name": "用户%s" % uid, "type_name": "普通用户",
            "has_password": False, "claw_fun_type_name": "", "qr_code_url": None}


def _goods_brief(gid):
    """列表/详情行内嵌 goods（acceptance_list.json 行内形状）。"""
    if gid is None:
        return None
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(gid):
            return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
                    "standard": g.get("standard"), "source": g.get("source"),
                    "source_name": g.get("source_name"),
                    "category_path_names": g.get("category_path_names"),
                    "produce_type_name": g.get("produce_type_name") or "",
                    "source_tag_class": g.get("source_tag_class") or "",
                    "qr_code_url": g.get("qr_code_url"),
                    "outsource_money": g.get("outsource_money"),
                    "stock_total_num": g.get("stock_total_num") or g.get("sum_stock_num"),
                    "unit": g.get("unit")}
    return None


def _supplier_brief(sid):
    if sid is None:
        return None
    for s in core.Store("suppliers").all():
        if str(s.get("id")) == str(sid):
            return {"id": s.get("id"), "name": s.get("name")}
    return None


def _craft_brief(cid):
    if cid is None:
        return None
    for c in core.Store("produce_bill_crafts").all():
        if str(c.get("id")) == str(cid):
            pub = None
            pid = c.get("pub_craft_id")
            for pc in core.Store("crafts").all():
                if str(pc.get("id")) == str(pid or ""):
                    pub = {"id": pc.get("id"), "name": pc.get("name")}
                    break
            return {"id": c.get("id"), "pub_craft_id": pid,
                    "craft_name": c.get("craft_name") or (pub or {}).get("name"),
                    "num": c.get("num"), "unit_money": c.get("unit_money"),
                    "valid_num": c.get("valid_num") or c.get("total_valid_num"),
                    "supplier_id": c.get("supplier_id"),
                    "supplier_linkman": c.get("supplier_linkman"),
                    "supplier_address": c.get("supplier_address"),
                    "pub_craft": pub}
    return None


def _pbill_brief(bid):
    """加工单迷你（任务外协验收引用 produce_bill 时）。"""
    if bid is None:
        return None
    for b in core.Store("produce_bills").all():
        if str(b.get("id")) == str(bid):
            g = None
            if b.get("goods_id"):
                g = _goods_brief(b.get("goods_id"))
            if g is None:
                g = {"id": b.get("goods_id"), "name": b.get("goods_name"),
                     "code": b.get("goods_code"), "standard": b.get("goods_standard")}
            return {"id": b.get("id"), "code": b.get("code"), "goods_id": b.get("goods_id"),
                    "status_name": STATUS.get(b.get("status"), ""), "goods": g}
    return None


def _next_bill_code(rows):
    """编号 WX+YYMMDD-序号（实测 WX261015-1 / WX260811-2，deep\21 §2.1/§11-5）。"""
    stamp = time.strftime("%y%m%d")
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith("WX" + stamp + "-"):
            try:
                mx = max(mx, int(c[len("WX" + stamp + "-"):]))
            except ValueError:
                pass
    return "WX%s-%d" % (stamp, mx + 1)


def _overdue_of(row):
    """逾期：交货日期 < 今天 且未完成/未取消（实测行 129595：status=1、dl 过期 → is_overdue=true）。"""
    import datetime
    dd = str(row.get("delivery_date") or "")[:10]
    today = core.now("%Y-%m-%d")
    if not dd or dd >= today or row.get("status") in (3, 4):
        return False, None
    try:
        day = (datetime.date.today() - datetime.date(*[int(x) for x in dd.split("-")])).days
    except ValueError:
        day = 0
    return True, day


def _reason(val, empty_msg):
    r = str(val or "").strip()
    if not r:
        return None, core.fail(empty_msg)
    if len(r) > 200:
        return None, core.fail("长度超出限制")
    return r, None


# ---------------- 外协工单：装饰 ----------------
def _goods_of(row):
    """兼容三种来源：自带 goods 快照（实测行）/ goods_id 回查商品库 / 导出扁平字段（io_xlsx·B11 行）。"""
    g = row.get("goods")
    if isinstance(g, dict) and g.get("id"):
        return g
    if row.get("goods_id"):
        gb = _goods_brief(row.get("goods_id"))
        if gb:
            return gb
    if row.get("goods_name") or row.get("goods_code"):
        return {"id": row.get("goods_id"), "name": row.get("goods_name"),
                "code": row.get("goods_code"), "standard": row.get("goods_standard")}
    return None


def _decorate(row):
    row = dict(row)
    st = row.get("status")
    for k, dft in (("num", "0.00000000"), ("qualified_num", "0.00000000"),
                   ("unqualified_num", "0.00000000"), ("stock_in_num", "0.00000000"),
                   ("outsource_money", "0.00000000"), ("labor_cost", "0.00000000"),
                   ("material_cost", "0.00000000"), ("total_cost", "0.00000000"),
                   ("total_production_value", "0.00000000"), ("gross_profit", "0.00000000")):
        row.setdefault(k, dft)
    # io_xlsx 导入行用 plan_start_time/plan_end_time → 对齐原站 start_produce_date/end_produce_date
    if not row.get("start_produce_date") and row.get("plan_start_time"):
        row["start_produce_date"] = row["plan_start_time"]
    if not row.get("end_produce_date") and row.get("plan_end_time"):
        row["end_produce_date"] = row["plan_end_time"]
    od, day = _overdue_of(row)
    row["is_overdue"] = bool(row.get("is_overdue")) or od
    row["overdue_day"] = row.get("overdue_day") if row.get("overdue_day") is not None else day
    row["status_name"] = STATUS.get(st, "")
    row["status_tag_class"] = TAG_CLASS.get(st, "")
    row["status_color"] = STATUS_COLOR.get(st, "")
    row["status_key"] = STATUS_KEY.get(st, "")
    cm = row.get("create_mode") or 1
    row["create_mode_name"] = CREATE_MODE_NAME.get(cm, "")
    row["create_mode_color"] = CREATE_MODE_COLOR.get(cm, "")
    row["goods"] = _goods_of(row)
    row["create_user"] = row.get("create_user") or _user_brief(row.get("create_user_id"))
    row["supplier"] = row.get("supplier") or _supplier_brief(row.get("supplier_id"))
    row["supplier_name"] = (row["supplier"] or {}).get("name") if row["supplier"] else None
    row["attachments"] = row.get("attachments") or []
    row["priority_configure"] = row.get("priority_configure") or None
    row.setdefault("produce_schedule", None)
    row.setdefault("produce_project", None)
    row.setdefault("customer_contract", None)
    row["material_list"] = row.get("material_list") or []
    for k in ("stock_out_bill_num", "stock_in_bill_num", "return_materiel_bill_num"):
        row.setdefault(k, 0)
    for k in ("stock_out_bill_codes", "stock_in_bill_codes", "return_materiel_bill_codes"):
        row.setdefault(k, "")
    # 入库进度 = stock_in_num/num（int 百分比）
    try:
        row["stock_in_percent"] = int(_f(row.get("stock_in_num")) / _f(row.get("num")) * 100) \
            if _f(row.get("num")) else 0
    except Exception:
        row["stock_in_percent"] = 0
    # 验收进度 = qualified_num/num（列表 tooltip：只计入验收合格的数量）
    try:
        row["progress"] = int(_f(row.get("qualified_num")) / _f(row.get("num")) * 100) \
            if _f(row.get("num")) else 0
    except Exception:
        row["progress"] = 0
    row["editAccessible"] = st in (1, 2, 5)
    return row


# ---------------- 外协工单：筛选/分页 ----------------
def _filter_bills(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        if str(r.get("del", 2)) == "1":
            continue
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        st = body.get("status")
        if st not in (None, "", "all"):
            sts = st if isinstance(st, list) else [st]
            if str(r.get("status")) not in [str(x) for x in sts]:
                continue
        if body.get("is_overdue") not in (None, ""):
            od, _ = _overdue_of(r)
            if _truthy(body.get("is_overdue")) != (bool(r.get("is_overdue")) or od):
                continue
        g = r.get("goods") or {}
        gk = s("goods_keyword")
        hay = "%s %s %s" % (g.get("name") or r.get("goods_name") or "",
                            g.get("code") or r.get("goods_code") or "",
                            g.get("standard") or r.get("goods_standard") or "")
        if gk and gk not in hay:
            continue
        for fk, rk in (("goods_name", "goods_name"), ("goods_code", "goods_code"),
                       ("goods_standard", "goods_standard")):
            fv = s(fk)
            if fv and fv not in str((g.get(rk.split("_", 1)[1]) if g else None) or r.get(rk) or ""):
                continue
        sids = body.get("supplier_ids")
        if sids not in (None, "", []):
            want = {str(x) for x in (sids if isinstance(sids, list) else str(sids).split(","))}
            if want and str(r.get("supplier_id")) not in want:
                continue
        if s("supplier_linkman") and s("supplier_linkman") not in str(r.get("supplier_linkman") or ""):
            continue
        if s("num") and _d8(r.get("num")) != _d8(s("num")):
            continue
        if s("outsource_money") and _d8(r.get("outsource_money")) != _d8(s("outsource_money")):
            continue
        if s("pause_reason") and s("pause_reason") not in str(r.get("pause_reason") or ""):
            continue
        if s("cancel_reason") and s("cancel_reason") not in str(r.get("cancel_reason") or ""):
            continue
        if s("priority_configure_id") and str(r.get("priority_configure_id") or "") != s("priority_configure_id"):
            continue
        cc = (r.get("customer_contract") or {}).get("code") if isinstance(r.get("customer_contract"), dict) \
            else r.get("contract_code")
        if s("contract_code") and s("contract_code") not in str(cc or ""):
            continue
        if s("produce_schedule_keyword"):
            ps = r.get("produce_schedule")
            hay2 = "%s%s" % ((ps or {}).get("code") if isinstance(ps, dict) else r.get("produce_schedule_code") or "",
                             (ps or {}).get("name") if isinstance(ps, dict) else r.get("produce_schedule_name") or "")
            if s("produce_schedule_keyword") not in hay2:
                continue
        if s("produce_project_keyword"):
            pj = r.get("produce_project")
            hay3 = "%s%s" % ((pj or {}).get("code") if isinstance(pj, dict) else r.get("project_code") or "",
                             (pj or {}).get("name") if isinstance(pj, dict) else r.get("project_name") or "")
            if s("produce_project_keyword") not in hay3:
                continue
        uids = body.get("create_user_ids")
        if uids not in (None, "", []):
            want = {str(x) for x in (uids if isinstance(uids, list) else str(uids).split(","))}
            if want and str(r.get("create_user_id")) not in want:
                continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        if s("start_produce_date_start") and str(r.get("start_produce_date") or "") < s("start_produce_date_start"):
            continue
        if s("start_produce_date_end") and str(r.get("start_produce_date") or "") > s("start_produce_date_end") + ":59:59":
            continue
        if s("end_produce_date_start") and str(r.get("end_produce_date") or "") < s("end_produce_date_start"):
            continue
        if s("end_produce_date_end") and str(r.get("end_produce_date") or "") > s("end_produce_date_end") + ":59:59":
            continue
        out.append(r)
    return out


def _paged(store, rows, body):
    backup = store.rows
    store.rows = rows
    try:
        return core.paged_ok(store.page(body))
    finally:
        store.rows = backup


def _list(body, session):
    store = _bills()
    rows = _filter_bills(store.all(), body)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate(r) for r in res["data"]["list"]]
    return res


def _status_count(body, session):
    """快捷筛选计数 + 【总计划数】num_sum（deep\21 §9：status_count{wait..}/priority_count/overdue_count/num_sum）。"""
    rows = _filter_bills(_bills().all(), body)
    sc = {k: 0 for k in STATUS}
    pc = {}
    od = notod = 0
    for r in rows:
        if r.get("status") in sc:
            sc[r["status"]] += 1
        if r.get("priority_configure_id") is not None:
            key = str(r["priority_configure_id"])
            pc[key] = pc.get(key, 0) + 1
        isod, _ = _overdue_of(r)
        if bool(r.get("is_overdue")) or isod:
            od += 1
        else:
            notod += 1
    return core.ok({
        "status_count": {STATUS_KEY[k]: v for k, v in sc.items()},
        "priority_count": pc,
        "overdue_count": {"overdue": od, "not_overdue": notod},
        "num_sum": _d8(sum((_dec(r.get("num")) for r in rows), Decimal(0))),
    })


# ---------------- 外协工单：增删改查 ----------------
def _norm_material_list(items, num=None):
    """用料清单：单位用量必填、生产用量>0（校验文案逐字，deep\21 §2.1）。"""
    out = []
    for it in items or []:
        n, pn = _dec(it.get("num")), _dec(it.get("produce_num"))
        if n <= 0:
            return None, "请填写用料清单所有单位用量"
        if pn <= 0:
            return None, "请填写用料清单所有生产用量"
        row = {
            "goods_id": it.get("goods_id"), "num": _d8(n), "produce_num": _d8(pn),
            "stock_in_craft_id": it.get("stock_in_craft_id"), "remark": it.get("remark"),
            "material_stock_out_num": int(_f(it.get("material_stock_out_num"))),
            "material_stock_in_num": int(_f(it.get("material_stock_in_num"))),
            "display_modify_delete": 2,
            "goods": _goods_brief(it.get("goods_id")) or it.get("goods"),
        }
        out.append(row)
    return out, None


def _check_form(num, start, end):
    """计划数/计划时间校验（文案逐字，deep\21 §2.1）。"""
    if num in (None, ""):
        return core.fail("请输入计划数")
    n = _dec(num)
    if n > MAX_NUM:
        return core.fail("计划数最大不能超过99999999")
    if n < Decimal("0.00000001"):
        return core.fail("计划数最小不能低于0.00000001")
    if start and end and str(start) > str(end):
        return core.fail("计划开始不能晚于计划结束")
    return None


def _add(body, session):
    store = _bills()
    goods = body.get("goods") or _goods_brief(body.get("goods_id"))
    if not goods:
        return core.fail("请选择商品")
    err = _check_form(body.get("num"), body.get("start_produce_date"), body.get("end_produce_date"))
    if err:
        return err
    code = str(body.get("code") or "").strip()
    if code:
        if len(code) > 50:
            return core.fail("长度超出限制")
    else:
        code = _next_bill_code(store.all())
    materials, merr = _norm_material_list(body.get("material_list"))
    if merr:
        return core.fail(merr)
    # 外协单价默认带出商品档案值（deep\21 §2.1/§5-1）
    om = body.get("outsource_money")
    if om in (None, ""):
        om = (goods or {}).get("outsource_money") if isinstance(goods, dict) else None
    remark = body.get("remark")
    if remark and len(str(remark)) > 255:
        return core.fail("长度超出限制")
    row = {
        "enterprise_id": session.get("enterprise_id") or 16807,
        "code": code,
        "goods_id": goods.get("id") if isinstance(goods, dict) else body.get("goods_id"),
        "goods": goods,
        "num": _d8(body.get("num")),
        "qualified_num": "0.00000000", "unqualified_num": "0.00000000",
        "outsource_money": _d8(om),
        "supplier_id": body.get("supplier_id"),
        "supplier_linkman": body.get("supplier_linkman"),
        "supplier_address": body.get("supplier_address"),
        "status": 1,
        "create_mode": int(body.get("create_mode") or 1),
        "customer_contract_id": body.get("customer_contract_id"),
        "customer_contract_item_id": body.get("customer_contract_item_id"),
        "produce_schedule_id": body.get("produce_schedule_id"),
        "produce_project_id": body.get("produce_project_id"),
        "priority_configure_id": body.get("priority_configure_id"),
        "delivery_date": body.get("delivery_date"),
        "start_produce_date": body.get("start_produce_date"),
        "end_produce_date": body.get("end_produce_date"),
        "remark": remark,
        "pause_reason": None, "cancel_reason": None,
        "start_time": None, "end_time": None,
        "stock_in_num": "0.00000000",
        "stock_out_bill_num": 0, "stock_out_bill_codes": "",
        "stock_in_bill_num": 0, "stock_in_bill_codes": "",
        "return_materiel_bill_num": 0, "return_materiel_bill_codes": "",
        "labor_cost": "0.00000000", "material_cost": "0.00000000",
        "total_cost": "0.00000000", "total_production_value": "0.00000000",
        "gross_profit": "0.00000000",
        "attachments": body.get("attachments") or [],
        "material_list": materials or [],
        "create_user_id": session.get("id"),
    }
    row = store.add(row)
    return core.ok(_decorate(row))


_EDITABLE = ("code", "num", "outsource_money", "supplier_id", "supplier_linkman", "supplier_address",
             "delivery_date", "priority_configure_id", "start_produce_date", "end_produce_date",
             "produce_schedule_id", "produce_project_id", "customer_contract_id", "remark",
             "attachments", "material_list")


def _edit(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") in (3, 4):
        return core.fail("当前状态不允许修改")
    if "num" in body:
        err = _check_form(body.get("num"), body.get("start_produce_date") or bill.get("start_produce_date"),
                          body.get("end_produce_date") or bill.get("end_produce_date"))
        if err:
            return err
    if str(body.get("start_produce_date") or bill.get("start_produce_date") or "") > \
            str(body.get("end_produce_date") or bill.get("end_produce_date") or "") and \
            body.get("start_produce_date") and body.get("end_produce_date"):
        return core.fail("计划开始不能晚于计划结束")
    materials = None
    if "material_list" in body:
        materials, merr = _norm_material_list(body.get("material_list"))
        if merr:
            return core.fail(merr)
    patch = {}
    for k in _EDITABLE:
        if k not in body:
            continue
        if k in ("num", "outsource_money"):
            patch[k] = _d8(body[k])
        elif k == "material_list":
            patch[k] = materials or []
        elif k == "code":
            c = str(body[k] or "").strip()
            if len(c) > 50:
                return core.fail("长度超出限制")
            patch[k] = c or bill.get("code")
        else:
            patch[k] = body[k]
    if "goods_id" in body or body.get("goods"):
        g = body.get("goods") or _goods_brief(body.get("goods_id"))
        if g:
            patch["goods"] = g
            patch["goods_id"] = g.get("id")
    store.update(bill["id"], patch)
    return core.ok(_decorate(store.get(bill["id"])))


def _del_acceptances_of_bill(bill_id):
    accs = _accs()
    for a in list(accs.all()):
        if str(a.get("outsource_produce_bill_id")) == str(bill_id):
            accs.remove(a["id"])


def _del(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    # 删除外协工单及相关验收数据（删除确认文案含"及相关验收数据"，deep\21 §1.4/§10-2）
    _del_acceptances_of_bill(bill["id"])
    store.remove(bill["id"])
    return core.ok()


def _batch_del(body, session):
    """批量删除 {ids: "1,2"} 逗号串（deep\21 §1.2）。"""
    ids = [x for x in str(body.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择外协工单")
    store = _bills()
    sc = ec = 0
    for i in ids:
        if store.get(i):
            _del_acceptances_of_bill(i)
            store.remove(i)
            sc += 1
        else:
            ec += 1
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "数据不存在"
    return core.ok(data)


def _batch_edit(body, session):
    """批量修改 {ids: 逗号串, 可选字段}（deep\21 §1.5：全部可选，仅提交填写项）。"""
    ids = [x for x in str(body.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择外协工单")
    store = _bills()
    sc = ec = 0
    msgs = []
    for i in ids:
        bill = store.get(i)
        if not bill:
            ec += 1
            msgs.append("数据不存在")
            continue
        patch = {}
        for k in ("num", "outsource_money"):
            if body.get(k) not in (None, ""):
                patch[k] = _d8(body[k])
        if body.get("num") not in (None, ""):
            n = _dec(body.get("num"))
            if n > MAX_NUM or n < Decimal("0.00000001"):
                ec += 1
                msgs.append("计划数填写错误")
                continue
        for k in ("supplier_linkman", "supplier_address", "delivery_date", "priority_configure_id",
                  "start_produce_date", "end_produce_date", "remark", "attachments"):
            if k in body:
                patch[k] = body[k]
        if body.get("supplier_id") is not None:
            patch["supplier_id"] = body.get("supplier_id")
            patch["supplier"] = _supplier_brief(body.get("supplier_id"))
        if patch:
            store.update(i, patch)
        sc += 1
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "；".join(msgs)
    return core.ok(data)


def _edit_priority(body, session):
    """优先级修改（列表优先级列点击弹窗，deep\21 §1.3/§9）。"""
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    store.update(bill["id"], {"priority_configure_id": body.get("priority_configure_id")})
    return core.ok()


# ---------------- 外协工单：状态机 ----------------
def _apply_edit_status(bill, body):
    """type 1开始/2撤回/3完成/4取消(+cancel_reason)。可见条件 deep\21 §1.4（前端条件逐字）：
    开始仅未开始；完成=未开始|生产中；撤回=生产中|已完成；取消≠已取消。"""
    try:
        t = int(body.get("type"))
    except Exception:
        return core.fail("参数错误")
    st = bill.get("status")
    if t == 1:
        if st != 1:
            return core.fail("只有未开始的外协工单可以开始生产")
        bill["status"] = 2
        bill["start_time"] = core.now()
    elif t == 2:
        if st not in (2, 3):
            return core.fail("只有生产中或已完成的外协工单可以撤回")
        if st == 2:      # 与加工单同构：生产中→未开始 / 已完成→生产中（deep\01 定论，目标态后端定）
            bill["status"] = 1
            bill["start_time"] = None
        else:
            bill["status"] = 2
            bill["end_time"] = None
    elif t == 3:
        if st not in (1, 2):
            return core.fail("已取消、已完成或已暂停的外协工单不能完成")
        if st == 1 and not bill.get("start_time"):
            bill["start_time"] = core.now()
        bill["status"] = 3
        bill["end_time"] = core.now()
    elif t == 4:
        if st == 4:
            return core.fail("已经取消的外协工单无法继续处理")
        reason, err = _reason(body.get("cancel_reason"), "请输入取消原因")
        if err:
            return err
        bill["status"] = 4
        bill["cancel_reason"] = reason
    else:
        return core.fail("参数错误")
    return None


def _edit_status(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    err = _apply_edit_status(bill, body)
    if err:
        return err
    store.flush()
    return core.ok()


def _pause(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") != 2:
        return core.fail("只有生产中的外协工单可以暂停")
    reason, err = _reason(body.get("pause_reason"), "请填写暂停原因")
    if err:
        return err
    bill["status"] = 5
    bill["pause_reason"] = reason
    store.flush()
    return core.ok()


def _cancel_pause(body, session):
    store = _bills()
    bill = store.get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    if bill.get("status") != 5:
        return core.fail("只有已暂停的外协工单可以继续生产")
    bill["status"] = 2
    bill["pause_reason"] = None
    store.flush()
    return core.ok()


def _batch_apply(body, kind, reason_key=None):
    ids = body.get("id_array") or []
    if not ids:
        return core.fail("请选择外协工单")
    if reason_key:
        empty_msg = "请填写暂停原因" if reason_key == "pause_reason" else "请输入取消原因"
        _, err = _reason(body.get(reason_key), empty_msg)
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
                err = core.fail("只有生产中的外协工单可以暂停")
            else:
                bill["status"] = 5
                bill["pause_reason"] = str(body.get("pause_reason") or "").strip()
        else:
            payload = {"type": {"start": 1, "finish": 3, "cancel": 4}[kind]}
            if reason_key:
                payload[reason_key] = body.get(reason_key)
            err = _apply_edit_status(bill, payload)
        if err:
            bill.update({k: before[k] for k in ("status", "start_time", "end_time",
                                                "cancel_reason", "pause_reason") if k in before})
            ec += 1
            if err.get("msg") not in msgs:
                msgs.append(err.get("msg"))
        else:
            sc += 1
    store.flush()
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "；".join(msgs)
    return core.ok(data)


def _batch_start(body, session):
    return _batch_apply(body, "start")


def _batch_pause(body, session):
    return _batch_apply(body, "pause", reason_key="pause_reason")


def _batch_finish(body, session):
    return _batch_apply(body, "finish")


def _batch_cancel(body, session):
    return _batch_apply(body, "cancel", reason_key="cancel_reason")


# ---------------- 外协工单：用料 / 选择器 ----------------
def _detail(body, session):
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    return core.ok(_decorate(bill))


def _outsource_material_list(body, session):
    """用料清单 {id}（deep\21 §4.1 行字段）。"""
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    out = []
    for idx, it in enumerate(bill.get("material_list") or []):
        row = dict(it)
        row.setdefault("id", int(bill["id"]) * 100 + idx)
        row.setdefault("outsource_produce_bill_id", bill["id"])
        row.setdefault("num", "0.00000000")
        row.setdefault("produce_num", "0.00000000")
        row.setdefault("material_stock_out_num", 0)
        row.setdefault("material_stock_in_num", 0)
        row.setdefault("display_modify_delete", 2)
        if not row.get("goods"):
            row["goods"] = _goods_brief(row.get("goods_id"))
        out.append(row)
    return core.ok(out)


def _edit_materials_used_list(body, session):
    """修改用料行 {id(物料行id), num, produce_num}（deep\21 §4.1/§11-2：载荷推断）。"""
    if body.get("outsource_produce_bill_id"):
        bill = _bills().get(body.get("outsource_produce_bill_id"))
    else:
        bill = None
        for b in _bills().all():
            if any(str(m.get("id")) == str(body.get("id")) for m in b.get("material_list") or []):
                bill = b
                break
    if not bill:
        return core.fail("数据不存在")
    n, pn = _dec(body.get("num")), _dec(body.get("produce_num"))
    if n <= 0:
        return core.fail("请填写用料清单所有单位用量")
    if pn <= 0:
        return core.fail("请填写用料清单所有生产用量")
    hit = False
    for m in bill.get("material_list") or []:
        if str(m.get("id")) == str(body.get("id")):
            m["num"], m["produce_num"] = _d8(n), _d8(pn)
            hit = True
    if not hit:
        return core.fail("数据不存在")
    _bills().flush()
    return core.ok()


def _goods_selector(body, session):
    """创建页商品选择：fixed-source 自产（GOODS_SOURCE.SELF_PRODUCE v1，deep\21 §2.1）。"""
    rows = [g for g in core.Store("goods").all()
            if str(g.get("del", 2)) != "1" and str(g.get("source") or "") == "1"]
    kw = str(body.get("keyword") or "").strip()
    if kw:
        rows = [g for g in rows if kw in "%s%s%s" % (g.get("name") or "", g.get("code") or "",
                                                     g.get("standard") or "")]
    out = []
    for g in rows:
        out.append({
            "id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
            "standard": g.get("standard"), "unit": g.get("unit"),
            "source": g.get("source"), "source_name": g.get("source_name") or "自产",
            "outsource_money": g.get("outsource_money"),
            "stock_total_num": g.get("stock_total_num") or g.get("sum_stock_num") or "0",
            "qr_code_url": g.get("qr_code_url"),
        })
    store = core.Store("goods")
    return _paged(store, out, body)


def _stock_in_selector(body, session):
    """待入库/验收选单 selector（deep\21 §6.2 弹窗列：外协工单号/合同编号/商品/验收进度/供应商三件套）。"""
    rows = [r for r in _filter_bills(_bills().all(), body) if r.get("status") != 4]
    out = []
    for r in rows:
        d = _decorate(r)
        out.append({
            "id": d["id"], "code": d["code"],
            "contract_code": (d.get("customer_contract") or {}).get("code") if d.get("customer_contract") else None,
            "goods": d.get("goods"), "num": d.get("num"),
            "qualified_num": d.get("qualified_num"),
            "stock_in_percent": d.get("stock_in_percent"),
            "supplier": d.get("supplier"), "supplier_name": d.get("supplier_name"),
            "supplier_linkman": d.get("supplier_linkman"),
            "supplier_address": d.get("supplier_address"),
            "status_name": d.get("status_name"),
        })
    store = _bills()
    return _paged(store, out, body)


def _get_import_titles(body, session):
    """导入表头（CUSTOM_IMPORT v21；模板字节下载与 web_import 由 io_xlsx 持有）。"""
    return {"success": True,
            "data": {"titles": list(IMPORT_TITLES), "required_titles": list(IMPORT_REQUIRED),
                     "auto_titles": []},
            "enterprise_config_update_time": core.now()}


# ---------------- 外协验收（outsource_acceptance） ----------------
def _waste_names():
    return {str(w.get("id")): w.get("name") for w in core.Store("wastes").all()}


def _remaining_of(a_type, bill, craft):
    """剩余可验收数量（deep\21 §6.2：工单型 = num-qualified_num；任务型 = produce_bill_craft_num-valid_num；负取 0）。"""
    try:
        if a_type == 1:
            return max(Decimal(0), _dec(bill.get("num")) - _dec(bill.get("qualified_num")))
        cn = _dec(craft.get("num") or craft.get("produce_bill_craft_num"))
        cv = _dec(craft.get("valid_num") if craft.get("valid_num") is not None
                  else craft.get("total_valid_num"))
        return max(Decimal(0), cn - cv)
    except Exception:
        return Decimal(0)


def _supplier_trio(a_type, bill, craft):
    """验收/结算行的供应商三件套：工单型取外协工单，任务型取任务（craft）行（deep\21 §7.2）。"""
    src = bill if a_type == 1 else craft
    if src is None:
        return None, None, None, None
    sid = src.get("supplier_id")
    sup = _supplier_brief(sid) or (src.get("supplier") if isinstance(src.get("supplier"), dict) else None)
    name = (sup or {}).get("name")
    if not name and a_type == 2:
        name = None
    return sup, name, src.get("supplier_linkman"), src.get("supplier_address")


def _decorate_acc(row):
    row = dict(row)
    t = row.get("type") or 1
    bill = _bills().get(row.get("outsource_produce_bill_id")) if t == 1 else None
    craft = None
    pbill = None
    if t == 2:
        pbill = _pbill_brief(row.get("produce_bill_id"))
        cb = _craft_brief(row.get("produce_bill_craft_id"))
        craft = cb
    sup, sup_name, sup_lman, sup_addr = _supplier_trio(t, bill, craft)
    row.setdefault("num", "0.00000000")
    row.setdefault("qualified_num", "0.00000000")
    row.setdefault("unqualified_num", "0.00000000")
    row.setdefault("unit_money", "0.00000000")
    row.setdefault("settle_money", "0.00000000")
    row["type_name"] = OUTSOURCE_TYPE.get(t, "")
    row["settle_status_name"] = SETTLE_STATUS_NAME.get(row.get("settle_status") or 1, "")
    row["settle_status_tag_class"] = SETTLE_TAG.get(row.get("settle_status") or 1, "")
    row["settle_status_key"] = SETTLE_STATUS_KEY.get(row.get("settle_status") or 1, "")
    row["create_user"] = row.get("create_user") or _user_brief(row.get("create_user_id"))
    row["settle_user"] = _user_brief(row.get("settle_user_id")) if row.get("settle_user_id") else None
    # 单据编码列：ORDER 取外协工单号，TASK 取加工单号（deep\21 §6.1）
    if t == 1:
        ob = _bills().get(row.get("outsource_produce_bill_id"))
        row["code"] = (ob or {}).get("code")
        row["outsource_produce_bill"] = _obill_mini(ob)
    else:
        pb = _pbill_brief(row.get("produce_bill_id"))
        row["code"] = (pb or {}).get("code")
        row["produce_bill"] = pb
        row["produce_bill_craft"] = _craft_acc_mini(craft)
        row["outsource_produce_bill"] = None
    row["acceptance_content"] = _content_mini(row.get("acceptance_content_id"))
    row["receipt"] = (row["acceptance_content"] or {}).get("title")
    row["supplier"] = sup
    row["supplier_name"] = sup_name
    row["supplier_names"] = sup_name
    row["supplier_linkman"] = row.get("supplier_linkman") or sup_lman
    row["supplier_address"] = row.get("supplier_address") or sup_addr
    # 结算列表列（deep\21 §7.1）：order_code / craft_name（工单型显示"无"）
    if t == 1:
        row["order_code"] = row.get("code")
        row["craft_name"] = "无"
        row["pub_craft_name"] = None
    else:
        row["order_code"] = (row.get("produce_bill") or {}).get("code")
        cn = ((craft or {}).get("craft_name") if craft else None) or \
             (((craft or {}).get("pub_craft") or {}).get("name") if craft else None)
        row["craft_name"] = cn
        row["pub_craft_name"] = cn
    g = None
    if t == 1 and bill:
        g = _goods_of(bill)
    elif t == 2:
        g = ((row.get("produce_bill") or {}).get("goods"))
    row["goods"] = g
    row["attachments"] = row.get("attachments") or []
    row["waste_list"] = row.get("waste_list") or []
    row["waste_item_names"] = ",".join(
        ((w.get("waste_item") or {}).get("name") or "") for w in row["waste_list"])
    row["stock_in_bill_num"] = row.get("stock_in_bill_num") or 0
    row["settle_money"] = _d8(_dec(row.get("unit_money")) * _dec(row.get("qualified_num")))
    return row


def _obill_mini(bill):
    if not bill:
        return None
    return {"id": bill.get("id"), "code": bill.get("code"),
            "supplier_id": bill.get("supplier_id"),
            "supplier_linkman": bill.get("supplier_linkman"),
            "supplier_address": bill.get("supplier_address"),
            "goods_id": bill.get("goods_id"),
            "status_name": STATUS.get(bill.get("status"), ""),
            "status_tag_class": TAG_CLASS.get(bill.get("status"), ""),
            "stock_in_percent": 0, "create_mode_name": "", "create_mode_color": "",
            "goods": _goods_of(bill), "supplier": _supplier_brief(bill.get("supplier_id"))}


def _craft_acc_mini(craft):
    """验收行内 produce_bill_craft（acceptance_list.json 形状）。"""
    if not craft:
        return None
    pub = craft.get("pub_craft") or {}
    return {"id": craft.get("id"), "pub_craft_id": craft.get("pub_craft_id"),
            "supplier_id": craft.get("supplier_id"),
            "supplier_linkman": craft.get("supplier_linkman"),
            "supplier_address": craft.get("supplier_address"),
            "status_name": "", "status_tag_class": "", "status_color": "",
            "price_mode_name": "", "wait_produce_num": "0",
            "pub_craft": {"id": pub.get("id"), "name": pub.get("name")} if pub else None,
            "supplier": _supplier_brief(craft.get("supplier_id"))}


def _filter_accs(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        if str(r.get("del", 2)) == "1":
            continue
        if body.get("type") not in (None, "", "null") and str(r.get("type")) != str(body.get("type")):
            continue
        if body.get("settle_status") not in (None, "", "null") and \
                str(r.get("settle_status")) != str(body.get("settle_status")):
            continue
        if s("produce_bill_code"):
            d = _decorate_acc(r)
            if s("produce_bill_code") not in str(d.get("order_code") or ""):
                continue
        if s("pub_craft_name"):
            d = _decorate_acc(r)
            pcn = ((d.get("produce_bill_craft") or {}).get("pub_craft") or {}).get("name", "")
            if s("pub_craft_name") not in "%s%s" % (pcn or "", d.get("craft_name") or ""):
                continue
        if s("goods_keyword") or s("supplier_name") or s("supplier_linkman") or s("create_user_name"):
            d = _decorate_acc(r)
            g = d.get("goods") or {}
            if s("goods_keyword") and s("goods_keyword") not in "%s%s%s" % (
                    g.get("name") or "", g.get("code") or "", g.get("standard") or ""):
                continue
            if s("supplier_name") and s("supplier_name") not in str(d.get("supplier_name") or ""):
                continue
            if s("supplier_linkman") and s("supplier_linkman") not in str(d.get("supplier_linkman") or ""):
                continue
            if s("create_user_name") and s("create_user_name") not in str(
                    (d.get("create_user") or {}).get("real_name") or ""):
                continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out


def _acc_list(body, session):
    store = _accs()
    rows = _filter_accs(store.all(), body)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate_acc(r) for r in res["data"]["list"]]
    return res


def _acc_detail(body, session):
    row = _accs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    d = _decorate_acc(row)
    d["log_list"] = row.get("log_list") or []
    if d.get("acceptance_content_id"):
        c = _contents().get(d["acceptance_content_id"])
        if c:
            d["content_items"] = _content_items_of(c)
    return core.ok(d)


def _acc_targets(body):
    """解析验收对象：type=1 外协工单 / type=2 加工单+任务（deep\21 §6.2）。"""
    try:
        t = int(body.get("type") or 1)
    except (TypeError, ValueError):
        return None, core.fail("参数错误")
    if t not in OUTSOURCE_TYPE:
        return None, core.fail("参数错误")
    if t == 1:
        bill = _bills().get(body.get("outsource_produce_bill_id"))
        if not bill:
            return None, core.fail("数据不存在")
        return (t, bill, None), None
    craft = None
    for c in core.Store("produce_bill_crafts").all():
        if str(c.get("id")) == str(body.get("produce_bill_craft_id") or ""):
            craft = c
            break
    if not craft:
        return None, core.fail("数据不存在")
    pbill = None
    for b in core.Store("produce_bills").all():
        if str(b.get("id")) == str(body.get("produce_bill_id") or craft.get("produce_bill_id") or ""):
            pbill = b
            break
    return (t, pbill, craft), None


def _default_unit_money(t, bill, craft):
    """验收默认单价（deep\21 §5-3 实测：type=1 ← bill.outsource_money（70=70）；type=2 ← craft.unit_money（80=80））。"""
    if t == 1:
        return _dec((bill or {}).get("outsource_money"))
    return _dec((craft or {}).get("unit_money"))


def _acc_num(body):
    q, u = body.get("qualified_num"), body.get("unqualified_num")
    if q in (None, ""):
        return None, None, None, core.fail("请输入合格数量")
    qd, ud = _dec(q), _dec(u or 0)
    if qd < 0 or ud < 0 or qd > MAX_NUM or ud > MAX_NUM:
        return None, None, None, core.fail("数量填写错误")
    if qd == 0 and ud == 0:
        return None, None, None, core.fail("合格数量和不合格数量不能同时为0")
    return qd, ud, qd + ud, None


def _acc_waste_list(body):
    names = _waste_names()
    out = []
    for it in body.get("waste_array") or body.get("report_waste_array") or []:
        n = _dec(it.get("num"))
        if n <= 0:
            return None, core.fail("不良品项的值必须大于0")
        out.append({"waste_item_id": it.get("waste_item_id"),
                    "waste_item": {"id": it.get("waste_item_id"),
                                   "name": names.get(str(it.get("waste_item_id")), "")},
                    "num": _d8(n)})
    return out, None


def _bill_apply_acceptance(bill, dq, du, sign=1):
    """工单型验收联动累计外协工单 qualified/unqualified（deep\21 §1.3 验收进度列）。"""
    bill["qualified_num"] = _d8(_dec(bill.get("qualified_num")) + dq * sign)
    bill["unqualified_num"] = _d8(_dec(bill.get("unqualified_num")) + du * sign)
    _bills().flush()


def _acc_add(body, session):
    targets, err = _acc_targets(body)
    if err:
        return err
    t, bill, craft = targets
    qd, ud, nd, err = _acc_num(body)
    if err:
        return err
    wl, err = _acc_waste_list(body)
    if err:
        return err
    # 剩余可验收数量限制（公式 deep\21 §6.2；超限文案未见实测，推断）
    remaining = _remaining_of(t, bill, craft)
    if nd > remaining:
        return core.fail("验收数量不能超过剩余可验收数量")
    if body.get("acceptance_content_id") and not _contents().get(body.get("acceptance_content_id")):
        return core.fail("验收内容不存在")
    # 单价链：body 显式单价 > 默认链（type=1 ← 工单外协单价；type=2 ← 工序外协单价）
    um = body.get("unit_money")
    unit = _dec(um) if um not in (None, "") else _default_unit_money(t, bill, craft)
    row = {
        "enterprise_id": session.get("enterprise_id") or 16807,
        "type": t,
        "outsource_produce_bill_id": bill["id"] if t == 1 else None,
        "produce_bill_id": craft.get("produce_bill_id") if t == 2 else None,
        "produce_bill_craft_id": craft["id"] if t == 2 else None,
        "num": _d8(nd), "qualified_num": _d8(qd), "unqualified_num": _d8(ud),
        "unit_money": _d8(unit),
        "settle_money": _d8(unit * qd),
        "settle_status": 1, "settle_time": None, "settle_user_id": None,
        "settle_remark": None, "log_list": [],
        "acceptance_content_id": body.get("acceptance_content_id"),
        "content_items": body.get("content_items") or {},
        "waste_list": wl or [],
        "remark": body.get("remark"),
        "stock_in_bill_num": 0,
        "attachments": body.get("attachments") or [],
        "create_user_id": session.get("id"),
    }
    row = _accs().add(row)
    if t == 1:
        _bill_apply_acceptance(bill, qd, ud)
    return core.ok(_decorate_acc(row))


def _acc_edit(body, session):
    store = _accs()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    if row.get("settle_status") == 2:
        return core.fail("已结算的外协验收不能修改")
    patch = {}
    if "qualified_num" in body or "unqualified_num" in body:
        qd, ud, nd, err = _acc_num({"qualified_num": body.get("qualified_num", row.get("qualified_num")),
                                    "unqualified_num": body.get("unqualified_num", row.get("unqualified_num"))})
        if err:
            return err
        old_q, old_u = _dec(row.get("qualified_num")), _dec(row.get("unqualified_num"))
        patch = {"qualified_num": _d8(qd), "unqualified_num": _d8(ud), "num": _d8(nd)}
        if row.get("type") == 1:
            bill = _bills().get(row.get("outsource_produce_bill_id"))
            if bill:
                bill["qualified_num"] = _d8(_dec(bill.get("qualified_num")) - old_q + qd)
                bill["unqualified_num"] = _d8(_dec(bill.get("unqualified_num")) - old_u + ud)
                _bills().flush()
    if "unit_money" in body:
        patch["unit_money"] = _d8(body.get("unit_money"))
    if "waste_array" in body or "report_waste_array" in body:
        wl, err = _acc_waste_list(body)
        if err:
            return err
        patch["waste_list"] = wl or []
    for k in ("remark", "acceptance_content_id", "content_items", "attachments"):
        if k in body:
            patch[k] = body[k]
    um = patch.get("unit_money", row.get("unit_money"))
    q2 = patch.get("qualified_num", row.get("qualified_num"))
    patch["settle_money"] = _d8(_dec(um) * _dec(q2))
    row = store.update(body.get("id"), patch)
    return core.ok(_decorate_acc(row))


def _acc_del(body, session):
    store = _accs()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    _acc_revert(row)
    store.remove(row["id"])
    return core.ok()


def _acc_batch_del(body, session):
    ids = [x for x in str(body.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择外协验收")
    store = _accs()
    sc = ec = 0
    for i in ids:
        row = store.get(i)
        if not row:
            ec += 1
            continue
        _acc_revert(row)
        store.remove(i)
        sc += 1
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "数据不存在"
    return core.ok(data)


def _acc_revert(row):
    """删除验收时回退工单累计（保持验收进度列一致，demo 级语义）。"""
    if row.get("type") == 1:
        bill = _bills().get(row.get("outsource_produce_bill_id"))
        if bill:
            _bill_apply_acceptance(bill, -_dec(row.get("qualified_num")), -_dec(row.get("unqualified_num")))


# ---------------- 外协结算（outsource_settle） ----------------
def _filter_settles(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    hs = body.get("has_settle")
    out = []
    for r in rows:
        if str(r.get("del", 2)) == "1":
            continue
        # Tab：全部/已结算/未结算（param has_settle；兼容 1|2 与 already/not 键，deep\21 §7.1）
        if hs not in (None, "", "null", "0", "all"):
            want = {"2": "2", "1": "1", "already_settle": "2", "not_settle": "1"}.get(str(hs))
            if want and str(r.get("settle_status")) != want:
                continue
        if body.get("type") not in (None, "", "null") and str(r.get("type")) != str(body.get("type")):
            continue
        d = _decorate_acc(r)
        if s("supplier_name") and s("supplier_name") not in str(d.get("supplier_name") or ""):
            continue
        if s("supplier_linkman") and s("supplier_linkman") not in str(d.get("supplier_linkman") or ""):
            continue
        if s("produce_bill_code") and s("produce_bill_code") not in str(d.get("order_code") or ""):
            continue
        if s("pub_craft_name") and s("pub_craft_name") not in str(d.get("craft_name") or ""):
            continue
        if s("goods_keyword"):
            g = d.get("goods") or {}
            if s("goods_keyword") not in "%s%s%s" % (g.get("name") or "", g.get("code") or "",
                                                     g.get("standard") or ""):
                continue
        if s("create_user_name") and s("create_user_name") not in str((d.get("create_user") or {}).get("real_name") or ""):
            continue
        if s("settle_user_name") and s("settle_user_name") not in str((d.get("settle_user") or {}).get("real_name") or ""):
            continue
        out.append(r)
    return out


def _settle_list(body, session):
    store = _accs()
    rows = _filter_settles(store.all(), body)
    sf = str(body.get("sortField") or "")
    if sf in ("created_at", "settle_time"):
        rows = sorted(rows, key=lambda r: str(r.get(sf) or ""), reverse=str(body.get("sortOrder") or "desc") != "asc")
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate_acc(r) for r in res["data"]["list"]]
    return res


def _settle_statistics(body, session):
    """统计卡（deep\21 §7.1 实测 settle_stats.json：全部字符串值）。"""
    rows = [r for r in _accs().all() if str(r.get("del", 2)) != "1"]
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 30 * 86400))
    unsettle = [r for r in rows if r.get("settle_status") != 2]
    month = [r for r in rows if r.get("settle_status") == 2 and str(r.get("settle_time") or "") >= cutoff]

    def total(rs, f):
        return _d8(sum((_dec(r.get(f)) for r in rs), Decimal(0)))

    return core.ok({
        "unsettle_count": str(len(unsettle)),
        "unsettle_money": total(unsettle, "settle_money"),
        "month_settle_count": str(len(month)),
        "month_settle_money": total(month, "settle_money"),
        "total_num": {"num": _d8(sum((_dec(r.get("num")) for r in rows), Decimal(0))),
                      "qualified_num": _d8(sum((_dec(r.get("qualified_num")) for r in rows), Decimal(0))),
                      "unqualified_num": _d8(sum((_dec(r.get("unqualified_num")) for r in rows), Decimal(0)))},
    })


def _settle_detail(body, session):
    row = _accs().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    d = _decorate_acc(row)
    d["log_list"] = row.get("log_list") or []
    return core.ok(d)


def _settle_one(row, settle_status, settle_remark, attachments, session):
    """结算落库：状态/时间/结算人 + 结算金额=单价×合格数量重算（实测 10 行全部满足该式，含未结算行）。"""
    row["settle_status"] = int(settle_status or 2)
    row["settle_time"] = core.now()
    row["settle_user_id"] = session.get("id")
    if settle_remark is not None:
        row["settle_remark"] = settle_remark
    if attachments is not None:
        row["attachments"] = attachments
    row["settle_money"] = _d8(_dec(row.get("unit_money")) * _dec(row.get("qualified_num")))
    row.setdefault("log_list", []).append({
        "settle_user": _user_brief(session.get("id")),
        "action": "结算", "remark": settle_remark or "", "settle_time": row["settle_time"],
    })


def _settle(body, session):
    """单条结算 {id, settle_status, settle_remark, attachments}（deep\21 §7.2）。"""
    store = _accs()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    if body.get("settle_status") in (None, ""):
        return core.fail("请选择状态")
    try:
        ss = int(body.get("settle_status"))
    except (TypeError, ValueError):
        return core.fail("请选择状态")
    if ss not in SETTLE_STATUS_NAME:
        return core.fail("请选择状态")
    if row.get("settle_status") == 2 and ss == 2:
        return core.fail("该单据已结算")
    _settle_one(row, ss, body.get("settle_remark"), body.get("attachments"), session)
    store.flush()
    return core.ok(_decorate_acc(row))


def _batch_settle(body, session):
    """批量结算 {ids: 逗号串, settle_status:2, settle_remark, attachments}；
    跳过已结算（通知字段 total/already_finish_num/already_reject_num，deep\21 §7.2 逐字）。"""
    ids = [x for x in str(body.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择要结算的外协单据")
    store = _accs()
    finish = reject = 0
    for i in ids:
        row = store.get(i)
        if not row:
            reject += 1
            continue
        if row.get("settle_status") == 2:   # 已跳过N条结算完成的单据
            reject += 1
            continue
        _settle_one(row, body.get("settle_status") or 2, body.get("settle_remark"),
                    body.get("attachments"), session)
        finish += 1
    store.flush()
    return core.ok({"total": len(ids), "already_finish_num": finish, "already_reject_num": reject})


def _set_unit_money(body, session):
    """修改单价 {id, unit_money}（0~1e6；改后重算 settle_money 保持 单价×合格数 不变式）。"""
    store = _accs()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    if body.get("unit_money") in (None, ""):
        return core.fail("请填写单价")
    um = _dec(body.get("unit_money"))
    if um < 0 or um > MAX_UNIT_MONEY:
        return core.fail("单价填写错误")
    row["unit_money"] = _d8(um)
    row["settle_money"] = _d8(um * _dec(row.get("qualified_num")))
    store.flush()
    return core.ok(_decorate_acc(row))


def _batch_set_unit_money(body, session):
    ids = [x for x in str(body.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择外协单据")
    if body.get("unit_money") in (None, ""):
        return core.fail("请填写单价")
    um = _dec(body.get("unit_money"))
    if um < 0 or um > MAX_UNIT_MONEY:
        return core.fail("单价填写错误")
    store = _accs()
    sc = ec = 0
    for i in ids:
        row = store.get(i)
        if not row:
            ec += 1
            continue
        row["unit_money"] = _d8(um)
        row["settle_money"] = _d8(um * _dec(row.get("qualified_num")))
        sc += 1
    store.flush()
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "数据不存在"
    return core.ok(data)


# ---------------- 验收内容（outsource_acceptance_content） ----------------
def _content_items_of(row):
    """检查项 → content_detail.json 实测形状（module=OUTSOURCE_ACCEPTANCE_CONTENT_ITEM_{id}）。"""
    items = row.get("content_items")
    if not isinstance(items, list):
        return []
    out = []
    for idx, it in enumerate(items):
        cid = it.get("id")
        out.append({
            "id": cid or (int(row.get("id") or 0) * 1000 + idx + 1),
            "module_type": 1,
            "module": "OUTSOURCE_ACCEPTANCE_CONTENT_ITEM_%s" % row.get("id"),
            "name": it.get("name"), "label": it.get("label"),
            "must_fill": it.get("must_fill") or 2,
            "type": it.get("type") or "text_ipt",
            "data": None, "del": 2, "reason": None,
            "describe": it.get("describe"),
            "order_number": it.get("order_number") or idx + 1,
            "searchable": None, "association_object_field_id": None,
            "auto_fill_rule": None, "input_type": "1",
        })
    return out


def _content_mini(cid):
    if cid is None:
        return None
    c = _contents().get(cid)
    if not c:
        return None
    return {"id": c.get("id"), "title": c.get("title"), "remark": c.get("remark"),
            "content_item_count": len(c.get("content_items") or [])}


def _content_list(body, session):
    store = _contents()
    kw = str(body.get("title") or body.get("keyword") or "").strip()
    rows = [r for r in store.all() if str(r.get("del", 2)) != "1" and (not kw or kw in str(r.get("title") or ""))]
    out = []
    for r in rows:
        d = dict(r)
        d["content_item_count"] = len(r.get("content_items") or [])
        d["create_user"] = _user_brief(r.get("create_user_id"))
        d.pop("content_items", None)
        out.append(d)
    return core.paged_ok(_paged_raw(store, out, body))


def _paged_raw(store, rows, body):
    backup = store.rows
    store.rows = rows
    try:
        return store.page(body)
    finally:
        store.rows = backup


def _content_detail(body, session):
    row = _contents().get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    d = dict(row)
    d["content_items"] = _content_items_of(row)
    d["content_item_count"] = len(d["content_items"])
    d["create_user"] = _user_brief(row.get("create_user_id"))
    return core.ok(d)


def _norm_content_items(items):
    out = []
    for idx, it in enumerate(items or []):
        label = str(it.get("label") or "").strip()
        if not label:
            return None, "请填写检查项名称"
        out.append({"name": it.get("name") or label, "label": label,
                    "type": it.get("type") or "text_ipt",
                    "must_fill": it.get("must_fill") or 2,
                    "describe": it.get("describe"),
                    "order_number": it.get("order_number") or idx + 1})
    return out, None


def _content_add(body, session):
    store = _contents()
    title = str(body.get("title") or "").strip()
    if not title:
        return core.fail("请填写标题")
    items, err = _norm_content_items(body.get("content_items"))
    if err:
        return core.fail(err)
    if not items:   # 启用状态下至少保留一个检测项（deep\21 §6.3）
        return core.fail("启用状态下至少保留一个检测项")
    row = store.add({"enterprise_id": session.get("enterprise_id") or 16807,
                     "title": title, "remark": body.get("remark"),
                     "content_items": items, "create_user_id": session.get("id")})
    return core.ok(_content_mini(row["id"]))


def _content_edit(body, session):
    store = _contents()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    patch = {}
    if "title" in body:
        if not str(body.get("title") or "").strip():
            return core.fail("请填写标题")
        patch["title"] = str(body.get("title")).strip()
    if "remark" in body:
        patch["remark"] = body.get("remark")
    if "content_items" in body:
        items, err = _norm_content_items(body.get("content_items"))
        if err:
            return core.fail(err)
        if not items:
            return core.fail("启用状态下至少保留一个检测项")
        patch["content_items"] = items
    store.update(row["id"], patch)
    return core.ok()


def _content_del(body, session):
    store = _contents()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    store.remove(row["id"])
    return core.ok()


# ---------------- 种子（文件缺失/为空时生成；实测样例优先） ----------------
def _seed():
    if not _bills().rows:
        _seed_bills()
    if not _contents().rows:
        _seed_contents()
    if not _accs().rows:
        _seed_acceptances()


def _seed_bills():
    """外协工单种子：10 行实测（opb_list.json 原字段截取）+ 18 行按实测形状合成（_seed=1）。"""
    import random
    random.seed(2100)
    bills = []
    # ---- 实测 10 行（api_dump/deep_m13/opb_list.json；goods 快照取库里实时对象，字段瘦身）----
    real = [
        (138780, "WX261015-1", 1, "1", 0, 0, "1", 1, 136873, "刘涛/1337561129", "江苏省 玉山城北广福村",
         None, "2026-10-15 14:03:55"),
        (129595, "WX260910-1", 1, "1", 0, 0, "0", 1, 136874, None, None, "2026-09-25", "2026-09-10 11:00:00"),
        (120419, "WX260811-2", 2, "500", 1, 1, "70", 2, None, None, None, None, "2026-08-11 10:00:00"),
        (120418, "WX260811-1", 1, "100", 0, 0, "0", 1, 136873, None, None, None, "2026-08-11 09:30:00"),
        (114760, "WX260722-3", 2, "50", 0, 0, "500", 1, 136874, None, None, "2026-07-31", "2026-07-22 15:00:00"),
        (114756, "WX260722-2", 2, "5", 0, 0, "50", 1, 136875, None, None, "2026-07-30", "2026-07-22 11:00:00"),
        (114755, "WX260722-1", 3, "5", 4, 1, "0.2", 1, 136873, None, None, "2026-07-30", "2026-07-22 10:00:00"),
        (108564, "WX260702-1", 3, "100", 0, 0, "100", 1, None, None, None, None, "2026-07-02 09:00:00"),
        (107884, "20260629003-2", 1, "10", 0, 0, "0", 1, None, None, None, None, "2026-06-29 16:00:00"),
        (107883, "20260629003-1", 1, "10", 0, 0, "0", 1, None, None, None, None, "2026-06-29 15:00:00"),
    ]
    real_goods = {138780: 9728493, 129595: None, 120419: 6333414, 120418: None, 114760: None,
                  114756: None, 114755: None, 108564: 8336256, 107884: None, 107883: None}
    goods_store = core.Store("goods")
    gmap = {}
    for g in goods_store.all():
        gmap[str(g.get("id"))] = g
    sup_store = core.Store("suppliers")
    smap = {str(s.get("id")): s for s in sup_store.all()}

    def goods_snap(gid):
        g = gmap.get(str(gid))
        if not g:
            return None
        return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
                "standard": g.get("standard"), "source": g.get("source"),
                "source_name": g.get("source_name"), "unit": g.get("unit"),
                "qr_code_url": g.get("qr_code_url"),
                "stock_total_num": g.get("stock_total_num") or g.get("sum_stock_num")}

    for (rid, code, st, num, q, u, om, cm, sid, lman, addr, dl, cat) in real:
        gid = real_goods.get(rid)
        row = {
            "id": rid, "_seed": 1, "enterprise_id": 16807, "code": code,
            "goods_id": gid, "goods": goods_snap(gid) if gid else None,
            "num": _d8(num), "qualified_num": _d8(q), "unqualified_num": _d8(u),
            "outsource_money": _d8(om), "status": st, "create_mode": cm,
            "supplier_id": sid, "supplier": {"id": sid, "name": (smap.get(str(sid)) or {}).get("name")} if sid else None,
            "supplier_linkman": lman, "supplier_address": addr,
            "delivery_date": dl, "priority_configure_id": None,
            "start_produce_date": None, "end_produce_date": None,
            "remark": None, "pause_reason": None, "cancel_reason": None,
            "start_time": "2026-08-12 08:00:00" if st in (2, 3, 5) else None,
            "end_time": "2026-09-01 17:00:00" if st == 3 else None,
            "stock_in_num": _d8(1 if rid in (114755, 108564) else 0),
            "stock_out_bill_num": 2 if rid == 108564 else 0,
            "stock_out_bill_codes": "CK20260702001,CK20260702002" if rid == 108564 else "",
            "stock_in_bill_num": 1 if rid in (114755, 108564) else 0,
            "stock_in_bill_codes": "RK20260801001" if rid in (114755, 108564) else "",
            "return_materiel_bill_num": 1 if rid == 108564 else 0,
            "return_materiel_bill_codes": "TL20260710001" if rid == 108564 else "",
            "labor_cost": "0.00000000", "material_cost": "0.00000000", "total_cost": "0.00000000",
            "total_production_value": "0.00000000", "gross_profit": "0.00000000",
            "attachments": [], "material_list": [], "create_user_id": 63097,
            "del": 2, "created_at": cat, "updated_at": cat,
        }
        bills.append(row)
    # ---- 合成 18 行：真实商品（外协来源优先）+ 真实供应商 + 实测编号节奏 ----
    out_goods = [g for g in goods_store.all() if g.get("source") == 2]      # 来源=外协加工（16 个）
    other_goods = [g for g in goods_store.all() if g.get("source") == 1][:20]
    sups = [s for s in sup_store.all()][:12]
    plan_days = ["260818", "260820", "260825", "260828", "260901", "260903", "260908",
                 "260912", "260915", "260918", "260922", "260925", "260928", "261005",
                 "261008", "261012", "261018", "261025"]
    seq_by_day = {}
    lman_samples = ["张强/13805161234", "李工/15950512345", "王海涛/13770314567", None]
    addr_samples = ["江苏省 昆山市周市镇长江北路", "浙江省 嘉兴市南湖区工业园", "安徽省 马鞍山市博望区", None]
    for i, day in enumerate(plan_days):
        seq_by_day[day] = seq_by_day.get(day, 0) + 1
        g = (out_goods + other_goods)[i % len(out_goods + other_goods)] if (out_goods + other_goods) else None
        sid = (sups[i % len(sups)] or {}).get("id") if sups else None
        st = [1, 2, 3, 1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 1, 2, 3, 1, 2][i]
        num = random.choice([10, 20, 50, 100, 200, 500])
        om = g.get("outsource_money") if g.get("source") == 2 else random.choice(
            ["0", "0.5", "1", "10", "20", "50", "70", "100"])
        created = "20%s-%s-%s %02d:00:00" % (day[:2], day[2:4], day[4:], 9 + i % 8)
        qn = random.choice([0, 0, num // 2, num]) if st == 3 else 0
        row = {
            "id": 138800 + i, "_seed": 1, "enterprise_id": 16807,
            "code": "WX%s-%d" % (day, seq_by_day[day]),
            "goods_id": g.get("id"), "goods": goods_snap(g.get("id")),
            "num": _d8(num), "qualified_num": _d8(qn),
            "unqualified_num": _d8(random.choice([0, 1, 2]) if qn else 0),
            "outsource_money": _d8(om), "status": st, "create_mode": 1 if i % 4 else 2,
            "supplier_id": sid, "supplier": {"id": sid, "name": (smap.get(str(sid)) or {}).get("name")} if sid else None,
            "supplier_linkman": random.choice(lman_samples),
            "supplier_address": random.choice(addr_samples),
            "delivery_date": "20%s-%s-%s" % (day[:2], day[2:4], day[4:]) if i % 3 else None,
            "priority_configure_id": None,
            "start_produce_date": "20%s-%s-%s 08:00:00" % (day[:2], day[2:4], day[4:]) if i % 2 else None,
            "end_produce_date": "20%s-%s-%s 18:00:00" % (day[:2], day[2:4], day[4:]) if i % 5 else None,
            "remark": "合成样例（deep_m21 域）" if i % 6 == 0 else None,
            "pause_reason": "等待供应商产能" if st == 5 else None,
            "cancel_reason": "合同取消" if st == 4 else None,
            "start_time": "20%s-%s-%s 08:30:00" % (day[:2], day[2:4], day[4:]) if st in (2, 3, 5) else None,
            "end_time": "20%s-%s-%s 17:30:00" % (day[:2], day[2:4], day[4:]) if st == 3 else None,
            "stock_in_num": _d8(qn),
            "stock_out_bill_num": 1 if i % 7 == 0 else 0,
            "stock_out_bill_codes": "CK20%s001" % day if i % 7 == 0 else "",
            "stock_in_bill_num": 1 if qn else 0,
            "stock_in_bill_codes": "RK20%s001" % day if qn else "",
            "return_materiel_bill_num": 0, "return_materiel_bill_codes": "",
            "labor_cost": "0.00000000", "material_cost": "0.00000000", "total_cost": "0.00000000",
            "total_production_value": "0.00000000", "gross_profit": "0.00000000",
            "attachments": [],
            "material_list": [{"goods_id": g.get("id"), "goods": goods_snap(g.get("id")),
                               "num": "1.00000000", "produce_num": _d8(num),
                               "stock_in_craft_id": None, "remark": None,
                               "material_stock_out_num": 0, "material_stock_in_num": 0,
                               "display_modify_delete": 2}] if i % 3 == 0 else [],
            "create_user_id": 63097,
            "del": 2, "created_at": created, "updated_at": created,
        }
        bills.append(row)
    bills.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    store = _bills()
    store.rows = bills
    store.flush()


def _seed_contents():
    """验收内容 4 条（content_list/content_detail.json 实测：光滑度/精度验证/运动性能测试/外观与结构检查）。"""
    rows = [
        {"id": 6365, "_seed": 1, "enterprise_id": 16807, "title": "光滑度", "remark": None,
         "create_user_id": 63097, "del": 2,
         "created_at": "2026-07-02 09:59:49", "updated_at": "2026-07-02 09:59:49",
         "content_items": [{"name": "guanghuadu", "label": "表面光滑度", "type": "text_ipt",
                            "must_fill": 2, "describe": "目视及手感检查加工面光滑度，无毛刺划痕。",
                            "order_number": 1}]},
        {"id": 3755, "_seed": 1, "enterprise_id": 16807, "title": "精度验证",
         "remark": "定位精度：使用激光干涉仪或高精度光栅尺，测量实际位移与标称值的偏差（如±0.005mm/m）。\n"
                   "重复定位精度：多次往返同一位置，用千分表记录偏差（典型值≤±0.003mm）。\n"
                   "平行度/直线度：用百分表检测移动平台在全程内的横向摆动（如≤0.01mm/100mm）。",
         "create_user_id": 63097, "del": 2,
         "created_at": "2026-01-04 15:19:31", "updated_at": "2026-01-04 15:19:31",
         "content_items": [
             {"name": "dingweijingdu", "label": "定位精度", "type": "text_ipt", "must_fill": 2,
              "describe": "使用激光干涉仪或高精度光栅尺，测量实际位移与标称值的偏差（如±0.005mm/m）。",
              "order_number": 1},
             {"name": "zfdwjd", "label": "重复定位精度", "type": "text_ipt", "must_fill": 2,
              "describe": "多次往返同一位置，用千分表记录偏差（典型值≤±0.003mm）。", "order_number": 2},
             {"name": "pxd_zxd", "label": "平行度-直线度", "type": "text_ipt", "must_fill": 2,
              "describe": "用百分表检测移动平台在全程内的横向摆动（如≤0.01mm/100mm）。", "order_number": 3}]},
        {"id": 3754, "_seed": 1, "enterprise_id": 16807, "title": "运动性能测试",
         "remark": "交叉滚柱引导式手动位移台的验收需从机械性能、精度、功能、材质及文档等方面进行全面检查，"
                   "确保其符合技术协议或行业标准",
         "create_user_id": 63097, "del": 2,
         "created_at": "2026-01-04 15:19:31", "updated_at": "2026-01-04 15:19:31",
         "content_items": [
             {"name": "jixie_xingneng", "label": "机械性能", "type": "text_ipt", "must_fill": 2,
              "describe": "全行程手动往复运动，检查滑移阻力是否均匀、有无爬行异响。", "order_number": 1},
             {"name": "gongneng_jiancha", "label": "功能检查", "type": "text_ipt", "must_fill": 2,
              "describe": "检查限位、锁紧、刻度等功能部件是否有效。", "order_number": 2},
             {"name": "caizhi_wendang", "label": "材质及文档", "type": "text_ipt", "must_fill": 2,
              "describe": "核对材质证明与出厂文档（合格证、检验报告）是否齐全。", "order_number": 3}]},
        {"id": 3753, "_seed": 1, "enterprise_id": 16807, "title": "外观与结构检查", "remark": None,
         "create_user_id": 63097, "del": 2,
         "created_at": "2026-01-04 15:19:30", "updated_at": "2026-01-04 15:19:30",
         "content_items": [
             {"name": "waiguan", "label": "外观", "type": "text_ipt", "must_fill": 2,
              "describe": "表面处理（阳极氧化/发黑）均匀，无锈蚀、磕碰伤。", "order_number": 1},
             {"name": "jiegou_chicun", "label": "结构尺寸", "type": "text_ipt", "must_fill": 2,
              "describe": "按图纸核对关键尺寸与形位公差。", "order_number": 2},
             {"name": "biaoji", "label": "标识铭牌", "type": "text_ipt", "must_fill": 2,
              "describe": "铭牌、编号刻印清晰并与单据一致。", "order_number": 3}]},
    ]
    store = _contents()
    store.rows = rows
    store.flush()


def _seed_acceptances():
    """验收种子：6 行实测（acceptance_list/settle_list.json：120419/114755/68984 型）+ 8 行合成；
    工单型合计数回写外协工单 qualified_num/unqualified_num。"""
    def base(aid, t, q, u, um, ss, cat, bill_id=None, pb_id=None, craft_id=None, remark=None,
             settle_time=None, wid=63097, content_id=None, stime=None):
        row = {
            "id": aid, "_seed": 1, "enterprise_id": 16807, "type": t,
            "outsource_produce_bill_id": bill_id if t == 1 else None,
            "produce_bill_id": pb_id if t == 2 else None,
            "produce_bill_craft_id": craft_id if t == 2 else None,
            "num": _d8(_dec(q) + _dec(u)), "qualified_num": _d8(q), "unqualified_num": _d8(u),
            "unit_money": _d8(um), "settle_status": ss, "settle_time": settle_time,
            "settle_user_id": wid if ss == 2 else None, "settle_remark": None,
            "acceptance_content_id": content_id, "content_items": {},
            "waste_list": [], "remark": remark, "stock_in_bill_num": 0,
            "attachments": [], "create_user_id": wid,
            "del": 2, "created_at": cat, "updated_at": settle_time or cat,
        }
        # 结算金额恒等式：settle_money = unit_money × qualified_num（实测 10/10 行）
        row["settle_money"] = _d8(_dec(um) * _dec(q))
        row["log_list"] = ([{"settle_user": _user_brief(wid), "action": "结算",
                             "remark": "", "settle_time": settle_time}] if ss == 2 else [])
        return row

    rows = [
        # 实测样式（ids 沿用真实 id：76469/68984/65056/61915 型）
        base(76469, 1, 0, 1, 70, 1, "2026-10-15 14:05:31", bill_id=120419, content_id=3755),
        base(68984, 2, 9, 1, 0.5, 2, "2026-09-02 09:54:41", pb_id=8835783, craft_id=880000016,
             settle_time="2026-09-02 09:54:58", content_id=3755),
        base(65057, 2, 1, 0, 80, 2, "2026-08-20 10:00:00", pb_id=8835783, craft_id=880000015,
             settle_time="2026-08-20 10:30:00"),
        base(65056, 1, 1, 0, 10, 2, "2026-08-20 09:00:00", bill_id=114755,
             settle_time="2026-08-21 09:00:00", remark="首批验收"),
        base(61915, 1, 4, 0, 0.2, 2, "2026-08-12 14:00:00", bill_id=114756,
             settle_time="2026-08-13 09:00:00"),
        base(53178, 1, 198, 2, 0, 1, "2026-07-15 11:00:00", bill_id=108564),
        # 合成（挂合成工单 1388xx / 实测任务）
        base(76470, 1, 30, 2, 70, 1, "2026-09-26 15:00:00", bill_id=138803, content_id=3754),
        base(76471, 1, 50, 0, 70, 2, "2026-09-28 16:00:00", bill_id=138804,
             settle_time="2026-09-29 09:00:00"),
        base(76472, 1, 8, 2, 10, 1, "2026-10-09 10:00:00", bill_id=138808, content_id=6365),
        base(76473, 2, 20, 1, 20, 1, "2026-10-11 14:00:00", pb_id=8835783, craft_id=880000014),
        base(76474, 1, 100, 5, 1, 2, "2026-10-13 09:30:00", bill_id=138811,
             settle_time="2026-10-14 08:30:00"),
        base(76475, 1, 12, 1, 0.5, 1, "2026-10-19 17:00:00", bill_id=138815, content_id=3753),
        base(76476, 2, 5, 0, 0.2, 2, "2026-10-22 10:00:00", pb_id=8835783, craft_id=880000013,
             settle_time="2026-10-23 09:00:00"),
        base(76477, 1, 40, 3, 100, 1, "2026-10-26 11:00:00", bill_id=138817),
    ]
    store = _accs()
    store.rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    store.flush()
    # 工单型验收合计 → 回写外协工单 qualified/unqualified（保持验收进度列一致；覆盖种子占位值）
    bstore = _bills()
    agg = {}
    for r in rows:
        if r.get("type") == 1 and r.get("outsource_produce_bill_id"):
            k = str(r["outsource_produce_bill_id"])
            agg.setdefault(k, [Decimal(0), Decimal(0)])
            agg[k][0] += _dec(r.get("qualified_num"))
            agg[k][1] += _dec(r.get("unqualified_num"))
    for b in bstore.all():
        k = str(b.get("id"))
        if k in agg:
            b["qualified_num"] = _d8(agg[k][0])
            b["unqualified_num"] = _d8(agg[k][1])
    bstore.flush()


_seed()

ROUTES = {
    # ---- 外协工单（deep\21 §9；web_import/import_template_header/download_import_template 由 io_xlsx 持有）----
    ("outsource_produce_bill", "list"): _list,
    ("outsource_produce_bill", "list_by_filter_view"): _list,
    ("outsource_produce_bill", "detail"): _detail,
    ("outsource_produce_bill", "add"): _add,
    ("outsource_produce_bill", "edit"): _edit,
    ("outsource_produce_bill", "del"): _del,
    ("outsource_produce_bill", "batch_del"): _batch_del,
    ("outsource_produce_bill", "batch_edit"): _batch_edit,
    ("outsource_produce_bill", "edit_status"): _edit_status,
    ("outsource_produce_bill", "pause"): _pause,
    ("outsource_produce_bill", "cancel_pause"): _cancel_pause,
    ("outsource_produce_bill", "batch_start_order"): _batch_start,
    ("outsource_produce_bill", "batch_pause_order"): _batch_pause,
    ("outsource_produce_bill", "batch_finish_order"): _batch_finish,
    ("outsource_produce_bill", "batch_cancel_order"): _batch_cancel,
    ("outsource_produce_bill", "edit_priority"): _edit_priority,
    ("outsource_produce_bill", "status_count"): _status_count,
    ("outsource_produce_bill", "outsource_material_list"): _outsource_material_list,
    ("outsource_produce_bill", "edit_materials_used_list"): _edit_materials_used_list,
    ("outsource_produce_bill", "goods_selector"): _goods_selector,
    ("outsource_produce_bill", "stock_in_outsource_produce_bill_selector"): _stock_in_selector,
    ("outsource_produce_bill", "get_import_titles"): _get_import_titles,
    # ---- 外协验收（deep\21 §6/§9）----
    ("outsource_acceptance", "list"): _acc_list,
    ("outsource_acceptance", "detail"): _acc_detail,
    ("outsource_acceptance", "add"): _acc_add,
    ("outsource_acceptance", "edit"): _acc_edit,
    ("outsource_acceptance", "del"): _acc_del,
    ("outsource_acceptance", "batch_del"): _acc_batch_del,
    # ---- 外协结算（deep\21 §7/§9）----
    ("outsource_settle", "list"): _settle_list,
    ("outsource_settle", "statistics"): _settle_statistics,
    ("outsource_settle", "detail"): _settle_detail,
    ("outsource_settle", "settle"): _settle,
    ("outsource_settle", "batch_settle"): _batch_settle,
    ("outsource_settle", "set_unit_money"): _set_unit_money,
    ("outsource_settle", "batch_set_unit_money"): _batch_set_unit_money,
    # ---- 验收内容（deep\21 §6.3/§9）----
    ("outsource_acceptance_content", "list"): _content_list,
    ("outsource_acceptance_content", "detail"): _content_detail,
    ("outsource_acceptance_content", "add"): _content_add,
    ("outsource_acceptance_content", "edit"): _content_edit,
    ("outsource_acceptance_content", "del"): _content_del,
}
