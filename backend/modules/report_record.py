# -*- coding: utf-8 -*-
"""报工记录域：module=report_work_record（列表/详情/结算族/工资单价/刷新绩效/统计/删除）。
依据：deep\\18-报工与工时工资.md
  §1   报工记录页（module=report_work_record，list/list_by_filter_view/status_count/detail）
  §4.3 工资公式（计件=良品数×单价；计时=时长秒/3600×单价；is_format_decimal=1 按 decimal_num=0 位【截断】，
        实测 19×0.2=3.8→3、98×2.3=225.4→225，非四舍五入）
  §4.4 结算（edit_settle_status/batch_edit_settle_status {ids,settle_status,settle_remark≤255}；
        已结算的不再结算/不重算；settle_user/settle_time/settle_remark 写回记录）
  §1.3 行内 修改工资单价 edit_craft_unit_money（4 位小数、max 1000000，改后工资联动重算）
       刷新绩效 batch_calculate_valid_money {ids}（已结算不重算）
  §2.4 report_statistics（工作台今日三数，probes/_m10_rwr_stats.json 同构）
样例：_gen_tmp\\kgd_reverse\\probes\\_m10_rwr_list.json / _m10_rwr_p2.json（784 条真实列表，行结构 73 键）
      / _m10_rwr_stats.json；基础 13-生产管理其他页面.md §1.6（筛选字段/统计块）。
数据：report_work_records（produce.py 报工动作写入，勿改其种子）+ report_work_settles（本模块自建
      结算态样例，合成行加 "_seed":1；两库合并对外呈现，结算写回记录所在库）。
导出/打印族（export/web_export/export_attach_module/*template_print）暂不实现（server 自然 fail 404）。
"""
import json
from decimal import Decimal, ROUND_DOWN

import core

# ---------------- 枚举（deep\18 §7 枚举逐字索引 + probes 实测 tag_class） ----------------
TYPE_NAME = {1: "生产报工", 2: "零活报工", 3: "返工报工"}
TYPE_KEY = {1: "produce", 2: "odd_work", 3: "rework"}
TYPE_TAG = {1: "3", 3: "4"}          # 实测；2 零活无实测样本，用 "1"（推断）
PRICE_MODE = {1: ("计件", "orange", "3"), 2: ("计时", "blue", "1")}   # (name,color,tag_class) 实测
SETTLE = {1: ("未结算", "3"), 2: ("已结算", "2")}   # 1 实测；2 的 tag_class 无实测（784 条全未结算），"2" 为推断
SETTLE_KEY = {1: "not_settle", 2: "already_settle"}
QUALITY_KEY = {1: "wait_quality", 2: "finish_quality", 3: "not_quality"}  # 键名取自 salary_report/status_count（deep\18 §4.5a）
BILL_STATUS = {1: "未开始", 2: "生产中", 3: "已完成", 4: "已取消", 5: "已暂停"}
BILL_STATUS_KEY = {1: "wait", 2: "in_produce", 3: "finish", 4: "cancel", 5: "pause"}
CRAFT_STATUS = {1: "未开始", 2: "进行中", 3: "已完成", 4: "已暂停"}
CRAFT_STATUS_KEY = {1: "wait_produce", 2: "in_produce", 3: "finish", 4: "pause"}

# 工资公式快照（deep\18 §4.2b probes/_m10_formula_config.json 逐字）
FORMULA_CONTENT = ("case(${任务.计价方式}==\"计件\",${报工.良品数}*${报工.工资单价};\n"
                   "${任务.计价方式}==\"计时\",${报工.报工时长}/3600*${报工.工资单价})")
WAGE_FORMULA = {"formula_content": FORMULA_CONTENT, "is_format_decimal": 1, "decimal_num": 0}

# ---------------- 存取（单例） ----------------
_RECORDS = _SETTLES = None


def _records():
    """活动库：produce.py 的 report_work / report_work_batch 写入。"""
    global _RECORDS
    if _RECORDS is None:
        _RECORDS = core.Store("report_work_records")
    return _RECORDS


def _settles():
    """本模块自建样例库（含结算态），行带 "_seed":1。"""
    global _SETTLES
    if _SETTLES is None:
        _SETTLES = core.Store("report_work_settles")
    return _SETTLES


def _all_rows():
    """两库合并，按报工时间倒序（原站列表默认新记录在前）。"""
    rows = [dict(r) for r in _records().all()] + [dict(r) for r in _settles().all()]
    rows.sort(key=lambda r: str(r.get("report_time") or r.get("created_at") or ""), reverse=True)
    return rows


def _find(rid):
    """按 id 在两库定位，返回 (store, row) 或 (None, None)。"""
    for store in (_records(), _settles()):
        r = store.get(rid)
        if r:
            return store, r
    return None, None


# ---------------- 数值工具 ----------------
def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _num(x, w=8):
    try:
        return ("%." + str(w) + "f") % float(x or 0)
    except Exception:
        return ("%.8f" % 0)


def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name") or str(uid)}
    return {"id": uid, "real_name": "用户%s" % uid}


