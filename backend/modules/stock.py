# -*- coding: utf-8 -*-
r"""B6 仓库管理域（简化版）。
依据：_gen_tmp\kgd_reverse\analysis\deep\12-仓库管理域.md、deep\01-加工单全流程链路.md §4.3-4.4
（可用库存 = 总库存 − Σ有效生产计划占用，has_material_occupancy_other_bill_use=2 的占用才扣减）、
probes\m4\ 实测（goods_stock_list 1221 行 / dd_in_out_ware_type / dd_stock_bill_type / stock_in·out_else_list4 / stock_item_list）。

ROUTES 覆盖：
- goods_stock: list / stock_statistics / goods_stock_warning_tip / get_stock_below_low_limit_count
- stock_in_bill: list（type=2 其他入库）/ else_stock_in_bill_add（其他入库，QTRK 前缀）
- stock_out_bill: list（type=2 其他出库 / 3 生产出库）/ else_stock_out_bill_add（QTCK）/ production_stock_out_bill_add（SCCK）
- stock_item: list（流水）
- dropdown: stock_bill_type（16 种单据类型）/ get_designate_dropdown（仓域键 in_ware_type/out_ware_type/ware/enable_ware/stock_direction）

库存操作直接改 goods_stocks Store 并写 stock_items 流水（模块间不 import：
B5 的 produce_stock_in_bill add 直接操作同一 goods_stocks / stock_items Store 即可，
行结构 = {goods_id, ware_id, num, waste_num, low_limit, upper_limit, stagnant_warning_day, cost_unit_price}）。
其他出库遇 可用库存<出库数 时按差额生成 purchase_plans 简化记录并返回提示文案（deep12 §3.2 采购申请补货路径）。
"""
import time

import core

# ---- 字典（实测 probes\m4\dd_in_out_ware_type.json 逐字，id 为企业字典 id） ----
IN_WARE_TYPE = {946701: "普通入库", 946739: "核验入库", 950910: "来料入库", 950912: "销售退货入库",
                950911: "成品入库", 950913: "退料 入库", 966270: "半成品入库"}
OUT_WARE_TYPE = {946702: "普通出库", 946741: "销售出库", 950854: "发货出库", 950855: "生产出库",
                 950914: "采购退货出库", 966298: "半成品出库", 1031515: "配件售后出库", 1031516: "配件销售出库"}

SOURCE_NAME = {1: "自产", 2: "外协加工", 3: "外购"}          # GOODS_SOURCE
DIRECTION_NAME = {1: "入库", 2: "出库", 3: "调拨"}           # STOCK_DIRECTION(+调拨 direction=3)
PRODUCTION_STOCK_OUT_TYPE_NAME = {1: "加工单生产出库", 2: "外协工单生产出库"}
OPERATION_TYPE_NAME = {1: "自动", 2: "人工", 3: "AMR"}       # PRODUCTION_STOCK_TYPE

# ---- 16 种单据类型（实测 probes\m4\dd_stock_bill_type.json 逐字；v=7/8/10/11 空缺） ----
STOCK_BILL_TYPES = [
    {"value": 1, "v": 1, "name": "生产入库单", "stock_direction": 1, "table_name": "produce_stock_in_bill"},
    {"value": 2, "v": 2, "name": "采购入库单", "stock_direction": 1, "table_name": "purchase_stock_in_bill"},
    {"value": 3, "v": 3, "name": "其他入库单", "stock_direction": 1, "table_name": "stock_in_bill"},
    {"value": 4, "v": 4, "name": "销售出库单", "stock_direction": 2, "table_name": "stock_out_bill"},
    {"value": 5, "v": 5, "name": "其他出库单", "stock_direction": 2, "table_name": "stock_out_bill"},
    {"value": 6, "v": 6, "name": "库存调拨单", "stock_direction": 3, "table_name": "stock_allocate_bill"},
    {"value": 9, "v": 9, "name": "维修工单销售", "stock_direction": 2, "table_name": "device_order"},
    {"value": 12, "v": 12, "name": "生产出库单", "stock_direction": 2, "table_name": "stock_out_bill"},
    {"value": 13, "v": 13, "name": "维保计划执行使用", "stock_direction": 2, "table_name": "inspection_record"},
    {"value": 14, "v": 14, "name": "服务工单销售", "stock_direction": 2, "table_name": "new_work_order"},
    {"value": 15, "v": 15, "name": "采购退货单", "stock_direction": 2, "table_name": "stock_out_bill"},
    {"value": 16, "v": 16, "name": "销售退货单", "stock_direction": 1, "table_name": "sale_return_stock_in_bill"},
    {"value": 17, "v": 17, "name": "生产退料单", "stock_direction": 1, "table_name": "produce_return_materiel_bill"},
    {"value": 19, "v": 19, "name": "备件出库", "stock_direction": 2, "table_name": "part_out_in_bill"},
    {"value": 20, "v": 20, "name": "备件入库", "stock_direction": 1, "table_name": "part_out_in_bill"},
    {"value": 21, "v": 21, "name": "半成品入库单", "stock_direction": 1, "table_name": "semi_finished_stock_in_bill"},
    {"value": 22, "v": 22, "name": "点检执行使用", "stock_direction": 2, "table_name": "point_inspection_record"},
]
BILL_TYPE_NAME = {t["v"]: t["name"] for t in STOCK_BILL_TYPES}


def _camel(snake):
    return "".join(p.capitalize() for p in str(snake).split("_"))


BILL_TYPE_CLASS = {t["v"]: "App\\Model\\%sModel" % _camel(t["table_name"]) for t in STOCK_BILL_TYPES}

