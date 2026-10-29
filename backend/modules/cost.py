# -*- coding: utf-8 -*-
"""B? 成本域（/cost_share/* 成本分摊 + /cost_price_item/* 成本单价台账）。
契约依据（逆向报告）：
- analysis/13-生产管理其他页面.md §1.9：成本分摊页 /apportioned_cost/list（chunk 80595）
  筛选 分摊单号(code)/分摊日期起止(share_date_start|end)/分摊类别(category)/审核状态(status)/
  创建时间起止(created_at_start|end)；工具栏 新增｜批量操作(批量审核)｜统计【分摊总金额】；
  列 分摊单号(code)/分摊日期(share_date)/分摊金额(元)(money)/分摊方案(type_name)/
  分摊类别(category_path_names)/审核状态(status_name)/审核备注(audit_remark)/备注(remark)/
  创建人(create_user.real_name)/创建时间(created_at)；行内 修改/删除；
  详情页路由 /apportioned_cost/detail/:id；接口 /cost_share/list（报告：同名族 add/edit/del/detail/audit，
  具体路径待确认——本模块按系统统一 /<module>/<action> 模式补齐，置信=推断）。
- analysis/12-业务规则.md §3.5/§3.7：/cost_price_item/list（param 含 goods_id、is_data_list=YES、
  月份区间，上月/本月 radio+a-month-picker）；列 单号(bill.code)/单据类型/入库仓库/出库仓库/操作人/
  操作时间/入库(stock_in_num|price|money)/出库(stock_out_*)/结存(num|price|money) 二级表头分组；
  期初 /cost_price_item/detail_by_goods_id_and_type {goods_id, type:14} → 初始单价/初始数量/初始金额。
  ★报告明示：cost_price_item/cost_share 体系与 bom_cost_money_total 是两条独立口径
  （后者只汇总物料成本单价×用量，见 deep/02：BOM 成本=全行平铺和）——本域不掺入 BOM 成本。
- 审核状态枚举沿用全系统 audit_status（api_dump purchase_bills 实测：1 不需审核/2 待审/3 通过/4 退回）。
- 分摊类别/方案字典键 cost_share_category / cost_share_type 在原站 dd 白名单内但本企业实例无数据
  （goods.py dropdown 返回空）——种子类别/方案取值为合成（"_seed":1），非原站字典。
种子：cost_shares.json 由 _seed() 自建（合成行 "_seed":1；分摊明细 item_list 挂真实 produce_bills）；
      cost_price_item 不落库，由 stock_items（真实流水）+ goods（成本单价兜底）现算派生。
"""
from decimal import Decimal

import core

ENTERPRISE_ID = 16807

# ---------------- 枚举（合成/沿用，注释标置信） ----------------

AUDIT_STATUS = {1: "不需审核", 2: "待审", 3: "通过", 4: "退回"}   # api_dump purchase_bills 实测枚举（确定）
SHARE_CATEGORY = {1: "人工成本", 2: "制造费用", 3: "能耗费用"}     # 合成字典（原站实例字典为空）
SHARE_TYPE = {1: "按产量分摊", 2: "按工时分摊"}                    # 合成字典（原站实例字典为空）
INIT_TYPE = 14                                                     # 期初类型（12 报告 §3.5：{goods_id, type:14} 确定）

# ---------------- 数字工具（对齐 stock.py 的 8 位小数口径） ----------------

def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _d(x):
    try:
        if x in (None, ""):
            return Decimal(0)
        return Decimal(str(x))
    except Exception:
        return Decimal(0)


def _f8(x):
    return "%.8f" % _f(x)


def _f2(x):
    return "%.2f" % _f(x)


def _div8(money, num):
    """结存单价 = 金额/数量（8 位小数，数量为 0 时取 0）。"""
    n = _d(num)
    if n == 0:
        return _f8(0)
    return _f8(_d(money) / n)


def _page(lst, body, default_size=30):
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return {"list": lst[(pageNo - 1) * pageSize: pageNo * pageSize], "total": len(lst),
            "pageNo": pageNo, "pageSize": pageSize}


