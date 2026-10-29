# -*- coding: utf-8 -*-
"""B12 生产计划域（produce_schedule）：查询与维护端点。
依据：deep\\22-项目与生产计划.md
      §5.1 列表（筛选字段/快捷筛选/进度三列 tooltip/行字段全集）
      §5.2 创建/修改（保存载荷 item_list、校验文案、/produce_schedule/add|edit）
      §5.3 详情（/produce_schedule/detail → 头 + item_list）
      §0.1 编号 JH+YYYYMMDD+3位（实测 JH20261023001）
      §9  接口清单存活状态（produce_plan 族原站全族 404 → 本模块完全不注册，server 路由缺失自然 fail("404")）
字段对齐 api_dump/deep_m14/plan_list.json / plan_detail.json 实测行结构。

范围约定（briefing 规则 + 任务指令）：
- 导入三动作 download_import_template / import_template_header / web_import 由 io_xlsx 持有，本模块【不重定义】
  （io_xlsx._imp_produce_schedule 落 produce_schedules 种子：扁平单商品行 code/name/goods/plan_num/...，本模块装饰层兼容）
- 排产投放（produce_schedule_analysis 族 / add_async_produce_bill 等）属排产/物料分析模块，不在本文件；
  produce_schedule 自身无"投放"动作（deep\\22 §7.4 全在 produce_schedule_analysis 域）→ 不实现
- 已分析计划（has_analysis=1）修改/删除入口整体消失（deep\\22 §10.3）→ 服务端同样拦截
- status_count 实测本租户返回 data:[]（deep\\22 §5.1），前端按 analysis_status_count.* 等键取数；
  demo 按 produce.py 先例返回真实计数（键名照前端读取路径，deep\\22 §5.1 quickFilterData）
"""
import core

# ---------------- 枚举（deep\22 §5.1 / §9，BOOL 族；tag_class 实测：已分析"5"/未分析"4"） ----------------
ANALYSIS_NAME = {1: "已分析", 2: "未分析"}
ANALYSIS_TAG = {1: "5", 2: "4"}
GOODS_SOURCE_NAME = {1: "自产", 2: "外协加工", 3: "外购"}   # GOODS_SOURCE（deep\22 §9）
GOODS_SOURCE_TAG = {1: "3", 2: "3", 3: "3"}                 # 实测自产 item source_tag_class="3"，余为 demo 占位
# 进度快捷筛选值（PROIECT_MANAGE.SCHEDULE 同构：1=已完成 / 2=未完成，deep\22 §1.2/§5.1）
PROGRESS_YES = "1"
PROGRESS_NO = "2"

# ---------------- 存取（单例） ----------------
_STORE = None


def _schedules():
    global _STORE
    if _STORE is None:
        _STORE = core.Store("produce_schedules")
    return _STORE


def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _n8(x):
    return "%.8f" % _f(x)


def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            nm = u.get("real_name") or u.get("name") or str(uid)
            return {"id": u.get("id"), "name": nm, "real_name": nm}
    return {"id": uid, "name": "用户%s" % uid, "real_name": "用户%s" % uid}


def _ref_goods(goods_id):
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(goods_id or ""):
            return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
                    "standard": g.get("standard"), "unit": g.get("unit")}
    return None


def _boms_of(goods_id):
    """商品可挂 BOM（默认 BOM 在前）。"""
    if goods_id in (None, ""):
        return []
    out = [b for b in core.Store("boms").all() if str(b.get("goods_id")) == str(goods_id)]
    out.sort(key=lambda b: (str(b.get("is_default") or 2) != "1",))
    return out


def _default_bom(goods_id):
    bs = _boms_of(goods_id)
    return bs[0] if bs else None


def _bom_mini(b):
    if not b:
        return None
    return {"id": b.get("id"), "name": b.get("name"), "code": b.get("code"),
            "goods_id": b.get("goods_id"), "is_default": b.get("is_default") or 2,
            "has_reference_structure": False}


def _bom_list_mini(goods_id):
    return [_bom_mini(b) for b in _boms_of(goods_id)]

# ---------------- 投产进度（deep\\22 §5.1 三个 tooltip 口径） ----------------
# 加工投产进度 = 已创建加工单计划数 / 总计划数；外协同构；采购进度 = 已采购的数量 / 总数量


def _items_of(r):
    """明细行：原生 item_list 或 io_xlsx 导入的扁平单商品行合成。"""
    items = r.get("item_list")
    if isinstance(items, list) and items:
        return items
    g = r.get("goods") or {}
    return [{"goods_id": r.get("goods_id") or g.get("id"), "goods": g or None,
             "num": r.get("plan_num") if r.get("plan_num") is not None else r.get("num"),
             "goods_source": r.get("goods_source") or 1}]