def _formula_of(rec):
    """记录的公式快照（performance_pay_config_json 可能是 dict 或 JSON 字符串）。"""
    cfg = rec.get("performance_pay_config_json")
    if isinstance(cfg, dict):
        f = cfg.get("formula_config")
        if isinstance(f, dict):
            return f
    elif isinstance(cfg, str) and cfg.strip():
        try:
            f = (json.loads(cfg) or {}).get("formula_config")
            if isinstance(f, dict):
                return f
        except Exception:
            pass
    return WAGE_FORMULA


def _wage(price_mode, valid_num, unit_money, working_minutes, formula=None):
    """工资公式（deep\18 §4.3，实测为十进制精确计算后【截断】，非四舍五入）：
    计件 = 良品数 × 工资单价；计时 = 时长(分×60)/3600 × 单价(元/小时)；
    is_format_decimal=1 时按 decimal_num 位截断（本实例 0 位），输出 8 位小数字符串。"""
    formula = formula or WAGE_FORMULA
    try:
        dn = int(formula.get("decimal_num") or 0)
    except Exception:
        dn = 0
    if str(formula.get("is_format_decimal", "1")) in ("0", "False", "false", "", None):
        dn = 8
    if str(price_mode) == "2":
        base = (Decimal(str(_f(working_minutes))) * 60 / Decimal(3600)) * Decimal(str(_f(unit_money)))
    else:
        base = Decimal(str(_f(valid_num))) * Decimal(str(_f(unit_money)))
    q = Decimal(1).scaleb(-dn)
    val = base.quantize(q, rounding=ROUND_DOWN)
    return format(val.quantize(Decimal(1).scaleb(-8)), "f")   # "225.00000000"（dump 同款 8 位小数字符串）


# ---------------- 关联对象（列表装饰，字段对齐 probes/_m10_rwr_list.json 行结构） ----------------
def _bill_ref(b, hay=False):
    if not b:
        return None
    g = b.get("goods") or {}
    ref = {
        "id": b.get("id"), "code": b.get("code"), "status": b.get("status"),
        "status_name": BILL_STATUS.get(b.get("status") or 1, ""),
        "goods_id": b.get("goods_id") or g.get("id"),
        "goods": {"id": g.get("id") or b.get("goods_id"),
                  "name": g.get("name") or b.get("goods_name"),
                  "code": g.get("code") or b.get("goods_code"),
                  "standard": g.get("standard") if g else b.get("goods_standard")},
        "num": b.get("num"),
    }
    if hay:  # 商品/客户检索用草堆
        return "%s %s %s %s" % (ref["goods"]["name"] or "", ref["goods"]["code"] or "",
                                ref["goods"]["standard"] or "",
                                (b.get("customer_contract") or {}).get("code") or b.get("contract_code") or "")
    return ref


def _bill_of(rec):
    """先查 produce_bills 种子库（B11 真实 431 单），无则用记录内嵌对象（真实报工样例自带）。"""
    if rec.get("produce_bill_id"):
        b = core.Store("produce_bills").get(rec.get("produce_bill_id"))
        if b:
            return b
    return rec.get("produce_bill") or None


def _craft_ref(c):
    if not c:
        return None
    st = c.get("status")
    pm = c.get("price_mode") or 1
    name = c.get("craft_name") or c.get("name")
    return {
        "id": c.get("id"), "produce_bill_id": c.get("produce_bill_id"),
        "status": st, "status_name": CRAFT_STATUS.get(st, ""),
        "price_mode": pm, "price_mode_name": PRICE_MODE.get(pm, ("", "", ""))[0],
        "num": c.get("num"), "produce_bill_craft_num": c.get("produce_bill_craft_num") or c.get("num"),
        "unit_money": c.get("unit_money") or "0.00000000",
        "craft_name": name, "display_craft_name": c.get("display_craft_name") or name,
        "total_valid_num": c.get("total_valid_num") or "0.00000000",
        "unit": c.get("unit"),
        "workshop_id": c.get("workshop_id"),
    }


def _craft_of(rec):
    if rec.get("produce_bill_craft_id"):
        c = core.Store("produce_bill_crafts").get(rec.get("produce_bill_craft_id"))
        if c:
            return c
    return rec.get("produce_bill_craft") or None


def _fmt_hm(dt):
    s = str(dt or "")
    return s[:16] if s else None


def _quality_tag(rec):
    """质检名/tag 实测规则：status=1→待质检(3)；status=2 且 quality_type=3→不需质检(4)，否则已质检(5)。"""
    qs = rec.get("quality_status")
    if str(qs) == "1":
        return "待质检", "3"
    if str(qs) == "3" or str(rec.get("quality_type") or "") == "3":
        return "不需质检", "4"
    return "已质检", "5"