# ---------------- 数值/通用小工具 ----------------

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def f8(x):
    """8 位小数字符串（原站 num/money 惯例），-0 归零。"""
    v = round(_f(x), 8)
    if abs(v) < 1e-9:
        v = 0.0
    return "%.8f" % v


def _s(x):
    """单据头 sum_num 惯例：整数不带小数（实测 "10"）。"""
    v = round(_f(x), 8)
    return str(int(v)) if v == int(v) else str(v)


def _eid(session):
    return session.get("enterprise_id") or 16807


def _page_of(rows, body, default_size=30):
    try:
        page_no = int(body.get("pageNo") or 1)
    except (TypeError, ValueError):
        page_no = 1
    try:
        page_size = int(body.get("pageSize") or default_size)
    except (TypeError, ValueError):
        page_size = default_size
    return {"list": rows[(page_no - 1) * page_size: page_no * page_size], "total": len(rows),
            "pageNo": page_no, "pageSize": page_size}


def _kw(body, *names):
    """筛选参数取值（兼容 str/int）。"""
    out = []
    for n in names:
        v = body.get(n)
        if v not in (None, "", []):
            out.append(str(v))
    return out


# ---------------- 关联对象装饰（goods / ware / user / stock_type） ----------------

def _goods_info(goods_map, gid):
    g = goods_map.get(str(gid))
    if not g:
        return {"id": gid, "code": "", "name": "商品%s" % gid, "standard": None, "jianpin": "",
                "unit_id": None, "source": None, "selling_money": None, "cost_money": None,
                "avatars": [], "category_id": None, "category_path_names": "", "category_path_ids": [],
                "source_name": None, "produce_type_name": "-", "source_tag_class": "",
                "qr_code_url": None, "unit": None, "category": None, "attachments": [], "fieldValueList": []}
    r = dict(g)
    r.setdefault("code", "")
    r.setdefault("name", "")
    r.setdefault("standard", None)
    r.setdefault("jianpin", "")
    r.setdefault("avatars", [])
    r.setdefault("category_path_names", "")
    r.setdefault("category_path_ids", [])
    r.setdefault("attachments", [])
    r.setdefault("fieldValueList", [])
    r.setdefault("unit", None)
    r.setdefault("source_name", SOURCE_NAME.get(r.get("source")))
    r.setdefault("produce_type_name", "-" if not r.get("produce_type") else str(r.get("produce_type")))
    r["source_tag_class"] = r.get("source_tag_class", "")
    return r


def _ware_info(ware_map, wid):
    if wid is None:
        return None
    w = ware_map.get(str(wid))
    if w:
        return {"id": w.get("id"), "name": w.get("name"), "qr_code_url": w.get("qr_code_url")}
    return {"id": wid, "name": "仓库%s" % wid, "qr_code_url": None}


def _user_info(user_map, session, uid):
    u = user_map.get(str(uid)) if uid is not None else None
    name = (u or {}).get("real_name") or (u or {}).get("name") or session.get("real_name") or session.get("name") or ""
    return {"id": (u or {}).get("id", uid), "real_name": name, "has_password": False,
            "type_name": "", "claw_fun_type_name": "", "qr_code_url": None}


def _stock_type(sid):
    if sid is None:
        return None
    return {"id": sid, "name": IN_WARE_TYPE.get(sid) or OUT_WARE_TYPE.get(sid)}


def _maps():
    return ({"%s" % r.get("id"): r for r in core.Store("goods").all()},
            {"%s" % r.get("id"): r for r in core.Store("wares").all()},
            {"%s" % r.get("id"): r for r in core.Store("users").all()})


# ---------------- 生产计划占用（deep01 §4.4：可用 = 总 − Σ有效占用） ----------------

def _lock_maps(locks_rows):
    """返回 (商品维有效占用, {(goods,ware): 仓维有效占用}, {goods: 未挂仓的有效占用})。
    有效占用 = has_material_occupancy_other_bill_use != 1（1=已被其他单据消化，不再扣减）；
    ware_id 为空的占用只扣商品维总量，不分摊到仓维展示列（真实数据 JH 生产计划占用即无仓维，deep01 §4.4）。
    """
    total, by_ware, null_ware = {}, {}, {}
    for r in locks_rows or []:
        if str(r.get("has_material_occupancy_other_bill_use") or 2) == "1":
            continue
        gid, num = r.get("goods_id"), _f(r.get("num"))
        total[gid] = total.get(gid, 0.0) + num
        if r.get("ware_id") is not None:
            key = (str(gid), str(r.get("ware_id")))
            by_ware[key] = by_ware.get(key, 0.0) + num
        else:
            null_ware[gid] = null_ware.get(gid, 0.0) + num
    return total, by_ware, null_ware


def _consume_locks(goods_id, num):
    """生产出库消化占用（FIFO）：把占用行标记为已被其他单据使用 / 扣减数量。"""
    locks = core.Store("goods_inventory_locks")
    need = _f(num)
    for r in sorted(locks.all(), key=lambda x: x.get("id") or 0):
        if need <= 0:
            break
        if str(r.get("goods_id")) != str(goods_id) or str(r.get("has_material_occupancy_other_bill_use") or 2) == "1":
            continue
        rn = _f(r.get("num"))
        if rn <= need:
            locks.update(r.get("id"), {"has_material_occupancy_other_bill_use": 1})
            need -= rn
        else:
            locks.update(r.get("id"), {"num": f8(rn - need)})
            need = 0


# ---------------- goods_stocks 操作 ----------------

