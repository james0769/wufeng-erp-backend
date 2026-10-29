# -*- coding: utf-8 -*-
"""B2 商品与字典域（goods / filter_view / columnsFilter / dropdown / field）。
依据：analysis/04-商品列表页.md、05-商品新增与导入.md、deep/14-系统管理域.md、12-业务规则.md
      + api_dump/goods_all.json（行结构）、dd_supplier/dd_team/dd_all_user/dic_type1_unit/
        deep_m6/category_list.json（字典形状）、deep_m5/filter_view_*.json（视图形状）。
要点：
- 分页/响应统一 core.paged_ok / core.ok / core.fail（demo 契约 data:{list,total,...}）。
- BOOL 语义：1=是/启用，2=否/禁用（del=2 未删除）——勿用 0。
- 商品编号 SP+YYYYMMDD+4位（core.next_code 默认即此），手填优先。
- source 枚举 1自产(tag3)/2外协加工(tag7)/3外购(tag1)；viewTab 自产/外购 = source 1/3。
- 派生字段（source_name/category/unit/create_user/category_path_*/supplier_names 等）输出时补全。
"""
import core

# ---- 枚举（12-业务规则 §3.2/§3.8、goods_all.json 实证） ----
GOODS_SOURCE = {1: "自产", 2: "外协加工", 3: "外购"}          # GOODS_SOURCE 枚举
SOURCE_TAG_CLASS = {1: "3", 2: "7", 3: "1"}                   # s-tag 色类：自产3/外购1/外协7

FILTER_VIEW_GOODS_TYPE = 20                                    # FILTER_MODULE_ENUMS.GOODS

# 商品表单可写字段白名单（05 报告 §2.1；扩展字段 fieldValueList 原样透传）
_FORM_FIELDS = [
    "code", "name", "standard", "remark", "source", "unit_id", "category_id",
    "selling_money", "cost_money", "outsource_money", "purchase_money",
    "commission_settle_money", "commission_settle_rate", "produce_type",
    "is_waste_recover", "stagnant_warning_day", "upper_limit", "low_limit",
    "avatars", "profile", "ware_id", "material_inspection_content",
    "material_inspection_content_title", "material_inspection_content_id",
    "fieldValueList",
]

# 视图兜底（filter_views 种子未生成时；id 528445=goods_all.json 抓包的全量视图）
_GOODS_VIEWS_FALLBACK = [
    {"id": 528445, "name": "全部视图", "type": FILTER_VIEW_GOODS_TYPE, "icon_type": 2,
     "icon_type_color_hex": "#00A578", "data_filter_condition": None,
     "default_sort_rule": None, "not_filter_condition": None, "type_name": "商品"},
    {"id": 528446, "name": "自产", "type": FILTER_VIEW_GOODS_TYPE, "icon_type": 1,
     "icon_type_color_hex": "#232A25", "data_filter_condition": {"source": 1},
     "default_sort_rule": None, "not_filter_condition": None, "type_name": "商品"},
    {"id": 528447, "name": "外购", "type": FILTER_VIEW_GOODS_TYPE, "icon_type": 1,
     "icon_type_color_hex": "#232A25", "data_filter_condition": {"source": 3},
     "default_sort_rule": None, "not_filter_condition": None, "type_name": "商品"},
]
# 视图名 → source 过滤（种子视图无 data_filter_condition 时按名兜底）
_VIEW_NAME_SOURCE = {"自产": 1, "外购": 3}

