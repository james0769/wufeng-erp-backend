# -*- coding: utf-8 -*-
"""生产排产域（production_scheduling）：/ProductionScheduling 排产甘特页数据端点（甘特图后端）。
依据：deep\\20-生产排产甘特.md + 实测样例 _gen_tmp\\kgd_reverse\\_work\\M12\\*.json：
  - list_by_filter_view：甘特行（加工单粒度，行内嵌 craft_list 供排产详情弹窗/工序甘特，字段对齐 api_list7.json）
  - production_scheduling_statistics：6 状态卡片 + 已逾期 + 数量柱组 + 报工饼图（对齐 api_stats3.json）
  - edit_produce_bill：主表拖拽/单元格改期 → 写回 produce_bills.start/end_produce_date（deep\\20 §2.4，秒级 HH:00:00/HH:59:59）
  - edit_produce_bill_craft：排产详情弹窗工序改期 → 写回 produce_bill_crafts.plan_start/plan_end（deep\\20 §2.7）
种子口径：不新增种子文件，数据现算自 produce_bills.json（431 行，导出转换扁平结构）+ produce_bill_crafts.json（2125 行）。
计划起止字段缺失时按报告口径【推导】：单据开始←start_produce_date || created_at；结束←end_produce_date ||
交货日 23:59:59 || 开始+7 天；工序窗口缺省 = 所属加工单窗口（api_list7 实测形态：同单工序共享计划工期）。
推导只在读取装饰阶段合成（不改种子文件本体）；edit_* 落库后真实字段优先。
另注册 ("filter_view","list")：排产视图页签（type=24）数据源——种子无 type24 视图时内存合成默认"全部视图"
（api_views24.json 原文，id 794598），不落盘。
"""
import time
import core

# ---------------- 枚举（deep\20 §8；status_tag_class 以 api_list7 实测为准） ----------------
STATUS = {1: "未开始", 2: "生产中", 3: "已完成", 4: "已取消", 5: "已暂停"}
STATUS_KEY = {1: "wait_produce", 2: "in_produce", 3: "finish", 4: "cancel", 5: "pause"}  # 统计卡片 key（api_stats3）
STATUS_TAG_CLASS = {1: "4", 2: "3", 3: "5", 4: "2", 5: "8"}  # 生产中="3" 实测；已完成/取消/暂停为 demo 占位（S_TAG 色系）
CRAFT_STATUS = {1: "未开始", 2: "进行中", 3: "已完成", 4: "已暂停"}
CRAFT_TAG_CLASS = {1: "4", 2: "3", 3: "5", 4: "8"}           # 未开始/进行中/已完成 实测（api_list7），暂停为 demo 占位
CRAFT_STATUS_COLOR = {1: "#a9a9a9", 2: "#FFB800", 3: "#3CB371", 4: "#eb2f96"}  # 后端下发实测（api_list7），暂停为 demo 占位
DAY = 86400

# type=24 默认视图（api_views24.json 实测原文，种子无 type24 视图时内存合成，不写库）
_DEFAULT_VIEW_24 = {
    "id": 794598, "enterprise_id": 16807, "type": 24, "name": "全部视图", "icon_type": 2,
    "fun_user_ids": None, "fun_role_ids": None, "fun_department_ids": None,
    "quick_filter": None, "display_fields": None, "data_filter_condition": None,
    "default_sort_rule": {"start_produce_date": "ascend", "priority_configure": "ascend"},
    "create_user_id": 63097, "order_number": 1, "del": 2,
    "created_at": "2025-12-24 21:10:25", "updated_at": "2025-12-24 21:10:25",
    "not_filter_condition": None, "fieldValueList": [],
    "icon_type_color_hex": "#00A578", "type_name": "生产排产",
}

# ---------------- 存取（与 produce.py 共享同名单例） ----------------
_BILLS = _CRAFTS = None

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

# ---------------- 数值/时间工具 ----------------
def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0

def _n0(x):
    """统计求和字符串：整数不带小数（api_stats3 "1651" 风格）。"""
    f = _f(x)
    r = round(f)
    return str(int(r)) if abs(f - r) < 1e-6 else ("%.2f" % f)

def _n8(x):
    """8 位小数字符串（api_list7 produce_task_total_num 风格）。"""
    return "%.8f" % _f(x)

_DT_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%d")

def _parse(v):
    s = str(v or "").strip()
    if not s:
        return None
    for f in _DT_FMTS:
        try:
            return time.strptime(s, f)
        except ValueError:
            continue
    return None

def _ts(v):
    p = _parse(v)
    return time.mktime(p) if p else None

