# -*- coding: utf-8 -*-
"""B13 报表/统计域（智能报表数据接口，demo 级）。
依据：api_dump/intelli/ 实测样例（list/detail/produce_bill_status_count/sales_sale_report/
produce_report_work_record_statistics/produce_report_work_record_whs/after_sales_profile_statistic/
produce_anomaly_statistics）+ deep\\04-报表图表体系.md + deep\\27-报表中心页面补全.md。

覆盖：
- /intelli_report 管理：list（首次访问落库 11 个真实仪表盘布局）/ detail（含 fieldValueList）/ add（简化）/ edit / del
- 图表数据（数据从 Store 直算，键名与实测样例逐字一致）：
  produce_bill_status_count（5 态计数 + 后端下发色值 #757575/#fa8c16/#52c41a/#f5222d/#eb2f96，即 PRODUCE_BILL_STATUS 枚举色）
  produce_report_work_record_statistics（计件报工简报，8 字段 8 位小数字符串）
  produce_report_work_record_working_hours_statistics（计时报工简报，同构）
  sales_sale_report（销售概要卡六指标）
  sales_every_month_sale_money（年度销售额：contracts 金额按月聚合）
  goods_stock_summary（库存概要：goods_stocks 汇总；demo 自有端点，无原站样例）
  stock_item_in_statistics / stock_item_out_statistics（入/出库流水统计，按商品聚合 Top10）
  produce_anomaly_statistics（逾期加工单/工序数）
  after_sales_profile_statistic（售后数据简报，无售后种子 → 样例零值）
- /statistics/waste_trend_analysis：不良品趋势（按 wastes/报工不良记录按日聚合，折线/面积/百分比面积三态同源）
- 占位返回空：/report_work_analysis/list、after_sales_user_order_line、after_sales_trend 族（服务工单走势空序列）
"""
import datetime
from decimal import Decimal

import core