def _decorate(rec):
    """补齐 probes/_m10_rwr_list.json 的行结构（73 键）与冗余名。"""
    d = dict(rec)
    t = d.get("type") or 1
    pm = d.get("price_mode") or 1
    ss = d.get("settle_status") or 1
    bill = _bill_of(d)
    craft = _craft_of(d)
    # ---- 报工核心 ----
    d.setdefault("report_num", _num(_f(d.get("valid_num")) + _f(d.get("waste_num"))))
    d.setdefault("rework_num", "0.00000000")
    d.setdefault("scrap_num", "0.00000000")
    d.setdefault("craft_unit_money", "0.00000000")
    d.setdefault("valid_money", "0.00000000")
    d.setdefault("unit_id", None)
    d.setdefault("standard_working_seconds", 0)
    d.setdefault("working_minutes", 0)
    try:
        d["working_minutes"] = int(_f(d.get("working_minutes")))
    except Exception:
        d["working_minutes"] = 0
    d.setdefault("report_time", d.get("created_at"))
    d["type"] = int(t)
    d["type_name"] = TYPE_NAME.get(int(t), "")
    d["type_tag_class"] = TYPE_TAG.get(int(t), "")
    d["price_mode"] = int(pm)
    d["price_mode_name"], d["price_mode_color"], d["price_mode_tag_class"] = PRICE_MODE.get(int(pm), ("", "", ""))
    d["settle_status"] = int(ss)
    d["settle_status_name"] = SETTLE.get(int(ss), ("", ""))[0]
    d["settle_status_tag_class"] = SETTLE.get(int(ss), ("", ""))[1]
    d["settle_user_id"] = d.get("settle_user_id")
    d["settle_user"] = d.get("settle_user") or (_user(d.get("settle_user_id")) if d.get("settle_user_id") else None)
    d["settle_time"] = d.get("settle_time")
    d["settle_remark"] = d.get("settle_remark")
    d["quality_status_name"], d["quality_status_tag_class"] = _quality_tag(d)
    d["craft_name"] = d.get("craft_name") or (craft or {}).get("craft_name") or ""
    d.setdefault("display_craft_name", d["craft_name"])
    d.setdefault("create_user_id", d.get("report_user_id"))
    d.setdefault("report_user_id", d.get("create_user_id"))
    d["report_user"] = d.get("report_user") or _user(d.get("report_user_id"))
    d["create_user"] = d.get("create_user") or _user(d.get("create_user_id"))
    d["last_quality_user"] = d.get("last_quality_user") or (_user(d.get("last_quality_user_id")) if d.get("last_quality_user_id") else None)
    d.setdefault("last_quality_user_id", None)
    d.setdefault("last_quality_time", None)
    d.setdefault("quality_remark", None)
    d.setdefault("remark", None)
    d.setdefault("order_number", None)
    d.setdefault("delivery_date", None)
    d.setdefault("traceability_codes", None)
    d.setdefault("traceability_code_count", None)
    d.setdefault("ai_quality_analysis_result", None)
    d.setdefault("rework_id", None)
    d.setdefault("need_quality", 2)
    d.setdefault("quality_type", 3)
    d.setdefault("device_id", None)
    d.setdefault("machine_tool_id", None)
    d.setdefault("fieldValueList", [])
    d.setdefault("attachment_list", [])
    d.setdefault("quality_attachment_list", [])
    d.setdefault("device", None)
    d.setdefault("machine_tool", None)
    d.setdefault("standard_efficiency", None)
    d.setdefault("practical_efficiency", None)
    d.setdefault("compliance_rate", None)
    d["report_time_format"] = _fmt_hm(d.get("report_time"))
    d["start_time_format"] = _fmt_hm(d.get("start_time"))
    d["end_time_format"] = _fmt_hm(d.get("end_time"))
    # ---- 不良品项（补齐 dump 同构字段；waste_item_sum=数量合计，空为 ""） ----
    eid = d.get("enterprise_id")
    rwl = []
    for it in d.get("report_waste_list") or []:
        wi = it.get("waste_item") or {}
        rwl.append({
            "id": it.get("id"), "enterprise_id": it.get("enterprise_id", eid),
            "report_work_record_id": it.get("report_work_record_id", d.get("id")),
            "waste_item_id": it.get("waste_item_id") or wi.get("id"),
            "num": _num(it.get("num")),
            "waste_item": {"id": wi.get("id") or it.get("waste_item_id"),
                           "enterprise_id": wi.get("enterprise_id", eid),
                           "name": wi.get("name"), "code": wi.get("code") or wi.get("name")},
        })
    d["report_waste_list"] = rwl
    d["waste_item_sum"] = _num(sum(_f(i.get("num")) for i in rwl)) if rwl else ""
    # ---- 绩效快照（dump 为 JSON 字符串） ----
    cfg = d.get("performance_pay_config_json")
    if isinstance(cfg, dict):
        d["performance_pay_config_json"] = json.dumps(cfg, ensure_ascii=False)
    elif not cfg:
        d["performance_pay_config_json"] = json.dumps(
            {"performance_pay": False, "formula_config": WAGE_FORMULA,
             "unit_money": d.get("craft_unit_money") or "0.00000000", "standard_working_seconds": 0},
            ensure_ascii=False)
    # ---- 报工单位（零活取记录 unit，生产/返工取工序 unit，deep\18 §1.1/§2.5） ----
    unit = d.get("unit") if int(t) == 2 else (craft or {}).get("unit") or d.get("unit")
    d["unit"] = unit
    d["unit_name"] = (unit or {}).get("name") if isinstance(unit, dict) else None
    # ---- 关联对象 ----
    d["produce_bill"] = _bill_ref(bill)
    d["produce_bill_craft"] = _craft_ref(craft)
    pc = d["produce_bill_craft"] or {}
    if int(t) != 2:
        d["pub_craft"] = {"id": (craft or {}).get("pub_craft_id"), "name": d.get("craft_name"),
                          "price_mode": d.get("price_mode"), "price_mode_name": d["price_mode_name"],
                          "unit": pc.get("unit")}
    else:
        d["pub_craft"] = None
    d.setdefault("produce_bill_id", (bill or {}).get("id") if bill else None)
    d.setdefault("produce_bill_craft_id", pc.get("id"))
    return d