def _hour_start(v):
    """规范为小时起点 "YYYY-MM-DD HH:00:00"（deep\20 §2.4 YSY·START；接受 HH / HH:00:00 / 全格式 / 纯日期）。"""
    p = _parse(v)
    return time.strftime("%Y-%m-%d %H:00:00", p) if p else None

def _hour_end(v):
    """规范为小时终点 "YYYY-MM-DD HH:59:59"（deep\20 §2.4 YSY·END）。"""
    p = _parse(v)
    return time.strftime("%Y-%m-%d %H:59:59", p) if p else None

# ---------------- 计划工期推导（种子缺字段时的合成派生口径，不改种子本体） ----------------
def _bill_plan(bill):
    """甘特行计划工期（deep\20 §2.2 直接读 start_produce_date/end_produce_date）。
    推导：开始←start_produce_date||created_at||start_time||今天；结束←end_produce_date||交货日 23:59:59||开始+7天；
    保证 end>start（不足 1 小时按 +1 小时兜底）。"""
    s = _hour_start(bill.get("start_produce_date") or bill.get("created_at")
                    or bill.get("start_time") or time.strftime("%Y-%m-%d %H"))
    e = _hour_end(bill.get("end_produce_date"))
    if not e:
        d = _parse(bill.get("delivery_date"))
        if d:
            e = time.strftime("%Y-%m-%d 23:59:59", d)
    if not e and s:
        e = time.strftime("%Y-%m-%d %H:59:59", time.localtime(_ts(s) + 7 * DAY))
    if s and e and _ts(e) <= _ts(s):
        e = time.strftime("%Y-%m-%d %H:59:59", time.localtime(_ts(s) + 3600))
    return s, e

def _craft_plan(craft, win):
    """工序计划窗口：真实 plan_start/plan_end 优先，缺省=加工单窗口（api_list7 实测：同单工序共享计划工期）。"""
    s = _hour_start(craft.get("plan_start")) or win[0]
    e = _hour_end(craft.get("plan_end")) or win[1]
    if _ts(e) <= _ts(s):
        e = time.strftime("%Y-%m-%d %H:59:59", time.localtime(_ts(s) + 3600))
    return s, e

def _overdue(end, status, now_ts):
    """逾期判定：非(已完成/已取消) 且计划结束已过（api_list7 实测口径：生产中+计划期过=逾期）。"""
    et = _ts(end)
    return bool(et and status not in (3, 4) and et < now_ts)

def _overdue_day(end, now_ts):
    et = _ts(end)
    return round((now_ts - et) / DAY, 1) if et and et < now_ts else 0

# ---------------- 行装饰（字段对齐 api_list7.json） ----------------
def _crafts_index():
    idx = {}
    for c in _crafts().all():
        idx.setdefault(str(c.get("produce_bill_id")), []).append(c)
    for lst in idx.values():
        lst.sort(key=lambda c: (c.get("order_number") or 0, str(c.get("id"))))
    return idx

def _craft_done_num(c):
    """工序完成数：真实 total_valid_num 优先；种子无该字段时按 已完成工序计计划数（与种子 task_progress 口径一致）。"""
    if c.get("total_valid_num") not in (None, ""):
        return _f(c["total_valid_num"])
    return _f(c.get("num")) if c.get("status") == 3 else 0.0

def _decorate_craft(c, bill, win, now_ts):
    st = c.get("status")
    s, e = _craft_plan(c, win)
    d = dict(c)
    num = c.get("num")
    d.setdefault("num", "0.00000000")
    d.setdefault("produce_bill_craft_num", num)
    d.setdefault("total_valid_num", _n8(_craft_done_num(c)))
    d["plan_start"], d["plan_end"] = s, e
    d["status_name"] = CRAFT_STATUS.get(st, "")
    d["status_tag_class"] = CRAFT_TAG_CLASS.get(st, "")
    d["status_color"] = CRAFT_STATUS_COLOR.get(st, "")  # 后端下发（api_list7 实测）
    d["goods_name"] = c.get("goods_name") or bill.get("goods_name")
    d["wait_produce_num"] = _n0(max(0.0, _f(num) - _f(d["total_valid_num"])))
    d["is_overdue"] = 1 if _overdue(e, st, now_ts) else 2  # 工序行 1/2（BOOL，api_list7 实测）
    d["overdue_day"] = _overdue_day(e, now_ts)
    d.setdefault("dispatch_list", [])
    total = _f(d.get("produce_bill_craft_num"))
    d["progress"] = int(_f(d["total_valid_num"]) / total * 100) if total > 0 else 0  # 弹窗工序进度圆
    return d