# /dropdown/get_designate_dropdown 101 键白名单中本域常被页面请求的键（无真实数据的先返回空列表）
_DD_EMPTY_KEYS = [
    "device_order_category", "cost_share_category", "cost_share_type",
    "material_inspection_content", "customer_tag", "interval", "warning_level",
    "device", "machine_tool", "supplier", "goods_bom", "user", "ware_user",
    "in_ware_type", "out_ware_type", "department", "ware", "enable_ware",
    "stock_out_bill_type", "stock_in_bill_type", "produce_bill_type",
    "material_requirements_item_status", "produce_plan_status",
    "purchase_bill_stock_in_status", "produce_bill_status",
    "material_draw_status", "matching_template_category", "region_tree",
    "province_city_tree", "purchase_requirements_item_type", "single_status",
    "plan_single_status", "audit_status", "single_node_status",
    "customer_follow_type", "msg_big_type", "produce_plan_material_ready_status",
    "sale_module", "produce_plan_bom_lack_material", "produce_priority",
    "work_order_priority", "customer_financier_direction",
    "customer_financier_status", "customer_invoice_status", "device_type",
    "customer_contract_status", "role", "stock_direction", "produce_type",
    "workshop", "fault_type", "work_order_handler", "craft_price_mode",
    "single_type", "waste_item", "craft_line", "pub_craft",
    "enable_pub_craft", "craft_status", "report_work_record_quality_status",
    "craft_produce_line", "craft_tree_by_produce_line", "team",
    "supplier_tag", "supplier_level", "mp_customer_source",
    "mp_customer_industry", "mp_invoice_type", "inspection_plan_category",
    "service_item_category", "mp_agent_source", "mp_agent_industry",
    "agent_customer_source", "agent_customer_industry", "agent_invoice_type",
    "category_workshop", "device_status", "self_agent_invoice_type",
    "time_granularity", "mall_goods_category", "purchase_progress_status",
    "stock_in_progress_status", "stock_out_progress_status",
    "sales_expense_type", "filter_view_type", "filter_view_icon_type",
    "produce_bill_filter_view", "customer", "machine_tool_type",
    "agv_category_type", "storage_location",
]


# ========================= 通用小工具 =========================

def _alive(row):
    return str(row.get("del", 2)) != "1"


def _as_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x for x in v.replace("，", ",").split(",") if x != ""]
    return [v]


def _page(rows, body, default_size=30):
    """与 Store.page 同契约的手工分页（先筛选后分页）。"""
    page_no = _as_int(body.get("pageNo"), 1) or 1
    page_size = _as_int(body.get("pageSize"), default_size) or default_size
    return {"list": rows[(page_no - 1) * page_size: page_no * page_size],
            "total": len(rows), "pageNo": page_no, "pageSize": page_size}


def _sort_rows(rows, body):
    """远程排序：sortField(dataIndex) + sortOrder(ascend|descend)。"""
    field = body.get("sortField")
    if not field:
        return rows
    order = str(body.get("sortOrder") or "ascend")

    def _key(r):
        v = r
        for part in str(field).split("."):        # 支持 "unit.name" 型 dataIndex
            v = (v or {}).get(part) if isinstance(v, dict) else None
        return (v is None, "" if v is None else str(v))

    return sorted(rows, key=_key, reverse=(order == "descend"))


def _jianpin(name):
    """简拼（12-业务规则 §3.10）：字母数字原样、空格删除、其余符号→下划线。
    汉字声母需拼音库，demo 级以 '_' 占位（原站为后端生成，前端只读）。"""
    out = []
    for ch in str(name or ""):
        if ch.isspace():
            continue
        out.append(ch if ch.isascii() and ch.isalnum() else "_")
    return "".join(out)


# ========================= 字典上下文（每请求构建） =========================

class _Ctx(object):
    """聚合照表：categories(units/suppliers/users) 的一次性索引。"""

    def __init__(self):
        self.categories = [r for r in core.Store("categories").all() if _alive(r)]
        self.cat_by_id = {str(r.get("id")): r for r in self.categories}
        self.children = {}
        for r in self.categories:
            self.children.setdefault(str(r.get("parent_id")), []).append(r)
        self.units = {str(r.get("id")): r for r in core.Store("units").all() if _alive(r)}
        self.suppliers = {str(r.get("id")): r for r in core.Store("suppliers").all() if _alive(r)}
        self.users = {str(r.get("id")): r for r in core.Store("users").all() if _alive(r)}

    def category_chain(self, cid):
        """自根到末级的类别链（ids, names）；防环。"""
        ids, names, cur, guard = [], [], self.cat_by_id.get(str(cid)), 0
        while cur and guard < 20:
            ids.insert(0, cur.get("id"))
            names.insert(0, cur.get("name"))
            cur = self.cat_by_id.get(str(cur.get("parent_id")))
            guard += 1
        return ids, names

    def subtree_ids(self, cid):
        """含自身的子树 id 集合（类别级联按层级过滤）。"""
        out, stack = set(), [str(cid)]
        while stack:
            pid = stack.pop()
            if pid in out:
                continue
            out.add(pid)
            for c in self.children.get(pid, []):
                stack.append(str(c.get("id")))
        return out

    def unit_obj(self, uid):
        u = self.units.get(str(uid)) if uid is not None else None
        return {"id": u.get("id"), "name": u.get("name")} if u else None

    def supplier_rel(self, sid):
        s = self.suppliers.get(str(sid)) if sid is not None else None
        if not s:
            return None
        return {"supplier_id": s.get("id"),
                "supplier": {"id": s.get("id"), "name": s.get("name"), "code": s.get("code")}}

    def user_obj(self, uid):
        u = self.users.get(str(uid)) if uid is not None else None
        if u:
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name"),
                    "has_password": False, "type_name": u.get("type_name") or "",
                    "claw_fun_type_name": "", "qr_code_url": None}
        return {"id": uid, "real_name": "", "has_password": False,
                "type_name": "", "claw_fun_type_name": "", "qr_code_url": None}