# ---------------- 11 个真实仪表盘布局（api_dump/intelli/list.json 逐字，首次访问落库） ----------------
SEED_REPORTS = [
    {
        "id": 163393, "enterprise_id": 16807, "name": "销售简报",
        "profile": "多维度展示销售统计数据。销售人员业绩、客户新增、跟单情况等数据一目了然",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":1,\"w\":12,\"h\":2,\"type\":27,\"i\":\"0\",\"moved\":false},{\"x\":6,\"y\":3,\"w\":6,\"h\":6,\"type\":12,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":3,\"w\":6,\"h\":6,\"type\":32,\"i\":\"2\",\"moved\":false},{\"x\":4,\"y\":15,\"w\":4,\"h\":6,\"type\":16,\"i\":\"3\",\"moved\":false},{\"x\":8,\"y\":15,\"w\":4,\"h\":6,\"type\":15,\"i\":\"4\",\"moved\":false},{\"x\":6,\"y\":9,\"w\":6,\"h\":6,\"type\":13,\"i\":\"5\",\"moved\":false},{\"x\":0,\"y\":9,\"w\":6,\"h\":6,\"type\":33,\"i\":\"6\",\"moved\":false},{\"x\":0,\"y\":15,\"w\":4,\"h\":6,\"type\":14,\"i\":\"7\",\"moved\":false},{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"8\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163394, "enterprise_id": 16807, "name": "生产简报",
        "profile": "多角度展示车间生产情况。加工单状态、工人生产效率、工序不良品等情况直观展示",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":12,\"h\":4,\"type\":42,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":5,\"w\":4,\"h\":6,\"type\":11,\"i\":\"2\",\"moved\":false},{\"x\":4,\"y\":5,\"w\":8,\"h\":6,\"type\":39,\"i\":\"3\",\"moved\":false},{\"x\":0,\"y\":11,\"w\":6,\"h\":6,\"type\":17,\"i\":\"4\",\"moved\":false},{\"x\":6,\"y\":11,\"w\":6,\"h\":6,\"type\":18,\"i\":\"5\",\"moved\":false},{\"x\":0,\"y\":17,\"w\":6,\"h\":6,\"type\":19,\"i\":\"6\",\"moved\":false},{\"x\":6,\"y\":17,\"w\":6,\"h\":6,\"type\":20,\"i\":\"7\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163395, "enterprise_id": 16807, "name": "服务简报",
        "profile": "监控服务工单进度，展示优秀员工榜单，工单待办事项和营收数据一览无余",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":12,\"h\":5,\"type\":36,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":6,\"w\":10,\"h\":2,\"type\":28,\"i\":\"2\",\"moved\":false},{\"x\":10,\"y\":6,\"w\":2,\"h\":2,\"type\":29,\"i\":\"3\",\"moved\":false},{\"x\":0,\"y\":8,\"w\":2,\"h\":11,\"type\":37,\"i\":\"4\",\"moved\":false},{\"x\":2,\"y\":8,\"w\":10,\"h\":8,\"type\":38,\"i\":\"5\",\"moved\":false},{\"x\":2,\"y\":16,\"w\":5,\"h\":6,\"type\":34,\"i\":\"6\",\"moved\":false},{\"x\":7,\"y\":16,\"w\":5,\"h\":6,\"type\":35,\"i\":\"7\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163396, "enterprise_id": 16807, "name": "服务工单报表",
        "profile": "展示服务工单各阶段执行进度，统计备件和服务使用情况",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":12,\"h\":3,\"type\":30,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":4,\"w\":10,\"h\":7,\"type\":2,\"i\":\"2\",\"moved\":false},{\"x\":10,\"y\":4,\"w\":2,\"h\":4,\"type\":5,\"i\":\"3\",\"moved\":false},{\"x\":10,\"y\":8,\"w\":2,\"h\":4,\"type\":6,\"i\":\"4\",\"moved\":false},{\"x\":0,\"y\":11,\"w\":10,\"h\":6,\"type\":22,\"i\":\"5\",\"moved\":false},{\"x\":10,\"y\":20,\"w\":2,\"h\":4,\"type\":7,\"i\":\"6\",\"moved\":false},{\"x\":5,\"y\":17,\"w\":5,\"h\":7,\"type\":23,\"i\":\"7\",\"moved\":false},{\"x\":0,\"y\":17,\"w\":5,\"h\":7,\"type\":24,\"i\":\"8\",\"moved\":false},{\"x\":10,\"y\":12,\"w\":2,\"h\":4,\"type\":8,\"i\":\"9\",\"moved\":false},{\"x\":10,\"y\":16,\"w\":2,\"h\":4,\"type\":9,\"i\":\"10\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163397, "enterprise_id": 16807, "name": "服务业务计划",
        "profile": "展示工单日历和客户位置分布，查看每日服务工单服务",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":12,\"h\":12,\"type\":3,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":13,\"w\":12,\"h\":10,\"type\":4,\"i\":\"2\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163398, "enterprise_id": 16807, "name": "服务客户报表",
        "profile": "展示客户数量和客户分布情况，统计客户满意度和服务排行",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":4,\"y\":1,\"w\":8,\"h\":6,\"type\":10,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":4,\"h\":6,\"type\":31,\"i\":\"2\",\"moved\":false},{\"x\":0,\"y\":7,\"w\":6,\"h\":8,\"type\":26,\"i\":\"3\",\"moved\":false},{\"x\":6,\"y\":7,\"w\":6,\"h\":8,\"type\":43,\"i\":\"4\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163399, "enterprise_id": 16807, "name": "服务人员报表",
        "profile": "展示服务人员服务情况，定位服务人员位置",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":12,\"h\":1,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":1,\"w\":12,\"h\":8,\"type\":40,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":9,\"w\":12,\"h\":8,\"type\":41,\"i\":\"2\",\"moved\":false},{\"x\":0,\"y\":17,\"w\":12,\"h\":8,\"type\":25,\"i\":\"3\",\"moved\":false}]",
        "type": 1, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163400, "enterprise_id": 16807, "name": "销售简报",
        "profile": "多维度展示销售统计数据。销售人员业绩、客户新增、跟单情况等数据一目了然",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":1,\"h\":2,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":2,\"w\":1,\"h\":6,\"type\":2,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":8,\"w\":1,\"h\":8,\"type\":3,\"i\":\"2\",\"moved\":false},{\"x\":0,\"y\":16,\"w\":1,\"h\":8,\"type\":4,\"i\":\"3\",\"moved\":false},{\"x\":0,\"y\":24,\"w\":1,\"h\":10,\"type\":20,\"i\":\"4\",\"moved\":false}]",
        "type": 2, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163401, "enterprise_id": 16807, "name": "生产简报",
        "profile": "多角度展示车间生产情况。加工单状态、工人生产效率、工序不良品等情况直观展示",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":1,\"h\":2,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":2,\"w\":1,\"h\":10,\"type\":21,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":12,\"w\":1,\"h\":10,\"type\":22,\"i\":\"2\",\"moved\":false},{\"x\":0,\"y\":22,\"w\":1,\"h\":8,\"type\":6,\"i\":\"3\",\"moved\":false},{\"x\":0,\"y\":30,\"w\":1,\"h\":10,\"type\":23,\"i\":\"4\",\"moved\":false}]",
        "type": 2, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 163402, "enterprise_id": 16807, "name": "服务简报",
        "profile": "监控服务工单进度，展示优秀员工榜单，一周工单趋势和营收数据一览无余",
        "fun_json": "{\"3\":{\"user_ids\":[63097],\"role_ids\":[162747]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":1,\"h\":2,\"type\":1,\"i\":\"0\",\"moved\":false},{\"x\":0,\"y\":2,\"w\":1,\"h\":6,\"type\":5,\"i\":\"1\",\"moved\":false},{\"x\":0,\"y\":8,\"w\":1,\"h\":10,\"type\":24,\"i\":\"2\",\"moved\":false},{\"x\":0,\"y\":18,\"w\":1,\"h\":10,\"type\":25,\"i\":\"3\",\"moved\":false},{\"x\":0,\"y\":28,\"w\":1,\"h\":8,\"type\":7,\"i\":\"4\",\"moved\":false}]",
        "type": 2, "del": 2, "updated_at": "2024-05-20 10:38:24", "created_at": "2024-05-20 10:38:24", "data_permission": 3,
    },
    {
        "id": 285825, "enterprise_id": 16807, "name": "工资", "profile": "工资查询",
        "fun_json": "{\"1\":{\"user_ids\":[151310,144814,144823,150360]}}",
        "content_json": "[{\"x\":0,\"y\":0,\"w\":1,\"h\":2,\"type\":1,\"i\":\"60\",\"moved\":false},{\"x\":0,\"y\":2,\"w\":1,\"h\":12,\"type\":27,\"i\":\"75\",\"moved\":false}]",
        "type": 2, "del": 2, "updated_at": "2025-10-26 20:22:37", "created_at": "2025-10-26 20:14:09", "data_permission": 3,
    },
]