def _kw(body, key):
    v = body.get(key)
    return str(v).strip() if v not in (None, "") else ""


def _user(uid):
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid or ""):
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name") or str(uid)}
    return {"id": uid, "real_name": "用户%s" % uid}

# =====================================================================
# cost_share 成本分摊（13 报告 §1.9；list 确定，其余同名族=推断补齐）
# =====================================================================

def _shares():
    return core.Store("cost_shares")


def _norm_items(raw):
    """归一分摊明细行：{produce_bill_id, money} 必备，其余字段可缺（后端补挂加工单快照）。"""
    out = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        pid = it.get("produce_bill_id")
        if pid in (None, "") and isinstance(it.get("produce_bill"), dict):
            pid = it["produce_bill"].get("id")
        out.append({"produce_bill_id": pid, "money": _f2(it.get("money") or 0),
                    "remark": it.get("remark") or ""})
    return out


def _fill_items(items):
    """给明细行挂加工单/商品快照（列表/详情展示用，来源=真实 produce_bills 种子）。"""
    bills = core.Store("produce_bills")
    for it in items:
        b = bills.get(it.get("produce_bill_id"))
        if b:
            it["produce_bill_code"] = b.get("code")
            it["goods_id"] = b.get("goods_id")
            it["goods_name"] = b.get("goods_name")
            it["goods_code"] = b.get("goods_code")
            it["num"] = b.get("num")
        else:
            it.setdefault("produce_bill_code", "")
            it.setdefault("goods_name", "")
            it.setdefault("goods_code", "")
            it.setdefault("num", "0.00")
    return items


def _decorate(r):
    """补展示冗余：type_name/category_path_names/status_name/create_user（种子已带，兜底防手工行缺字段）。"""
    r.setdefault("type_name", SHARE_TYPE.get(r.get("type"), ""))
    r.setdefault("category_path_names", SHARE_CATEGORY.get(r.get("category"), ""))
    r["status_name"] = AUDIT_STATUS.get(r.get("status"), "")
    if not r.get("create_user") and r.get("create_user_id"):
        r["create_user"] = _user(r["create_user_id"])
    r.setdefault("audit_remark", "")
    r.setdefault("remark", "")
    return r


def _filter_shares(rows, body):
    code = _kw(body, "code")
    cat = _kw(body, "category")
    status = body.get("status")
    ds, de = _kw(body, "share_date_start"), _kw(body, "share_date_end")
    cs, ce = _kw(body, "created_at_start"), _kw(body, "created_at_end")
    out = []
    for r in rows:
        if code and code not in str(r.get("code") or ""):
            continue
        if cat and str(r.get("category") or "") != cat:
            continue
        if status not in (None, "", "all"):
            sts = status if isinstance(status, list) else [status]
            if str(r.get("status")) not in [str(x) for x in sts]:
                continue
        sd = str(r.get("share_date") or "")
        if ds and sd < ds[:10]:
            continue
        if de and sd > de[:10]:
            continue
        ct = str(r.get("created_at") or "")
        if cs and ct < cs:
            continue
        if ce and ct > ce + " 23:59:59":
            continue
        out.append(r)
    return out


def _share_list(body, session):
    rows = _filter_shares(_shares().all(), body)
    return core.paged_ok(_page([_decorate(dict(r)) for r in rows], body))


def _share_detail(body, session):
    r = _shares().get(body.get("id"))
    if not r:
        return core.fail("数据不存在或已经被删除")
    d = _decorate(dict(r))
    d["item_list"] = _fill_items([dict(i) for i in (r.get("item_list") or [])])
    # 明细合计（平铺口径：Σ明细金额，与表头 money 对账用）
    d["item_money_total"] = _f2(sum(_f(i.get("money")) for i in d["item_list"]))
    return core.ok(d)


def _next_share_code(rows):
    """分摊单号：原站无样例（合成种子规则 FT+YYYYMMDD+3位，置信=合成）。"""
    import time
    stamp = time.strftime("%Y%m%d")
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith("FT" + stamp):
            try:
                mx = max(mx, int(c[10:]))
            except ValueError:
                pass
    return "FT" + stamp + str(mx + 1).zfill(3)