# ========================= 商品行派生字段补全 =========================

def _decorate(g, ctx):
    """对照 goods_all.json 首行补全派生/缺省字段（不覆盖已有的业务存量字段）。"""
    gid = g.get("id")
    # 默认值（仅缺失时补）
    g.setdefault("is_enable", 1)
    g.setdefault("is_waste_recover", 2)
    g.setdefault("stagnant_warning_day", 0)
    g.setdefault("upper_limit", None)
    g.setdefault("low_limit", None)
    g.setdefault("produce_type", 0)
    g.setdefault("purchase_money", "0.00000000")
    g.setdefault("goods_bom_count", 0)
    g.setdefault("stock_total_num", 0)
    g.setdefault("stock_total_waste_num", "0")
    g.setdefault("stock_total_available_num", "0.00000000")
    g.setdefault("lock_stock_num", 0)
    g.setdefault("actual_lock_stock_num", 0)
    g.setdefault("is_exist_ware", 2)
    g.setdefault("ware_id", None)
    g.setdefault("ware_name", None)
    g.setdefault("work_order_count", 0)
    g.setdefault("not_finish_work_order_count", 0)
    for k in ("avatars", "attachments", "craft_list", "supplier_relation_list",
              "material_inspection_content_relation_list", "fieldValueList"):
        g.setdefault(k, [])

    # 来源三件套（source_name / source_tag_class）
    src = _as_int(g.get("source"))
    if src in GOODS_SOURCE:
        g["source"] = src
        g["source_name"] = GOODS_SOURCE[src]
        g["source_tag_class"] = SOURCE_TAG_CLASS[src]
    else:
        g["source_name"] = None
        g["source_tag_class"] = ""

    # 生产类型展示名（本租户全 0 → "-"）
    g["produce_type_name"] = "-" if not g.get("produce_type") else str(g.get("produce_type"))

    # 类别链与末级对象（照表命中才覆盖，避免种子行已有对象被空值冲掉）
    cid = g.get("category_id")
    if cid:
        ids, names = ctx.category_chain(cid)
        if ids:
            g["category_path_ids"] = ids
            g["category_path_names"] = "/".join(names) if names else None
        else:
            g.setdefault("category_path_ids", [cid])
            g.setdefault("category_path_names", None)
        leaf = ctx.cat_by_id.get(str(cid))
        if leaf:
            g["category"] = {"id": leaf.get("id"), "name": leaf.get("name"),
                             "parent_id": leaf.get("parent_id")}
    else:
        g.setdefault("category_path_ids", [])
        g.setdefault("category_path_names", None)
        g["category"] = None

    # 单位对象
    unit = ctx.unit_obj(g.get("unit_id"))
    if unit is not None or "unit" not in g:
        g["unit"] = unit

    # 创建人对象
    if g.get("create_user_id"):
        u = ctx.user_obj(g["create_user_id"])
        if u.get("real_name") or "create_user" not in g:
            g["create_user"] = u

    # 供应商拼接名（前端 loadOver 组装，后端一并补全）
    rels = g.get("supplier_relation_list") or []
    names = [str((r.get("supplier") or {}).get("name") or "") for r in rels
             if (r.get("supplier") or {}).get("name")]
    if names:
        g["supplier_names"] = ",".join(names)

    # 二维码 / 简拼
    g["qr_code_url"] = "https://single.kgd.ltd/#/g?qr_id=%s" % gid
    if not g.get("jianpin") and g.get("name"):
        g["jianpin"] = _jianpin(g["name"])
    return g