def _stock_row(gs, goods_map, goods_id, ware_id, enterprise_id):
    for r in gs.all():
        if str(r.get("goods_id")) == str(goods_id) and str(r.get("ware_id")) == str(ware_id):
            return r
    g = goods_map.get(str(goods_id)) or {}
    return gs.add({
        "enterprise_id": enterprise_id, "ware_id": ware_id, "goods_id": goods_id,
        "num": "0.00000000", "waste_num": "0.00000000",
        "low_limit": g.get("low_limit"), "upper_limit": g.get("upper_limit"),
        "stagnant_warning_day": g.get("stagnant_warning_day") or 0,
        "cost_unit_price": f8(g.get("cost_money") or 0),
        "lock_stock_num": 0, "actual_lock_stock_num": 0, "purchase_in_transit_num": "0",
    })


def _write_flow(bill, item, direction, bill_type, ware_id, balance, session):
    """写 stock_items 流水一行（结余 = 操作后仓维库存快照）。"""
    si = core.Store("stock_items")
    code = core.next_code(str(_eid(session)), si.all(), width=3)  # 实测：企业id+YYYYMMDD+3位
    row = {
        "code": code, "goods_id": item.get("goods_id"), "batch_name": item.get("batch_name") or "无批次",
        "in_ware_id": ware_id if direction == 1 else None,
        "out_ware_id": ware_id if direction == 2 else None,
        "operator_id": session.get("id"), "stock_type_id": bill.get("stock_type_id"),
        "bill_id": bill.get("id"), "bill_type": bill_type,
        "num": f8(item.get("num")), "waste_num": f8(item.get("waste_num") or 0),
        "selling_price": item.get("selling_price"), "selling_money": f8(item.get("selling_money") or 0),
        "cost_price": item.get("cost_price"), "cost_money": f8(item.get("cost_money") or 0),
        "remark": item.get("remark"), "bill_date": bill.get("bill_date"),
        "in_storage_location_id": None, "out_storage_location_id": None,
        "customer_contract_item_id": None, "received_money": "0.00000000", "return_money": "0.00000000",
        "contract_stock_out_bill_id": None, "contract_stock_out_bill_item_id": None,
        "in_stock_balance_num": f8(balance) if direction == 1 else None,
        "out_stock_balance_num": f8(balance) if direction == 2 else None,
        "sum_num": f8(item.get("num")), "fieldValueList": [],
    }
    si.add(row)
    return row


# ---------------- goods_stock：库存管理 ----------------

def _warning_state(num, low, upper):
    """行报警判定（deep12 §1.4 红底规则原文：双零不算超下限）。"""
    n, lo, up = _f(num), low, upper
    lo_v = _f(lo) if lo not in (None, "") else None
    up_v = _f(up) if up not in (None, "") else None
    below = lo_v is not None and n < lo_v and not (n == 0 and lo_v == 0)
    above = up_v is not None and up_v != 0 and n > up_v
    return below, above


def _is_stagnant(row, last_flow):
    """呆滞：stagnant_warning_day>0 且 距最近流水超过该天数（0 不统计）。"""
    days = row.get("stagnant_warning_day") or 0
    try:
        days = int(float(days))
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return False
    last = last_flow.get(row.get("goods_id"))
    if not last:
        return True
    try:
        elapsed = (time.time() - time.mktime(time.strptime(str(last)[:19], "%Y-%m-%d %H:%M:%S"))) / 86400.0
    except ValueError:
        return False
    return elapsed > days


def _decorate_gs(r, goods_map, ware_map, totals, lock_total, lock_ware, last_flow):
    num, waste = _f(r.get("num")), _f(r.get("waste_num"))
    gid = r.get("goods_id")
    g = _goods_info(goods_map, gid)
    tot, tw = totals.get(gid, (num, waste))
    cup = _f(r.get("cost_unit_price"))
    selp = g.get("selling_money")
    sell_p = _f(selp) if selp not in (None, "") else None
    return {
        "id": r.get("id"), "enterprise_id": r.get("enterprise_id"), "ware_id": r.get("ware_id"), "goods_id": gid,
        "num": f8(num), "waste_num": f8(waste), "sum_stock_num": f8(num + waste),
        "low_limit": r.get("low_limit"), "upper_limit": r.get("upper_limit"),
        "stagnant_warning_day": r.get("stagnant_warning_day") or 0,
        "cost_unit_price": f8(cup), "cost_unit_price_formatter": "%.2f" % cup,
        "goods_cost_price": f8(g["cost_money"]) if g.get("cost_money") not in (None, "") else None,
        "goods_cost_money": f8(num * cup),
        "goods_selling_price": f8(sell_p) if sell_p is not None else None,
        "goods_selling_money": f8(num * sell_p) if sell_p is not None else "0.00000000",
        "is_stagnant_material": 1 if _is_stagnant(r, last_flow) else 2,
        "stock_total_num": f8(tot), "stock_total_waste_num": f8(tw),
        "stock_total_available_num": f8(tot - lock_total.get(gid, 0.0)),
        "is_exist_ware": 1,
        "lock_stock_num": r.get("lock_stock_num") if r.get("lock_stock_num") is not None else 0,
        "actual_lock_stock_num": r.get("actual_lock_stock_num") if r.get("actual_lock_stock_num") is not None else 0,
        "stock_num": f8(num), "stock_waste_num": f8(waste),
        "stock_available_num": f8(num - lock_ware.get((str(gid), str(r.get("ware_id"))), 0.0)),
        "purchase_in_transit_num": r.get("purchase_in_transit_num") or "0",
        "ware": r.get("ware") or _ware_info(ware_map, r.get("ware_id")), "goods": g,
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"), "fieldValueList": [],
    }


def _gs_totals(gs_rows):
    totals = {}
    for r in gs_rows:
        t = totals.setdefault(r.get("goods_id"), [0.0, 0.0])
        t[0] += _f(r.get("num"))
        t[1] += _f(r.get("waste_num"))
    return {k: (v[0], v[1]) for k, v in totals.items()}