def _share_add(body, session):
    st = _shares()
    items = _norm_items(body.get("item_list"))
    money = body.get("money")
    if money in (None, ""):
        money = _f2(sum(_f(i["money"]) for i in items))   # 明细平铺和兜底
    if _f(money) <= 0:
        return core.fail("请填写分摊金额")
    if not items:
        return core.fail("请选择分摊的加工单")
    row = st.add({
        "enterprise_id": (session or {}).get("enterprise_id") or ENTERPRISE_ID,
        "code": str(body.get("code") or "").strip() or _next_share_code(st.all()),
        "share_date": body.get("share_date") or core.now("%Y-%m-%d"),
        "money": _f2(money),
        "type": int(body.get("type") or 1) if str(body.get("type") or "1").isdigit() else 1,
        "category": int(body.get("category") or 1) if str(body.get("category") or "1").isdigit() else 1,
        "status": 2,                       # 新建=待审（沿用 audit_status 枚举）
        "audit_remark": "",
        "remark": body.get("remark") or "",
        "create_user_id": (session or {}).get("id"),
        "create_user": _user((session or {}).get("id")) if session else None,
        "item_list": items,
        "_seed": 2,                        # 运行期用户自建行（非合成种子）
    })
    row["type_name"] = SHARE_TYPE.get(row["type"], "")
    row["category_path_names"] = SHARE_CATEGORY.get(row["category"], "")
    st.flush()
    return core.ok(_decorate(dict(row)))


def _share_edit(body, session):
    st = _shares()
    r = st.get(body.get("id"))
    if not r:
        return core.fail("数据不存在或已经被删除")
    patch = {}
    if _kw(body, "code"):
        patch["code"] = _kw(body, "code")
    if "share_date" in body:
        patch["share_date"] = body.get("share_date")
    if "type" in body:
        patch["type"] = int(body["type"]) if str(body["type"]).isdigit() else r.get("type")
        patch["type_name"] = SHARE_TYPE.get(patch["type"], "")
    if "category" in body:
        patch["category"] = int(body["category"]) if str(body["category"]).isdigit() else r.get("category")
        patch["category_path_names"] = SHARE_CATEGORY.get(patch["category"], "")
    if "remark" in body:
        patch["remark"] = body.get("remark") or ""
    if body.get("item_list") is not None:
        items = _norm_items(body.get("item_list"))
        if not items:
            return core.fail("请选择分摊的加工单")
        patch["item_list"] = items
        if body.get("money") in (None, ""):
            patch["money"] = _f2(sum(_f(i["money"]) for i in items))
    if body.get("money") not in (None, ""):
        if _f(body["money"]) <= 0:
            return core.fail("请填写分摊金额")
        patch["money"] = _f2(body["money"])
    st.update(r.get("id"), patch)
    return core.ok(_decorate(dict(st.get(r.get("id")))))


def _share_del(body, session):
    st = _shares()
    if not st.get(body.get("id")):
        return core.fail("数据不存在或已经被删除")
    st.remove(body.get("id"))
    return core.ok()


def _share_audit(body, session):
    """批量审核（13 报告 §1.9 工具栏「批量操作-批量审核」）：
    {id_array|ids, status?=3 通过|4 退回, audit_remark?}，部分成功语义对齐 produce_bill 批量族。"""
    ids = body.get("id_array") or body.get("ids") or ([body.get("id")] if body.get("id") else [])
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    if not ids:
        return core.fail("请选择分摊单")
    try:
        status = int(body.get("status") or 3)
    except Exception:
        status = 3
    if status not in (3, 4):
        return core.fail("参数错误")
    st = _shares()
    sc = ec = 0
    msgs = []
    for i in ids:
        r = st.get(i)
        if not r:
            ec += 1
            if "数据不存在或已经被删除" not in msgs:
                msgs.append("数据不存在或已经被删除")
            continue
        st.update(r.get("id"), {"status": status, "audit_remark": body.get("audit_remark") or r.get("audit_remark") or ""})
        sc += 1
    data = {"success_count": sc, "error_count": ec}
    if ec:
        data["error_msg"] = "；".join(msgs)
    return core.ok(data)