def _form_row(body, session, ctx):
    """从请求体提取白名单字段组装商品行。"""
    row = {}
    for k in _FORM_FIELDS:
        if k in body:
            row[k] = body.get(k)
    # 类别级联提交的是路径数组 → 取最后一级
    if isinstance(row.get("category_id"), list):
        path = [x for x in row["category_id"] if x not in (None, "")]
        row["category_id"] = path[-1] if path else None
    src = _as_int(row.get("source"))
    row["source"] = src  # None/1/2/3
    # 供应商关联（外购场景，supplier_ids）
    rels = []
    for sid in _as_list(body.get("supplier_ids")):
        rel = ctx.supplier_rel(sid)
        if rel:
            rels.append(rel)
    if rels or body.get("supplier_ids") is not None:
        row["supplier_relation_list"] = rels
    row["create_user_id"] = session.get("id")
    row["enterprise_id"] = body.get("enterprise_id") or session.get("enterprise_id") or 16807
    return row


# ========================= 商品：筛选 =========================

def _filter_goods(rows, body, ctx, view=None):
    """列表筛选（字段名以 goods_all.json 真实字段为准）。"""
    out = []
    kw = str(body.get("goods_keyword") or "").strip()
    for g in rows:
        if not _alive(g):
            continue
        if view is not None:
            vs = _view_source(view)
            if vs is not None and _as_int(g.get("source")) != vs:
                continue
        if body.get("name") and str(body.get("name")) not in str(g.get("name") or ""):
            continue
        if body.get("code") and str(body.get("code")) not in str(g.get("code") or ""):
            continue
        if body.get("standard") and str(body.get("standard")) not in str(g.get("standard") or ""):
            continue
        if kw and not any(kw in str(g.get(f) or "")
                          for f in ("name", "code", "standard", "jianpin")):
            continue
        cid = _as_int(body.get("category_id") or body.get("category"))
        if cid and (g.get("category_id") is None or str(g.get("category_id")) not in ctx.subtree_ids(cid)):
            continue
        src = _as_int(body.get("source"))
        if src is not None and _as_int(g.get("source")) != src:
            continue
        sup = str(body.get("supplier") or body.get("supplier_name") or "").strip()
        if sup and sup not in str(g.get("supplier_names") or ""):
            continue
        cu = str(body.get("create_user") or body.get("create_user_name") or "").strip()
        if cu and cu not in str((g.get("create_user") or {}).get("real_name") or ""):
            continue
        en = _as_int(body.get("is_enable"))
        if en is not None and _as_int(g.get("is_enable"), 2) != en:
            continue
        cs = str(body.get("created_at_start") or "")
        ce = str(body.get("created_at_end") or "")
        created = str(g.get("created_at") or "")
        if cs and created and created < cs:
            continue
        if ce and created and created > (ce + " 23:59:59" if len(ce) == 10 else ce):
            continue
        out.append(g)
    return out


# ========================= 视图 =========================

def _view_source(view):
    """视图数据过滤里的 source 条件（自产=1/外购=3；无则按视图名兜底）。"""
    cond = view.get("data_filter_condition") or {}
    if isinstance(cond, dict):
        s = _as_int(cond.get("source"))
        if s is not None:
            return s
    return _VIEW_NAME_SOURCE.get(view.get("name"))


def _views_of_type(vtype):
    rows = [r for r in core.Store("filter_views").all()
            if _alive(r) and _as_int(r.get("type")) == _as_int(vtype)]
    return rows or ([dict(v) for v in _GOODS_VIEWS_FALLBACK]
                    if _as_int(vtype) in (None, FILTER_VIEW_GOODS_TYPE) else [])


def _find_view(vid):
    if vid is None:
        return None
    for r in _views_of_type(None):
        if str(r.get("id")) == str(vid):
            return r
    return None


# ========================= goods handlers =========================

def _goods_list(body, session, use_view):
    ctx = _Ctx()
    store = core.Store("goods")
    view = None
    if use_view:
        vid = body.get("filter_view_id")
        if vid in (None, ""):              # 04 报告 §3.7-7：filter_view_id 必带
            return core.fail("参数校验错误")
        view = _find_view(vid)             # 未知视图 id：按无附加过滤处理（demo 宽松）
    rows = _filter_goods(store.all(), body, ctx, view)
    rows = _sort_rows(rows, body)
    result = _page(rows, body)
    result["list"] = [_decorate(r, ctx) for r in result["list"]]
    return core.paged_ok(result)


def _list_by_filter_view(body, session):
    return _goods_list(body, session, use_view=True)


