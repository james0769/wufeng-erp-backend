# -*- coding: utf-8 -*-
"""B?? 报表中心数据端点（demo 级）。
依据：analysis\\14-其他模块清单.md §2.2（每页代表接口与列名逐字）+ deep\\04-报表图表体系.md §3/§4
+ deep\\27-报表中心页面补全.md §2（合同报表/成本报表/不良品项明细/售后工资列名）。

数据口径（全部从现有种子现算，不造无源数据）：
- 报工行全集 = report_work_records(活动库) + report_work_settles(结算样例) + report_work_quality(全检视图种子)，
  与 report_record.py「两库合并」、quality.py「质检视图」同源合并（三库只读，不写回）。
- 机台/机床报表：报工记录无 machine_tool_id 字段（种子无机台报工关联）→ 按机台档案出行、指标列聚合为 0。
- 售后三表（工单用时/员工分析/客户分析）+ 售后工资 + 考勤：无售后/考勤种子 → 恒空 {success,count:0,data:{list:[],total:0}}。
- 采购对账入库明细 / 销售对账出库明细：单据类型联跑（来料入库/销售出库 + 合同/采购单关联字段），当前种子无该类单据 → 空列表（逻辑真实，数据不造假）。
- 成本报表：人工费用=报工工资按加工单归集；成本分摊金额=cost_shares 明细按加工单归集；
  材料费用(估算)/外协费用在加工单维度无源 → 0；外协 Tab 直接聚合 outsource_produce_bills 自带成本字段（外协总费用=外协单价×计划数）。
- AI 库存监控列表：与 goods_stock_out_analysis/get_month_prepared_suggestion（purchase.py _shortage_rows）同源派生。
"""
import datetime

import core

# ---------------- 数值/通用工具 ----------------
def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _d8(x):
    try:
        return "%.8f" % float(x or 0)
    except Exception:
        return "%.8f" % 0


def _d2(x):
    try:
        return "%.2f" % float(x or 0)
    except Exception:
        return "%.2f" % 0


def _pct(a, b):
    """百分比（2 位小数字符串），分母 0 → "0.00"。"""
    try:
        return "%.2f" % (_f(a) / _f(b) * 100) if _f(b) else "0.00"
    except Exception:
        return "0.00"


def _alive(r):
    return str(r.get("del", 2)) != "1"


def _pdate(v):
    if v is None:
        return None
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _row_date(r, *keys):
    for k in keys:
        d = _pdate(r.get(k))
        if d:
            return d
    return None


def _range(body):
    """筛选时间范围：start_date/end_date（报表页统一参数，stats.py 同款）。"""
    return _pdate(body.get("start_date")), _pdate(body.get("end_date"))


def _page_rows(rows, body, default_size=30):
    """对计算出的行列表做契约分页（core.paged_ok 同构）。"""
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return {"success": True, "count": len(rows),
            "data": {"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize],
                     "total": len(rows), "pageNo": pageNo, "pageSize": pageSize}}


def _empty_paged(body=None, session=None):
    """无数据源端点的统一空返回（前端有演示数据回落）。"""
    return {"success": True, "count": 0, "data": {"list": [], "total": 0, "pageNo": 1, "pageSize": 30}}


def _kw(body, *keys):
    """取第一个非空字符串筛选值。"""
    for k in keys:
        v = str(body.get(k) or "").strip()
        if v:
            return v
    return ""


# ---------------- 索引（只读聚合用） ----------------
def _users():
    return core.Store("users").all()


def _user(uid):
    uid = uid if uid is not None else 0
    for u in _users():
        if str(u.get("id")) == str(uid):
            return u
    return None


def _user_name(uid):
    u = _user(uid)
    return (u or {}).get("real_name") or (u or {}).get("name") or "用户%s" % uid


def _user_brief(uid):
    u = _user(uid) or {}
    return {"id": uid, "real_name": u.get("real_name") or u.get("name") or ("用户%s" % uid),
            "department": u.get("department_path_names") or ""}


def _bills_idx():
    return {str(r.get("id")): r for r in core.Store("produce_bills").all() if _alive(r)}


def _crafts_idx():
    return {str(r.get("id")): r for r in core.Store("produce_bill_crafts").all() if _alive(r)}


def _pub_crafts():
    """工序档案（pub_craft）：craft_name → 档案行（取 code/standard_working_seconds）。"""
    m = {}
    for c in core.Store("crafts").all():
        if not _alive(c):
            continue
        name = str(c.get("name") or "")
        if name and name not in m:
            m[name] = c
    return m


def _craft_code(pub):
    """工序档案编号（pub_craft.code，种子多为 None → 前端空列）。"""
    return (pub or {}).get("code")


def _goods_by_code():
    return {str(g.get("code")): g for g in core.Store("goods").all() if _alive(g)}


def _ware_name(wid):
    for w in core.Store("wares").all():
        if str(w.get("id")) == str(wid or ""):
            return w.get("name")
    return None


# ---------------- 报工行全集（三库合并，只读） ----------------
def _report_rows():
    rows = []
    for name in ("report_work_records", "report_work_settles", "report_work_quality"):
        for r in core.Store(name).all():
            if _alive(r):
                rows.append(dict(r))
    rows.sort(key=lambda r: str(r.get("report_time") or r.get("created_at") or ""), reverse=True)
    return rows


def _r_craft_name(r, crafts_idx):
    """报工行的工序名：自带 craft_name 优先，否则按 produce_bill_craft_id 回查任务。"""
    if r.get("craft_name"):
        return str(r["craft_name"])
    c = crafts_idx.get(str(r.get("produce_bill_craft_id") or ""))
    return (c or {}).get("craft_name")


def _r_goods(r, bills_idx, goods_by_code):
    """报工行的商品信息（经加工单）。"""
    b = bills_idx.get(str(r.get("produce_bill_id") or ""))
    if b:
        code = b.get("goods_code")
        g = goods_by_code.get(str(code)) if code else None
        unit = (g or {}).get("unit")
        return {
            "goods_id": (g or {}).get("id") or b.get("goods_id"),
            "goods_code": b.get("goods_code"),
            "goods_name": b.get("goods_name"),
            "goods_standard": b.get("goods_standard"),
            "goods_unit_name": (unit or {}).get("name") if isinstance(unit, dict) else unit,
            "goods_category_name": b.get("category_path_names"),
            "bill_code": b.get("code"),
            "bill_num": b.get("num"),
            "bill": b,
        }
    return {}


# =====================================================================
# 生产管理组
# =====================================================================