# =====================================================================
# cost_price_item 成本单价台账（12 报告 §3.5/§3.7，确定）
# 数据源：stock_items 真实流水现算派生（不落库）；单价缺省回落 goods.cost_money
# =====================================================================

def _items_store():
    return core.Store("stock_items")


def _direction(r):
    if r.get("direction") in (1, 2):
        return r["direction"]
    if r.get("in_ware_id") or r.get("in_ware"):
        return 1
    return 2


def _goods(goods_id):
    return core.Store("goods").get(goods_id)


def _ware(obj_id, obj):
    if isinstance(obj, dict):
        return obj
    if obj_id in (None, ""):
        return None
    for w in core.Store("wares").all():
        if str(w.get("id")) == str(obj_id):
            return {"id": w.get("id"), "name": w.get("name")}
    return {"id": obj_id, "name": ""}


def _month_range(body):
    """月份区间归一：接受 month / start_month|end_month / month_start|month_end / bill_date_start|end。
    返回 (起, 止) 闭开日期串（YYYY-MM-DD），无则 (None, None)。radio 上月/本月由前端折算成月份值上送。"""
    m = _kw(body, "month") or _kw(body, "month_date")
    s = _kw(body, "start_month") or _kw(body, "month_start") or _kw(body, "bill_date_start")
    e = _kw(body, "end_month") or _kw(body, "month_end") or _kw(body, "bill_date_end")

    def _first_of_month(v):
        v = str(v)[:7]
        y, mo = int(v[:4]), int(v[5:7])
        ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return "%04d-%02d-01" % (y, mo), "%04d-%02d-01" % (ny, nm)

    try:
        if m:
            return _first_of_month(m)
        if s:
            st, _ = _first_of_month(s)
        else:
            st = None
        if e:
            _, en = _first_of_month(e)
        else:
            en = None
        if st or en:
            return st or "0000-01-01", en or "9999-12-31"
    except Exception:
        pass
    return None, None


def _flow_price(r, g):
    """行成本单价：流水 cost_price 缺省回落商品 cost_money（deep/02 §1.3：行单价与商品当前价一致）。"""
    cp = r.get("cost_price")
    if cp in (None, ""):
        cp = (g or {}).get("cost_money")
    return cp


def _ledger(goods_id, body=None):
    """单商品台账：流水按时间排序，累加 结存数量/金额（出库按其成本单价扣减），返回 (台账行列表, 期初)。
    期初 = 所选月份起始前的累计结存（无月份参数时恒 0——全期期初）。"""
    body = body or {}
    ms, me = _month_range(body)
    src = [r for r in _items_store().all() if str(r.get("goods_id")) == str(goods_id)]
    src.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))))
    g = _goods(goods_id)
    num = money = Decimal(0)
    init_num = init_money = Decimal(0)
    out = []
    for r in src:
        d = _direction(r)
        n = _d(r.get("num"))
        price = _d(_flow_price(r, g))
        row_money = _d(r.get("cost_money")) if r.get("cost_money") not in (None, "") else (n * price)
        day = str(r.get("bill_date") or str(r.get("created_at") or "")[:10])
        if ms and day < ms:
            # 期初前的流水：只进期初累计，不进台账
            if d == 1:
                init_num += n
                init_money += row_money
            else:
                init_num -= n
                init_money -= row_money
            num, money = init_num, init_money
            continue
        num += n if d == 1 else -n
        money += row_money if d == 1 else -row_money
        if me and day >= me:
            continue                     # 月末之后的行不进台账（结存已累计至上月末口径）
        in_flag = d == 1
        out.append({
            "id": r.get("id"), "code": r.get("code"), "goods_id": r.get("goods_id"),
            "bill": r.get("bill") or {"id": r.get("bill_id")},
            "bill_id": r.get("bill_id"), "bill_type": r.get("bill_type"),
            "bill_type_name": r.get("bill_type_name") or "",
            "in_ware": _ware(r.get("in_ware_id"), r.get("in_ware")) if in_flag else None,
            "out_ware": _ware(r.get("out_ware_id"), r.get("out_ware")) if not in_flag else None,
            "operator": r.get("operator") or (_user(r.get("operator_id")) if r.get("operator_id") else None),
            "created_at": r.get("created_at"), "bill_date": r.get("bill_date"),
            "stock_in_num": _f8(n) if in_flag else None,
            "stock_in_price": _f8(price) if in_flag else None,
            "stock_in_money": _f8(row_money) if in_flag else None,
            "stock_out_num": _f8(n) if not in_flag else None,
            "stock_out_price": _f8(price) if not in_flag else None,
            "stock_out_money": _f8(row_money) if not in_flag else None,
            "num": _f8(num), "price": _div8(money, num), "money": _f8(money),
            "del": 2,
        })
    init = {"goods_id": goods_id, "type": INIT_TYPE,
            "num": _f8(init_num), "price": _div8(init_money, init_num), "money": _f8(init_money)}
    return out, init