def _list(body, session):
    return _goods_list(body, session, use_view=False)


def _add(body, session):
    name = str(body.get("name") or "").strip()
    if not name:
        return core.fail("请输入商品名")
    src = _as_int(body.get("source"))
    if body.get("source") not in (None, "") and src not in GOODS_SOURCE:
        return core.fail("商品来源不正确")
    ctx = _Ctx()
    store = core.Store("goods")
    row = _form_row(body, session, ctx)
    row["name"] = name
    # 编号：手填优先，空则 SP+YYYYMMDD+4位（按天重置连续）
    code = str(row.get("code") or "").strip()
    if code:
        if any(_alive(r) and str(r.get("code")) == code for r in store.all()):
            return core.fail("编号已存在")
    else:
        code = core.next_code("SP", [r for r in store.all() if _alive(r)], 4, "%Y%m%d")
    row["code"] = code
    # 库存上下限校验（12-业务规则 §3.5）
    low, up = row.get("low_limit"), row.get("upper_limit")
    if low is not None and up is not None and _as_float(up) is not None \
            and _as_float(low) is not None and _as_float(up) <= _as_float(low):
        return core.fail("库存上限警告值需大于库存下限警告值")
    g = store.add(row)
    return core.ok(_decorate(g, ctx))


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _edit(body, session):
    gid = body.get("id")
    if gid is None:
        return core.fail("参数校验错误")
    store = core.Store("goods")
    g = store.get(gid)
    if not g or not _alive(g):
        return core.fail("数据不存在")
    ctx = _Ctx()
    patch = _form_row(body, session, ctx)
    if "name" in patch and not str(patch.get("name") or "").strip():
        return core.fail("请输入商品名")
    if patch.get("source") is not None and patch.get("source") not in GOODS_SOURCE \
            and body.get("source") not in (None, ""):
        return core.fail("商品来源不正确")
    # 编辑不改编号归属（code 允许改但需唯一）
    new_code = str(patch.get("code") or "").strip()
    if new_code and any(_alive(r) and str(r.get("code")) == new_code and str(r.get("id")) != str(gid)
                        for r in store.all()):
        return core.fail("编号已存在")
    low = patch.get("low_limit", g.get("low_limit"))
    up = patch.get("upper_limit", g.get("upper_limit"))
    if low is not None and up is not None and _as_float(up) is not None \
            and _as_float(low) is not None and _as_float(up) <= _as_float(low):
        return core.fail("库存上限警告值需大于库存下限警告值")
    patch.pop("create_user_id", None)       # 创建人不随编辑变更
    patch.pop("enterprise_id", None)
    g = store.update(gid, patch)
    return core.ok(_decorate(g, ctx))


def _detail(body, session):
    gid = body.get("id")
    if gid is None:
        return core.fail("参数校验错误")
    g = core.Store("goods").get(gid)
    if not g or not _alive(g):
        return core.fail("数据不存在")
    return core.ok(_decorate(g, _Ctx()))


def _del(body, session):
    gid = body.get("id")
    if gid is None:
        return core.fail("参数校验错误")
    g = core.Store("goods").get(gid)
    if not g:
        return core.fail("数据不存在")
    core.Store("goods").update(gid, {"del": 1})       # 软删（del=1，列表不再返回）
    return core.ok()


def _batch_del(body, session):
    ids = _as_list(body.get("ids"))
    if not ids:
        return core.fail("参数校验错误")
    store = core.Store("goods")
    for gid in ids:
        if store.get(gid):
            store.update(gid, {"del": 1})
    return core.ok()


def _enable(body, session):
    return _switch_enable(body, 1)


def _disable(body, session):
    return _switch_enable(body, 2)


def _switch_enable(body, value):
    gid = body.get("id")
    if gid is None:
        return core.fail("参数校验错误")
    g = core.Store("goods").get(gid)
    if not g:
        return core.fail("数据不存在")
    core.Store("goods").update(gid, {"is_enable": value})
    return core.ok()


def _switch_waste_recover(body, session):
    gid = body.get("id")
    if gid is None:
        return core.fail("参数校验错误")
    val = _as_int(body.get("is_waste_recover"), 2)
    if val not in (1, 2):
        return core.fail("参数校验错误")
    g = core.Store("goods").get(gid)
    if not g:
        return core.fail("数据不存在")
    core.Store("goods").update(gid, {"is_waste_recover": val})
    return core.ok()