def _decorate(bill, crafts, now_ts):
    """甘特行：保留种子原有扁平字段，叠加 api_list7 形态的装饰字段。"""
    r = dict(bill)
    st = bill.get("status")
    s, e = _bill_plan(bill)
    r["start_produce_date"], r["end_produce_date"] = s, e  # 规范为 HH:00:00/HH:59:59 全格式（派生不改库）
    total_task = sum(_f(c.get("num")) for c in crafts)
    fin_task = sum(_craft_done_num(c) for c in crafts)
    fin_c = len([c for c in crafts if c.get("status") == 3])
    cur = None
    for c in crafts:  # 当前工序=第一个进行中，否则第一个未开始（deep\01 §1.5）
        if c.get("status") == 2:
            cur = c.get("craft_name")
            break
    if not cur:
        for c in crafts:
            if c.get("status") == 1:
                cur = c.get("craft_name")
                break
    r["status_name"] = STATUS.get(st, "")
    r["status_tag_class"] = STATUS_TAG_CLASS.get(st, "")
    r.setdefault("num", "0.00000000")
    r.setdefault("actual_num", "0.00000000")
    r.setdefault("stock_in_num", "0.00000000")
    r.setdefault("requisition_status_name", "")
    r.setdefault("work_type_name", "")
    r["current_craft_name"] = cur or bill.get("current_craft_name")
    r["finish_craft_count"] = fin_c
    r["all_craft_count"] = len(crafts)
    r["finish_craft_percent"] = int(fin_c / len(crafts) * 100) if crafts else 0  # int（api_list7：50/20/0）
    # 甘特进度分子/分母（deep\20 §2.2：progress = floor(finish_produce_task_total_num/produce_task_total_num*100)）
    r["produce_task_total_num"] = _n8(total_task)
    r["finish_produce_task_total_num"] = _n8(fin_task)
    r["progress"] = int(fin_task / total_task * 100) if total_task > 0 else 0
    # 逾期判定（证据优先级）：真实 end_produce_date（含 edit 落库值）现算 > 导出表 has_overdue 实测标记 > 派生窗口现算
    real_end = bill.get("end_produce_date")
    if real_end not in (None, ""):
        r["is_overdue"] = _overdue(e, st, now_ts)  # e 已= 规范后的真实结束
    elif bill.get("has_overdue") is not None:
        r["is_overdue"] = bool(str(bill.get("has_overdue")) == "1")
    else:
        r["is_overdue"] = _overdue(e, st, now_ts)
    r["overdue_day"] = _overdue_day(e, now_ts) if r["is_overdue"] else 0
    r["working_start_minutes_name"] = bill.get("total_working_time") or ""  # 已开工时间列
    dd = _parse(bill.get("delivery_date"))
    if dd:
        left = int((_ts(time.strftime("%Y-%m-%d", dd)) - _ts(time.strftime("%Y-%m-%d"))) / DAY)
        r["delivery_countdown"] = str(max(0, left))
    else:
        r.setdefault("delivery_countdown", "")
    r.setdefault("sort_num", None)
    r["goods"] = {"id": bill.get("goods_id"), "code": bill.get("goods_code"),
                  "name": bill.get("goods_name"), "standard": bill.get("goods_standard")}
    pid = bill.get("priority_configure_id")
    r["priority_configure"] = ({"id": pid, "name": bill.get("priority_configure_name") or None,
                                "color": None} if pid is not None else None)
    r.setdefault("priority_configure_id", None)
    if bill.get("create_user_name"):
        r.setdefault("create_user", {"id": bill.get("create_user_id"), "real_name": bill["create_user_name"]})
    r["craft_list"] = [_decorate_craft(c, bill, (s, e), now_ts) for c in crafts]
    return r