# PRODUCE_BILL_STATUS 枚举（deep\01 §1.1）：1 未开始#757575 / 2 生产中#fa8c16 / 3 已完成#52c41a /
# 4 已取消#f5222d / 5 已暂停#eb2f96；接口键名沿用实测样例（wait_produce/in_produce/pause/finish + 补 cancel）
_STATUS_KEYS = {1: ("wait_produce", "#757575"), 2: ("in_produce", "#fa8c16"), 3: ("finish", "#52c41a"),
                4: ("cancel", "#f5222d"), 5: ("pause", "#eb2f96")}

# ---------------- 通用小工具 ----------------
def _dec(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(0)

def _num(v):
    """数值输出：整数值回 int，其余回 float（与样例中 79/1466710/5428658 等整型一致）。"""
    d = _dec(v)
    return int(d) if d == d.to_integral_value() else float(d)

def _f8(v):
    """8 位小数字符串（样例 "2.00000000"）。"""
    return "%.8f" % float(_dec(v))

def _dval(row, *keys):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row.get(k)
    return None

def _pdate(v):
    """取 'YYYY-MM-DD' 前缀；解析失败返回 None。"""
    if v is None:
        return None
    s = str(v)[:10]
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None

def _row_date(row, *keys):
    for k in keys:
        d = _pdate(row.get(k))
        if d:
            return d
    return None

def _alive(row):
    return str(row.get("del", 2)) == "2"

def _in_range(d, start, end):
    return (start is None or d >= start) and (end is None or d <= end)

def _wrap(data):
    """带 enterprise_config_update_time 的成功响应（与实测样例外层一致）。"""
    return {"success": True, "data": data, "enterprise_config_update_time": core.now()}

def _range(body):
    return _pdate(body.get("start_date")), _pdate(body.get("end_date"))

def _week_monday(d):
    return d - datetime.timedelta(days=d.weekday())

def _prev_month(ym):
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)