def _linked_bills(r):
    """同计划关联的加工单：按 produce_schedule_id（produce.py add 落库）或编号（B11 导出行 produce_schedule_code）。"""
    rid = str(r.get("id") or "")
    code = r.get("code")
    out = []
    for b in core.Store("produce_bills").all():
        if rid and str(b.get("produce_schedule_id") or "") == rid:
            out.append(b)
        elif code and str(b.get("produce_schedule_code") or "") == str(code):
            out.append(b)
    return out


def _linked(store_name, r):
    rid = str(r.get("id") or "")
    if not rid:
        return []
    return [x for x in core.Store(store_name).all() if str(x.get("produce_schedule_id") or "") == rid]


def _progress(r):
    """{维度: (总计划数, 已创建数)}：总计划数按明细来源分流（自产→加工单、外协→外协单），采购总数量属材料维度、
    未做物料分析时为 0（实测已分析行 purchase_bill_total_count 来自分析结果，如 1438，deep\\22 §5.1）。"""
    items = _items_of(r)
    pt = sum(_f(i.get("num")) for i in items if str(i.get("goods_source") or 1) == "1")
    ot = sum(_f(i.get("num")) for i in items if str(i.get("goods_source")) == "2")
    pp = sum(_f(b.get("num")) for b in _linked_bills(r))
    op = sum(_f(b.get("num")) for b in _linked("outsource_produce_bills", r))
    up = sum(_f(b.get("num")) for b in _linked("purchase_bills", r))
    return {"produce": (pt, pp), "outsource": (ot, op), "purchase": (0.0, up)}


def _done(total, prog):
    return _f(total) > 0 and _f(prog) >= _f(total)

# ---------------- 装饰（列表/详情行结构对齐 plan_list.json 实测） ----------------


def _dec(r):
    r = dict(r)
    ha = r.get("has_analysis") or 2
    p = _progress(r)
    r["has_analysis"] = ha
    r["has_analysis_name"] = ANALYSIS_NAME.get(ha, "")
    r["has_analysis_tag_class"] = ANALYSIS_TAG.get(ha, "")
    # 策略七开关/分析元数据：未分析时置空（字段形状对齐实测行）
    for k in ("is_invalid", "analysis_time", "analysis_user_id", "analysis_remark",
              "analysis_strategy_id", "ware_ids"):
        r.setdefault(k, None if k != "is_invalid" else 2)
    r.setdefault("delivery_date", None)
    r.setdefault("remark", None)
    r.setdefault("fieldValueList", [])
    r["produce_bill_total_count"] = _n8(p["produce"][0])
    r["outsource_bill_total_count"] = _n8(p["outsource"][0])
    r["purchase_bill_total_count"] = _n8(p["purchase"][0])
    r["produce_bill_progress_count"] = _n8(p["produce"][1])
    r["outsource_bill_progress_count"] = _n8(p["outsource"][1])
    r["purchase_bill_progress_count"] = _n8(p["purchase"][1])
    if not r.get("create_user"):
        # B11 聚合行只带 create_user_name（加工单导出行字段）→ 合成展示用户
        if r.get("create_user_name"):
            nm = r["create_user_name"]
            r["create_user"] = {"id": r.get("create_user_id"), "name": nm, "real_name": nm}
        else:
            r["create_user"] = _user(r.get("create_user_id"))
    return r


def _dec_item(it, r):
    it = dict(it)
    g = it.get("goods") or _ref_goods(it.get("goods_id")) or {}
    src = it.get("goods_source") or 1
    it["goods_id"] = it.get("goods_id") or g.get("id")
    if g:
        it["goods"] = g
    it["goods_source"] = src
    it["source_name"] = GOODS_SOURCE_NAME.get(src, "")
    it["source_tag_class"] = GOODS_SOURCE_TAG.get(src, "3")
    it["num"] = _n8(it.get("num"))
    it["goods_bom_id"] = it.get("goods_bom_id") or (_default_bom(it.get("goods_id")) or {}).get("id")
    it["goods_bom"] = _bom_mini(_find_bom(it.get("goods_bom_id")))
    it["goods_bom_list"] = _bom_list_mini(it.get("goods_id"))
    it.setdefault("customer_contract_id", None)
    it.setdefault("customer_contract_item_id", None)
    it.setdefault("remark", None)
    return it


def _find_bom(bom_id):
    if bom_id in (None, ""):
        return None
    for b in core.Store("boms").all():
        if str(b.get("id")) == str(bom_id):
            return b
    return None

# ---------------- 列表筛选（deep\\22 §5.1 S 常量 + 快捷筛选组） ----------------
_SORT_FIELDS = {"code", "name", "has_analysis", "delivery_date", "created_at"}  # PRODUCTION_PLAN_VIEW_SORT_ENUM（除 create_user_id 人维度）