# ---------------- 筛选（字段名以 deep\18 §1.2 + 基础 13 §1.6 为准） ----------------
def _vals(body, *keys):
    out = []
    for k in keys:
        v = body.get(k)
        if v is None:
            continue
        vs = v if isinstance(v, list) else [v]
        out.extend(str(x) for x in vs if str(x).strip() != "")
    return out


def _s(body, k):
    return str(body.get(k) or "").strip()


def _match_range(body, field, val):
    lo, hi = body.get(field + "_start"), body.get(field + "_end")
    if lo in (None, "") and hi in (None, ""):
        return True
    try:
        v = _f(val)
    except Exception:
        return True
    if lo not in (None, "") and v < _f(lo):
        return False
    if hi not in (None, "") and v > _f(hi):
        return False
    return True


def _match_date(body, field, val):
    lo, hi = body.get(field + "_start"), body.get(field + "_end")
    v = str(val or "")
    if lo not in (None, "") and v and v < str(lo):
        return False
    if hi not in (None, "") and v:
        hi = str(hi)
        if len(hi) == 10:   # 仅日期粒度的结束日含当天全天（与 produce.py 同款口径）
            hi += " 23:59:59"
        if v > hi:
            return False
    return True


def _filter(rows, body):
    """对字段直查用原始行；加工单/商品/工序维度经关联解析（page() 前完成）。"""
    f_type = _vals(body, "type")
    f_settle = _vals(body, "settle_status")
    f_pm = _vals(body, "price_mode", "price_mode_list")
    f_quality = _vals(body, "quality_status")
    f_bill_st = _vals(body, "produce_bill_status")
    f_craft_ids = _vals(body, "craft_ids", "produce_bill_craft_ids")
    f_report_user = _vals(body, "report_user_ids", "report_user_id")
    f_create_user = _vals(body, "create_user_ids", "create_user_id")
    f_quality_user = _vals(body, "last_quality_user_ids", "last_quality_user_id")
    f_settle_user = _vals(body, "settle_user_ids", "settle_user_id")
    f_device = _vals(body, "device_ids", "device_id")
    f_unit = _vals(body, "unit_ids", "unit_id")
    f_workshop = _vals(body, "workshop_ids", "workshop_id", "workshop")
    craft_store = core.Store("produce_bill_crafts")

    out = []
    for r in rows:
        if f_type and str(r.get("type") or 1) not in f_type:
            continue
        if f_settle and str(r.get("settle_status") or 1) not in f_settle:
            continue
        if f_pm and str(r.get("price_mode") or 1) not in f_pm:
            continue
        if f_quality and str(r.get("quality_status") or 3) not in f_quality:
            continue
        if f_report_user and str(r.get("report_user_id")) not in f_report_user:
            continue
        if f_create_user and str(r.get("create_user_id")) not in f_create_user:
            continue
        if f_quality_user and str(r.get("last_quality_user_id")) not in f_quality_user:
            continue
        if f_settle_user and str(r.get("settle_user_id")) not in f_settle_user:
            continue
        if f_device and str(r.get("device_id")) not in f_device:
            continue
        if f_unit and str(r.get("unit_id")) not in f_unit:
            continue
        # ---- 数字区间（搜索区"工资单价/良品数/不良品数/报工数/返工数/报废数/工资"） ----
        if not _match_range(body, "valid_num", r.get("valid_num")):
            continue
        if not _match_range(body, "waste_num", r.get("waste_num")):
            continue
        if not _match_range(body, "report_num", r.get("report_num")):
            continue
        if not _match_range(body, "rework_num", r.get("rework_num")):
            continue
        if not _match_range(body, "scrap_num", r.get("scrap_num")):
            continue
        if not _match_range(body, "craft_unit_money", r.get("craft_unit_money")):
            continue
        if not _match_range(body, "valid_money", r.get("valid_money")):
            continue
        # ---- 日期/时间起止 ----
        if not _match_date(body, "report_time", r.get("report_time") or r.get("created_at")):
            continue
        if not _match_date(body, "created_at", r.get("created_at") or r.get("report_time")):
            continue
        if not _match_date(body, "settle_time", r.get("settle_time")):
            continue
        if not _match_date(body, "last_quality_time", r.get("last_quality_time")):
            continue
        if not _match_date(body, "start_time", r.get("start_time")):
            continue
        if not _match_date(body, "end_time", r.get("end_time")):
            continue
        # ---- 文本模糊 ----
        if _s(body, "craft_name") and _s(body, "craft_name") not in (
                "%s %s" % (r.get("craft_name") or "", r.get("display_craft_name") or "")):
            continue
        if _s(body, "remark") and _s(body, "remark") not in str(r.get("remark") or ""):
            continue
        if _s(body, "quality_remark") and _s(body, "quality_remark") not in str(r.get("quality_remark") or ""):
            continue
        if _s(body, "traceability_code_code") and _s(body, "traceability_code_code") not in str(r.get("traceability_codes") or ""):
            continue
        # ---- 关联维度：加工单 / 工序 / 商品 / 客户 / 车间 ----
        bill = _bill_of(r)
        craft = _craft_of(r) or {}
        if f_bill_st and str((bill or {}).get("status")) not in f_bill_st:
            continue
        if f_craft_ids and str(r.get("produce_bill_craft_id")) not in f_craft_ids:
            continue
        if _s(body, "produce_bill_code") and _s(body, "produce_bill_code") not in str((bill or {}).get("code") or ""):
            continue
        gk = _s(body, "goods_keyword")
        if gk and gk not in (_bill_ref(bill, hay=True) if bill else " ".join(
                str(r.get(x) or "") for x in ("goods_name", "goods_code", "goods_standard"))):
            continue
        if _s(body, "customer_name"):
            cc = (bill or {}).get("customer_contract") or {}
            cust = cc.get("customer") or (bill or {}).get("customer") or {}
            if _s(body, "customer_name") not in str(cust.get("name") or ""):
                continue
        if f_workshop:
            ws = craft.get("workshop_id")
            if not ws or str(ws) not in f_workshop:
                continue
        if _s(body, "craft_code"):
            pc = craft_store.get(r.get("produce_bill_craft_id"))
            if not pc or _s(body, "craft_code") not in str((pc.get("pub_craft") or pc).get("code") or pc.get("craft_code") or ""):
                continue
        out.append(r)
    return out