def _cp_list(body, session):
    """成本明细台账：/cost_price_item/list（param goods_id、is_data_list、月份区间）。
    每行 = 一条出入库流水的 入库/出库/结存 三栏（12 报告 §3.7 二级表头）。"""
    goods_id = _kw(body, "goods_id")
    rows, init = _ledger(goods_id, body) if goods_id else ([], None)
    if not goods_id:
        # 无 goods_id：全商品流水台账（通用兜底，按商品+时间排序）
        all_rows = []
        for g in core.Store("goods").all():
            lr, _ = _ledger(g.get("id"), body)
            all_rows.extend(lr)
        all_rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))))
        for r in all_rows:
            g = _goods(r.get("goods_id"))
            r["goods"] = {"id": r.get("goods_id"), "code": (g or {}).get("code"), "name": (g or {}).get("name"),
                          "standard": (g or {}).get("standard"), "unit": (g or {}).get("unit")}
        res = core.paged_ok(_page(all_rows, body))
    else:
        g = _goods(goods_id)
        for r in rows:
            r["goods"] = {"id": g.get("id") if g else goods_id, "code": (g or {}).get("code"),
                          "name": (g or {}).get("name"), "standard": (g or {}).get("standard"),
                          "unit": (g or {}).get("unit")}
        res = core.paged_ok(_page(rows, body))
    res["data"]["init"] = init          # 期初随列表一并返回（前端亦可单独调期初端点）
    return res


def _cp_init(body, session):
    """期初：/cost_price_item/detail_by_goods_id_and_type {goods_id, type:14}
    → 初始数量(num)/初始单价(price)/初始金额(money)（月份参数决定期初切点）。"""
    goods_id = _kw(body, "goods_id")
    if not goods_id:
        return core.fail("参数错误")
    try:
        t = int(body.get("type") or INIT_TYPE)
    except Exception:
        t = INIT_TYPE
    if t != INIT_TYPE:
        return core.fail("参数错误")     # 仅文档载明的 type:14
    _, init = _ledger(goods_id, body)
    g = _goods(goods_id)
    init["goods"] = {"id": g.get("id") if g else goods_id, "code": (g or {}).get("code"),
                     "name": (g or {}).get("name")}
    return core.ok(init)

# =====================================================================
# 种子：cost_shares.json（合成行 "_seed":1；明细挂真实 produce_bills，按产量占比分摊金额）
# =====================================================================