def _filter(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        if s("name") and s("name") not in str(r.get("name") or ""):
            continue
        if s("create_user_name"):
            cu = r.get("create_user") or {}
            hay = "%s%s%s" % (cu.get("real_name") or "", cu.get("name") or "", r.get("create_user_name") or "")
            if s("create_user_name") not in hay:
                continue
        if s("has_analysis") not in ("", "all") and str(r.get("has_analysis") or 2) != s("has_analysis"):
            continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        # 三个投产进度快捷组：1=已完成 / 2=未完成
        p = _progress(r)
        for key, dim in (("produce_bill_progress_status", "produce"),
                         ("outsource_bill_progress_status", "outsource"),
                         ("purchase_bill_progress_status", "purchase")):
            v = s(key)
            if v not in ("", "all"):
                t, g = p[dim]
                if v == PROGRESS_YES and not _done(t, g):
                    continue
                if v == PROGRESS_NO and _done(t, g):
                    continue
        out.append(r)
    sf = str(body.get("sortField") or "")
    if sf in _SORT_FIELDS:
        rev = str(body.get("sortOrder") or "") == "descend"
        out = sorted(out, key=lambda r: (r.get(sf) is None, str(r.get(sf) or "")), reverse=rev)
    return out


def _paged(store, rows, body):
    backup = store.rows
    store.rows = rows
    try:
        return core.paged_ok(store.page(body))
    finally:
        store.rows = backup


def _list(body, session):
    store = _schedules()
    rows = _filter(store.all(), body)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_dec(r) for r in res["data"]["list"]]
    return res


def _list_by_filter_view(body, session):
    """页面主接口（type=39 视图，本租户仅"全部视图"→ filter_view_id 不参与过滤，接受即忽略）。"""
    return _list(body, session)


def _status_count(body, session):
    """快捷筛选四组计数（键名照前端读取路径，deep\\22 §5.1 quickFilterData）。"""
    rows = _filter(_schedules().all(), body)
    data = {"analysis_status_count": {"analyzed_count": 0, "unanalyzed_count": 0},
            "produce_bill_progress_count": {"completed_count": 0, "uncompleted_count": 0},
            "outsource_bill_progress_count": {"completed_count": 0, "uncompleted_count": 0},
            "purchase_bill_progress_count": {"completed_count": 0, "uncompleted_count": 0}}
    for r in rows:
        if r.get("has_analysis") == 1:
            data["analysis_status_count"]["analyzed_count"] += 1
        else:
            data["analysis_status_count"]["unanalyzed_count"] += 1
        p = _progress(r)
        for dim in ("produce", "outsource", "purchase"):
            key = dim + "_bill_progress_count"
            t, g = p[dim]
            data[key]["completed_count" if _done(t, g) else "uncompleted_count"] += 1
    return core.ok(data)

# ---------------- 增删改查 ----------------


def _detail(body, session):
    r = _schedules().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    d = _dec(r)
    d["item_list"] = [_dec_item(it, r) for it in _items_of(r)]
    return core.ok(d)


def _check_name(name):
    if not str(name or "").strip():
        return None, core.fail("请输入名称")
    if len(str(name)) > 255:
        return None, core.fail("长度超出限制")
    return str(name).strip(), None


def _build_item(it, schedule_id):
    """保存载荷行（deep\\22 §5.2）→ 落库明细：{goods_id, goods_source, goods_bom_id, num, 合同两键, remark}。"""
    g = _ref_goods(it.get("goods_id")) or it.get("goods") or {}
    goods_id = it.get("goods_id") or g.get("id")
    bom = _find_bom(it.get("goods_bom_id")) or _default_bom(goods_id)
    return {
        "produce_schedule_id": schedule_id,
        "customer_contract_id": it.get("customer_contract_id"),
        "customer_contract_item_id": it.get("customer_contract_item_id"),
        "goods_id": goods_id,
        "goods": g or None,
        "goods_source": it.get("goods_source") or 1,
        "goods_bom_id": (bom or {}).get("id"),
        "num": _n8(it.get("num")),
        "remark": it.get("remark"),
    }


def _validate_items(item_list):
    """明细校验（deep\\22 §5.2 保存校验逐字；BOM 必填前端已拦，服务端 demo 放行）。"""
    if not item_list:
        return core.fail("请添加商品明细")
    for it in item_list:
        if _f(it.get("num")) <= 0:
            return core.fail("每条商品明细的计划数量不能为空")
    return None