def _page_rows(rows, body, default_size=30):
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return {"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize], "total": len(rows),
            "pageNo": pageNo, "pageSize": pageSize}


# ---------------- handlers ----------------
def _list(body, session):
    """报工记录列表（记录页/工作台今日报工/报工详情弹窗共用；list_by_filter_view 同构，
    filter_view_id 可选——视图条件种子未含本域，未知视图按无附加过滤处理）。"""
    rows = _filter(_all_rows(), body)
    res = core.paged_ok(_page_rows(rows, body))
    res["data"]["list"] = [_decorate(r) for r in res["data"]["list"]]
    return res


def _status_count(body, session):
    """快捷筛选计数（deep\18 §1.2：type/settle/bill/craft/price_mode/quality 六组）。"""
    rows = _filter(_all_rows(), body)
    tc = {k: 0 for k in TYPE_KEY.values()}
    sc = {k: 0 for k in SETTLE_KEY.values()}
    bc = {k: 0 for k in BILL_STATUS_KEY.values()}
    cc = {k: 0 for k in CRAFT_STATUS_KEY.values()}
    pmc = {"compute_piece": 0, "compute_time": 0}
    qc = {k: 0 for k in QUALITY_KEY.values()}
    for r in rows:
        tc[TYPE_KEY.get(int(r.get("type") or 1), "produce")] += 1
        sc[SETTLE_KEY.get(int(r.get("settle_status") or 1), "not_settle")] += 1
        pm = int(r.get("price_mode") or 1)
        pmc["compute_piece" if pm == 1 else "compute_time"] += 1
        qs = int(r.get("quality_status") or 3)
        if qs == 1:
            qc["wait_quality"] += 1
        elif str(r.get("quality_type") or "") == "3":
            qc["not_quality"] += 1
        else:
            qc["finish_quality"] += 1
        bill = _bill_of(r)
        bs = (bill or {}).get("status")
        if bs in BILL_STATUS_KEY:
            bc[BILL_STATUS_KEY[bs]] += 1
        cs = (_craft_of(r) or {}).get("status")
        if cs in CRAFT_STATUS_KEY:
            cc[CRAFT_STATUS_KEY[cs]] += 1
    return core.ok({"type_count": tc, "settle_status_count": sc, "bill_status_count": bc,
                    "craft_status_count": cc, "price_mode_count": pmc, "quality_status_count": qc})