# ---------------- 1. 机台报表 /statistics/machine_analysis_list ----------------
def _machine_analysis_list(body, session):
    """机台报表（04 §4-1 列名逐字）。种子报工记录无机台关联（无 machine_tool_id）→ 指标列聚合为 0，
    机台档案列（编号/名称/规格/状态/类别）为真实数据。"""
    kw = _kw(body, "keyword", "machine_keyword").lower()
    st = _kw(body, "device_status_id")
    rows = []
    for m in core.Store("machines").all():
        if not _alive(m):
            continue
        if kw and kw not in "%s %s %s" % (m.get("name") or "", m.get("code") or "", m.get("standard") or "").lower():
            continue
        if st and str(m.get("device_status_id") or "") != st:
            continue
        rows.append({
            "id": m.get("id"), "code": m.get("code"), "name": m.get("name"),
            "standard": m.get("standard"), "device_status": m.get("device_status"),
            "device_status_name": (m.get("device_status") or {}).get("name"),
            "category_path_names": m.get("category_path_names"),
            "craft_name": None,                       # 机台-工序关联无种子 → 空
            "report_num": _d8(0), "valid_num": _d8(0), "waste_num": _d8(0),
            "valid_rate": _pct(0, 0),
            "standard_work_time": 0, "working_seconds": 0,
            "utilization_rate": _pct(0, 0),
            "standard_produce_num": _d8(0), "actual_produce_num": _d8(0),
            "compliance_rate": _pct(0, 0),
        })
    return _page_rows(rows, body)


# ---------------- 2. 机床报表 /machine_tool/statistics ----------------
def _machine_tool_statistics(body, session):
    """机床报表（04 §4-2 列名逐字，dataIndex 前缀 report_data./net_link_data. 照抄）。
    机床联网时长（加工/待机/离线/报警）无源 → net_link_data 全 0。"""
    kw = _kw(body, "keyword", "machine_keyword").lower()
    rows = []
    for m in core.Store("machines").all():
        if not _alive(m):
            continue
        if kw and kw not in "%s %s" % (m.get("name") or "", m.get("code") or "").lower():
            continue
        rows.append({
            "id": m.get("id"), "code": m.get("code"), "name": m.get("name"),
            "status_name": (m.get("device_status") or {}).get("name"),
            "category_path_names": m.get("category_path_names"),
            "report_data": {"report_num": _d8(0), "valid_num": _d8(0), "waste_num": _d8(0),
                            "waste_rate": _pct(0, 0)},
            "net_link_data": {"working_duration_sec": 0, "idle": 0, "offline": 0, "fault": 0},
            "utilization_rate": _pct(0, 0), "standard_produce_num": _d8(0),
        })
    return _page_rows(rows, body)


# ---------------- 3/4. 在制品报表 /work_in_progress_report/* ----------------
def _craft_report_agg(crafts_idx):
    """报工指标按工序聚合：任务库内的行按 craft_id 归入其工序名；库外行按自带 craft_name 归入（只计一次）。"""
    by_name = {}
    for r in _report_rows():
        name = _r_craft_name(r, crafts_idx)
        if not name:
            name = "未指定工序"
        g = by_name.setdefault(name, {"report_num": 0.0, "valid_num": 0.0, "waste_num": 0.0})
        g["report_num"] += _f(r.get("report_num"))
        g["valid_num"] += _f(r.get("valid_num"))
        g["waste_num"] += _f(r.get("waste_num"))
    return by_name


def _wip_statistics(body, session):
    """在制品统计表（指标配置原文：计划数=该工序计划生产数；未清数量=计划数-良品数；不良品率=不良品数/报工数）。
    在制品数定义原文为空 → 取「进行中任务的剩余数量」= Σ(计划数-良品数-不良品数)，只在 status=2(进行中) 任务上计。"""
    crafts_idx = _crafts_idx()
    rep = _craft_report_agg(crafts_idx)
    plan, wip = {}, {}
    for c in crafts_idx.values():
        name = str(c.get("craft_name") or "未指定工序")
        plan[name] = plan.get(name, 0.0) + _f(c.get("num"))
        if str(c.get("status")) == "2":
            done = _f(c.get("total_valid_num")) + _f(c.get("waste_num"))
            wip[name] = wip.get(name, 0.0) + max(0.0, _f(c.get("num")) - done)
    rows = []
    for name in sorted(set(plan) | set(rep), key=lambda n: -plan.get(n, 0.0)):
        p = plan.get(name, 0.0)
        g = rep.get(name, {"report_num": 0.0, "valid_num": 0.0, "waste_num": 0.0})
        rows.append({
            "name": name, "num": _d8(p),
            "work_in_progress_num": _d8(wip.get(name, 0.0)),
            "unfinished_num": _d8(p - g["valid_num"]),          # 未清数量 = 计划数 - 良品数
            "valid_num": _d8(g["valid_num"]), "waste_num": _d8(g["waste_num"]),
            "waste_rate": _pct(g["waste_num"], g["report_num"]),
            "completion_rate": _pct(g["valid_num"], p),
        })
    return core.ok(rows)


def _wip_list(body, session):
    """在制品明细（list_by_filter_view）：produce_bill_crafts × produce_bills 行级数据。"""
    bills_idx = _bills_idx()
    kw = _kw(body, "craft_name", "craft_keyword")
    bkw = _kw(body, "produce_bill_code", "code")
    rows = []
    for c in core.Store("produce_bill_crafts").all():
        if not _alive(c):
            continue
        if kw and kw not in str(c.get("craft_name") or ""):
            continue
        b = bills_idx.get(str(c.get("produce_bill_id") or ""))
        if bkw and bkw not in str((b or {}).get("code") or c.get("produce_bill_code") or ""):
            continue
        rows.append({
            "id": c.get("id"), "code": c.get("produce_bill_code") or (b or {}).get("code"),
            "produce_bill_id": c.get("produce_bill_id"),
            "produce_bill_code": c.get("produce_bill_code") or (b or {}).get("code"),
            "status": c.get("status"), "status_name": c.get("status_name"),
            "craft_name": c.get("craft_name"), "order_number": c.get("order_number"),
            "num": _d8(c.get("num")), "valid_num": _d8(c.get("total_valid_num")),
            "waste_num": _d8(c.get("waste_num")),
            "wait_produce_num": _d8(max(0.0, _f(c.get("num")) - _f(c.get("total_valid_num")))),
            "is_current": c.get("is_current"),
            "goods_name": (b or {}).get("goods_name"), "goods_code": (b or {}).get("goods_code"),
            "goods_standard": (b or {}).get("goods_standard"),
            "bill_status_name": (b or {}).get("status_name"),
            "start_produce_date": (b or {}).get("start_produce_date"),
            "end_produce_date": (b or {}).get("end_produce_date"),
            "delivery_date": (b or {}).get("delivery_date"),
            "created_at": c.get("created_at"),
        })
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return _page_rows(rows, body)