def _filter_gs(rows, body):
    ware_ids = _kw(body, "ware_id")
    kw = (_kw(body, "goods_keyword") or [""])[0].lower()
    cat = (_kw(body, "category") or [None])[0]
    warn = (_kw(body, "low_limit_warning") or [None])[0]
    stag = (_kw(body, "is_stagnant_material") or [None])[0]
    src = (_kw(body, "goods_source") or [None])[0]
    has_stock = body.get("has_stock", 1)  # 默认只看有库存（实测请求 has_stock:1）
    out = []
    for r in rows:
        if ware_ids and str(r["ware_id"]) not in ware_ids:
            continue
        g = r["goods"]
        if kw:
            hay = " ".join(str(x or "") for x in (g.get("name"), g.get("jianpin"), g.get("code"), g.get("standard"))).lower()
            if kw not in hay:
                continue
        if cat:
            paths = [str(i) for i in g.get("category_path_ids") or []]
            if str(g.get("category_id")) != cat and cat not in paths:
                continue
        if src and str(g.get("source")) != src:
            continue
        if stag and str(r["is_stagnant_material"]) != stag:
            continue
        if str(has_stock) in ("1", "True", "true") and _f(r["num"]) <= 0:
            continue
        if warn in ("1", "3", "4", "2"):
            below, above = _warning_state(r["num"], r["low_limit"], r["upper_limit"])
            hit = (below or above) if warn == "1" else (below if warn == "3" else (above if warn == "4" else False))
            if warn == "2":
                hit = not (below or above)
            if not hit:
                continue
        out.append(r)
    return out


def _goods_stock_list(body, session):
    gs = core.Store("goods_stocks")
    goods_map, ware_map, _user_map = _maps()
    lock_total, lock_ware, _lock_null = _lock_maps(core.Store("goods_inventory_locks").all())
    last_flow = {}
    for it in core.Store("stock_items").all():
        d, gid = str(it.get("created_at") or ""), it.get("goods_id")
        if d and (gid not in last_flow or d > last_flow[gid]):
            last_flow[gid] = d
    totals = _gs_totals(gs.all())
    rows = [_decorate_gs(r, goods_map, ware_map, totals, lock_total, lock_ware, last_flow) for r in gs.all()]
    rows = _filter_gs(rows, body)
    return core.paged_ok(_page_of(rows, body))


def _gs_filtered_rows(body, session):
    """统计/预警共用：按 body 过滤后的商品维聚合（唯一商品一行）。"""
    gs = core.Store("goods_stocks")
    goods_map, ware_map, _user_map = _maps()
    lock_total, lock_ware, _lock_null = _lock_maps(core.Store("goods_inventory_locks").all())
    last_flow = {}
    for it in core.Store("stock_items").all():
        d, gid = str(it.get("created_at") or ""), it.get("goods_id")
        if d and (gid not in last_flow or d > last_flow[gid]):
            last_flow[gid] = d
    totals = _gs_totals(gs.all())
    rows = [_decorate_gs(r, goods_map, ware_map, totals, lock_total, lock_ware, last_flow) for r in gs.all()]
    rows = _filter_gs(rows, body)
    by_goods = {}
    for r in rows:  # 仓维行 → 商品维（取首次出现的行装载警告值/名称）
        gid = r["goods_id"]
        if gid not in by_goods:
            by_goods[gid] = {"goods": r["goods"], "total": _f(r["stock_total_num"]),
                             "low": r["low_limit"], "upper": r["upper_limit"],
                             "cost_money": 0.0, "selling_money": 0.0}
        by_goods[gid]["cost_money"] += _f(r["goods_cost_money"])
        by_goods[gid]["selling_money"] += _f(r["goods_selling_money"])
    return list(by_goods.values())


def _stock_statistics(body, session):
    """统计条（deep12 §1.3）：库存数量总计/总成本金额/总销售金额。"""
    rows = _gs_filtered_rows(body, session)
    total = sum(r["total"] for r in rows)
    return core.ok({
        "total_stock_num": f8(total),
        "total_stock_waste_num": f8(0),
        "sum_total_stock_num": f8(total),
        "selling_money_total": f8(sum(r["selling_money"] for r in rows)),
        "cost_money_total": f8(sum(r["cost_money"] for r in rows)),
    })


def _get_stock_below_low_limit_count(body, session):
    """工作台双计数（deep12 §9：高于上限 N 种 / 低于下限 N 种，商品种类数）。"""
    below = above = 0
    for r in _gs_filtered_rows({}, session):
        b, a = _warning_state(r["total"], r["low"], r["upper"])
        below += 1 if b else 0
        above += 1 if a else 0
    return core.ok({"stock_below_low_limit_count": below, "stock_above_upper_limit_count": above})


def _goods_stock_warning_tip(body, session):
    """登录库存警告弹窗（deep12 §9：is_show_warning_tip）。"""
    show = any(any(_warning_state(r["total"], r["low"], r["upper"])) for r in _gs_filtered_rows({}, session))
    return core.ok({"is_show_warning_tip": show})


# ---------------- 单据共用：明细解析 / 单据装饰 ----------------

def _parse_items(raw, goods_map):
    """item_list 兼容 {goods:{id}} / {goods_id}，补金额。"""
    out = []
    for it in raw or []:
        gid = it.get("goods_id") or (it.get("goods") or {}).get("id")
        if gid is None:
            continue
        g = _goods_info(goods_map, gid)
        num = _f(it.get("num"))
        cost_p = it.get("cost_price")
        sell_p = it.get("selling_price")
        cost_p_v = _f(cost_p) if cost_p not in (None, "") else _f(g.get("cost_money"))
        sell_p_v = _f(sell_p) if sell_p not in (None, "") else _f(g.get("selling_money") or 0)
        out.append({"goods_id": gid, "goods": g, "num": num,
                    "cost_price": f8(cost_p_v), "selling_price": f8(sell_p_v),
                    "cost_money": f8(num * cost_p_v), "selling_money": f8(num * sell_p_v),
                    "remark": it.get("remark"), "batch_name": it.get("batch_name") or "无批次"})
    return out