# ---------------- 筛选（deep\20 §1.1-§1.3 字段） ----------------
def _filter_rows(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    pds, pde = _hour_start(body.get("produce_date_start")), _hour_end(body.get("produce_date_end"))
    st_f = body.get("status")
    ov = s("is_overdue")
    kw = s("production_scheduling_keyword")
    pc = [x for x in (body.get("pub_craft") or [])] if isinstance(body.get("pub_craft"), list) else []
    out = []
    for r in rows:
        if st_f not in (None, "", "all"):  # 卡片状态过滤（6=已逾期走 is_overdue）
            sts = st_f if isinstance(st_f, list) else [st_f]
            if str(r.get("status")) not in [str(x) for x in sts]:
                continue
        if ov == "1" and not r.get("is_overdue"):
            continue
        if pds and (r.get("end_produce_date") or "") < pds:  # 计划工期导航：窗口重叠（左出界剔除）
            continue
        if pde and (r.get("start_produce_date") or "") > pde:  # 右出界剔除
            continue
        if kw:  # 支持加工单号/商品名/合同编号/生产计划名查询（deep\20 §1.1 tooltip 逐字）
            hay = " ".join(str(r.get(k) or "") for k in
                           ("code", "goods_name", "contract_code", "produce_schedule_code", "produce_schedule_name"))
            if kw not in hay:
                continue
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        skip = False
        for k in ("goods_name", "goods_code", "goods_standard", "contract_code", "project_code",
                  "produce_schedule_code", "produce_schedule_name", "pause_reason", "cancel_reason"):
            if s(k) and s(k) not in str(r.get(k) or ""):
                skip = True
                break
        if skip:
            continue
        if s("priority_configure_id") and str(r.get("priority_configure_id") or "") != s("priority_configure_id"):
            continue
        if pc and str(r.get("current_craft_name") or "") not in [str(x) for x in pc]:  # 当前工序（视图筛选）
            continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out

# ---------------- 排序（视图排序枚举 deep\20 §1.3；默认=default_sort_rule） ----------------
def _sk(v):
    return (0, v, "") if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, 0, str(v))

def _sort_value(field):
    g = {
        "code": lambda r: str(r.get("code") or ""),
        "goods_code": lambda r: str(r.get("goods_code") or ""),
        "finish_craft_percent": lambda r: r.get("finish_craft_percent") or 0,
        "delivery_date": lambda r: str(r.get("delivery_date") or ""),
        "priority_configure": lambda r: r.get("priority_configure_id") or 0,
        "start_produce_date": lambda r: str(r.get("start_produce_date") or ""),
        "end_produce_date": lambda r: str(r.get("end_produce_date") or ""),
        "created_at": lambda r: str(r.get("created_at") or ""),
        "start_time": lambda r: str(r.get("start_time") or ""),
        "end_time": lambda r: str(r.get("end_time") or ""),
        "num": lambda r: _f(r.get("num")),
        "status": lambda r: r.get("status") or 0,
        "create_user_id": lambda r: r.get("create_user_id") or 0,
    }.get(field)
    return g

def _sort_rows(rows, body):
    sf = str(body.get("sortField") or "").strip()
    g = _sort_value(sf) if sf else None
    if g:
        desc = str(body.get("sortOrder") or "ascend").lower() in ("descend", "desc")
        return sorted(rows, key=lambda r: _sk(g(r)), reverse=desc)
    # 默认视图排序：计划开始升序 + 优先级升序（api_views24 default_sort_rule）；缺计划开始（不会发生，已推导）置尾
    return sorted(rows, key=lambda r: (str(r.get("start_produce_date") or "9999"),
                                       _f(r.get("priority_configure_id"))))

# ---------------- 列表 / 统计 ----------------
def _build_rows(body):
    """装饰全量 → 筛选 → 排序（派生字段参与筛选排序，故先装饰后过滤）。"""
    now_ts = time.time()
    idx = _crafts_index()
    rows = [_decorate(b, idx.get(str(b.get("id")), []), now_ts) for b in _bills().all()]
    rows = _filter_rows(rows, body)
    return _sort_rows(rows, body)

def _paged(store, rows, body):
    """分页复用 core.paged_ok(store.page(body))：临时换入已排序装饰行（同 produce.py 手法）。"""
    backup = store.rows
    store.rows = rows
    try:
        return core.paged_ok(store.page(body))
    finally:
        store.rows = backup

def _list(body, session, strict):
    if strict and body.get("filter_view_id") in (None, ""):  # 必带有效 filter_view_id（deep\20 §2.6，空→参数校验错误）
        return core.fail("参数校验错误")
    rows = _build_rows(body)
    return _paged(_bills(), rows, body)

def _list_by_filter_view(body, session):
    return _list(body, session, strict=True)

def _list_lenient(body, session):
    return _list(body, session, strict=False)