def _add_months(ym, n):
    y, m = ym
    t = (y * 12 + m - 1) + n
    return (t // 12, t % 12 + 1)

# ---------------- /intelli_report 管理（list/detail/add/edit/del） ----------------
def _reports_store():
    s = core.Store("intelli_reports")
    if not s.rows:
        s.rows = [dict(r) for r in SEED_REPORTS]
        s.flush()
    return s

def _intelli_list(body, session):
    rows = [r for r in _reports_store().all() if _alive(r)]
    t = body.get("type")
    if t not in (None, ""):
        rows = [r for r in rows if str(r.get("type")) == str(t)]
    return _wrap(rows)

def _intelli_detail(body, session):
    row = _reports_store().get(body.get("id"))
    if not row:
        return core.fail("404")
    d = dict(row)
    d["fieldValueList"] = []
    return _wrap(d)

def _intelli_add(body, session):
    """add 简化：{name, profile, content_json?, fun_json?, type?}（原站校验报表名/简介必填）。"""
    name = str(body.get("name") or "").strip()
    profile = str(body.get("profile") or "").strip()
    if not name:
        return core.fail("请输入报表名")
    if not profile:
        return core.fail("请输入报表简介")
    row = {
        "enterprise_id": (session or {}).get("enterprise_id", 16807),
        "name": name, "profile": profile,
        "fun_json": str(body.get("fun_json") or "{}"),
        "content_json": str(body.get("content_json") or "[]"),
        "type": int(body.get("type") or 1), "del": 2, "data_permission": int(body.get("data_permission") or 3),
    }
    return core.ok(_reports_store().add(row))

def _intelli_edit(body, session):
    s = _reports_store()
    patch = {k: body[k] for k in ("name", "profile", "fun_json", "content_json", "type", "data_permission") if k in body}
    row = s.update(body.get("id"), patch) if patch else s.get(body.get("id"))
    if not row:
        return core.fail("404")
    return core.ok(row)

def _intelli_del(body, session):
    _reports_store().remove(body.get("id"))
    return core.ok()

# ---------------- 图表数据端点 ----------------
def _produce_bill_status_count(body, session):
    """加工单状态统计饼图：按 produce_bills 统计 5 态计数，色值后端下发（样例 produce_bill_status_count.json）。"""
    start, end = _range(body)
    counts = {v: 0 for v in _STATUS_KEYS}
    for r in core.Store("produce_bills").all():
        if not _alive(r):
            continue
        d = _row_date(r, "created_at")
        if (start or end) and d and not _in_range(d, start, end):
            continue
        try:
            st = int(r.get("status") or 0)
        except Exception:
            st = 0
        if st in counts:
            counts[st] += 1
    # 键序与样例一致：wait_produce/in_produce/pause/finish，新增第 5 态 cancel，末尾 all_produce
    data = {}
    for st in (1, 2, 5, 3, 4):
        key, color = _STATUS_KEYS[st]
        data[key] = {"count": counts[st], "color": color}
    data["all_produce"] = {"count": sum(counts.values())}
    return _wrap(data)

def _report_rows():
    """报工统计的数据源：优先 B11 的 report_work_records（真实字段 report_num/valid_money），
    兼容 report_works；否则退回 produce_bill_crafts（生产任务）。"""
    for name in ("report_work_records", "report_works"):
        s = core.Store(name)
        if s.rows:
            return s.rows
    return core.Store("produce_bill_crafts").all()

def _bucket_stats(body, session, piece=True):
    """今/周/月/上月 四桶聚合：计件=报工数+工资(良品数×单价)；计时=工时(秒)+工资(时长/3600×单价)。"""
    today = datetime.date.today()
    cur_ym, pre_ym = (today.year, today.month), None
    pre_ym = _prev_month(cur_ym)
    week_mon = _week_monday(today)
    num = {"today": Decimal(0), "week": Decimal(0), "month": Decimal(0), "pre_month": Decimal(0)}
    money = {"today": Decimal(0), "week": Decimal(0), "month": Decimal(0), "pre_month": Decimal(0)}
    for r in _report_rows():
        if not _alive(r):
            continue
        d = _row_date(r, "report_time", "updated_at", "created_at", "end_time", "start_time")
        if not d:
            continue
        # 桶为嵌套关系（与样例一致）：今天的报工同时计入 周/月 桶
        buckets = []
        if d == today:
            buckets.append("today")
        if _week_monday(d) == week_mon:
            buckets.append("week")
        if (d.year, d.month) == cur_ym:
            buckets.append("month")
        elif (d.year, d.month) == pre_ym:
            buckets.append("pre_month")
        if not buckets:
            continue
        try:
            mode = int(r.get("price_mode") or 1)
        except Exception:
            mode = 1
        unit = _dec(_dval(r, "craft_unit_money", "unit_money"))
        for b in buckets:
            if piece:
                valid, waste = _dec(_dval(r, "report_num", "valid_num", "num")), _dec(_dval(r, "waste_num"))
                vm = _dval(r, "valid_money")
                # 工资优先取报工记录自带 valid_money，缺省按计件公式 良品数×单价（deep\18）
                m = _dec(vm) if vm is not None else (unit * _dec(_dval(r, "valid_num", "num")) if mode == 1 else Decimal(0))
                num[b] += valid + waste
                money[b] += m
            else:
                secs = _seconds(r)
                money[b] += unit * (Decimal(secs) / Decimal(3600)) if mode == 2 and secs else Decimal(0)
                num[b] += Decimal(secs) // 3600                          # 工时取整（样例为 int）
    return num, money

def _seconds(r):
    if r.get("working_minutes") not in (None, ""):   # 报工记录自带报工时长（分钟）
        try:
            return int(float(r.get("working_minutes")) * 60)
        except Exception:
            pass
    fmt = "%Y-%m-%d %H:%M:%S"
    a, b = r.get("report_start_time") or r.get("start_time"), r.get("report_end_time") or r.get("end_time")
    try:
        return int((datetime.datetime.strptime(str(b), fmt) - datetime.datetime.strptime(str(a), fmt)).total_seconds())
    except Exception:
        return 0

def _produce_report_work_record_statistics(body, session):
    """计件报工简报（样例 produce_report_work_record_statistics.json，8 字段全 8 位小数字符串）。"""
    num, money = _bucket_stats(body, session, piece=True)
    return _wrap({
        "today_report_num": _f8(num["today"]), "today_money": _f8(money["today"]),
        "week_report_num": _f8(num["week"]), "week_money": _f8(money["week"]),
        "month_report_num": _f8(num["month"]), "month_money": _f8(money["month"]),
        "pre_month_report_num": _f8(num["pre_month"]), "pre_month_money": _f8(money["pre_month"]),
    })

def _produce_report_work_record_working_hours_statistics(body, session):
    """计时报工简报（样例 produce_report_work_record_whs.json：工时 int、金额 8 位小数字符串）。"""
    hours, money = _bucket_stats(body, session, piece=False)
    return _wrap({
        "today_work_hours": int(hours["today"]), "today_money": _f8(money["today"]),
        "week_work_hours": int(hours["week"]), "week_money": _f8(money["week"]),
        "month_work_hours": int(hours["month"]), "month_money": _f8(money["month"]),
        "pre_month_work_hours": int(hours["pre_month"]), "pre_month_money": _f8(money["pre_month"]),
    })

def _sales_sale_report(body, session):
    """销售概要卡（样例 sales_sale_report.json 六指标，数值型）：合同数/金额、回款、欠款按时间范围统计。"""
    start, end = _range(body)
    contracts = [r for r in core.Store("contracts").all() if _alive(r)]
    if start or end:
        contracts = [r for r in contracts if _row_date(r, "created_at") and _in_range(_row_date(r, "created_at"), start, end)]
    money = sum((_dec(_dval(r, "money", "sale_total")) for r in contracts), Decimal(0))
    income = sum((_dec(_dval(r, "income_money")) for r in contracts), Decimal(0))
    debt = sum((_dec(_dval(r, "debt_money")) for r in contracts), Decimal(0))
    customers = [r for r in core.Store("customers").all() if _alive(r)]
    if start or end:
        customers = [r for r in customers if _row_date(r, "created_at") and _in_range(_row_date(r, "created_at"), start, end)]
    return core.ok({
        "customer_count": len(customers),   # 新增客户数（范围内新建客户）
        "follow_count": 0,                  # 客户跟进次数（无跟进种子，占位 0）
        "contract_num": len(contracts),
        "contract_money": _num(money),
        "income": _num(income),
        "expense": 0,
        "now_debt": _num(debt),
    })

def _sales_every_month_sale_money(body, session):
    """年度销售额：contracts 金额按月聚合（无实测样例，键名取 date_name/money 与趋势图族一致）。"""
    start, end = _range(body)
    today = datetime.date.today()
    if start and end:
        first, last = (start.year, start.month), (end.year, end.month)
    else:
        first, last = _add_months((today.year, today.month), -11), (today.year, today.month)
    agg = {}
    for r in core.Store("contracts").all():
        if not _alive(r):
            continue
        d = _row_date(r, "created_at")
        if not d:
            continue
        ym = (d.year, d.month)
        if first <= ym <= last:
            agg[ym] = agg.get(ym, Decimal(0)) + _dec(_dval(r, "money", "sale_total"))
    data, cur = [], first
    while True:
        data.append({"date_name": "%04d-%02d" % cur, "money": _num(agg.get(cur, Decimal(0)))})
        if cur == last:
            break
        cur = _add_months(cur, 1)
    return core.ok(data)

def _goods_stock_summary(body, session):
    """库存概要（demo 自有端点，无原站样例）：goods_stocks 汇总。"""
    rows = [r for r in core.Store("goods_stocks").all() if _alive(r)]
    goods_ids, ware_ids, num_total, money_total = set(), set(), Decimal(0), Decimal(0)
    for r in rows:
        if r.get("goods_id") is not None:
            goods_ids.add(r.get("goods_id"))
        if r.get("ware_id") is not None:
            ware_ids.add(r.get("ware_id"))
        n = _dec(_dval(r, "num", "stock_total_num", "sum_stock_num"))
        num_total += n
        price = _dval(r, "cost_unit_price", "goods_cost_price", "cost_price")
        total = _dval(r, "cost_money")
        if total is not None:
            money_total += _dec(total)
        elif price is not None:
            money_total += n * _dec(price)
    return core.ok({
        "goods_count": len(goods_ids) or len({(r.get("goods") or {}).get("name") for r in rows} - {None}),
        "ware_count": len(ware_ids),
        "stock_num_total": _num(num_total),
        "stock_money_total": _num(money_total),
    })

def _goods_name(r):
    """商品名：goods_name / goods.name / name，兜底查 goods 字典表。"""
    if isinstance(r.get("goods"), dict) and r["goods"].get("name"):
        return r["goods"]["name"]
    g = r.get("goods_name") or r.get("name")
    if g:
        return str(g)
    if r.get("goods_id") is not None:
        for x in core.Store("goods").all():
            if str(x.get("id")) == str(r.get("goods_id")):
                return str(_dval(x, "name", "code") or "商品#" + str(r.get("goods_id")))
    return "商品#" + str(r.get("goods_id") or "?")

def _stock_item_flow_statistics(body, session, direction):
    """入/出库流水统计（真实 URL stock_item_in/out_statistics）：按商品聚合数量 Top10（ranking bar）。"""
    start, end = _range(body)
    agg = {}
    for r in core.Store("stock_items").all():
        if not _alive(r):
            continue
        # direction 1=入库 2=出库（B11 真实字段 direction/direction_name；兼容 type/type_name）
        d = str(_dval(r, "direction_name", "type_name", "stock_type_name") or "")
        t = r.get("direction", r.get("type", r.get("stock_type")))
        is_in = (d == "入库") or str(t) == "1"
        is_out = (d == "出库") or str(t) == "2"
        if direction == "in" and not is_in:
            continue
        if direction == "out" and not is_out:
            continue
        dt = _row_date(r, "bill_date", "created_at", "stock_date", "date")
        if (start or end) and dt and not _in_range(dt, start, end):
            continue
        g = _goods_name(r)
        agg[g] = agg.get(g, Decimal(0)) + _dec(_dval(r, "num", "change_num"))
    top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return core.ok([{"name": k, "num": _num(v)} for k, v in top])

def _produce_anomaly_statistics(body, session):
    """生产异常统计（样例 produce_anomaly_statistics.json）：逾期加工单/逾期工序数。"""
    today = datetime.date.today()
    bills = crafts = 0
    for r in core.Store("produce_bills").all():
        if not _alive(r) or str(r.get("status")) in ("3", "4"):   # 已完成/已取消不计
            continue
        d = _row_date(r, "delivery_date", "end_produce_date", "plan_end")
        if d and d < today:
            bills += 1
    for r in core.Store("produce_bill_crafts").all():
        if not _alive(r) or str(r.get("status")) == "3":
            continue
        d = _row_date(r, "plan_end", "end_produce_date")
        if d and d < today:
            crafts += 1
    return _wrap({"overdue_produce_num": bills, "overdue_produce_craft_num": crafts})

def _after_sales_profile_statistic(body, session):
    """售后数据简报（样例 after_sales_profile_statistic.json；无售后工单种子 → 样例零值占位）。"""
    return _wrap({
        "service_customer_count": 0, "finish_count": 0, "income_money": 0, "high_score_rate": None,
        "pause_count": 0, "timeout_count": 0, "already_settle_count": 0, "already_settle_money": 0,
        "not_settle_count": 0, "not_settle_money": "0", "settle_reward": 0, "not_settle_reward": 0,
        "settle_punishment": 0, "not_settle_punishment": 0, "total_commission_money": "0.00000000",
    })

# ---------------- /statistics/waste_trend_analysis（不良品趋势，三态同源） ----------------
def _waste_records():
    """[(waste_item_id, waste_item_name, date, num)]，按优先级取源：
    1) report_work_records.report_waste_list（报工不良记录逐项，真实字段 waste_item_id/num）；
    2) report_work_records.waste_num>0 但无逐项 → 归「未指定不良品项」；
    3) wastes 字典行自身带日期+数量（若 B11 直接造报工不良记录）；
    4) produce_bill_crafts.waste_num（按 waste_item_ids 均摊，合计守恒）。
    注：B13 报工样例分布在 records/settles/quality 三库（report_work_records 本体为空），合并读取。"""
    wastes = [r for r in core.Store("wastes").all() if _alive(r)]
    name_of = {r.get("id"): str(_dval(r, "name", "code")) for r in wastes}
    recs = []
    for r in (core.Store("report_work_records").all()
              + core.Store("report_work_settles").all()
              + core.Store("report_work_quality").all()):
        if not _alive(r):
            continue
        d = _row_date(r, "report_time", "created_at", "updated_at")
        if not d:
            continue
        listed = False
        for w in (r.get("report_waste_list") or []):
            n = _dec(_dval(w, "num", "waste_num", "report_waste_num"))
            if n <= 0:
                continue
            wi = w.get("waste_item") or {}          # quality 库形态：{waste_item:{id,name}, num}
            wid = w.get("waste_item_id", w.get("id", wi.get("id")))
            wname = _dval(w, "waste_item_name") or wi.get("name") or name_of.get(wid) \
                or ("未指定不良品项" if wid is None else "不良品项#" + str(wid))
            recs.append((wid, str(wname), d, n))
            listed = True
        wnum = _dec(_dval(r, "waste_num"))
        if not listed and wnum > 0:
            recs.append((None, "未指定不良品项", d, wnum))
    if recs:
        return recs
    for r in wastes:
        d = _row_date(r, "report_time", "waste_date", "date")
        n = _dval(r, "waste_num", "report_waste_num", "num")
        if not d or n is None:
            continue
        recs.append((_dval(r, "waste_item_id", "id"), str(_dval(r, "waste_item_name", "name", "code")), d, _dec(n)))
    if recs:
        return recs
    for r in core.Store("produce_bill_crafts").all():
        if not _alive(r):
            continue
        w = _dec(_dval(r, "waste_num"))
        if w <= 0:
            continue
        d = _row_date(r, "report_time", "updated_at", "created_at", "end_time", "start_time")
        if not d:
            continue
        ids = r.get("waste_item_ids") or []
        if ids:
            share = (w / len(ids)).quantize(Decimal("0.00000001"))
            for i, wid in enumerate(ids):
                n = w - share * (len(ids) - 1) if i == 0 else share   # 余数归首项，合计守恒
                recs.append((wid, name_of.get(wid, "不良品项#" + str(wid)), d, n))
        else:
            recs.append((None, "未指定不良品项", d, w))
    return recs

def _waste_buckets(start, end, gran):
    """时间粒度分桶标签（TIME_GRANULARITY：3 每天 / 2 每周 / 1 每月）。"""
    if gran == 1:
        cur, last, out = (start.year, start.month), (end.year, end.month), []
        while True:
            out.append("%04d-%02d" % cur)
            if cur == last:
                return out
            cur = _add_months(cur, 1)
    if gran == 2:
        cur, last_mon, out = _week_monday(start), _week_monday(end), []
        while cur <= last_mon:
            out.append(cur.isoformat())
            cur += datetime.timedelta(days=7)
        return out
    out, cur = [], start
    while cur <= end:
        out.append(cur.isoformat())
        cur += datetime.timedelta(days=1)
    return out

def _waste_bucket_key(d, gran):
    if gran == 1:
        return "%04d-%02d" % (d.year, d.month)
    if gran == 2:
        return _week_monday(d).isoformat()
    return d.isoformat()

def _waste_trend_analysis(body, session):
    """不良品趋势分析（deep\04 §2.3 结构）：table_data + intelligent_analytics，折线/面积/百分比面积三态同源。"""
    today = datetime.date.today()
    end = _pdate(body.get("end_date")) or today
    start = _pdate(body.get("start_date")) or end - datetime.timedelta(days=29)
    if start > end:
        start = end
    try:
        gran = int(body.get("time_granularity") or 3)
    except Exception:
        gran = 3
    want_ids = body.get("waste_item_ids") or []
    buckets = _waste_buckets(start, end, gran)
    per = {}      # (iid,name) -> {bucket: Decimal}
    totals = {}   # bucket -> Decimal
    for iid, name, d, n in _waste_records():
        if not _in_range(d, start, end):
            continue
        if want_ids and str(iid) not in [str(x) for x in want_ids]:
            continue
        key = _waste_bucket_key(d, gran)
        if key not in buckets:
            continue
        per.setdefault((iid, name), {}).setdefault(key, Decimal(0))
        per[(iid, name)][key] += n
        totals[key] = totals.get(key, Decimal(0)) + n
    grand = sum(totals.values(), Decimal(0))
    table = []
    for (iid, name), by in sorted(per.items(), key=lambda kv: sum(kv[1].values()), reverse=True):
        itotal = sum(by.values(), Decimal(0))
        item = []
        for b in buckets:
            v = by.get(b, Decimal(0))
            btotal = totals.get(b, Decimal(0))
            item.append({"date_name": b, "waste_num": _num(v),
                         "date_percent": _f8(v / btotal * 100) if btotal else "0.00000000"})
        table.append({
            "waste_item_id": iid, "waste_item_name": name,
            "waste_item_percent": _f8(itotal / grand * 100) if grand else "0.00000000",
            "waste_item_total": _num(itotal), "item": item,
        })
    analytics = []
    if grand > 0 and table:
        top = table[0]
        analytics.append({"desc": "统计周期内不良品总数为 %s，其中「%s」占比最高，约 %s%%。" %
                          (_num(grand), top["waste_item_name"], float(top["waste_item_percent"]))})
    return core.ok({"table_data": table, "intelligent_analytics": analytics})

# ---------------- 占位（返回空） ----------------
def _empty_paged(body, session):
    return core.paged_ok({"list": [], "total": 0, "pageNo": 1, "pageSize": 30})

def _empty_list(body, session):
    return core.ok([])

def _empty_trend(body, session):
    """服务工单走势族趋势占位：空序列。"""
    return core.ok({"list": []})

def _after_sales_trend_statistics(body, session):
    """服务工单走势（deep\03 §4 工作台卡 + 智能报表 v2）：双折线结构
    {create_line:[{date,count}], finish_line:[{date,count}]}；无售后工单种子 → 空序列占位。"""
    return core.ok({"create_line": [], "finish_line": []})

ROUTES = {
    # /intelli_report 管理
    ("intelli_report", "list"): _intelli_list,
    ("intelli_report", "detail"): _intelli_detail,
    ("intelli_report", "add"): _intelli_add,
    ("intelli_report", "edit"): _intelli_edit,
    ("intelli_report", "del"): _intelli_del,
    # 图表数据（真实 URL + 实测样例键名）
    ("intelli_report", "produce_bill_status_count"): _produce_bill_status_count,
    ("intelli_report", "produce_report_work_record_statistics"): _produce_report_work_record_statistics,
    ("intelli_report", "produce_report_work_record_working_hours_statistics"): _produce_report_work_record_working_hours_statistics,
    ("intelli_report", "sales_sale_report"): _sales_sale_report,
    ("intelli_report", "sales_every_month_sale_money"): _sales_every_month_sale_money,
    ("intelli_report", "goods_stock_summary"): _goods_stock_summary,
    ("intelli_report", "stock_item_in_statistics"): lambda b, s: _stock_item_flow_statistics(b, s, "in"),
    ("intelli_report", "stock_item_out_statistics"): lambda b, s: _stock_item_flow_statistics(b, s, "out"),
    ("intelli_report", "produce_anomaly_statistics"): _produce_anomaly_statistics,
    ("intelli_report", "after_sales_profile_statistic"): _after_sales_profile_statistic,
    # /statistics/waste_trend_analysis（不良品趋势，三态同源）
    ("statistics", "waste_trend_analysis"): _waste_trend_analysis,
    # 占位返回空（服务工单走势为 create_line/finish_line 双折线结构，deep\03 §4）
    ("report_work_analysis", "list"): _empty_paged,
    ("intelli_report", "after_sales_user_order_line"): _empty_list,
    ("intelli_report", "after_sales_trend_statistics"): _after_sales_trend_statistics,
    ("intelli_report", "after_sales_count_statistics"): _empty_trend,
    ("intelli_report", "after_sales_year_trend"): _empty_trend,
    ("intelli_report", "after_sales_profile_trend"): _empty_trend,
    ("intelli_report", "after_sales_profile_trend\n"): _empty_trend,   # 原站 URL 尾带换行（源码笔误，保持对拍）
}