def _find_stock_page_list(body, session):
    """/goods/find_stock_page_list（stock-goods-selector 数据源）：
    商品 + 库存维度的分页列表；ware_id 给定时按仓库库存过滤（goods_stocks 档案为空则退化为全量）。"""
    ctx = _Ctx()
    rows = _filter_goods(core.Store("goods").all(), body, ctx)
    ware_id = body.get("ware_id")
    stock_by_goods = {}
    if ware_id is not None:
        for s in core.Store("goods_stocks").all():
            if _alive(s) and str(s.get("ware_id")) == str(ware_id):
                stock_by_goods.setdefault(str(s.get("goods_id")), s)
        if stock_by_goods:                       # 有档案：限定该仓有库存的商品
            rows = [g for g in rows if str(g.get("id")) in stock_by_goods]
    out = []
    for g in rows:
        g = _decorate(g, ctx)
        s = stock_by_goods.get(str(g.get("id")))
        if s:
            g["ware_id"] = s.get("ware_id")
            g["ware_name"] = (s.get("ware") or {}).get("name") or s.get("ware_name")
            g["num"] = s.get("num", g.get("stock_total_num"))
        g.setdefault("num", g.get("stock_total_num") or 0)
        out.append(g)
    out = _sort_rows(out, body)
    return core.paged_ok(_page(out, body))


# ========================= filter_view handlers =========================

def _fv_list_by_permission(body, session):
    vtype = body.get("type")
    rows = _views_of_type(vtype)
    return core.ok(rows)


def _fv_detail(body, session):
    vid = body.get("id")
    if vid is None:
        return core.fail("参数校验错误")
    view = _find_view(vid)
    if not view:
        return core.fail("数据不存在")
    view = dict(view)
    # 04 报告 §1.2：detail 额外返回 quick_filter / display_fields
    view.setdefault("quick_filter", ["created_at"])
    view.setdefault("display_fields", [])
    view.setdefault("filter_view_user_list", [])
    return core.ok(view)


# ========================= columnsFilter handlers =========================

def _cf_store():
    return core.Store("columns_filter")


def _cf_detail(body, session):
    user_id = body.get("user_id") or (session or {}).get("id")
    table_name = body.get("table_name")
    rows = [r for r in _cf_store().all()
            if str(r.get("table_name")) == str(table_name)
            and str(r.get("user_id")) == str(user_id)
            and str(r.get("type")) == str(body.get("type") or 1)]
    return core.ok(rows)          # 前端取 data[0].json_data（40443 chunk loadShowColumn）


def _cf_save(body, session):
    table_name = body.get("table_name")
    if not table_name:
        return core.fail("参数校验错误")
    user_id = body.get("user_id") or (session or {}).get("id")
    store = _cf_store()
    for r in store.all():
        if str(r.get("table_name")) == str(table_name) and str(r.get("user_id")) == str(user_id) \
                and str(r.get("type")) == str(body.get("type") or 1):
            store.update(r.get("id"), {"json_data": body.get("json_data")})
            return core.ok()
    store.add({"user_id": user_id, "table_name": table_name,
               "json_data": body.get("json_data"), "type": body.get("type") or 1})
    return core.ok()


# ========================= dropdown handlers =========================

def _cat_busi(r):
    """类别行的 busi_type（种子简化行缺省视为商品类别 5）。"""
    return _as_int(r.get("busi_type"), 5) or 5


def _dd_category(body, session):
    """类别树（category 表，商品类别 busi_type=5；返回 children 树）。"""
    busi = _as_int(body.get("busi_type") or body.get("type"), 5) or 5
    rows = sorted([r for r in core.Store("categories").all()
                   if _alive(r) and _cat_busi(r) == busi],
                  key=lambda r: (r.get("order_number") or 0, str(r.get("id"))))

    def build(parent):
        return [{"id": r.get("id"), "name": r.get("name"),
                 "parent_id": r.get("parent_id"), "children": build(r.get("id"))}
                for r in rows if str(r.get("parent_id")) == str(parent)]

    return core.ok(build(None))


def _dd_unit(body, session):
    rows = [r for r in core.Store("units").all()
            if _alive(r) and _as_int(r.get("type_id"), 1) == 1]
    return core.ok([{"id": r.get("id"), "name": r.get("name")} for r in rows])