def _bill_totals(items):
    num = sum(i["num"] for i in items)
    return num, f8(sum(_f(i["cost_money"]) for i in items)), f8(sum(_f(i["selling_money"]) for i in items))


def _item_view(it, bill_id, idx, direction):
    r = dict(it)
    r["id"] = int("%s%02d" % (bill_id, idx + 1))
    r["bill_id"] = bill_id
    r["num"] = f8(r["num"])
    r["waste_num"] = f8(r.get("waste_num") or 0)
    r["sum_num"] = r["num"]
    r["in_storage_location_id"] = None if direction != 1 else r.get("in_storage_location_id")
    r["out_storage_location_id"] = None if direction != 2 else r.get("out_storage_location_id")
    r["in_storage_location"] = None
    r["out_storage_location"] = None
    r["direction_name"] = ""
    r["bill_type_name"] = ""
    r["bill_type_class"] = ""
    r["finish_bill_type_class"] = ""
    r["direction_tag_class"] = ""
    r["fieldValueList"] = []
    return r


def _in_bill_view(r, goods_map, ware_map, user_map, session):
    num, cost_t, sell_t = _bill_totals(r.get("item_list") or [])
    uid = r.get("create_user_id") or session.get("id")
    items = [_item_view(it, r["id"], i, 1) for i, it in enumerate(r.get("item_list") or [])]
    return {
        "id": r["id"], "enterprise_id": r.get("enterprise_id"), "code": r.get("code"),
        "num": f8(num), "waste_num": f8(0), "selling_money_total": sell_t, "cost_money_total": cost_t,
        "stock_type_id": r.get("stock_type_id"), "ware_id": r.get("ware_id"), "bill_date": r.get("bill_date"),
        "audit_status": 1, "audit_config_json": None, "current_audit_level": None, "remark": r.get("remark"),
        "create_user_id": uid, "supplier_id": r.get("supplier_id"), "consignee_id": r.get("consignee_id"),
        "created_at": r.get("created_at"), "del": 2, "fieldValueList": [],
        "auditAccessible": False, "allow_withdrawal": 2, "editAccessible": True,
        "type_name": "", "type_color": "", "audit_status_name": "不需审核", "audit_status_tag_class": "4",
        "sum_num": _s(num),
        "create_user": _user_info(user_map, session, uid),
        "ware": _ware_info(ware_map, r.get("ware_id")),
        "supplier": _user_info(user_map, session, r.get("supplier_id")) if r.get("supplier_id") else None,
        "consignee": _user_info(user_map, session, r.get("consignee_id") or uid),
        "stock_type": _stock_type(r.get("stock_type_id")),
        "item_list": items,
    }


def _out_bill_view(r, goods_map, ware_map, user_map, session):
    num, cost_t, sell_t = _bill_totals(r.get("item_list") or [])
    uid = r.get("create_user_id") or session.get("id")
    items = [_item_view(it, r["id"], i, 2) for i, it in enumerate(r.get("item_list") or [])]
    psot = r.get("production_stock_out_type")
    ot = r.get("operation_type")
    pb = None
    if r.get("produce_bill_id"):
        pb_row = core.Store("produce_bills").get(r.get("produce_bill_id"))
        pb = {"id": r.get("produce_bill_id"), "code": (pb_row or {}).get("code")}
    return {
        "id": r["id"], "enterprise_id": r.get("enterprise_id"), "type": r.get("type") or 2,
        "production_stock_out_type": psot, "sale_stock_out_bill_way_type": r.get("sale_stock_out_bill_way_type"),
        "code": r.get("code"), "bill_date": r.get("bill_date"),
        "create_user_id": uid, "update_user_id": r.get("update_user_id"), "operation_type": ot,
        "ware_id": r.get("ware_id"), "shipper_id": r.get("shipper_id"), "stock_type_id": r.get("stock_type_id"),
        "outsource_produce_bill_id": r.get("outsource_produce_bill_id"), "produce_bill_id": r.get("produce_bill_id"),
        "produce_bill_craft_id": r.get("produce_bill_craft_id"), "report_work_record_id": r.get("report_work_record_id"),
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        "supplier_id": r.get("supplier_id"), "remark": r.get("remark"),
        "num": f8(num), "waste_num": f8(0), "del": 2,
        "audit_status": 1, "audit_config_json": None, "current_audit_level": None,
        "supplier_linkman": None, "supplier_address": None, "purchase_bill_id": r.get("purchase_bill_id"),
        "customer_id": None, "linkman_name": None, "province_code": None, "city_code": None,
        "area_code": None, "address": None,
        "selling_money_total": sell_t, "cost_money_total": cost_t, "fieldValueList": [],
        "auditAccessible": False, "allow_withdrawal": 2, "editAccessible": True,
        "goods_names": ",".join(str((it.get("goods") or {}).get("name") or "") for it in r.get("item_list") or []),
        "contract_codes": "", "sale_user_names": "", "sale_person_names": "",
        "sum_num": _s(num), "set_ware_permission": False, "region_address": "", "full_address": "",
        "type_name": "", "type_color": "",
        "production_stock_out_type_name": PRODUCTION_STOCK_OUT_TYPE_NAME.get(psot),
        "sale_stock_out_bill_way_type_name": None,
        "operation_type_name": OPERATION_TYPE_NAME.get(ot),
        "operation_type_color": "", "operation_type_tag_class": "",
        "audit_status_name": "不需审核", "audit_status_tag_class": "4",
        "create_user": _user_info(user_map, session, uid),
        "update_user": _user_info(user_map, session, r.get("update_user_id")) if r.get("update_user_id") else None,
        "ware": _ware_info(ware_map, r.get("ware_id")),
        "shipper": _user_info(user_map, session, r.get("shipper_id") or uid),
        "stock_type": _stock_type(r.get("stock_type_id")),
        "item_list": items,
        "produce_bill": pb, "produce_bill_craft": None, "outsource_produce_bill": None,
        "supplier": _user_info(user_map, session, r.get("supplier_id")) if r.get("supplier_id") else None,
        "purchase_bill": None, "customer": None, "province": None, "city": None, "area": None, "agv_shipment": None,
    }


