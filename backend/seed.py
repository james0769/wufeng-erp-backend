# -*- coding: utf-8 -*-
"""B11 · 种子生成器（可重复运行，输出幂等——同输入必得同输出，不含随机时间因子）。

把逆向抓取的真实数据转成 backend/data/*.json，供各模块 Store(<name>) 读取。
约定文件名见 briefing_backend.md 第 4 条。

数据源（均可通过环境变量覆盖路径：KGD_DUMP / KGD_PROBES / KGD_XLSX）：
  - <跨境报表>/_gen_tmp/kgd_reverse/api_dump/   真实接口抓包（goods_all/bom_all/craft_all/...）
  - <跨境报表>/_gen_tmp/kgd_reverse/probes/m4/  仓库域探针（goods_stock 样例行结构）
  - <跨境报表>/快工单批量导入模板与导出数据/03-生产管理/加工单_全量导出.xlsx（431 行×41 列）

合成说明（行内带 "_seed": 1 标记，便于与真实抓包数据区分）：
  - customers/contracts 仅抓到第 1 页各 20 行，按样例克隆补足到原站 count（39/184）
  - messages/notices 按样例结构补造若干条
  - goods_stocks 每商品一行：库存取 goods 自带 stock 字段、锁定恒 0
  - produce_bill_crafts 依加工单"工序进度 N%(done/total)"展开生成生产任务行
"""
import os
import sys
import re
import json
import copy
import random

HERE = os.path.dirname(os.path.abspath(__file__))            # backend
DATA_DIR = os.path.join(HERE, "data")
WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 跨境报表 工作区
DUMP = os.environ.get("KGD_DUMP", os.path.join(WS, "_gen_tmp", "kgd_reverse", "api_dump"))
PROBES = os.environ.get("KGD_PROBES", os.path.join(WS, "_gen_tmp", "kgd_reverse", "probes"))
XLSX = os.environ.get("KGD_XLSX", os.path.join(WS, "快工单批量导入模板与导出数据", "03-生产管理", "加工单_全量导出.xlsx"))

ENT_ID = 16807
RNG = random.Random(20261028)   # 固定种子：保证可重复运行结果一致

# ---- 枚举（出处：analysis/deep/01-加工单全流程链路.md §1.1、analysis/13-生产管理其他页面.md 枚举表）----
PRODUCE_STATUS = {"未开始": 1, "生产中": 2, "已完成": 3, "已取消": 4, "已暂停": 5}       # PRODUCE_BILL_STATUS
REQUISITION_STATUS = {"无需领料": 1, "未领料": 2, "部分领料": 3, "全部领料": 4}          # REQUISITION_STATUS
WORK_TYPE = {"生产工单": 1, "返工工单": 2}
PRIORITY_ID = {"加急": 145449, "正常": 145450, "延期": 145451}                          # deep_m14/priority_configure_list
BOOL = {"是": 1, "否": 2}

# 消息小类 → notice_type（misc.py NOTICE_TYPE_NAMES 同源；1121=2 待办 为 B9 联调反馈：原站实测，
# deepcopy 库存预警模板会把 notice_type=1 带进合成消息，msg/list 按 notice_type 过滤会失真）
MSG_NOTICE_TYPE = {1121: 2, 1205: 1}
NOTICE_TYPE_NAMES = {1: "报警", 2: "待办", 3: "通知"}   # MSG_NOTICE_TYPE：WARING/WAIT/INFO