def _dd_supplier(body, session):
    rows = [r for r in core.Store("suppliers").all() if _alive(r)]
    return core.ok([{"id": r.get("id"), "name": r.get("name"),
                     "code": r.get("code"), "del": False} for r in rows])


def _dd_team(body, session):
    rows = [r for r in core.Store("teams").all() if _alive(r)]
    return core.ok([{"id": r.get("id"), "name": r.get("name"), "del": False,
                     "parent_id": r.get("parent_id"), "name_path": r.get("name_path"),
                     "id_path": r.get("id_path"), "level": r.get("level")} for r in rows])


def _dd_all_user(body, session):
    rows = [r for r in core.Store("users").all() if _alive(r)]
    out = []
    for r in rows:
        u = {"id": r.get("id"), "name": r.get("name"), "real_name": r.get("real_name") or r.get("name"),
             "is_enable": r.get("is_enable"), "role_id": r.get("role_id"),
             "avatar": r.get("avatar"), "type": r.get("type") or 1,
             "mobile": r.get("mobile"), "department_id": r.get("department_id"),
             "department_path_names": r.get("department_path_names") or "",
             "department_path_ids": r.get("department_path_ids") or [],
             "team": r.get("team") or [], "team_names": r.get("team_names") or "",
             "has_password": False, "type_name": r.get("type_name") or "普通用户",
             "fieldValueList": r.get("fieldValueList") or [],
             "role": r.get("role"), "department": r.get("department")}
        out.append(u)
    return core.ok(out)


def _dd_address(body, session):
    return core.ok([])


def _dd_designate(body, session):
    """/dropdown/get_designate_dropdown（101 键白名单批量聚合）。
    本域有真实数据：goods_category / goods_source / unit；其余常用键先返回空列表。"""
    keys = body.get("type_list") or []
    if isinstance(keys, str):
        keys = [keys]
    ctx = _Ctx()
    goods_cat = [{"id": r.get("id"), "name": r.get("name"), "parent_id": r.get("parent_id")}
                 for r in sorted([c for c in ctx.categories if _cat_busi(c) == 5],
                                 key=lambda r: (r.get("order_number") or 0, str(r.get("id"))))]
    units = [{"id": r.get("id"), "name": r.get("name")}
             for r in core.Store("units").all()
             if _alive(r) and _as_int(r.get("type_id"), 1) == 1]
    real = {
        "goods_category": goods_cat,
        "goods_source": [{"v": v, "name": n} for v, n in GOODS_SOURCE.items()],
        "unit": units,
    }
    data = {}
    for k in keys:
        data[k] = real.get(k, [])
    return core.ok(data)


# ========================= field handlers =========================

def _field_list(body, session):
    """/field/list（扩展字段管理；goods 模块 37 个扩展字段由种子/field 弹窗维护，demo 先回存量）。"""
    module = body.get("module")
    rows = core.Store("fields").all()
    if module:
        rows = [r for r in rows if str(r.get("module")) == str(module)]
    return core.ok([r for r in rows if _alive(r)])


# ========================= ROUTES =========================

ROUTES = {
    # 商品
    ("goods", "list_by_filter_view"): _list_by_filter_view,
    ("goods", "list"): _list,
    ("goods", "add"): _add,
    ("goods", "edit"): _edit,
    ("goods", "del"): _del,
    ("goods", "batch_del"): _batch_del,
    ("goods", "detail"): _detail,
    ("goods", "enable"): _enable,
    ("goods", "disable"): _disable,
    ("goods", "switch_waste_recover"): _switch_waste_recover,
    ("goods", "find_stock_page_list"): _find_stock_page_list,
    # 视图（商品页签：全部视图/自产/外购）
    ("filter_view", "list_by_permission"): _fv_list_by_permission,
    ("filter_view", "detail"): _fv_detail,
    # 列配置
    ("columnsFilter", "detail"): _cf_detail,
    ("columnsFilter", "save"): _cf_save,
    # 下拉字典
    ("dropdown", "category"): _dd_category,
    ("dropdown", "unit"): _dd_unit,
    ("dropdown", "supplier"): _dd_supplier,
    ("dropdown", "team"): _dd_team,
    ("dropdown", "all_user"): _dd_all_user,
    ("dropdown", "address"): _dd_address,
    ("dropdown", "get_designate_dropdown"): _dd_designate,
    # 扩展字段
    ("field", "list"): _field_list,
}