def _filter_bills(rows, body):
    """出入库单通用筛选（deep12 §2.1/§3.1：单号/类型/仓库/审核状态/商品关键字/日期）。"""
    codes = _kw(body, "code")
    ware_ids = _kw(body, "ware_id")
    stids = _kw(body, "stock_type_id")
    audits = _kw(body, "audit_status")
    kw = (_kw(body, "goods_keyword") or [""])[0].lower()
    ds, de = (_kw(body, "bill_date_start") or [None])[0], (_kw(body, "bill_date_end") or [None])[0]
    out = []
    for r in rows:
        if codes and not any(c in str(r.get("code") or "") for c in codes):
            continue
        if ware_ids and str(r.get("ware_id")) not in ware_ids:
            continue
        if stids and str(r.get("stock_type_id")) not in stids:
            continue
        if audits and str(r.get("audit_status")) not in audits:
            continue
        if ds and str(r.get("bill_date") or "") < ds:
            continue
        if de and str(r.get("bill_date") or "") > de:
            continue
        if kw:
            hay = " ".join(str((it.get("goods") or {}).get("name") or "") + " " +
                           str((it.get("goods") or {}).get("code") or "") + " " +
                           str((it.get("goods") or {}).get("standard") or "")
                           for it in r.get("item_list") or []).lower()
            if kw not in hay:
                continue
        out.append(r)
    return out


def _bill_list(store_name, view_fn, session, body, type_map):
    store = core.Store(store_name)
    goods_map, ware_map, user_map = _maps()
    rows = [r for r in store.all() if not type_map or str(r.get("type") or 2) in type_map]
    rows = _filter_bills(rows, body)
    rows = [view_fn(r, goods_map, ware_map, user_map, session) for r in rows]
    return core.paged_ok(_page_of(rows, body))


# ---------------- stock_in_bill：其他入库 ----------------

def _stock_in_bill_list(body, session):
    # 其他入库单列表必带 type=2（deep12 §2.2，枚举复用 ELSE=2）
    t = str(body.get("type") or 2)
    return _bill_list("stock_in_bills", _in_bill_view, session, body, {t})


def _else_stock_in_bill_add(body, session):
    """其他入库：/stock_in_bill/else_stock_in_bill_add（QTRK 前缀，入库 +num 并写流水）。"""
    ware_id = body.get("ware_id")
    if not ware_id:
        return core.fail("请选择仓库")
    goods_map, ware_map, user_map = _maps()
    items = _parse_items(body.get("item_list"), goods_map)
    if not items:
        return core.fail("请选择入库商品")
    for it in items:
        if it["num"] <= 0:
            return core.fail("%s的入库数量填写错误" % it["goods"].get("name"))
    bills = core.Store("stock_in_bills")
    bill = bills.add({
        "type": 2, "enterprise_id": _eid(session),
        "code": str(body.get("code") or "") or core.next_code("QTRK", bills.all(), width=3),
        "ware_id": ware_id, "bill_date": body.get("bill_date") or core.now("%Y-%m-%d"),
        "stock_type_id": body.get("stock_type_id"), "supplier_id": body.get("supplier_id"),
        "consignee_id": body.get("consignee_id") or session.get("id"),
        "remark": body.get("remark"), "item_list": items, "audit_status": 1,
    }, code_field="code")
    gs = core.Store("goods_stocks")
    for it in items:
        row = _stock_row(gs, goods_map, it["goods_id"], ware_id, _eid(session))
        row["num"] = f8(_f(row.get("num")) + it["num"])
        gs.flush()
        _write_flow(bill, it, 1, 3, ware_id, _f(row["num"]), session)  # bill_type=3 其他入库单
    return core.ok(_in_bill_view(bills.get(bill["id"]), goods_map, ware_map, user_map, session))


# ---------------- stock_out_bill：其他出库 / 生产出库 ----------------

def _stock_out_bill_list(body, session):
    t = str(body.get("type") or 2)  # 2 其他出库 / 3 生产出库（STOCK_OUT_BILL_TYPE）
    return _bill_list("stock_out_bills", _out_bill_view, session, body, {t})


def _make_out_bill(body, session, items, bill_type, code_prefix, extra=None):
    bills = core.Store("stock_out_bills")
    bill = bills.add(dict({
        "type": bill_type, "enterprise_id": _eid(session),
        "code": str(body.get("code") or "") or core.next_code(code_prefix, bills.all(), width=3),
        "ware_id": body.get("ware_id"), "bill_date": body.get("bill_date") or core.now("%Y-%m-%d"),
        "stock_type_id": body.get("stock_type_id"),
        "shipper_id": body.get("shipper_id") or session.get("id"),
        "remark": body.get("remark"), "item_list": items, "audit_status": 1,
    }, **(extra or {})), code_field="code")
    return bills, bill