# 加工单导出表（41 列）表头 → API 字段名（analysis/13-生产管理其他页面.md §1.3 表格列）
XLSX_COLMAP = [
    ("排单序号", "sort_num"), ("加工单号", "code"), ("二维码信息", "bill_qr_code"),
    ("是否逾期", "has_overdue_name"), ("生产计划编号", "produce_schedule_code"),
    ("生产计划名", "produce_schedule_name"), ("项目编号", "project_code"),
    ("项目名称", "project_name"), ("合同编号", "contract_code"), ("业务员", "sales_user_name"),
    ("商品", "goods_name"), ("商品编号", "goods_code"), ("商品规格", "goods_standard"),
    ("BOM名称", "goods_bom_name"), ("商品类别", "category_path_names"), ("计划数", "num"),
    ("商品库存", "sum_stock_num"), ("实际生产数", "actual_num"), ("状态", "status_name"),
    ("工单类型", "work_type_name"), ("领料状态", "requisition_status_name"),
    ("暂停原因", "pause_reason"), ("取消原因", "cancel_reason"),
    ("优先级", "priority_configure_name"), ("当前工序", "current_craft_name"),
    ("工序进度", "finish_craft_percent"), ("任务进度", "task_progress"),
    ("生产出库单号", "stock_out_bill_num"), ("成品入库单号", "stock_in_bill_num"),
    ("入库进度", "stock_in_percent"), ("备注", "remark"), ("图纸", "drawing_name"),
    ("交货倒计时", "delivery_countdown"), ("交货日期", "delivery_date"),
    ("计划开始", "start_produce_date"), ("计划结束", "end_produce_date"),
    ("加工单总工时", "total_working_time"), ("开始时间", "start_time"), ("完成时间", "end_time"),
    ("创建人", "create_user_name"), ("创建时间", "created_at"),
]