def _add(body, session):
    store = _schedules()
    name, err = _check_name(body.get("name"))
    if err:
        return err
    code = str(body.get("code") or "").strip()
    if len(code) > 50:
        return core.fail("长度超出限制")
    if not code:  # 忽略将自动生成：JH+YYYYMMDD+3位（deep\\22 §0.1 实测）
        code = core.next_code("JH", store.all(), width=3)
    item_list = body.get("item_list") or []
    err = _validate_items(item_list)
    if err:
        return err
    row = {
        "enterprise_id": session.get("enterprise_id"),
        "code": code,
        "name": name,
        "delivery_date": body.get("delivery_date"),
        "remark": body.get("remark"),
        "has_analysis": 2,          # 新建计划未分析（deep\\22 §10.3：分析后才锁定）
        "is_invalid": 2,
        "analysis_time": None, "analysis_user_id": None, "analysis_remark": None,
        "analysis_strategy_id": None, "ware_ids": None,
        "create_user_id": session.get("id"),
        "item_list": [], "fieldValueList": [],
    }
    row = store.add(row)
    row["item_list"] = [_build_item(it, row["id"]) for it in item_list]
    store.flush()
    d = _dec(row)
    d["item_list"] = [_dec_item(it, row) for it in row["item_list"]]
    return core.ok(d)


def _edit(body, session):
    store = _schedules()
    r = store.get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if r.get("has_analysis") == 1:  # 已分析计划入口整体消失（deep\\22 §5.1/§5.3/§10.3）
        return core.fail("已分析的生产计划不能修改")
    patch = {}
    if "name" in body:
        name, err = _check_name(body.get("name"))
        if err:
            return err
        patch["name"] = name
    if "code" in body:
        code = str(body.get("code") or "").strip()
        if len(code) > 50:
            return core.fail("长度超出限制")
        patch["code"] = code
    for k in ("delivery_date", "remark"):
        if k in body:
            patch[k] = body.get(k)
    if "item_list" in body:
        item_list = body.get("item_list") or []
        err = _validate_items(item_list)
        if err:
            return err
        patch["item_list"] = [_build_item(it, r["id"]) for it in item_list]
    if not patch:
        return core.fail("参数错误")
    store.update(r["id"], patch)
    return core.ok()


def _del(body, session):
    store = _schedules()
    r = store.get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if r.get("has_analysis") == 1:
        return core.fail("已分析的生产计划不能删除")
    store.remove(r["id"])
    return core.ok()

# ---------------- 种子：produce_schedules 为空时从 produce_bills 聚合补齐 ----------------


def _seed():
    """io_xlsx 的 web_import 未跑过时 produce_schedules 为空库 → 按 produce_bills 的
    produce_schedule_code/produce_schedule_name 聚合（B11 加工单导出转换行携带）；
    合成行标 "_seed":1，落 backend\\data\\produce_schedules.json（本模块自建种子文件，不手改他人生成文件）。"""
    store = _schedules()
    if store.rows:
        return
    groups = {}
    order = []
    for b in core.Store("produce_bills").all():
        sc = str(b.get("produce_schedule_code") or "").strip()
        if not sc:
            continue
        if sc not in groups:
            groups[sc] = {"name": b.get("produce_schedule_name") or sc, "items": [],
                          "delivery": None, "created": None, "enterprise_id": b.get("enterprise_id"),
                          "create_user_name": None}
            order.append(sc)
        g = groups[sc]
        if not g["create_user_name"] and b.get("create_user_name"):
            g["create_user_name"] = b.get("create_user_name")
        g["items"].append({
            "goods_id": None,
            "goods": {"id": None, "code": b.get("goods_code"), "name": b.get("goods_name"),
                      "standard": b.get("goods_standard")},
            "goods_source": 1, "num": _n8(b.get("num")),
        })
        if not g["delivery"] and b.get("delivery_date"):
            g["delivery"] = b.get("delivery_date")
        c = b.get("created_at")
        if c and (g["created"] is None or c < g["created"]):
            g["created"] = c
    for sc in order:
        g = groups[sc]
        store.add({
            "enterprise_id": g["enterprise_id"],
            "code": sc, "name": g["name"],
            "delivery_date": g["delivery"], "remark": None,
            "has_analysis": 2, "is_invalid": 2,
            "analysis_time": None, "analysis_user_id": None, "analysis_remark": None,
            "analysis_strategy_id": None, "ware_ids": None,
            "create_user_id": None, "create_user_name": g["create_user_name"],
            "item_list": g["items"], "fieldValueList": [],
            "created_at": g["created"] or core.now(),
            "_seed": 1,
        })


_seed()

# ---------------- 路由 ----------------
ROUTES = {
    # produce_schedule（deep\\22 §5；produce_plan 族 404 不注册）
    ("produce_schedule", "list"): _list,
    ("produce_schedule", "list_by_filter_view"): _list_by_filter_view,
    ("produce_schedule", "status_count"): _status_count,
    ("produce_schedule", "detail"): _detail,
    ("produce_schedule", "add"): _add,
    ("produce_schedule", "edit"): _edit,
    ("produce_schedule", "del"): _del,
    # 注：download_import_template / import_template_header / web_import 由 io_xlsx 注册，此处不重定义
}