def _detail(body, session):
    # 路由共存：质检中心域（quality.py，B12）也在 ("report_work_record","detail") 注册了
    # _rwq_detail（其全检种子 report_work_quality 专用装饰）。模块按文件名排序加载、后者覆盖，
    # 本模块后加载，故对 quality 种子行委托其 handler，保证质检详情链路不被打断。
    try:
        if body.get("id") is not None and core.Store("report_work_quality").get(body.get("id")):
            from modules.quality import _rwq_detail
            return _rwq_detail(body, session)
    except ImportError:
        pass
    store, rec = _find(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    return core.ok(_decorate(rec))


# ---------------- 结算族（deep\18 §4.4） ----------------
def _apply_settle(rec, ss, remark, session):
    """写回结算状态/结算人/结算时间/结算备注，并留结算日志（settle_report_work_record_log 用）。"""
    rec["settle_status"] = int(ss)
    rec["settle_status_name"] = SETTLE.get(int(ss), ("",))[0]
    rec["settle_remark"] = remark
    if int(ss) == 2:
        rec["settle_user_id"] = session.get("id")
        rec["settle_user"] = {"id": session.get("id"), "real_name": session.get("real_name")}
        rec["settle_time"] = core.now()
    else:
        rec["settle_user_id"] = None
        rec["settle_user"] = None
        rec["settle_time"] = None
    rec.setdefault("settle_log", []).append({
        "settle_status": int(ss), "settle_status_name": SETTLE.get(int(ss), ("",))[0],
        "settle_user": rec["settle_user"], "settle_user_id": rec["settle_user_id"],
        "settle_time": rec["settle_time"], "settle_remark": remark,
    })


def _edit_settle_status(body, session):
    """单条结算：{id, settle_status(1/2), settle_remark?≤255}（弹窗"工资结算"）。"""
    store, rec = _find(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    try:
        ss = int(body.get("settle_status"))
    except Exception:
        return core.fail("请选择结算状态")
    if ss not in (1, 2):
        return core.fail("请选择结算状态")
    remark = body.get("settle_remark")
    if remark is not None and len(str(remark)) > 255:
        return core.fail("长度超出限制")
    _apply_settle(rec, ss, remark, session)
    store.flush()
    return core.ok()


def _batch_edit_settle_status(body, session):
    """批量结算：{ids, settle_status, settle_remark?}。已结算的不再结算（确认文案语义），计 skip。"""
    ids = body.get("ids") or body.get("id_array") or []
    if not ids:
        return core.fail("请选择报工记录")
    try:
        ss = int(body.get("settle_status"))
    except Exception:
        return core.fail("请选择结算状态")
    if ss not in (1, 2):
        return core.fail("请选择结算状态")
    remark = body.get("settle_remark")
    if remark is not None and len(str(remark)) > 255:
        return core.fail("长度超出限制")
    sc = ec = skip = 0
    touched = []
    for i in ids:
        store, rec = _find(i)
        if not rec:
            ec += 1
            continue
        if int(rec.get("settle_status") or 1) == int(ss):   # 已经是目标状态：不再结算
            skip += 1
            continue
        _apply_settle(rec, ss, remark, session)
        if store not in touched:
            touched.append(store)
        sc += 1
    for st in touched:
        st.flush()
    return core.ok({"success_count": sc, "error_count": ec, "skip_count": skip})


def _settle_log(body, session):
    """结算日志（结算弹窗展示；无实测样例，取记录内结算轨迹，demo 形态）。"""
    store, rec = _find(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    log = rec.get("settle_log")
    if not log and rec.get("settle_status") == 2:
        log = [{"settle_status": 2, "settle_status_name": SETTLE[2][0],
                "settle_user": rec.get("settle_user"), "settle_user_id": rec.get("settle_user_id"),
                "settle_time": rec.get("settle_time"), "settle_remark": rec.get("settle_remark")}]
    return core.ok(log or [])


# ---------------- 工资单价 / 刷新绩效（deep\18 §1.3 / §4.1） ----------------
def _edit_craft_unit_money(body, session):
    """修改工资单价：{id, craft_unit_money}。4 位小数、max 1000000；仅生产/零活报工可改；
    改后按公式重算 valid_money（报告推断：服务端联动重算）。"""
    store, rec = _find(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    if int(rec.get("type") or 1) not in (1, 2):
        return core.fail("该报工记录不能修改工资单价")
    try:
        m = Decimal(str(body.get("craft_unit_money")))
    except Exception:
        return core.fail("参数错误")
    if m < 0 or m > Decimal(1000000):
        return core.fail("工资单价最大为1000000")
    if -m.as_tuple().exponent > 4:
        return core.fail("参数错误")
    rec["craft_unit_money"] = _num(m)
    rec["valid_money"] = _wage(rec.get("price_mode") or 1, rec.get("valid_num"),
                               m, rec.get("working_minutes"), _formula_of(rec))
    cfg = rec.get("performance_pay_config_json")
    if isinstance(cfg, dict):
        cfg["unit_money"] = rec["craft_unit_money"]
    store.flush()
    return core.ok()


def _batch_calculate_valid_money(body, session):
    """刷新绩效：{ids}。按最新公式配置重算 valid_money；已经结算完成的不会重新计算（确认文案语义）。"""
    ids = body.get("ids") or body.get("id_array") or []
    if not ids:
        return core.fail("请选择报工记录")
    sc = ec = skip = 0
    touched = []
    for i in ids:
        store, rec = _find(i)
        if not rec:
            ec += 1
            continue
        if int(rec.get("settle_status") or 1) == 2:
            skip += 1
            continue
        rec["valid_money"] = _wage(rec.get("price_mode") or 1, rec.get("valid_num"),
                                   rec.get("craft_unit_money"), rec.get("working_minutes"), _formula_of(rec))
        if store not in touched:
            touched.append(store)
        sc += 1
    for st in touched:
        st.flush()
    return core.ok({"success_count": sc, "error_count": ec, "skip_count": skip})


# ---------------- 统计 / 删除 ----------------
def _num_or_int(x):
    x = _f(x)
    return int(x) if x == int(x) else x


def _report_statistics(body, session):
    """汇总统计（工作台"今日报工"三数；probes/_m10_rwr_stats.json 同构：
    total_valid_money / now_page_total_valid_money / total_report_num / total_waste_num /
    total_working_minutes / total_valid_num）。"""
    rows = _filter(_all_rows(), body)
    pg = _page_rows(rows, body)
    return core.ok({
        "total_valid_money": _num_or_int(sum(_f(r.get("valid_money")) for r in rows)),
        "now_page_total_valid_money": _num_or_int(sum(_f(r.get("valid_money")) for r in pg["list"])),
        "total_report_num": _num_or_int(sum(_f(r.get("report_num")) for r in rows)),
        "total_waste_num": _num_or_int(sum(_f(r.get("waste_num")) for r in rows)),
        "total_working_minutes": _num_or_int(sum(_f(r.get("working_minutes")) for r in rows)),
        "total_valid_num": _num_or_int(sum(_f(r.get("valid_num")) for r in rows)),
    })


def _del(body, session):
    store, rec = _find(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    store.remove(rec["id"])
    return core.ok()


def _batch_del(body, session):
    ids = body.get("ids") or body.get("id_array") or []
    if not ids:
        return core.fail("请选择报工记录")
    for i in ids:
        store, rec = _find(i)
        if rec:
            store.remove(rec["id"])
    return core.ok()


# ---------------- 种子（report_work_settles：真实样例复刻 + 结算态补造，行加 "_seed":1） ----------------
def _emb_bill(bid, code, status, goods_name, goods_code, num, goods_id=None):
    return {"id": bid, "code": code, "status": status, "goods_id": goods_id,
            "goods": {"id": goods_id, "name": goods_name, "code": goods_code, "standard": None},
            "num": num}


def _emb_craft(cid, bid, craft_name, status, num, unit_money, price_mode=1):
    return {"id": cid, "produce_bill_id": bid, "craft_name": craft_name, "status": status,
            "price_mode": price_mode, "num": num, "produce_bill_craft_num": num,
            "unit_money": unit_money}


def _mk(rid, craft_name, valid, waste=0, unit_money="0", price_mode=1, working_minutes=0,
        start_time=None, end_time=None, report_time="", report_user_id=63097, quality_status=2,
        quality_type=3, settle_status=1, settle_user_id=None, settle_time=None, settle_remark=None,
        remark=None, rtype=1, display_craft_name=None, produce_bill=None, produce_bill_craft=None,
        report_waste_list=None, unit=None, rework_num="0.00000000", scrap_num="0.00000000"):
    """合成行：工资一律走 _wage（保证与公式/截断一致）。"""
    return {
        "_seed": 1, "id": rid, "enterprise_id": 16807,
        "type": rtype, "craft_name": craft_name, "display_craft_name": display_craft_name or craft_name,
        "report_user_id": report_user_id, "create_user_id": report_user_id,
        "produce_bill_id": (produce_bill or {}).get("id") if produce_bill else None,
        "produce_bill_craft_id": (produce_bill_craft or {}).get("id") if produce_bill_craft else None,
        "valid_num": _num(valid), "waste_num": _num(waste),
        "report_num": _num(_f(valid) + _f(waste)),
        "rework_num": rework_num, "scrap_num": scrap_num,
        "price_mode": price_mode, "craft_unit_money": _num(unit_money),
        "valid_money": _wage(price_mode, valid, unit_money, working_minutes),
        "working_minutes": working_minutes, "start_time": start_time, "end_time": end_time,
        "remark": remark, "quality_status": quality_status, "quality_type": quality_type,
        "last_quality_user_id": None, "last_quality_time": None,
        "settle_status": settle_status,
        "settle_user_id": settle_user_id, "settle_time": settle_time, "settle_remark": settle_remark,
        "report_time": report_time, "created_at": report_time,
        "standard_working_seconds": 0, "unit_id": (unit or {}).get("id") if unit else None,
        "unit": unit, "produce_bill": produce_bill, "produce_bill_craft": produce_bill_craft,
        "report_waste_list": report_waste_list or [],
        "performance_pay_config_json": {"performance_pay": False, "formula_config": WAGE_FORMULA,
                                        "unit_money": _num(unit_money), "standard_working_seconds": 0},
        "settle_log": ([{"settle_status": settle_status, "settle_status_name": SETTLE[settle_status][0],
                         "settle_user": _user(settle_user_id), "settle_user_id": settle_user_id,
                         "settle_time": settle_time, "settle_remark": settle_remark}]
                       if settle_status == 2 else []),
    }


def _seed():
    st = _settles()
    if st.rows:
        return
    rows = [
        # 1) probes/_m10_rwr_list.json 行1 复刻：下单 1×0=0（加工单 20261028001-10 生产中，种子里有此单）
        _mk(25659424, "下单", 1, unit_money="0", report_time="2026-10-28 10:44:13",
            start_time="2026-10-28 10:43:54", end_time="2026-10-28 10:44:13",
            produce_bill=_emb_bill(8835784, "20261028001-10", 2, "JH测试1", "SP202610260004", "1.00000000", 9838992),
            produce_bill_craft=_emb_craft(37537683, 8835784, "下单", 3, "1.00000000", "0.00000000")),
        # 2) 同 dump 行2 复刻：JH测试1工序 1×100=100（JG261027-4 已完成）
        _mk(25650003, "JH测试1工序", 1, unit_money="100", report_time="2026-10-28 08:46:50",
            start_time="2026-10-28 08:46:45", end_time="2026-10-28 08:46:50",
            produce_bill=_emb_bill(8818055, "JG261027-4", 3, "JH测试1", "SP202610260004", "1.00000000", 9838992),
            produce_bill_craft=_emb_craft(37460346, 8818055, "JH测试1工序", 3, "1.00000000", "100.00000000")),
        # 3) _m10_rwr_p2 复刻 + 补造结算态：编织 98×2.3=225.4→225（截断实证），不良品 氧化/脱碳 2
        _mk(25550904, "编织", 98, waste=2, unit_money="2.3", quality_status=2, quality_type=1,
            report_time="2026-10-26 11:04:53", settle_status=2, settle_user_id=63097,
            settle_time="2026-10-27 15:20:31", settle_remark="10月下旬工资已核对",
            produce_bill=_emb_bill(8726880, "JG261021-2", 2, "袜子", "SP202610210001", "200.00000000"),
            produce_bill_craft=_emb_craft(37054730, 8726880, "编织", 2, "200.00000000", "2.30000000"),
            report_waste_list=[{"id": 1504212, "waste_item_id": 31634, "num": "2.00000000",
                                "waste_item": {"id": 31634, "name": "氧化/脱碳"}}]),
        # 4) _m10_rwr_p2 复刻：返工报工 组装#2 1×20=20，待质检（type=3 覆盖）
        _mk(25302217, "组装", 1, unit_money="20", rtype=3, display_craft_name="组装#2",
            quality_status=1, quality_type=1, report_time="2026-10-20 20:49:40",
            produce_bill=_emb_bill(8722001, "JG261020-1", 2, "设备", "0902001", "100.00000000"),
            produce_bill_craft=_emb_craft(37030988, 8722001, "组装", 3, "1.00000000", "20.00000000")),
        # 5) _m10_rwr_p2 DIP 改造：计时 3元/小时 × 180 分钟 = 9（时长口径演示），补造结算态
        _mk(25531758, "DIP", 0, unit_money="3", price_mode=2, working_minutes=180,
            start_time="2026-10-25 21:20:00", end_time="2026-10-26 00:20:00",
            report_time="2026-10-26 00:20:10", report_user_id=249248, quality_status=2, quality_type=3,
            settle_status=2, settle_user_id=250574, settle_time="2026-10-26 09:30:12",
            settle_remark="计时工资已核对",
            produce_bill=_emb_bill(8759537, "JG261023-1", 3, "电子连接器", "SP202601070033", "100.00000000"),
            produce_bill_craft=_emb_craft(37201568, 8759537, "DIP", 3, "100.00000000", "3.00000000", 2)),
        # 6) _m10_rwr_p2 复刻：插芯研磨 80×0=0，待质检（报工人≠生产人员覆盖）
        _mk(25438861, "插芯研磨", 80, unit_money="0", quality_status=1, quality_type=1,
            report_time="2026-10-23 16:43:45", report_user_id=230342,
            produce_bill=_emb_bill(8749075, "JG261022-2", 3, "光纤连接器", "FC-SC-01", "100.00000000"),
            produce_bill_craft=_emb_craft(37152150, 8749075, "插芯研磨", 3, "100.00000000", "0.00000000")),
        # 7) deep\18 §4.3 截断实证行：19×0.2=3.8→3；挂 demo 种子真实工序 880000001（加工单 8835784）
        _mk(25660002, "JH测试1工序", 19, waste=1, unit_money="0.2", quality_status=2, quality_type=1,
            report_time="2026-10-27 17:41:20", report_user_id=250574, remark="首件试产",
            produce_bill={"id": 8835784, "code": "20261028001-10", "status": 2,
                          "goods": {"id": 9838992, "name": "JH测试1", "code": "SP202610260004", "standard": None},
                          "num": "1.00000000", "goods_id": 9838992},
            produce_bill_craft=_emb_craft(880000001, 8835784, "JH测试1工序", 3, "1.00000000", "0.20000000"),
            report_waste_list=[{"id": 15043001, "waste_item_id": 31629, "num": "1.00000000",
                                "waste_item": {"id": 31629, "name": "毛刺/飞边"}}]),
        # 8) 零活报工（type=2 覆盖，无加工单/工序，单位取记录 unit，deep\18 §2.5）
        _mk(25660003, "搬料", 12, unit_money="1.5", rtype=2, report_time="2026-10-26 08:30:00",
            report_user_id=157707, quality_status=2, quality_type=3,
            unit={"id": 946698, "name": "个"}, working_minutes=45),
    ]
    for r in rows:
        st.add(r)


_seed()

ROUTES = {
    # 列表族（deep\18 §1；list_by_filter_view 为记录页表格默认 url）
    ("report_work_record", "list"): _list,
    ("report_work_record", "list_by_filter_view"): _list,
    ("report_work_record", "status_count"): _status_count,
    ("report_work_record", "detail"): _detail,
    # 结算族（deep\18 §4.4，动作名逐字）
    ("report_work_record", "edit_settle_status"): _edit_settle_status,
    ("report_work_record", "batch_edit_settle_status"): _batch_edit_settle_status,
    ("report_work_record", "settle_report_work_record_log"): _settle_log,
    # 工资单价 / 刷新绩效（deep\18 §1.3 / §4.1）
    ("report_work_record", "edit_craft_unit_money"): _edit_craft_unit_money,
    ("report_work_record", "batch_calculate_valid_money"): _batch_calculate_valid_money,
    # 统计 / 删除（deep\18 §2.4 / §1.3）
    ("report_work_record", "report_statistics"): _report_statistics,
    ("report_work_record", "report_statistics_by_filter_view"): _report_statistics,
    ("report_work_record", "del"): _del,
    ("report_work_record", "batch_del"): _batch_del,
    # 导出/打印族（export/web_export/export_attach_module/batch_template_print/web_batch_template_print/
    # print_preview）暂不实现：server 对未注册路由返回 fail("404")
}