# ---------------------------------------------------------------- 基础工具
def _load(path):
    if not os.path.exists(path):
        sys.exit("[seed] 缺少数据源：%s" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rows(path, list_key="rows", count_key="count"):
    """api_dump 通用结构 {endpoint, req, count, rows} → (rows, count)。"""
    d = _load(path)
    if isinstance(d, list):
        return d, len(d)
    rows = d.get(list_key) or d.get("data") or []
    return rows, d.get(count_key)


def _dd(path):
    """dd_* 下拉结构 {success, data:[...]} → data 列表。"""
    d = _load(path)
    data = d.get("data")
    if not isinstance(data, list):
        sys.exit("[seed] 下拉数据不可用（抓包失败态）：%s" % path)
    return data


def _num(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _dec(v):
    """字符串数字（openpyxl 全为字符串）保留两位小数形态。"""
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return None


def save(name, rows):
    """原子写：先写临时文件再 os.replace，避免与正在运行的 server（Store.flush 整文件覆盖）
    交错写坏 JSON——core 的锁只对同进程生效，跨进程必须靠原子替换兜底。
    Windows 下目标文件恰被其他进程读着时 replace 会 WinError 5，重试若干次，
    仍不行则退化为直接写（保证 seed 不因并发而中断）。"""
    import time as _time
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, name + ".json")
    tmp = p + ".seedtmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    for _ in range(10):
        try:
            os.replace(tmp, p)
            return len(rows)
        except PermissionError:
            _time.sleep(0.3)
    try:
        os.replace(tmp, p)
    except PermissionError:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        os.remove(tmp)
    return len(rows)


# ---------------------------------------------------------------- 各集合构建
def build_goods():
    """goods(899) + 派生 categories / units。"""
    goods, count = _rows(os.path.join(DUMP, "goods_all.json"))
    cats, units = {}, {}
    for r in goods:
        c = r.get("category")
        if isinstance(c, dict) and c.get("id"):
            cats[c["id"]] = {"id": c["id"], "name": c.get("name"), "parent_id": c.get("parent_id")}
        elif r.get("category_path_ids"):
            # 无内嵌 category 时按路径兜底（末级 id + 末级名）
            ids, names = r["category_path_ids"], str(r.get("category_path_names") or "").split("/")
            last = ids[-1]
            if last and last not in cats:
                nm = names[-1] if names and names[-1] else None
                parent = ids[-2] if len(ids) > 1 else None
                cats[last] = {"id": last, "name": nm, "parent_id": parent}
        u = r.get("unit")
        if isinstance(u, dict) and u.get("id"):
            units[u["id"]] = {"id": u["id"], "name": u.get("name")}
        elif r.get("unit_id"):
            units.setdefault(r["unit_id"], {"id": r["unit_id"], "name": None})
    return goods, list(cats.values()), list(units.values())


def build_craft_side():
    """crafts(335) + 派生 workshops / produce_lines / units 补充。"""
    crafts, _ = _rows(os.path.join(DUMP, "craft_all.json"))
    workshops, plines, units = {}, {}, {}
    for r in crafts:
        w = r.get("workshop")
        if isinstance(w, dict) and w.get("id"):
            workshops[w["id"]] = w
        m = r.get("produce_line_map") or {}
        if isinstance(m, dict):
            for k, v in m.items():
                try:
                    plines[int(k)] = {"id": int(k), "name": v}
                except (TypeError, ValueError):
                    pass
        u = r.get("unit")
        if isinstance(u, dict) and u.get("id"):
            units[u["id"]] = {"id": u["id"], "name": u.get("name")}
    produce_lines = [plines[k] for k in sorted(plines)]
    return crafts, list(workshops.values()), produce_lines, list(units.values())


def build_bom_trees():
    """bom_details.json（10 个 BOM 详情，含 structure_tree）→ bom_trees。"""
    details = _load(os.path.join(DUMP, "bom_details.json"))
    trees = []
    for r in details:
        trees.append({
            "bom_id": r.get("id"), "code": r.get("code"), "name": r.get("name"),
            "goods_id": r.get("goods_id"), "goods_bom_id": r.get("id"),
            "structure": r.get("structure"), "structure_tree": r.get("structure_tree"),
        })
    return trees


def build_produce_bills():
    """加工单_全量导出.xlsx（431 行×41 列）→ produce_bills + produce_bill_crafts。"""
    try:
        import openpyxl
    except ImportError:
        sys.exit("[seed] 需要 openpyxl：pip install openpyxl")
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header, data = list(rows[0]), rows[1:]
    idx = {h: i for i, h in enumerate(header)}

    qr_pat = re.compile(r"[?&]i=(\d+)")
    prog_pat = re.compile(r"(\d+)%\((\d+)/(\d+)\)")
    bills, crafts = [], []
    seq = 0
    for r in data:
        b = {"enterprise_id": ENT_ID, "del": 2}
        for cn, fn in XLSX_COLMAP:
            b[fn] = r[idx[cn]]
        # id 取二维码里的 i 参数（如 http://tzwl.cn/p?i=8835784）
        m = qr_pat.search(str(b.get("bill_qr_code") or ""))
        if not m:
            sys.exit("[seed] 加工单二维码缺 id 参数：%s" % b.get("code"))
        b["id"] = int(m.group(1))
        b["sort_num"] = _num(b.get("sort_num"))
        b["num"] = _dec(b.get("num"))
        b["actual_num"] = _dec(b.get("actual_num"))
        b["sum_stock_num"] = _dec(b.get("sum_stock_num"))
        b["delivery_countdown"] = _num(b.get("delivery_countdown"))
        # 中文枚举 → 1-N
        sname = b.get("status_name")
        b["status"] = PRODUCE_STATUS.get(sname)
        wt = b.get("work_type_name")
        b["work_type"] = WORK_TYPE.get(wt)
        rq = b.get("requisition_status_name")
        b["requisition_status"] = REQUISITION_STATUS.get(rq)
        b["has_overdue"] = BOOL.get(b.get("has_overdue_name"))
        pname = b.get("priority_configure_name")
        b["priority_configure_id"] = PRIORITY_ID.get(pname)
        if pname == "无":
            b["priority_configure_name"] = None
        bills.append(b)

        # 生产任务（produce_bill_craft）：按"工序进度 done/total"展开
        pm = prog_pat.search(str(b.get("finish_craft_percent") or ""))
        done, total = (int(pm.group(2)), int(pm.group(3))) if pm else (0, 0)
        b["finish_craft_count"] = done
        b["craft_count"] = total
    return bills


def build_produce_bill_crafts(bills, craft_pool):
    """依加工单工序进度生成 produce_bill_crafts（任务状态：1 未开始/2 进行中/3 已完成/4 已暂停）。"""
    TASK_STATUS_NAME = {1: "未开始", 2: "进行中", 3: "已完成", 4: "已暂停"}
    out, seq = [], 0
    for bi, b in enumerate(bills):
        done, total = b.get("finish_craft_count") or 0, b.get("craft_count") or 0
        if total <= 0:
            continue
        for j in range(total):
            seq += 1
            if j < done:
                st = 3
            elif j == done:
                st = {2: 2, 5: 4}.get(b.get("status"), 1)   # 生产中→进行中；已暂停→已暂停
            else:
                st = 1
            name = None
            if j == done and st == 2 and b.get("current_craft_name"):
                name = b["current_craft_name"]
            pool = craft_pool[(bi * 7 + j * 13) % len(craft_pool)]
            craft_id, pool_name = pool
            out.append({
                "id": 880000000 + seq, "_seed": 1,
                "enterprise_id": ENT_ID, "produce_bill_id": b["id"], "produce_bill_code": b["code"],
                "pub_craft_id": craft_id, "craft_name": name or pool_name,
                "order_number": j + 1, "num": b.get("num"),
                "status": st, "status_name": TASK_STATUS_NAME[st],
                "is_current": 1 if j == done else 2,
                "created_at": b.get("created_at"),
            })
    return out


def build_customers():
    """customers：真实 20 行 + 克隆补足到原站 count=39。"""
    real, count = _rows(os.path.join(DUMP, "deep_m1", "customer_list_live.json"), list_key="data")
    rows = list(real)
    max_id = max(r["id"] for r in real)
    for i in range(count - len(real)):
        tpl = copy.deepcopy(real[i % len(real)])
        tpl["_seed"] = 1
        tpl["id"] = max_id + 1 + i
        tpl["name"] = "%s·%02d" % (tpl.get("name") or "客户", i + 1)
        tpl["code"] = "%s%02d" % (tpl.get("code") or "KH", i + 1)
        rows.append(tpl)
    return rows, count


def build_contacts():
    contacts, _ = _rows(os.path.join(DUMP, "deep_m1", "linkman_live.json"), list_key="data")
    return contacts


def build_contracts(customers):
    """contracts：真实 20 行 + 克隆补足到原站 count=184。"""
    real, count = _rows(os.path.join(DUMP, "deep_m1", "contract_live.json"), list_key="data")
    rows = list(real)
    max_id = max(r["id"] for r in real)
    cust_ids = [c["id"] for c in customers]
    moneys = ["0.00000000", "100.00000000", "1000.00000000", "5000.00000000",
              "10000.00000000", "50000.00000000", "100000.00000000"]
    for i in range(count - len(real)):
        tpl = copy.deepcopy(real[i % len(real)])
        tpl["_seed"] = 1
        tpl["id"] = max_id + 1 + i
        day = i % 27 + 1
        tpl["code"] = "HT2026%02d%03d" % (day, i % 500 + 1)
        tpl["customer_id"] = cust_ids[(i * 3) % len(cust_ids)]
        tpl["money"] = RNG.choice(moneys)
        tpl["created_at"] = tpl["updated_at"] = "2026-10-%02d %02d:00:00" % (day, i % 24)
        rows.append(tpl)
    return rows, count


def build_goods_stocks(goods):
    """goods_stocks：每商品一行，结构仿 probes/m4/goods_stock_list.json；
    库存取 goods 自带 stock 字段，锁定恒 0，可用 = available。"""
    tpl_list, _ = _rows(os.path.join(PROBES, "m4", "goods_stock_list.json"), list_key="data", count_key="count")
    tpl = tpl_list[0] if tpl_list else {}
    rows = []
    for i, g in enumerate(goods):
        total = g.get("stock_total_num")
        waste = g.get("stock_total_waste_num")
        avail = g.get("stock_total_available_num")
        ware_id = g.get("ware_id")
        rows.append({
            "id": 3190000 + i, "_seed": 1,
            "enterprise_id": ENT_ID,
            "ware_id": ware_id,
            "ware": {"id": ware_id, "name": g.get("ware_name")} if ware_id else None,
            "goods_id": g["id"],
            # 原站样例行内嵌完整 goods 对象（probes/m4 实测），库存域按
            # goods.jianpin/category_path_ids 等字段做筛选，故整体嵌入而非瘦身版
            "goods": copy.deepcopy(g),
            "num": str(total if total is not None else 0),
            "sum_stock_num": str(total if total is not None else 0),
            "stock_total_num": total if total is not None else 0,
            "waste_num": str(waste or 0),
            "stock_total_waste_num": waste or 0,
            "lock_stock_num": 0,
            "actual_lock_stock_num": 0,
            "stock_total_available_num": avail if avail is not None else 0,
            "stock_num": str(total if total is not None else 0),
            "stock_waste_num": str(waste or 0),
            "stock_available_num": str(avail if avail is not None else 0),
            "purchase_in_transit_num": "0.00000000",
            "low_limit": g.get("low_limit"),
            "upper_limit": g.get("upper_limit"),
            "stagnant_warning_day": g.get("stagnant_warning_day") or 0,
            "is_stagnant_material": 2,
            "is_exist_ware": g.get("is_exist_ware") or 2,
            "cost_unit_price": "0.00000000",
            "cost_unit_price_formatter": "0.00",
            "goods_cost_price": str(g.get("cost_money") or "0"),
            "fieldValueList": [],
            "created_at": g.get("created_at"),
            "updated_at": g.get("updated_at"),
            # 保留样例行里的其余展示字段形态
            "goods_selling_price": str(g.get("selling_money") or "0"),
            "goods_selling_money": "0.00",
            "goods_cost_money": "0.00",
        })
    return rows


def build_messages():
    """messages：真实 5 条 + 按样例结构补 3 条（消息小类来自 msg_small_types 抓包）。"""
    real = _load(os.path.join(DUMP, "msg_list_p1.json"))["data"]
    rows = list(real)
    types = _load(os.path.join(DUMP, "msg_small_types.json"))
    base_id = max(r["id"] for r in real)
    for i, t in enumerate(types[:3]):
        src = copy.deepcopy(real[i % len(real)])
        src["_seed"] = 1
        src["id"] = base_id + 1 + i
        src["big_type"], src["big_type_name"] = t["big_type"], t["big_type_name"]
        src["small_type"], src["small_type_name"] = t["small_type"], t["small_type_name"]
        src["web_url"], src["miniapp_url"] = t["web_url"], t["miniapp_url"]
        src["has_read"] = 2
        # notice_type 按小类覆盖（B9 联调反馈：1121 质量分析待办原站实测 notice_type=2 待办，
        # 模板消息（库存预警）为 1 报警——deepcopy 会把模板的 notice_type 一并带过来，必须显式改写）
        nt = MSG_NOTICE_TYPE.get(t["small_type"], (src["msg"].get("notice_type") or 1))
        src["msg"] = dict(src.get("msg") or {}, id=src["msg"]["id"] + 1 + i,
                          title=t["msg_title"], big_type=t["big_type"], small_type=t["small_type"],
                          big_type_name=t["big_type_name"], small_type_name=t["small_type_name"],
                          notice_type=nt, notice_type_name=NOTICE_TYPE_NAMES.get(nt, ""))
        rows.append(src)
    return rows


def build_wares():
    """wares：仓库档案——probes/m4 各样例行内嵌 ware 对象按 id 聚合去重（B9 联调反馈：无仓库档案，
    库存域只能靠行内嵌 ware 名兜底）。同 id 保留字段更全的形态。"""
    seen = {}

    def _keep(w):
        old = seen.get(str(w["id"]))
        if old is None or len(json.dumps(w, ensure_ascii=False)) > len(json.dumps(old, ensure_ascii=False)):
            seen[str(w["id"])] = w

    def _walk(o):
        if isinstance(o, dict):
            w = o.get("ware")
            if isinstance(w, dict) and w.get("id"):
                _keep(w)
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)

    pdir = os.path.join(PROBES, "m4")
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(pdir, fn), encoding="utf-8") as f:
                _walk(json.load(f))
        except Exception:
            continue   # 样例损坏时跳过，不阻断种子生成
    return [seen[k] for k in sorted(seen, key=lambda x: int(x))]