def _statistics(body, session, strict):
    if strict and body.get("filter_view_id") in (None, ""):
        return core.fail("参数校验错误")
    rows = _build_rows(body)
    sc = {k: 0 for k in STATUS_KEY.values()}
    overdue = 0
    num_sum = actual_sum = stock_in_sum = valid_sum = waste_sum = 0.0
    for r in rows:
        k = STATUS_KEY.get(r.get("status"))
        if k:
            sc[k] += 1
        if r.get("is_overdue"):
            overdue += 1
        num_sum += _f(r.get("num"))
        actual_sum += _f(r.get("actual_num"))
        stock_in_sum += _f(r.get("stock_in_num"))
        valid_sum += _f(r.get("finish_produce_task_total_num"))  # 种子无报工记录，良品口径=完成生产任务数（推导）
        waste_sum += sum(_f(c.get("waste_num")) for c in r.get("craft_list") or [])
    total_report = valid_sum + waste_sum
    vr = _f(valid_sum) / total_report * 100 if total_report else 0.0
    return core.ok({
        "status_count": sc,
        "overdue_count": {"overdue": overdue},
        "stock_in_num_sum": _n0(stock_in_sum),
        "num_sum": _n0(num_sum),
        "actual_num_sum": _n0(actual_sum),
        "report_count": {
            "valid_num_sum": _n0(valid_sum),
            "waste_num_sum": _n0(waste_sum),
            "total_report_num": _n0(total_report),
            "valid_rate_percent": "%.2f%%" % vr,
            "waste_rate_percent": "%.2f%%" % (100 - vr if total_report else 0.0),
        },
    })

def _statistics_strict(body, session):
    return _statistics(body, session, strict=True)

def _statistics_lenient(body, session):
    return _statistics(body, session, strict=False)

# ---------------- 改期（拖拽/单元格编辑，deep\20 §2.4 / §2.7） ----------------
def _edit_produce_bill(body, session):
    """主表拖拽改期：{id, start_produce_date, end_produce_date}（小时精度，HH:00:00/HH:59:59）。
    校验：结束<=开始 → "计划结束时间不能早于计划开始时间"（文案逐字，deep\20 §2.4）。"""
    bill = _bills().get(body.get("id"))
    if not bill:
        return core.fail("数据不存在")
    s, e = _hour_start(body.get("start_produce_date")), _hour_end(body.get("end_produce_date"))
    if not s or not e:
        return core.fail("参数校验错误")
    if _ts(e) <= _ts(s):
        return core.fail("计划结束时间不能早于计划开始时间")
    _bills().update(bill["id"], {"start_produce_date": s, "end_produce_date": e})
    return core.ok()

def _edit_produce_bill_craft(body, session):
    """排产详情弹窗工序改期：{id, produce_bill_id, plan_start, plan_end}（小时精度）→ 写回工序计划时间。"""
    c = _crafts().get(body.get("id"))
    if not c:
        return core.fail("数据不存在")
    s, e = _hour_start(body.get("plan_start")), _hour_end(body.get("plan_end"))
    if not s or not e:
        return core.fail("参数校验错误")
    if _ts(e) <= _ts(s):
        return core.fail("计划结束时间不能早于计划开始时间")
    _crafts().update(c["id"], {"plan_start": s, "plan_end": e})
    return core.ok()

# ---------------- 排产视图页签（filter_view type=24，deep\20 §1.1/§2.6） ----------------
def _fv_list(body, session):
    """通用 filter_view/list：按 type 过滤；type=24 无视图时合成默认"全部视图"（内存合成，不落盘）。
    其余模块未注册本路由（goods 域走 list_by_permission），此处为排产页签兜底。"""
    vtype = body.get("type")
    rows = []
    if vtype in (None, ""):
        rows = [dict(r) for r in core.Store("filter_views").all() if r.get("del", 2) == 2]
    else:
        rows = [dict(r) for r in core.Store("filter_views").all()
                if r.get("type") is not None and str(r.get("type")) == str(vtype) and r.get("del", 2) == 2]
    if str(vtype) == "24" and not rows:
        rows = [dict(_DEFAULT_VIEW_24)]
    return core.ok(rows, count=len(rows))

ROUTES = {
    # 排产甘特列表（deep\20 §2.6；data:{list,total} 分页契约同本仓库其余 list 端点）
    ("production_scheduling", "list_by_filter_view"): _list_by_filter_view,
    ("production_scheduling", "list"): _list_lenient,
    # 汇总卡片 + 图表（api_stats3 结构）
    ("production_scheduling", "production_scheduling_statistics"): _statistics_strict,
    ("production_scheduling", "statistics"): _statistics_lenient,
    # 拖拽改期（deep\20 §2.4/§2.7）
    ("production_scheduling", "edit_produce_bill"): _edit_produce_bill,
    ("production_scheduling", "edit_produce_bill_craft"): _edit_produce_bill_craft,
    # 排产视图页签（type=24）
    ("filter_view", "list"): _fv_list,
}