# ---------------- 5. 工序打卡计时 /produce_craft_time_record/list ----------------
def _craft_time_list(body, session):
    """工序打卡流水：从报工行派生可得子集（姓名/加工单/项目/计划/工序/打卡时间=报工开始时间、
    区间时长=报工时长×60 秒）；打卡类型/区间类型枚举与下次打卡时间无源 → None（前端回落）。"""
    crafts_idx = _crafts_idx()
    bills_idx = _bills_idx()
    start, end = _range(body)
    kw_user = _kw(body, "user_name", "keyword")
    kw_craft = _kw(body, "craft_name")
    rows = []
    for r in _report_rows():
        d = _row_date(r, "report_time", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        uname = _user_name(r.get("report_user_id"))
        if kw_user and kw_user not in uname:
            continue
        cname = _r_craft_name(r, crafts_idx) or ""
        if kw_craft and kw_craft not in cname:
            continue
        b = bills_idx.get(str(r.get("produce_bill_id") or "")) or {}
        rows.append({
            "id": r.get("id"), "user_name": uname,
            "department": (_user(r.get("report_user_id")) or {}).get("department_path_names") or "",
            "produce_bill_code": b.get("code"), "produce_project_code": b.get("project_code"),
            "produce_project_name": b.get("project_name"),
            "produce_schedule_code": b.get("produce_schedule_code"),
            "produce_schedule_name": b.get("produce_schedule_name"),
            "craft_name": cname or None,
            "clock_time": r.get("start_time") or r.get("report_time"),
            "next_clock_time": None, "clock_type": None, "clock_type_name": None,
            "next_clock_type": None, "next_clock_type_name": None,
            "interval_type": None, "interval_type_name": None,
            "use_time": int(_f(r.get("working_minutes")) * 60),
            "report_time": r.get("report_time"),
        })
    return _page_rows(rows, body)


# =====================================================================
# 质量管理组
# =====================================================================

# ---------------- 6/7. 不良品柏拉图 /statistics/waste_main_analysis_list|charts ----------------
# SUMMARY_DIMENSION（04 §3.1 逐字）：1 工序 / 2 生产人员 / 3 工序+不良品项 / 4 工序+生产人员 /
# 5 加工单+不良品项（getXAxis 用商品名）/ 6 工序+生产人员+不良品项
def _waste_main_groups(body):
    """按汇总维度聚合报工不良：返回排序后的分组行（含 percentage / percentage_rank 累计占比）。"""
    try:
        dim = int(body.get("summary_dimension") or 1)
    except Exception:
        dim = 1
    start, end = _range(body)
    crafts_idx = _crafts_idx()
    bills_idx = _bills_idx()
    goods_by_code = _goods_by_code()
    groups = {}

    def g(key):
        return groups.setdefault(key, {"report_num": 0.0, "valid_num": 0.0, "waste_num": 0.0,
                                       "report_waste_num": 0.0})

    for r in _report_rows():
        d = _row_date(r, "report_time", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        craft = _r_craft_name(r, crafts_idx) or ""
        user = _user_name(r.get("report_user_id"))
        info = _r_goods(r, bills_idx, goods_by_code)
        items = []
        if dim in (3, 5, 6):   # 含不良品项维度：不良按逐项数归组
            for w in (r.get("report_waste_list") or []):
                items.append(((w.get("waste_item") or {}).get("name") or "不良品项#%s" % w.get("waste_item_id"),
                              _f(w.get("num"))))
            if not items and _f(r.get("waste_num")) > 0:
                items.append(("未指定不良品项", _f(r.get("waste_num"))))
        keys = []
        if dim == 1:
            keys = [(craft,)]
        elif dim == 2:
            keys = [(user,)]
        elif dim == 3:
            keys = [(craft, iname) for iname, _ in items]
        elif dim == 4:
            keys = [(craft, user)]
        elif dim == 5:
            keys = [(info.get("goods_name") or "未关联商品", iname) for iname, _ in items]
        elif dim == 6:
            keys = [(craft, user, iname) for iname, _ in items]
        else:
            keys = [(craft,)]
        for i, key in enumerate(keys):
            row = g(key)
            row["report_num"] += _f(r.get("report_num"))
            row["valid_num"] += _f(r.get("valid_num"))
            if dim in (3, 5, 6):
                row["waste_num"] += items[i][1] if i < len(items) else 0.0
                row["report_waste_num"] += items[i][1] if i < len(items) else 0.0
            else:
                row["waste_num"] += _f(r.get("waste_num"))
                row["report_waste_num"] += _f(r.get("waste_num"))
    out = []
    total = sum(x["waste_num"] for x in groups.values())
    cum = 0.0
    for key, x in sorted(groups.items(), key=lambda kv: -kv[1]["waste_num"]):
        cum += x["waste_num"]
        row = dict(x)
        row["key"] = key
        row["percentage"] = _pct(x["waste_num"], x["report_num"])
        row["percentage_rank"] = _pct(cum, total)
        out.append(row)
    return dim, out, total


def _waste_row_fields(dim, x):
    """柏拉图行 → 前端字段（chart_data 名字段 getXAxis 同名：pub_craft_name/user_real_name/goods_name/waste_item_name）。"""
    pub = _pub_crafts().get(x["key"][0] if dim in (1, 3, 4, 6) else "") or {}
    row = {"report_num": _d8(x["report_num"]), "valid_num": _d8(x["valid_num"]),
           "waste_num": _d8(x["waste_num"]), "report_waste_num": _d8(x["report_waste_num"]),
           "percentage": x["percentage"], "percentage_rank": x["percentage_rank"]}
    if dim in (1, 3, 4, 6):
        row["pub_craft_name"] = x["key"][0]
        row["pub_craft_code"] = _craft_code(pub)
    if dim == 2:
        row["user_real_name"] = x["key"][0]
    if dim == 5:
        row["goods_name"] = x["key"][0]
    if dim in (3, 5):
        row["waste_item_name"] = x["key"][-1]
    if dim == 4:
        row["user_real_name"] = x["key"][1]
    if dim == 6:
        row["user_real_name"] = x["key"][1]
        row["waste_item_name"] = x["key"][2]
    if dim in (3, 5, 6):
        row["waste_item_percentage"] = _pct(x["report_waste_num"], x["report_num"])
    return row


def _waste_main_list(body, session):
    """柏拉图数据明细表（04 §3.4 列名逐字，只分析排名前 50 的数据）。"""
    dim, groups, _total = _waste_main_groups(body)
    rows = []
    for i, x in enumerate(groups[:50]):
        row = _waste_row_fields(dim, x)
        row["index_w"] = i + 1
        rows.append(row)
    return _page_rows(rows, body)


def _waste_main_charts(body, session):
    """柏拉图 chart_data（bar=不良品数 + line=累计占比 + percentage_rank 定位 80% 参考线）
    + analysis_report 智能分析报告（柏拉图页报告区字段名，04 §2.4）。"""
    dim, groups, total = _waste_main_groups(body)
    chart = [_waste_row_fields(dim, x) for x in groups[:50]]
    report = []
    if total > 0 and groups:
        top = groups[0]
        top_name = "/".join(str(k) for k in top["key"] if k)
        n80 = next((i + 1 for i, x in enumerate(groups) if _f(x["percentage_rank"]) >= 80), len(groups))
        report.append({"desc": "统计周期内不良品总数为 %s，其中「%s」不良品数最多，占比 %s%%；"
                              "累计占比达 80%% 的重点工序/对象共 %d 项。" %
                       (_d8(total), top_name, _pct(top["waste_num"], total), n80)})
    return core.ok({"chart_data": chart, "analysis_report": report})


# ---------------- 8. 不良品项明细 /waste_detail/list ----------------
_WASTE_DETAIL_DIMS = {   # SUMMARY_DIMENSION_TYPE（27 §2.6 逐字 9 项）
    "report_work": "报工", "craft": "工序", "produce_bill": "加工单", "produce_goods": "商品",
    "produce_staff": "生产人员", "produce_bill_and_craft": "加工单+任务",
    "goods_and_craft": "商品+工序", "goods_and_staff": "商品+生产人员",
    "craft_and_staff": "工序+生产人员",
}


def _waste_detail_list(body, session):
    """不良品项明细分组列表（27 §2.6 列名逐字；报工维度出全列，其余维度为投影聚合）。"""
    dim = str(body.get("summary_dimensions_type") or "report_work")
    start, end = _range(body)
    crafts_idx = _crafts_idx()
    bills_idx = _bills_idx()
    goods_by_code = _goods_by_code()
    pubs = _pub_crafts()
    kw_craft = _kw(body, "craft_name")
    kw_user = _kw(body, "produce_user_name", "user_name")
    kw_bill = _kw(body, "bill_code", "produce_bill_code")
    waste_ids = [str(x) for x in (body.get("waste_item_ids") or [])]

    def craft_full(r):
        name = _r_craft_name(r, crafts_idx) or ""
        pub = pubs.get(name) or {}
        c = crafts_idx.get(str(r.get("produce_bill_craft_id") or "")) or {}
        return name, pub, c

    rows, agg = [], {}

    def g(key):
        return agg.setdefault(key, {"report_num": 0.0, "valid_num": 0.0, "waste_num": 0.0})

    for r in _report_rows():
        d = _row_date(r, "report_time", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        if waste_ids:
            ids = [str((w.get("waste_item_id") or (w.get("waste_item") or {}).get("id")))
                   for w in (r.get("report_waste_list") or [])]
            if not any(i in waste_ids for i in ids):
                continue
        cname, pub, c = craft_full(r)
        uname = _user_name(r.get("report_user_id"))
        if kw_craft and kw_craft not in cname:
            continue
        if kw_user and kw_user not in uname:
            continue
        info = _r_goods(r, bills_idx, goods_by_code)
        if kw_bill and kw_bill not in str(info.get("bill_code") or ""):
            continue
        base = {
            "goods_code": info.get("goods_code"), "goods_name": info.get("goods_name"),
            "goods_standard": info.get("goods_standard"), "goods_unit_name": info.get("goods_unit_name"),
            "bill_code": info.get("bill_code"), "bill_num": info.get("bill_num"),
            "craft_name": cname or None, "craft_code": _craft_code(pub),
            "produce_user_name": uname,
            "report_num": _d8(r.get("report_num")), "valid_num": _d8(r.get("valid_num")),
            "waste_num": _d8(r.get("waste_num")),
            "waste_ratio": _pct(r.get("waste_num"), r.get("report_num")),
        }
        if dim == "report_work":
            row = dict(base)
            row.update({
                "id": r.get("id"),
                "bill_start_produce_date": (info.get("bill") or {}).get("start_produce_date"),
                "bill_end_produce_date": (info.get("bill") or {}).get("end_produce_date"),
                "craft_status": c.get("status"), "craft_status_name": c.get("status_name"),
                "craft_plan_start": c.get("plan_start"), "craft_plan_end": c.get("plan_end"),
                "craft_standard_working_seconds": (pub.get("standard_working_seconds")
                                                   or r.get("standard_working_seconds")),
                "last_quality_time": r.get("last_quality_time"),
                "report_start_time": r.get("start_time"), "report_end_time": r.get("end_time"),
                "report_working_minutes": r.get("working_minutes"),
                "report_price_mode": r.get("price_mode"),
                "craft_unit_money": r.get("craft_unit_money"),
                "report_settle_status": r.get("settle_status"),
                "settle_user_name": _user_name(r.get("settle_user_id")) if r.get("settle_user_id") else None,
            })
            rows.append(row)
            continue
        # 分组维度
        gk = {"craft": (cname,), "produce_bill": (info.get("bill_code"),),
              "produce_goods": (info.get("goods_name"),), "produce_staff": (uname,),
              "produce_bill_and_craft": (info.get("bill_code"), cname),
              "goods_and_craft": (info.get("goods_name"), cname),
              "goods_and_staff": (info.get("goods_name"), uname),
              "craft_and_staff": (cname, uname)}.get(dim, (cname,))
        x = g(gk)
        x["report_num"] += _f(r.get("report_num"))
        x["valid_num"] += _f(r.get("valid_num"))
        x["waste_num"] += _f(r.get("waste_num"))
        x.setdefault("base", base)
    for gk, x in agg.items():
        row = {"index_w": len(rows) + 1,
               "report_num": _d8(x["report_num"]), "valid_num": _d8(x["valid_num"]),
               "waste_num": _d8(x["waste_num"]),
               "waste_ratio": _pct(x["waste_num"], x["report_num"])}
        b = x.get("base", {})
        if dim in ("craft", "craft_and_staff", "produce_bill_and_craft", "goods_and_craft"):
            row["craft_code"], row["craft_name"] = b.get("craft_code"), b.get("craft_name")
        if dim in ("produce_bill", "produce_bill_and_craft"):
            row["bill_code"], row["bill_num"] = b.get("bill_code"), b.get("bill_num")
        if dim in ("produce_goods", "goods_and_craft", "goods_and_staff"):
            row["goods_code"], row["goods_name"] = b.get("goods_code"), b.get("goods_name")
            row["goods_standard"], row["goods_unit_name"] = b.get("goods_standard"), b.get("goods_unit_name")
        if dim in ("produce_staff", "goods_and_staff", "craft_and_staff"):
            row["produce_user_name"] = b.get("produce_user_name")
        rows.append(row)
    return _page_rows(rows, body)


# ---------------- 9. 采购检验分析 /purchase_inspection_report/purchase_inspection_list ----------------
def _purchase_inspection_list(body, session):
    """采购检验统计（04 §4-6 列名逐字）。检验数据源（采购检验单/明细）无种子 → 恒空（任务口径：无检验数据返回空）。"""
    return _empty_paged()


# ---------------- 10. 生产抽检分析 /inspection_random_analysis/inspection_random_list ----------------
def _inspection_random_list(body, session):
    """生产抽检统计（04 §4-7 列名逐字）：produce_inspection_randoms 行级 + 工序/生产人员关联。"""
    crafts_idx = _crafts_idx()
    pubs = _pub_crafts()
    bills_idx = _bills_idx()
    kw_craft = _kw(body, "pub_craft_name", "craft_name")
    kw_user = _kw(body, "produce_user_name", "user_name")
    rows = []
    for r in core.Store("produce_inspection_randoms").all():
        if not _alive(r):
            continue
        c = crafts_idx.get(str(r.get("produce_bill_craft_id") or "")) or {}
        cname = c.get("craft_name") or ""
        pub = pubs.get(cname) or {}
        uname = _user_name(r.get("produce_user_id")) if r.get("produce_user_id") else None
        if kw_craft and kw_craft not in cname:
            continue
        if kw_user and (not uname or kw_user not in uname):
            continue
        b = bills_idx.get(str(r.get("produce_bill_id") or "")) or {}
        rows.append({
            "id": r.get("id"), "code": r.get("code"),
            "produce_bill_code": b.get("code"),
            "num": _d8(r.get("num")), "valid_num": _d8(r.get("valid_num")),
            "waste_num": _d8(r.get("waste_num")),
            "waste_ratio": _pct(r.get("waste_num"), r.get("num")),
            "produce_user_name": uname,
            "pub_craft_name": cname or None, "pub_craft_code": _craft_code(pub),
            "status": r.get("status"), "inspect_time": r.get("inspect_time"),
            "inspect_user_name": _user_name(r.get("inspect_user_id")) if r.get("inspect_user_id") else None,
            "created_at": r.get("created_at"),
        })
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return _page_rows(rows, body)


# ---------------- 11. 报废返工统计 /scrap_rework_analysis/list ----------------
def _scrap_rework_list(body, session):
    """报废/返工统计（04 §4-8 列名逐字），按 工序×生产人员 分组。
    报废/返工数取报工行 rework_num/scrap_num 字段；率=数/报工数，占不良比=数/不良数。"""
    crafts_idx = _crafts_idx()
    bills_idx = _bills_idx()
    goods_by_code = _goods_by_code()
    start, end = _range(body)
    agg = {}
    for r in _report_rows():
        d = _row_date(r, "report_time", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        cname = _r_craft_name(r, crafts_idx) or ""
        uname = _user_name(r.get("report_user_id"))
        info = _r_goods(r, bills_idx, goods_by_code)
        key = (cname, uname)
        x = agg.setdefault(key, {"report_num": 0.0, "valid_num": 0.0, "waste_num": 0.0,
                                 "scrap_num": 0.0, "rework_num": 0.0,
                                 "goods_category_name": info.get("goods_category_name")})
        x["report_num"] += _f(r.get("report_num"))
        x["valid_num"] += _f(r.get("valid_num"))
        x["waste_num"] += _f(r.get("waste_num"))
        x["scrap_num"] += _f(r.get("scrap_num"))
        x["rework_num"] += _f(r.get("rework_num"))
    rows = []
    for (cname, uname), x in sorted(agg.items(), key=lambda kv: -kv[1]["waste_num"]):
        pub = _pub_crafts().get(cname) or {}
        rows.append({
            "pub_craft_code": _craft_code(pub), "pub_craft_name": cname or None,
            "user_name": uname,
            "report_num": _d8(x["report_num"]), "valid_num": _d8(x["valid_num"]),
            "waste_num": _d8(x["waste_num"]), "scrap_num": _d8(x["scrap_num"]),
            "scrap_ratio": _pct(x["scrap_num"], x["report_num"]),
            "scrap_waste_ratio": _pct(x["scrap_num"], x["waste_num"]),
            "rework_num": _d8(x["rework_num"]),
            "rework_ratio": _pct(x["rework_num"], x["report_num"]),
            "rework_waste_ratio": _pct(x["rework_num"], x["waste_num"]),
            "goods_category_name": x["goods_category_name"],
        })
    return _page_rows(rows, body)


# =====================================================================
# 售后管理组（无售后工单种子 → 恒空）
# =====================================================================
def _new_work_order_total_use_time_list(body, session):
    """工单用时报表（04 §4-12）：无售后工单种子 → 空。"""
    return _empty_paged()


def _new_work_order_total_use_time_statistics(body, session):
    """工单用时统计：无售后工单种子 → 空列表。"""
    return _empty_paged()


def _new_work_order_user_analysis_list(body, session):
    """员工分析报表（04 §4-13）：无售后工单种子 → 空。"""
    return _empty_paged()


def _new_work_order_customer_analysis_list(body, session):
    """客户分析报表（04 §4-14）：无售后工单种子 → 空。"""
    return _empty_paged()


# =====================================================================
# 经营报表组
# =====================================================================

# 采购入库单据类型：来料入库（stock.py IN_WARE_TYPE 950910 = 采购入库口径）
_PURCHASE_IN_STOCK_TYPE = 950910
# 销售出库单据类型：销售出库（OUT_WARE_TYPE 946741）
_SALE_OUT_STOCK_TYPE = 946741


def _purchase_stock_in_items(body):
    """采购对账·入库明细行：采购入库单（来料入库/供应商或采购单关联）× 明细展开。
    当前种子无该类单据 → 空列表（关联逻辑真实：供应商/采购单号/仓库/收货人联动）。"""
    start, end = _range(body)
    kw = _kw(body, "supplier_name", "keyword")
    rows = []
    for b in core.Store("stock_in_bills").all():
        if not _alive(b):
            continue
        is_purchase = (str(b.get("stock_type_id") or "") == str(_PURCHASE_IN_STOCK_TYPE)
                       or b.get("supplier_id") is not None or b.get("purchase_bill_id") is not None)
        if not is_purchase:
            continue
        d = _row_date(b, "bill_date", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        supplier = b.get("supplier")
        if not supplier and b.get("supplier_id"):
            for s in core.Store("suppliers").all():
                if str(s.get("id")) == str(b.get("supplier_id")):
                    supplier = {"id": s.get("id"), "name": s.get("name")}
                    break
        if kw and supplier and kw not in str(supplier.get("name") or ""):
            continue
        pb_code = b.get("purchase_bill_code")
        if not pb_code and b.get("purchase_bill_id"):
            pb = core.Store("purchase_bills").get(b.get("purchase_bill_id"))
            pb_code = (pb or {}).get("code")
        for i, it in enumerate(b.get("item_list") or []):
            goods = it.get("goods") or {}
            rows.append({
                "id": int("%s%02d" % (b.get("id"), i + 1)),
                "code": b.get("code"),                       # 采购入库单号
                "purchase_bill_code": pb_code,               # 采购单号
                "ware_id": b.get("ware_id"), "ware_name": _ware_name(b.get("ware_id")),
                "bill_date": b.get("bill_date"),
                "audit_status": b.get("audit_status"), "audit_status_name": "不需审核",
                "supplier": supplier, "supplier_name": (supplier or {}).get("name"),
                "goods_id": it.get("goods_id"), "goods": goods,
                "goods_code": goods.get("code"), "goods_name": goods.get("name"),
                "unit_name": (goods.get("unit") or {}).get("name") if isinstance(goods.get("unit"), dict) else goods.get("unit"),
                "num": _d8(it.get("num")), "cost_price": _d8(it.get("cost_price")),
                "money": _d8(_f(it.get("num")) * _f(it.get("cost_price"))),
                "consignee": _user_brief(b.get("consignee_id")) if b.get("consignee_id") else None,
                "remark": it.get("remark") or b.get("remark"),
            })
    return rows


def _get_purchase_stock_in_bill_item(body, session):
    """/stock_item/get_purchase_stock_in_bill_item：采购对账单·入库明细列表。"""
    return _page_rows(_purchase_stock_in_items(body), body)


def _contract_stock_out_items(body):
    """销售对账·出库明细行：销售出库单（销售出库类型/合同关联字段）× 明细展开 + 合同收欠款联动。
    当前种子无该类单据 → 空列表。"""
    start, end = _range(body)
    kw = _kw(body, "contract_code", "keyword")
    rows = []
    for b in core.Store("stock_out_bills").all():
        if not _alive(b):
            continue
        is_sale = (str(b.get("stock_type_id") or "") == str(_SALE_OUT_STOCK_TYPE)
                   or b.get("customer_contract_id") is not None or b.get("customer_contract_item_id") is not None)
        if not is_sale:
            continue
        d = _row_date(b, "bill_date", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        contract = None
        if b.get("customer_contract_id"):
            contract = core.Store("contracts").get(b.get("customer_contract_id"))
        ccode = (contract or {}).get("code") or b.get("contract_code") or ""
        if kw and kw not in str(ccode):
            continue
        for i, it in enumerate(b.get("item_list") or []):
            goods = it.get("goods") or {}
            sell = _f(it.get("selling_price"))
            num = _f(it.get("num"))
            received = _f(it.get("received_money"))
            money = num * sell
            rows.append({
                "id": int("%s%02d" % (b.get("id"), i + 1)),
                "code": b.get("code"),                        # 销售出库单号
                "ware_id": b.get("ware_id"), "ware_name": _ware_name(b.get("ware_id")),
                "bill_date": b.get("bill_date"),
                "audit_status": b.get("audit_status"), "audit_status_name": "不需审核",
                "shipper": _user_brief(b.get("shipper_id")) if b.get("shipper_id") else None,
                "contract_code": ccode,
                "customer_contract_id": b.get("customer_contract_id"),
                "goods_id": it.get("goods_id"), "goods": goods,
                "goods_code": goods.get("code"), "goods_name": goods.get("name"),
                "unit_name": (goods.get("unit") or {}).get("name") if isinstance(goods.get("unit"), dict) else goods.get("unit"),
                "num": _d8(num), "selling_price": _d8(sell), "selling_money": _d8(money),
                "received_money": _d8(received), "debt_money_total": _d8(max(0.0, money - received)),
                "remark": it.get("remark") or b.get("remark"),
                "created_at": b.get("created_at"),
            })
    return rows


def _get_contract_stock_out_bill_item(body, session):
    """/stock_item/get_contract_stock_out_bill_item：销售对账单·出库明细列表。"""
    return _page_rows(_contract_stock_out_items(body), body)


def _sale_account_statement_list(body, session):
    """/sale_account_statement/list_by_filter_view：销售对账单（与出库明细同源，按合同补已收款/欠款）。"""
    return _page_rows(_contract_stock_out_items(body), body)


def _customer_contract_item_list(body, session):
    """合同报表（27 §2.2 列名逐字）：contracts.item_list 展开行。"""
    kw_cust = _kw(body, "customer_name")
    kw_code = _kw(body, "contract_code")
    kw_goods = _kw(body, "goods_name", "goods_keyword")
    kw_gcode = _kw(body, "goods_code")
    ds = str(body.get("created_at_start") or "")[:10]
    de = str(body.get("created_at_end") or "")[:10]
    rows = []
    for c in core.Store("contracts").all():
        if not _alive(c):
            continue
        if kw_code and kw_code not in str(c.get("code") or ""):
            continue
        cust = c.get("customer") or {}
        cname = cust.get("name") if isinstance(cust, dict) else cust
        if kw_cust and kw_cust not in str(cname or ""):
            continue
        created = str(c.get("created_at") or "")
        if ds and created[:10] < ds:
            continue
        if de and created[:10] > de:
            continue
        for it in (c.get("item_list") or []):
            goods = it.get("goods") or {}
            unit = goods.get("unit")
            num, out = _f(it.get("num")), _f(it.get("stock_out_num"))
            if kw_goods and kw_goods not in str(goods.get("name") or ""):
                continue
            if kw_gcode and kw_gcode not in str(goods.get("code") or ""):
                continue
            rows.append({
                "id": it.get("id"), "customer_contract_id": c.get("id"),
                "contract_code": c.get("code"),
                "customer": {"id": c.get("customer_id"), "name": cname},
                "customer_name": cname,
                "goods_id": it.get("goods_id"), "goods": goods,
                "goods_code": goods.get("code"), "goods_name": goods.get("name"),
                "goods_standard": goods.get("standard"),
                "goods_unit_name": (unit or {}).get("name") if isinstance(unit, dict) else unit,
                "category_path_names": goods.get("category_path_names"),
                "goods_unit_price": _d8(it.get("unit_price")),
                "goods_num": _d8(num),
                "discount": it.get("discount"), "discount_money": _d8(it.get("discount_money")),
                "after_discount_money": _d8(it.get("after_discount_money")),
                "goods_remark": it.get("remark"),
                "stock_out_num": _d8(out),
                "not_stock_out_num": _d8(max(0.0, num - out)),   # 未出库 = 销售数量 - 已出库（27 §2.2 前端同式）
                "delivery_date": c.get("delivery_date"),
                "contract_created_at": c.get("created_at"),
                "contract_audit_status_name": c.get("audit_status_name"),
                "contract_business_status_name": c.get("business_status_name"),
            })
    return _page_rows(rows, body)


def _produce_bill_cost_rows():
    """加工单成本行：报工工资按加工单归集为人工费用；cost_shares 明细归集为成本分摊金额；
    材料(估算)/外协费用无源 → 0；生产总值=合同明细单价(按商品匹配)×实际生产数，无合同单价回退商品销售单价。"""
    labor = {}
    for r in _report_rows():
        if r.get("produce_bill_id") is None:
            continue
        k = str(r.get("produce_bill_id"))
        labor[k] = labor.get(k, 0.0) + _f(r.get("valid_money"))
    share = {}
    for s in core.Store("cost_shares").all():
        if not _alive(s):
            continue
        for it in (s.get("item_list") or []):
            k = str(it.get("produce_bill_id"))
            share[k] = share.get(k, 0.0) + _f(it.get("money"))
    contracts = {}
    for c in core.Store("contracts").all():
        if _alive(c) and c.get("code"):
            contracts[str(c.get("code"))] = c
    goods_by_code = _goods_by_code()
    rows = []
    for b in core.Store("produce_bills").all():
        if not _alive(b):
            continue
        k = str(b.get("id"))
        actual = _f(b.get("actual_num"))
        price = None
        c = contracts.get(str(b.get("contract_code") or ""))
        if c:
            for it in (c.get("item_list") or []):
                g = it.get("goods") or {}
                if str(g.get("code") or "") == str(b.get("goods_code") or "") \
                        and str(g.get("name") or "") == str(b.get("goods_name") or ""):
                    price = _f(it.get("unit_price"))
                    break
        if not price:
            price = _f((goods_by_code.get(str(b.get("goods_code"))) or {}).get("selling_money"))
        value = actual * price
        cost = labor.get(k, 0.0)
        rows.append({
            "id": b.get("id"), "code": b.get("code"),
            "goods_name": b.get("goods_name"), "goods_code": b.get("goods_code"),
            "goods_standard": b.get("goods_standard"),
            "num": _d8(b.get("num")), "actual_num": _d8(actual),
            "total_working_time": b.get("total_working_time"),
            "labor_cost": _d8(labor.get(k, 0.0)), "material_cost": _d8(0),
            "outsource_cost": _d8(0), "total_cost": _d8(cost),
            "share_cost": _d8(share.get(k, 0.0)),
            "total_production_value": _d8(value),
            "gross_profit": _d8(value - cost),
            "contract_code": b.get("contract_code"),
            "delivery_date": b.get("delivery_date"),
        })
    return rows


def _produce_bill_cost_statistics(body, session):
    """/produce_bill_cost/statistics_by_filter_view：成本报表汇总条（27 §2.3 六指标 + 成本分摊）。"""
    rows = _produce_bill_cost_rows()
    return core.ok({
        "labor_cost": _d8(sum(_f(r["labor_cost"]) for r in rows)),
        "material_cost": _d8(sum(_f(r["material_cost"]) for r in rows)),
        "outsource_cost": _d8(sum(_f(r["outsource_cost"]) for r in rows)),
        "total_cost": _d8(sum(_f(r["total_cost"]) for r in rows)),
        "share_cost": _d8(sum(_f(r["share_cost"]) for r in rows)),
        "total_production_value": _d8(sum(_f(r["total_production_value"]) for r in rows)),
        "gross_profit": _d8(sum(_f(r["gross_profit"]) for r in rows)),
        "bill_count": len(rows),
    })


def _produce_bill_cost_list(body, session):
    """/produce_bill_cost/list_by_filter_view：成本报表加工单明细行（报表页主列表）。"""
    rows = _produce_bill_cost_rows()
    kw = str(body.get("code") or "").strip()
    gk = str(body.get("goods_keyword") or "").strip()
    if kw:
        rows = [r for r in rows if kw in str(r.get("code") or "")]
    if gk:
        rows = [r for r in rows if gk in ("%s %s" % (r.get("goods_name") or "", r.get("goods_code") or ""))]
    return _page_rows(rows, body)


def _outsource_produce_bill_cost_list(body, session):
    """/outsource_produce_bill_cost/list_by_filter_view：成本报表外协 Tab 明细行。"""
    rows = []
    for b in core.Store("outsource_produce_bills").all():
        if not _alive(b):
            continue
        rows.append({
            "id": b.get("id"), "code": b.get("code"),
            "goods_name": b.get("goods_name"), "goods_code": b.get("goods_code"),
            "goods_standard": b.get("goods_standard"),
            "num": _d8(b.get("num")),
            "labor_cost": _d8(_f(b.get("labor_cost"))),
            "material_cost": _d8(_f(b.get("material_cost"))),
            "outsource_cost": _d8(_f(b.get("outsource_money")) * _f(b.get("num"))),
            "total_cost": _d8(_f(b.get("total_cost"))),
            "total_production_value": _d8(_f(b.get("total_production_value"))),
            "gross_profit": _d8(_f(b.get("gross_profit"))),
            "supplier_name": (b.get("supplier") or {}).get("name") if isinstance(b.get("supplier"), dict) else b.get("supplier_name"),
            "delivery_date": b.get("delivery_date"),
        })
    kw = str(body.get("code") or "").strip()
    if kw:
        rows = [r for r in rows if kw in str(r.get("code") or "")]
    return _page_rows(rows, body)


def _outsource_produce_bill_cost_statistics(body, session):
    """/outsource_produce_bill_cost/statistics_by_filter_view：外协 Tab 汇总（外协总费用=外协单价×计划数，其余取自带成本字段）。"""
    labor = material = outsource = total = value = gross = 0.0
    n = 0
    for b in core.Store("outsource_produce_bills").all():
        if not _alive(b):
            continue
        n += 1
        labor += _f(b.get("labor_cost"))
        material += _f(b.get("material_cost"))
        outsource += _f(b.get("outsource_money")) * _f(b.get("num"))
        total += _f(b.get("total_cost"))
        value += _f(b.get("total_production_value"))
        gross += _f(b.get("gross_profit"))
    return core.ok({
        "labor_cost": _d8(labor), "material_cost": _d8(material),
        "outsource_cost": _d8(outsource), "total_cost": _d8(total),
        "total_production_value": _d8(value), "gross_profit": _d8(gross),
        "bill_count": n,
    })


def _goods_stock_out_analysis_list(body, session):
    """AI 库存监控列表：与 get_month_prepared_suggestion 同源（purchase._shortage_rows 缺料行）派生。
    库存=可用库存；预测库存/实际需求量=未完工加工单×BOM 物料需求；备料建议=需求-库存（下限 0）；
    每月/预测需求量同口径（无按月拆分源）；本月库存预测值=库存-需求（下限 0）。"""
    from modules.purchase import _shortage_rows   # 只读复用，勿改 purchase 模块
    rows = []
    for s in _shortage_rows():
        g = s.get("goods") or {}
        unit = g.get("unit")
        rows.append({
            "goods_id": s.get("goods_id"), "goods": g,
            "goods_code": g.get("code"), "goods_name": g.get("name"),
            "goods_standard": g.get("standard"),
            "unit_name": (unit or {}).get("name") if isinstance(unit, dict) else unit,
            "stock_num": _d8(s.get("stock")),                       # 库存
            "predict_stock_out_num": _d8(s.get("need")),            # 预测库存（需求口径）
            "initial_stock_num": _d8(s.get("stock")),               # 期初库存
            "actual_demand_num": _d8(s.get("need")),                # 实际需求量
            "month_demand_num": _d8(s.get("need")),                 # 每月需求量（同口径）
            "predict_demand_num": _d8(s.get("need")),               # 预测需求量（同口径）
            "month_predict_stock_num": _d8(max(0.0, _f(s.get("stock")) - _f(s.get("need")))),  # 本月库存预测值
            "material_preparation_suggestions": _d8(s.get("num")),  # 备料建议数量
            "has_purchase_order": None,                             # 无采购单关联源
        })
    return _page_rows(rows, body)


# =====================================================================
# 人力薪酬组
# =====================================================================

# ---------------- 生产工资报表 /salary_report/list_by_filter_view ----------------
def _salary_report_list(body, session):
    """生产工资报表（04 §4-16 列名逐字）：报工行按人聚合。
    计件/计时工资按 settle_status 分列（valid_money 原值，计件=mode1 / 计时=mode2）。"""
    start, end = _range(body)
    kw = _kw(body, "user_name", "keyword")
    agg = {}
    for r in _report_rows():
        d = _row_date(r, "report_time", "created_at")
        if (start or end) and (not d or not (start is None or d >= start) or not (end is None or d <= end)):
            continue
        uid = r.get("report_user_id")
        x = agg.setdefault(uid, {"rows": 0, "report": 0.0, "valid": 0.0, "waste": 0.0,
                                 "not_settle": 0, "already_settle": 0,
                                 "item_ns": 0.0, "item_as": 0.0, "time_ns": 0.0, "time_as": 0.0,
                                 "task": 0, "odd": 0, "money": 0.0})
        settled = str(r.get("settle_status") or 1) == "2"
        money = _f(r.get("valid_money"))
        piece = str(r.get("price_mode") or 1) == "1"
        x["rows"] += 1
        x["report"] += _f(r.get("report_num"))
        x["valid"] += _f(r.get("valid_num"))
        x["waste"] += _f(r.get("waste_num"))
        x["money"] += money
        if settled:
            x["already_settle"] += 1
            x["item_as"] += money if piece else 0.0
            x["time_as"] += money if not piece else 0.0
        else:
            x["not_settle"] += 1
            x["item_ns"] += money if piece else 0.0
            x["time_ns"] += money if not piece else 0.0
        t = r.get("type")
        if str(t) == "2":
            x["odd"] += 1          # 零活报工（report_record.py TYPE 2）
        else:
            x["task"] += 1         # 任务（生产/返工）报工
    rows = []
    for uid, x in sorted(agg.items(), key=lambda kv: -kv[1]["money"]):
        u = _user(uid) or {}
        name = u.get("real_name") or u.get("name") or "用户%s" % uid
        if kw and kw not in name:
            continue
        rows.append({
            "user_id": uid, "realName": name,
            "department": u.get("department_path_names") or "",
            "team": u.get("team_names") or "",
            "reportNumNotSettle": x["not_settle"], "reportNumAlreadySettle": x["already_settle"],
            "moneyOfItemNotSettle": _d8(x["item_ns"]), "moneyOfItemAlreadySettle": _d8(x["item_as"]),
            "moneyOfTimeNotSettle": _d8(x["time_ns"]), "moneyOfTimeAlreadySettle": _d8(x["time_as"]),
            "reportNumCount": x["rows"], "validNum": _d8(x["valid"]), "wasteNum": _d8(x["waste"]),
            "taskWorkNum": x["task"], "flexibleWorkNum": x["odd"],
            "moneyCount": _d8(x["money"]),
        })
    return _page_rows(rows, body)


# ---------------- 售后工资 / 考勤（无种子 → 恒空） ----------------
def _after_sale_salary_list(body, session):
    """售后工资报表（27 §2.5）：无售后结算种子 → 空。"""
    return _empty_paged()


def _clock_in_report_forms(body, session):
    """考勤报表（04 §4-17）：无考勤打卡种子 → 空。"""
    return _empty_paged()


ROUTES = {
    # ---- 生产管理 ----
    ("statistics", "machine_analysis_list"): _machine_analysis_list,
    ("machine_tool", "statistics"): _machine_tool_statistics,
    ("work_in_progress_report", "work_in_process_statistics"): _wip_statistics,
    ("work_in_progress_report", "list_by_filter_view"): _wip_list,
    ("produce_craft_time_record", "list"): _craft_time_list,
    # ---- 质量管理 ----
    ("statistics", "waste_main_analysis_list"): _waste_main_list,
    ("statistics", "waste_main_analysis_charts"): _waste_main_charts,
    ("waste_detail", "list"): _waste_detail_list,
    ("purchase_inspection_report", "purchase_inspection_list"): _purchase_inspection_list,
    ("inspection_random_analysis", "inspection_random_list"): _inspection_random_list,
    ("scrap_rework_analysis", "list"): _scrap_rework_list,
    # ---- 售后管理（恒空） ----
    ("statistics", "new_work_order_total_use_time_list"): _new_work_order_total_use_time_list,
    ("statistics", "new_work_order_total_use_time_statistics"): _new_work_order_total_use_time_statistics,
    ("statistics", "new_work_order_user_analysis_list"): _new_work_order_user_analysis_list,
    ("statistics", "new_work_order_customer_analysis_list"): _new_work_order_customer_analysis_list,
    # ---- 经营报表 ----
    ("stock_item", "get_purchase_stock_in_bill_item"): _get_purchase_stock_in_bill_item,
    ("stock_item", "get_contract_stock_out_bill_item"): _get_contract_stock_out_bill_item,
    ("sale_account_statement", "list_by_filter_view"): _sale_account_statement_list,
    ("customer_contract_item", "list_by_filter_view"): _customer_contract_item_list,
    ("produce_bill_cost", "statistics_by_filter_view"): _produce_bill_cost_statistics,
    ("produce_bill_cost", "list_by_filter_view"): _produce_bill_cost_list,
    ("outsource_produce_bill_cost", "statistics_by_filter_view"): _outsource_produce_bill_cost_statistics,
    ("outsource_produce_bill_cost", "list_by_filter_view"): _outsource_produce_bill_cost_list,
    ("goods_stock_out_analysis", "list"): _goods_stock_out_analysis_list,
    # ---- 人力薪酬 ----
    ("salary_report", "list_by_filter_view"): _salary_report_list,
    ("after_sale_salary_report", "list_by_filter_view"): _after_sale_salary_list,
    ("clock_in", "report_forms"): _clock_in_report_forms,
}