def _seed():
    st = _shares()
    if st.rows:
        return
    bills = [b for b in core.Store("produce_bills").all()
             if b.get("status") in (2, 3) and b.get("code")]      # 分摊对象=生产中/已完成的加工单
    bills.sort(key=lambda b: str(b.get("created_at") or ""))
    if not bills:
        return
    users = [u.get("id") for u in core.Store("users").all()[:4]] or [63097]
    # 12 条合成分摊单：日期对齐真实加工单日期区间，类别/方案/状态覆盖各枚举值
    plan = [
        # (share_date, money, type, category, status, audit_remark, remark)
        ("2026-09-30", "12800.00", 1, 1, 3, "9月人工已核对", "9月计件工资分摊"),
        ("2026-09-30", "6400.50", 1, 2, 3, "", "9月设备折旧与制费"),
        ("2026-10-08", "3150.00", 2, 3, 3, "电费单已附", "国庆期间能耗分摊"),
        ("2026-10-10", "8600.00", 1, 1, 3, "", "10月上旬计件工资"),
        ("2026-10-12", "2260.75", 1, 2, 4, "发票未到，退回补录", "外协加工制费"),
        ("2026-10-15", "5400.00", 2, 2, 2, "", "10月中旬车间制费"),
        ("2026-10-18", "1980.00", 1, 3, 2, "", "空压机电费分摊"),
        ("2026-10-20", "15300.00", 1, 1, 3, "", "10月下旬计件工资"),
        ("2026-10-22", "4120.30", 2, 3, 2, "", "烤房能耗分摊"),
        ("2026-10-24", "7200.00", 1, 2, 2, "", "模具摊销"),
        ("2026-10-26", "990.00", 1, 3, 3, "金额小，直接通过", "辅料损耗分摊"),
        ("2026-10-28", "6800.00", 1, 1, 2, "", "10月末计件工资预提"),
    ]
    rid = 9600001
    for idx, (share_date, money, typ, cat, status, audit_remark, remark) in enumerate(plan):
        # 每单挂 2~4 条真实加工单，金额按计划数(产量)占比分摊，末行吸收尾差（合成口径，非原站公式）
        k = 2 + idx % 3
        pick = [bills[(idx * 5 + j * 7) % len(bills)] for j in range(k)]
        seen, uniq = set(), []
        for b in pick:
            if str(b.get("id")) not in seen:
                seen.add(str(b.get("id")))
                uniq.append(b)
        total_num = sum(_f(b.get("num")) for b in uniq) or 1.0
        m = _d(money)
        items = []
        acc = Decimal(0)
        for j, b in enumerate(uniq):
            if j == len(uniq) - 1:
                part = m - acc
            else:
                part = (m * _d(_f(b.get("num")) / total_num)).quantize(Decimal("0.01"))
                acc += part
            items.append({"produce_bill_id": b.get("id"), "money": _f2(part), "remark": ""})
        uid = users[idx % len(users)]
        created_at = share_date + " 09:%02d:00" % (10 + idx * 3 % 40)
        row = st.add({
            "id": rid + idx,
            "enterprise_id": ENTERPRISE_ID,
            "code": "FT%s%03d" % (share_date.replace("-", ""), idx + 1),
            "share_date": share_date,
            "money": money,
            "type": typ, "type_name": SHARE_TYPE[typ],
            "category": cat, "category_path_names": SHARE_CATEGORY[cat],
            "status": status, "status_name": AUDIT_STATUS[status],
            "audit_remark": audit_remark,
            "remark": remark,
            "create_user_id": uid, "create_user": _user(uid),
            "item_list": items,
            "_seed": 1,
        })
        row["created_at"] = created_at      # add() 的 now() 覆盖为分摊日期当日
        row["updated_at"] = created_at
    st.flush()


_seed()

ROUTES = {
    # cost_share（13 报告 §1.9：list 确定；add/edit/del/audit 为报告"同名族"按系统统一模式补齐，推断）
    ("cost_share", "list"): _share_list,
    ("cost_share", "detail"): _share_detail,
    ("cost_share", "add"): _share_add,
    ("cost_share", "edit"): _share_edit,
    ("cost_share", "del"): _share_del,
    ("cost_share", "audit"): _share_audit,
    # cost_price_item（12 报告 §3.5/§3.7：list + 期初 detail_by_goods_id_and_type，确定）
    ("cost_price_item", "list"): _cp_list,
    ("cost_price_item", "detail_by_goods_id_and_type"): _cp_init,
}