def build_notices():
    """notices：真实 1 条 + 按样例结构补 3 条（demo 通知）。"""
    real = _load(os.path.join(DUMP, "info_notice_list.json"))["data"]
    rows = list(real)
    base = real[0]["id"]
    demo = [("停机维护通知", "本周六 00:00-06:00 系统停机维护，请提前安排生产。"),
            ("生产安全培训", "本周五 15:00 车间安全培训，请各工序组长组织参加。"),
            ("月度盘点安排", "本月末进行仓库盘点，盘点期间库存单据暂停审批。")]
    for i, (title, text) in enumerate(demo):
        tpl = copy.deepcopy(real[0])
        tpl.update({"_seed": 1, "id": base + 1 + i, "type": 1, "title": title,
                    "content": "<p>%s</p>" % text,
                    "created_at": "2026-10-%02d 09:00:00" % (5 + i * 7),
                    "updated_at": "2026-10-%02d 09:00:00" % (5 + i * 7)})
        rows.append(tpl)
    return rows


def main():
    # 1) 商品域（goods + 派生 categories/units）
    goods, categories, units_g = build_goods()
    # 2) 工序域（crafts + 派生 workshops/produce_lines/units）
    crafts, workshops, produce_lines, units_c = build_craft_side()
    units = {u["id"]: u for u in units_g}
    for u in units_c:
        units.setdefault(u["id"], u)
    units = [units[k] for k in sorted(units)]
    # 3) BOM / 工艺路线 / 不良品项 / 基础档案
    boms, _ = _rows(os.path.join(DUMP, "bom_all.json"))
    bom_trees = build_bom_trees()
    routings, _ = _rows(os.path.join(DUMP, "routing_all.json"))
    wastes, _ = _rows(os.path.join(DUMP, "waste_all.json"))
    suppliers = _dd(os.path.join(DUMP, "dd_supplier.json"))
    users = _dd(os.path.join(DUMP, "dd_all_user.json"))
    teams = _dd(os.path.join(DUMP, "dd_team.json"))
    machines = _dd(os.path.join(DUMP, "dd_machine.json"))
    # 4) 生产域（xlsx 加工单 → produce_bills + produce_bill_crafts）
    bills = build_produce_bills()
    craft_pool = [(c["id"], c["name"]) for c in crafts if c.get("id") and c.get("name")]
    pbc = build_produce_bill_crafts(bills, craft_pool)
    # 5) 客户域
    customers, cust_count = build_customers()
    contacts = build_contacts()
    contracts, _ = build_contracts(customers)
    invoices, _ = _rows(os.path.join(DUMP, "deep_m1", "invoice_live.json"), list_key="data")
    # 6) 采购/仓库样例
    purchase_bills, _ = _rows(os.path.join(DUMP, "purchase", "purchase_bill_list.json"), list_key="data")
    stock_items, _ = _rows(os.path.join(PROBES, "m4", "stock_item_list.json"), list_key="data", count_key="count")
    # 7) 库存/消息/公告/视图
    goods_stocks = build_goods_stocks(goods)
    messages = build_messages()
    notices = build_notices()
    filter_views = [{"id": 528445, "name": "全部视图"},
                    {"id": 528446, "name": "自产"},
                    {"id": 528447, "name": "外购"}]

    out = {
        "goods": goods, "boms": boms, "bom_trees": bom_trees, "crafts": crafts,
        "routings": routings, "wastes": wastes, "suppliers": suppliers, "users": users,
        "teams": teams, "categories": categories, "units": units, "workshops": workshops,
        "produce_lines": produce_lines, "machines": machines, "wares": build_wares(),
        "produce_bills": bills, "produce_bill_crafts": pbc,
        "customers": customers, "contacts": contacts, "contracts": contracts, "invoices": invoices,
        "purchase_bills": purchase_bills, "stock_items": stock_items, "goods_stocks": goods_stocks,
        "messages": messages, "notices": notices, "filter_views": filter_views,
    }
    print("%-20s %6s  %s" % ("文件(data/*.json)", "行数", "说明"))
    print("-" * 72)
    notes = {
        "goods": "api_dump/goods_all", "categories": "goods 内嵌 category 去重",
        "units": "goods+crafts 内嵌 unit 去重", "workshops": "crafts 内嵌 workshop 去重",
        "produce_lines": "crafts.produce_line_map 去重", "bom_trees": "bom_details 结构树",
        "produce_bills": "加工单_全量导出.xlsx", "produce_bill_crafts": "按工序进度展开(合成)",
        "customers": "真实20+合成%d(原站count=%d)" % (cust_count - 20, cust_count),
        "contracts": "真实20+合成164(原站count=184)",
        "goods_stocks": "goods.stock 字段合成", "messages": "真实5+合成3", "notices": "真实1+合成3",
        "wares": "probes/m4 行内嵌 ware 聚合",
    }
    for name, rows in out.items():
        n = save(name, rows)
        print("%-20s %6d  %s" % (name + ".json", n, notes.get(name, "")))
    print("-" * 72)
    print("共 %d 个文件 → %s" % (len(out), DATA_DIR))


if __name__ == "__main__":
    main()