def _else_stock_out_bill_add(body, session):
    """其他出库（QTCK）：可用<出库数 → 差额生成 purchase_plans 简化记录并返回提示文案。"""
    ware_id = body.get("ware_id")
    if not ware_id:
        return core.fail("请选择仓库")
    goods_map, ware_map, user_map = _maps()
    items = _parse_items(body.get("item_list"), goods_map)
    if not items:
        return core.fail("请选择出库商品")
    for it in items:
        if it["num"] <= 0:
            return core.fail("%s的出库数量填写错误" % it["goods"].get("name"))
    if not body.get("stock_type_id"):
        body = dict(body, stock_type_id=946702)  # 默认 普通出库
    bills, bill = _make_out_bill(body, session, items, 2, "QTCK")
    gs = core.Store("goods_stocks")
    _lock_total, lock_ware, lock_null = _lock_maps(core.Store("goods_inventory_locks").all())
    plans, tips = core.Store("purchase_plans"), []
    for it in items:
        row = _stock_row(gs, goods_map, it["goods_id"], ware_id, _eid(session))
        num = _f(row.get("num"))
        # 出库校验口径（deep12 §3.2）：可用 = 仓维库存 − 有效占用（未挂仓的生产计划占用全额计入，真实 JH 占用无仓维）
        avail = num - (lock_ware.get((str(it["goods_id"]), str(ware_id)), 0.0) + lock_null.get(it["goods_id"], 0.0))
        if avail < it["num"]:  # deep12 §3.2：差额 = 出库数 − 可用，unit_price = cost_price
            diff = round(it["num"] - avail, 8)
            plans.add({"goods_id": it["goods_id"], "goods": it["goods"], "num": f8(diff),
                       "unit_price": it["cost_price"], "source": "else_stock_out_bill",
                       "stock_out_bill_id": bill["id"], "remark": "可用库存不足自动生成"}, code_field="code")
            tips.append("%s可用库存不足（可用%s，出库数%s），已生成采购申请，采购数量%s" %
                        (it["goods"].get("name"), _s(avail), _s(it["num"]), _s(diff)))
        row["num"] = f8(num - it["num"])
        gs.flush()
        _write_flow(bill, it, 2, 5, ware_id, _f(row["num"]), session)  # bill_type=5 其他出库单
    resp = core.ok(_out_bill_view(bills.get(bill["id"]), goods_map, ware_map, user_map, session))
    if tips:
        resp["msg"] = "；".join(tips)
    return resp


def _production_stock_out_bill_add(body, session):
    """生产出库（SCCK，bill_type=12）：加工单/外协领料出库，扣减库存并消化生产计划占用。"""
    ware_id = body.get("ware_id")
    if not ware_id:
        return core.fail("请选择仓库")
    goods_map, ware_map, user_map = _maps()
    items = _parse_items(body.get("item_list"), goods_map)
    if not items:
        return core.fail("请选择出库商品")
    psot = int(body.get("production_stock_out_type") or 1)
    extra = {"production_stock_out_type": psot, "operation_type": body.get("operation_type") or 2}
    if body.get("produce_bill_id"):
        extra["produce_bill_id"] = body.get("produce_bill_id")
    if body.get("outsource_produce_bill_id"):
        extra["outsource_produce_bill_id"] = body.get("outsource_produce_bill_id")
        extra["production_stock_out_type"] = psot = 2
    body = dict(body, stock_type_id=body.get("stock_type_id") or 950855)  # 生产出库
    bills, bill = _make_out_bill(body, session, items, 3, "SCCK", extra)
    gs = core.Store("goods_stocks")
    for it in items:
        row = _stock_row(gs, goods_map, it["goods_id"], ware_id, _eid(session))
        row["num"] = f8(_f(row.get("num")) - it["num"])
        gs.flush()
        _consume_locks(it["goods_id"], it["num"])  # 领料消化生产计划占用 → 不再扣可用
        _write_flow(bill, it, 2, 12, ware_id, _f(row["num"]), session)  # bill_type=12 生产出库单
    return core.ok(_out_bill_view(bills.get(bill["id"]), goods_map, ware_map, user_map, session))


# ---------------- stock_item：出入库流水 ----------------

def _stock_item_list(body, session):
    si = core.Store("stock_items")
    goods_map, ware_map, user_map = _maps()
    in_bills = {str(r.get("id")): r for r in core.Store("stock_in_bills").all()}
    out_bills = {str(r.get("id")): r for r in core.Store("stock_out_bills").all()}
    codes = _kw(body, "code")
    bill_codes = _kw(body, "bill_code")
    dirs = _kw(body, "direction")
    stids = _kw(body, "stock_type_id")
    bts = _kw(body, "bill_type")
    in_wares = _kw(body, "in_ware_id")
    out_wares = _kw(body, "out_ware_id")
    wares_any = _kw(body, "ware_id")
    kw = (_kw(body, "goods_keyword") or [""])[0].lower()
    ds, de = (_kw(body, "bill_date_start") or [None])[0], (_kw(body, "bill_date_end") or [None])[0]
    rows = []
    for r in si.all():
        g = _goods_info(goods_map, r.get("goods_id"))
        if codes and not any(c in str(r.get("code") or "") for c in codes):
            continue
        if dirs and str(r.get("direction")) not in dirs:
            continue
        if stids and str(r.get("stock_type_id")) not in stids:
            continue
        if bts and str(r.get("bill_type")) not in bts:
            continue
        if in_wares and str(r.get("in_ware_id")) not in in_wares:
            continue
        if out_wares and str(r.get("out_ware_id")) not in out_wares:
            continue
        if wares_any and str(r.get("in_ware_id")) not in wares_any and str(r.get("out_ware_id")) not in wares_any:
            continue
        if ds and str(r.get("bill_date") or "") < ds:
            continue
        if de and str(r.get("bill_date") or "") > de:
            continue
        if kw:
            hay = " ".join(str(x or "") for x in (g.get("name"), g.get("code"), g.get("standard"))).lower()
            if kw not in hay:
                continue
        src_bill = in_bills.get(str(r.get("bill_id"))) or out_bills.get(str(r.get("bill_id")))
        bill_view = {"id": (src_bill or {}).get("id"), "code": (src_bill or {}).get("code")} if src_bill else r.get("bill")
        if bill_codes:
            bc = (bill_view or {}).get("code")
            if not bc or not any(c in str(bc) for c in bill_codes):
                continue
        d = r.get("direction")
        rows.append({
            "id": r.get("id"), "code": r.get("code"), "goods_id": r.get("goods_id"),
            "batch_name": r.get("batch_name") or "无批次",
            "in_ware_id": r.get("in_ware_id"), "out_ware_id": r.get("out_ware_id"),
            "operator_id": r.get("operator_id"), "stock_type_id": r.get("stock_type_id"),
            "bill_id": r.get("bill_id"), "bill_type": r.get("bill_type"),
            "num": r.get("num"), "waste_num": r.get("waste_num") or "0.00000000",
            "selling_price": r.get("selling_price"), "selling_money": r.get("selling_money"),
            "cost_price": r.get("cost_price"), "cost_money": r.get("cost_money"),
            "remark": r.get("remark"), "bill_date": r.get("bill_date"), "created_at": r.get("created_at"),
            "in_storage_location_id": None, "out_storage_location_id": None, "del": 2,
            "customer_contract_item_id": None, "received_money": r.get("received_money") or "0.00000000",
            "return_money": r.get("return_money") or "0.00000000",
            "contract_stock_out_bill_id": None, "contract_stock_out_bill_item_id": None,
            "out_stock_balance_num": r.get("out_stock_balance_num"),
            "in_stock_balance_num": r.get("in_stock_balance_num"),
            "fieldValueList": [], "sum_num": r.get("num"),
            "direction_name": DIRECTION_NAME.get(d, ""), "bill_type_name": BILL_TYPE_NAME.get(r.get("bill_type"), ""),
            "bill_type_class": BILL_TYPE_CLASS.get(r.get("bill_type"), ""),
            "finish_bill_type_class": "", "direction_tag_class": r.get("direction_tag_class") or ("5" if d == 1 else ""),
            "goods": g, "in_ware": r.get("in_ware") or _ware_info(ware_map, r.get("in_ware_id")),
            "out_ware": r.get("out_ware") or _ware_info(ware_map, r.get("out_ware_id")),
            "operator": r.get("operator") or (_user_info(user_map, {"real_name": ""}, r.get("operator_id")) if r.get("operator_id") else None),
            "stock_type": r.get("stock_type") or _stock_type(r.get("stock_type_id")),
            "bill": bill_view,
            "stock_out_bill": r.get("stock_out_bill") or bill_view,
            "finish_bill": None, "in_storage_location": None, "out_storage_location": None,
            "customer_contract_item": None,
        })
    return core.paged_ok(_page_of(rows, body))


# ---------------- dropdown：单据类型字典 / 仓域下拉 ----------------

def _dropdown_stock_bill_type(body, session):
    return core.ok([dict(t) for t in STOCK_BILL_TYPES])


def _ware_options():
    rows = core.Store("wares").all()
    if not rows:  # 无仓库档案种子时按库存行兜底（优先取行内嵌 ware 名）
        seen, rows = set(), []
        for r in core.Store("goods_stocks").all():
            wid = r.get("ware_id")
            if wid is None or str(wid) in seen:
                continue
            seen.add(str(wid))
            emb = r.get("ware") or {}
            rows.append({"id": wid, "name": emb.get("name") or "仓库%s" % wid})
    return [{"id": r.get("id"), "name": r.get("name"), "is_enable": r.get("is_enable", 1) == 1,
             "resp_user_ids": r.get("resp_user_ids") or "", "qr_code_url": r.get("qr_code_url")} for r in rows]


def _get_designate_dropdown(body, session):
    """仓域键部分实现：in_ware_type / out_ware_type / ware / enable_ware / stock_direction（其余键返回空）。"""
    res = {}
    for key in body.get("type_list") or []:
        k = str(key)
        if k == "in_ware_type":
            res[k] = [{"id": i, "name": n} for i, n in IN_WARE_TYPE.items()]
        elif k == "out_ware_type":
            res[k] = [{"id": i, "name": n} for i, n in OUT_WARE_TYPE.items()]
        elif k == "ware":
            res[k] = _ware_options()
        elif k == "enable_ware":
            res[k] = [w for w in _ware_options() if w["is_enable"]]
        elif k == "stock_direction":
            res[k] = [{"v": 1, "name": "入库"}, {"v": 2, "name": "出库"}]
        else:
            res[k] = []
    return core.ok(res)


ROUTES = {
    ("goods_stock", "list"): _goods_stock_list,
    ("goods_stock", "stock_statistics"): _stock_statistics,
    ("goods_stock", "goods_stock_warning_tip"): _goods_stock_warning_tip,
    ("goods_stock", "get_stock_below_low_limit_count"): _get_stock_below_low_limit_count,
    ("stock_in_bill", "list"): _stock_in_bill_list,
    ("stock_in_bill", "else_stock_in_bill_add"): _else_stock_in_bill_add,
    ("stock_out_bill", "list"): _stock_out_bill_list,
    ("stock_out_bill", "else_stock_out_bill_add"): _else_stock_out_bill_add,
    ("stock_out_bill", "production_stock_out_bill_add"): _production_stock_out_bill_add,
    ("stock_item", "list"): _stock_item_list,
    ("dropdown", "stock_bill_type"): _dropdown_stock_bill_type,
    ("dropdown", "get_designate_dropdown"): _get_designate_dropdown,
}
