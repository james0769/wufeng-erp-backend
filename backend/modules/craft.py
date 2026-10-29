# -*- coding: utf-8 -*-
"""B4 工序/工艺路线/商品工序/不良品项域。
依据：_gen_tmp/kgd_reverse/analysis/07-工序管理.md、08-工艺路线.md、09-商品工序挂接.md、12-业务规则.md
数据：api_dump/craft_all.json(335) / routing_all.json(25) / waste_all.json(8) / goods_craft_samples.json
种子：Store("crafts") / Store("routings") / Store("wastes") / Store("goods")（craft_list 内嵌 goods_craft 行）
模块：pub_craft（公共工序）、craft_line（工艺路线，编号 GYLX+YYYYMM+3位）、
      goods_craft（商品挂接工序，order_number=下标 0 起）、waste_item（不良品项）、
      goods/batch_insert_goods_craft（批量插入工序，简化版）
"""
import time
import core

# ========================= 常量与枚举（analysis 07 §2.1 / 12-业务规则 §2） =========================
PRICE_MODE = {1: {"name": "计件", "color": "orange", "tag_class": "3"},
              2: {"name": "计时", "color": "blue", "tag_class": "1"}}
QUALITY_TYPE = {1: {"name": "全检", "tag_class": "5"},
                2: {"name": "抽检", "tag_class": "1"},
                3: {"name": "不需检验", "tag_class": "4"}}
BOOL_NAME = {1: "是", 2: "否"}
MAX_UNIT_MONEY = 1000000          # 工资单价上限（07 §1.2 / 12-业务规则）
MAX_WORKING_SECONDS = 5999940     # 标准工时 ≤99999 分钟（07 §1.2）

# pub_craft 导入表头（真实 import_template_header 抓包，与 io_xlsx 同源；聚合时 io_xlsx 后加载以其为准）
PUB_CRAFT_TITLES = [
    "工序名", "工序编号", "检验方案", "抽检方案", "是否需要返工/报废", "是否需外协", "报工附加组件",
    "质检附加组件", "车间", "产线", "单位关系(生产1个计划数需要多少)", "计价方式", "工资单价", "报工单位",
    "标准工时", "工时单位", "机台编号", "生产人员姓名", "质检人员姓名", "不良品项名称", "备注",
    "是否开启生产任务计时", "工艺要求"]
PUB_CRAFT_REQUIRED = ["工序名", "检验方案", "是否需外协", "计价方式"]


# ========================= 通用小工具 =========================
def _alive(row):
    return str(row.get("del", 2)) != "1"


def _as_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ids(v):
    """任意标量/逗号串/数组 → id 列表（str 化便于比较）。"""
    if v is None or v == "":
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            out.extend(_ids(x))
        return out
    return [x for x in str(v).replace("，", ",").split(",") if x != ""]


def _like(val, kw):
    kw = str(kw or "").strip()
    if not kw:
        return True
    return kw.lower() in str(val or "").lower()


def _page(rows, body, default_size=30):
    page_no = _as_int(body.get("pageNo"), 1) or 1
    page_size = _as_int(body.get("pageSize"), default_size) or default_size
    return {"list": rows[(page_no - 1) * page_size: page_no * page_size],
            "total": len(rows), "pageNo": page_no, "pageSize": page_size}


def _paged(rows, body, default_size=30):
    return core.paged_ok(_page(rows, body, default_size))


def _sort_rows(rows, body):
    """远程排序：sortField + sortOrder(ascend|descend)（工艺路线列表 编号/名称 可排序）。"""
    field = body.get("sortField")
    if not field:
        return rows
    order = str(body.get("sortOrder") or "ascend")

    def key(r):
        v = r
        for part in str(field).split("."):
            v = (v or {}).get(part) if isinstance(v, dict) else None
        return (v is None, "" if v is None else str(v))
    return sorted(rows, key=key, reverse=(order == "descend"))


def _jianpin(name):
    """简拼（12-业务规则 §3.10）：字母数字原样、空格删除、其余→下划线（汉字声母需拼音库，demo 以 '_' 占位）。"""
    out = []
    for ch in str(name or ""):
        if ch.isspace():
            continue
        out.append(ch if ch.isascii() and ch.isalnum() else "_")
    return "".join(out)


def _fmt_money(v):
    """工资单价 → 8 位小数字符串（原站存储格式，如 "3.50000000"）；空返回 None。"""
    if v is None or v == "":
        return None
    try:
        return "%.8f" % float(v)
    except (TypeError, ValueError):
        return None


def _money_text(row):
    """工资单价展示派生（任务指定）：{单价去尾零}元 + 计时→/小时；计件有报报单位→/单位名。
    07 §1.1 第6列 / 12-业务规则 §2 单位拼接公式。"""
    um = row.get("unit_money")
    if um is None or um == "":
        return None
    try:
        num = float(um)
    except (TypeError, ValueError):
        return None
    text = ("%g" % num) + "元"
    if _as_int(row.get("price_mode"), 1) == 2:
        return text + "/小时"
    unit = row.get("unit") or {}
    return text + "/" + unit["name"] if isinstance(unit, dict) and unit.get("name") else text


def _seconds_name(sec):
    """standard_working_name 服务端格式化（样例：5小时 / 30分钟 / 1分钟；0/空→空串）。"""
    n = _as_int(sec, 0) or 0
    if n <= 0:
        return ""
    h, rest = divmod(n, 3600)
    m, s = divmod(rest, 60)
    out = ""
    if h:
        out += "%d小时" % h
    if m:
        out += "%d分钟" % m
    if s:
        out += "%d秒" % s
    return out


def _trim_zero(v):
    try:
        return "%g" % float(v)
    except (TypeError, ValueError):
        return str(v or "")


def _new_id(base, i=0):
    return int(time.time() * 1000) % 100000000 + base + i


# ========================= 照表（车间/产线/机台/人员/单位/不良品项） =========================
def _by_id(store_name, field_names=("name",)):
    idx = {}
    for r in core.Store(store_name).all():
        idx[str(r.get("id"))] = r
    return idx


def _lookup(idx, rid):
    return idx.get(str(rid)) if rid is not None else None


def _obj(idx, rid, fields=("id", "name")):
    r = _lookup(idx, rid)
    return {f: r.get(f) for f in fields if r.get(f) is not None} if r else None


def _names_by_ids(idx, ids, name_field="name"):
    out = []
    for i in _ids(ids):
        r = _lookup(idx, i)
        if r:
            out.append(r.get(name_field) or "")
    return ",".join([x for x in out if x != ""])


def _workshop_path(workshops_idx, wid):
    """车间路径 ids/names（自末级上溯，防环）。"""
    ids, names, cur, guard = [], [], _lookup(workshops_idx, wid), 0
    while cur and guard < 20:
        ids.insert(0, cur.get("id"))
        names.insert(0, cur.get("name"))
        cur = _lookup(workshops_idx, cur.get("parent_id"))
        guard += 1
    return ids, names


class _Ctx(object):
    """每请求聚合照表（与 goods.py _Ctx 同思路）。"""

    def __init__(self):
        self.workshops = _by_id("workshops")
        self.produce_lines = _by_id("produce_lines")
        self.machines = _by_id("machines")
        self.users = _by_id("users")
        self.units = _by_id("units")
        self.wastes = _by_id("wastes")


# ========================= pub_craft 行装饰与业务规则 =========================
def _pm_triple(pm):
    p = _as_int(pm, 1) or 1
    d = PRICE_MODE.get(p, PRICE_MODE[1])
    return {"price_mode": p, "price_mode_name": d["name"],
            "price_mode_color": d["color"], "price_mode_tag_class": d["tag_class"]}


def _qt_triple(qt):
    q = _as_int(qt, 3) or 3
    d = QUALITY_TYPE.get(q, QUALITY_TYPE[3])
    return {"quality_type": q, "quality_type_name": d["name"],
            "quality_type_tag_class": d["tag_class"]}


def _apply_linkage(row):
    """开关联动（07 §3.5 / 09 §3.7，确定）：
    需外协=1 → 检验方案=不需检验(3)、计时=否、返工报废=否；计时=1 → 需外协=2；检验方案≠全检 → 返工报废=2。"""
    if _as_int(row.get("need_outsource")) == 1:
        row["need_outsource"] = 1
        row["quality_type"] = 3
        row["is_open_clock"] = 2
        row["need_scrap_rework"] = 2
    if _as_int(row.get("is_open_clock")) == 1:
        row["need_outsource"] = 2
        row["need_outsource_name"] = BOOL_NAME[2]
    if _as_int(row.get("quality_type"), 3) != 1:
        row["need_scrap_rework"] = 2
    for f in ("need_outsource", "is_open_clock", "need_scrap_rework"):
        v = _as_int(row.get(f), 2) or 2
        row[f] = v
        row[f + "_name"] = BOOL_NAME.get(v, "")
    row.update(_qt_triple(row.get("quality_type")))
    return row


def _decorate_craft(row, ctx):
    """写入前补全派生字段（对照 craft_all.json 行结构）。"""
    cid = row.get("id")
    row.setdefault("enterprise_id", 16807)
    row.setdefault("is_enable", 1)
    row.setdefault("del", 2)
    row.setdefault("jianpin", _jianpin(row.get("name")))
    row.setdefault("qr_code_url", "https://single.kgd.ltd/#/c?qr_id=%s" % cid)
    # 计价方式三件套 + 工资单价 8 位小数
    pm = _as_int(row.get("price_mode"), 1) or 1
    row["price_mode"] = pm
    row.update(_pm_triple(pm))
    if row.get("unit_money") not in (None, ""):
        row["unit_money"] = _fmt_money(row["unit_money"])
    # 标准工时
    sec = row.get("standard_working_seconds")
    row["standard_working_seconds"] = _as_int(sec) if sec not in (None, "") else None
    row["standard_working_name"] = _seconds_name(row["standard_working_seconds"])
    # 车间
    wid = row.get("workshop_id")
    if wid:
        ids, names = _workshop_path(ctx.workshops, wid)
        w = _lookup(ctx.workshops, wid)
        row["workshop_id"] = w.get("id")
        row["workshop"] = {"id": w.get("id"), "name": w.get("name"), "parent_id": w.get("parent_id"),
                           "busi_type": w.get("busi_type"), "is_leaf": w.get("is_leaf"),
                           "order_number": w.get("order_number"), "name_path": w.get("name_path")}
        row["workshop_path_ids"] = ids
        row["workshop_path_names"] = "/".join(names)
    else:
        row["workshop_id"] = None
        row["workshop"] = None
        row.setdefault("workshop_path_ids", [])
        row.setdefault("workshop_path_names", "")
    # 产线
    pl_ids = _ids(row.get("produce_line_ids"))
    row["produce_line_ids"] = [_as_int(x) for x in pl_ids if _as_int(x) is not None]
    pl_names = [(_lookup(ctx.produce_lines, i) or {}).get("name") for i in pl_ids]
    pl_names = [x for x in pl_names if x]
    row["produce_line_names"] = ",".join(pl_names)
    row["produce_line_map"] = {i: (_lookup(ctx.produce_lines, i) or {}).get("name")
                               for i in pl_ids if _lookup(ctx.produce_lines, i)}
    # 报工单位 / 单位关系
    uid = row.get("unit_id")
    u = _lookup(ctx.units, uid)
    row["unit_id"] = u.get("id") if u else None
    row["unit"] = {"id": u.get("id"), "name": u.get("name")} if u else None
    row.setdefault("from_unit_num", "1.00000000")
    row.setdefault("to_unit_num", "1.00000000")
    row.setdefault("ratio", "1.00000000000000000000")
    row["unit_relation"] = "生产1个计划数，需要%s%s" % (
        _trim_zero(row.get("to_unit_num")), (row.get("unit") or {}).get("name") or "")
    # 机台（device_names = code-name 逗号串）
    dev_ids = _ids(row.get("device_ids"))
    row["device_ids"] = [_as_int(x) for x in dev_ids if _as_int(x) is not None]
    dev_names = []
    devs = []
    for i in dev_ids:
        m = _lookup(ctx.machines, i)
        if m:
            dev_names.append("%s-%s" % (m.get("code"), m.get("name")))
            devs.append({"id": m.get("id"), "code": m.get("code"), "name": m.get("name"),
                         "standard": m.get("standard"),
                         "category_path_names": m.get("category_path_names"),
                         "device_status": m.get("device_status")})
    row["device_names"] = ",".join(dev_names)
    row["devices"] = devs
    # 生产/质检人员
    for f in ("reportable_user_ids", "quality_able_user_ids"):
        uids = _ids(row.get(f))
        row[f] = [_as_int(x) for x in uids if _as_int(x) is not None]
        row[f.replace("_ids", "_names")] = _names_by_ids(ctx.users, uids, "real_name")
        row[f.replace("_ids", "s")] = [
            {"id": (_lookup(ctx.users, x) or {}).get("id"),
             "real_name": (_lookup(ctx.users, x) or {}).get("real_name") or (_lookup(ctx.users, x) or {}).get("name"),
             "type": 1} for x in uids if _lookup(ctx.users, x)]
    # 不良品项
    wids = _ids(row.get("waste_item_ids"))
    row["waste_item_ids"] = [_as_int(x) for x in wids if _as_int(x) is not None]
    row["waste_item_list"] = [{"id": (_lookup(ctx.wastes, x) or {}).get("id"),
                               "code": (_lookup(ctx.wastes, x) or {}).get("code"),
                               "name": (_lookup(ctx.wastes, x) or {}).get("name")}
                              for x in wids if _lookup(ctx.wastes, x)]
    row["waste_item_names"] = ",".join(x["name"] for x in row["waste_item_list"] if x.get("name"))
    # 检验方案/开关联动 + need_quality 兼容映射（07 §3.12）
    _apply_linkage(row)
    nq = 1 if _as_int(row.get("quality_type"), 3) == 1 else 2
    row["need_quality"] = _as_int(row.get("need_quality")) or nq
    row["need_quality_name"] = BOOL_NAME.get(row["need_quality"], "")
    # 其余缺省
    row.setdefault("item_list", [])
    row.setdefault("attachments", [])
    row.setdefault("drawing_attachments", [])
    row.setdefault("work_guide_attachments", [])
    row.setdefault("fieldValueList", [])
    row.setdefault("inspection_scheme_id", None)
    row.setdefault("inspection_scheme", None)
    row.setdefault("report_attach_module_id", None)
    row.setdefault("report_attach_module", None)
    row.setdefault("quality_attach_module_id", None)
    row.setdefault("quality_attach_module", None)
    row.setdefault("work_code", None)
    row.setdefault("report_work_ext_field", None)
    row.setdefault("quality_ext_field", None)
    row.setdefault("remark", None)
    # 工资单价展示派生（任务指定）
    row["unit_money_text"] = _money_text(row)
    return row


def _craft_from_body(body, ctx, session):
    """从提交表单取 pub_craft 可写字段（07 §2.3）。"""
    row = {}
    for f in ("name", "code", "unit_money", "standard_working_seconds", "workshop_id",
              "unit_id", "from_unit_num", "to_unit_num", "ratio", "remark", "work_code"):
        if f in body:
            row[f] = body.get(f)
    for f in ("produce_line_ids", "device_ids", "reportable_user_ids", "quality_able_user_ids",
              "waste_item_ids", "item_list", "attachments", "drawing_attachments",
              "work_guide_attachments", "fieldValueList", "is_enable"):
        if f in body:
            row[f] = body.get(f)
    if "price_mode" in body:
        row["price_mode"] = body.get("price_mode")
    for f in ("quality_type", "need_scrap_rework", "need_outsource", "is_open_clock",
              "need_quality", "inspection_scheme_id", "report_attach_module_id",
              "quality_attach_module_id"):
        if f in body:
            row[f] = body.get(f)
    return row


def _validate_craft_form(body, row):
    """服务端兜底校验（与前端文案一致的口径）。"""
    if not str(row.get("name") or "").strip():
        return "请输入工序名"
    um = row.get("unit_money")
    if um not in (None, ""):
        try:
            if float(um) > MAX_UNIT_MONEY:
                return "工资单价最大不能超过1000000"
        except (TypeError, ValueError):
            return "工资单价填写错误"
    sec = row.get("standard_working_seconds")
    if sec not in (None, ""):
        s = _as_int(sec, 0) or 0
        if s < 0 or s > MAX_WORKING_SECONDS:
            return "标准工时的取值范围为大于等于0，小于等于99999分钟"
    return None


def _set_create_user(row, session):
    if not row.get("create_user_id"):
        row["create_user_id"] = (session or {}).get("id")
    if not row.get("create_user"):
        u = session or {}
        row["create_user"] = {"id": u.get("id"), "name": u.get("name"),
                              "real_name": u.get("real_name"), "avatar": None,
                              "type_name": "普通用户", "qr_code_url": None}
    return row


# ========================= pub_craft 端点 =========================
def _pc_list(body, session):
    """列表：name(名/简拼)/code/workshop_id 筛选 + 页签 tab 全部/启用(1)/禁用(2)（07 §1.1）。"""
    rows = [r for r in core.Store("crafts").all() if _alive(r)]
    kw = str(body.get("name") or "").strip()
    if kw:
        rows = [r for r in rows if _like(r.get("name"), kw) or _like(r.get("jianpin"), kw)]
    if str(body.get("code") or "").strip():
        rows = [r for r in rows if _like(r.get("code"), body.get("code"))]
    wid = body.get("workshop_id")
    if wid not in (None, "", []):
        rows = [r for r in rows if str(r.get("workshop_id")) == str(wid)
                or str(wid) in [str(x) for x in (r.get("workshop_path_ids") or [])]]
    plid = body.get("produce_line_id")
    if plid not in (None, ""):
        rows = [r for r in rows if str(plid) in [str(x) for x in (r.get("produce_line_ids") or [])]]
    for f in ("reportable_user_name", "quality_able_user_name", "device_name"):
        if str(body.get(f) or "").strip():
            col = {"reportable_user_name": "reportable_user_names",
                   "quality_able_user_name": "quality_able_user_names",
                   "device_name": "device_names"}[f]
            rows = [r for r in rows if _like(r.get(col), body.get(f))]
    for f in ("report_attach_module_title", "quality_attach_module_title"):
        if str(body.get(f) or "").strip():
            key = "report_attach_module" if f.startswith("report") else "quality_attach_module"
            rows = [r for r in rows
                    if _like((r.get(key) or {}).get("title"), body.get(f))]
    if str(body.get("waste_item_id") or "").strip():
        rows = [r for r in rows if str(body.get("waste_item_id"))
                in [str(x) for x in (r.get("waste_item_ids") or [])]]
    # 页签：is_enable ""/缺省=全部（tab 兼容前端旧参数）
    tab = str(body.get("tab") or "").strip()
    is_enable = body.get("is_enable")
    if is_enable in (None, "") and tab:
        is_enable = {"启用": 1, "禁用": 2}.get(tab, "")
    if is_enable not in (None, ""):
        rows = [r for r in rows if _as_int(r.get("is_enable"), 1) == _as_int(is_enable, 1)]
    for f in ("created_at_start", "created_at_end"):
        if str(body.get(f) or "").strip():
            rows = [r for r in rows if (
                str(r.get("created_at") or "") >= str(body.get(f)) if f.endswith("_start")
                else str(r.get("created_at") or "") <= str(body.get(f)) + " 23:59:59")]
    return _paged(_sort_rows(rows, body), body)


def _pc_detail(body, session):
    c = core.Store("crafts").get(body.get("id"))
    if not c:
        return core.fail("数据不存在")
    return core.ok(c)


def _pc_add(body, session):
    if not str(body.get("name") or "").strip():
        return core.fail("请输入工序名")
    ctx = _Ctx()
    row = _craft_from_body(body, ctx, session)
    err = _validate_craft_form(body, row)
    if err:
        return core.fail(err)
    row.setdefault("price_mode", 1)          # 默认 计件（07 §1.2）
    row.setdefault("quality_type", 3)        # 默认 不需质检
    row.setdefault("need_outsource", 2)
    row.setdefault("is_open_clock", 2)
    row.setdefault("need_scrap_rework", 2)
    row.setdefault("id", _new_id(700000))    # 先有 id 再装饰（qr_code_url 依赖）
    row = _set_create_user(row, session)
    row = _decorate_craft(row, ctx)
    core.Store("crafts").add(row)
    return core.ok(row)


def _pc_edit(body, session):
    store = core.Store("crafts")
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    ctx = _Ctx()
    patch = _craft_from_body(body, ctx, session)
    err = _validate_craft_form(body, patch if patch.get("name") is not None else row)
    if err:
        return core.fail(err)
    # 空值字段不覆盖（前端保存逻辑：空值字段删除不提交，07 §1.2 save）
    for k, v in list(patch.items()):
        if v in (None, "", []):
            patch.pop(k)
    row.update(patch)
    row = _set_create_user(row, session)
    _decorate_craft(row, ctx)
    store.flush()
    return core.ok(row)


def _pc_del(body, session):
    row = core.Store("crafts").get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    core.Store("crafts").update(body.get("id"), {"del": 1})
    return core.ok()


def _pc_batch_del(body, session):
    store = core.Store("crafts")
    for i in _ids(body.get("ids")):
        if store.get(i):
            store.update(i, {"del": 1})
    return core.ok()


def _pc_batch_edit(body, session):
    """批量修改：只提交填写项（07 §1.3）。"""
    ctx = _Ctx()
    store = core.Store("crafts")
    for i in _ids(body.get("ids")):
        row = store.get(i)
        if not row:
            continue
        patch = _craft_from_body(body, ctx, session)
        for k, v in list(patch.items()):
            if v in (None, "", []):
                patch.pop(k)
        row.update(patch)
        _decorate_craft(row, ctx)
    store.flush()
    return core.ok()


def _pc_switch_enable(body, value):
    row = core.Store("crafts").get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    core.Store("crafts").update(body.get("id"), {"is_enable": value})
    return core.ok()


def _pc_toggle(field):
    def h(body, session):
        row = core.Store("crafts").get(body.get("id"))
        if not row:
            return core.fail("数据不存在")
        ctx = _Ctx()
        row[field] = _as_int(body.get(field), 2) or 2
        _apply_linkage(row)
        _decorate_craft(row, ctx)
        core.Store("crafts").flush()
        return core.ok(row)
    return h


def _pc_update_quality_type(body, session):
    """修改检验方案弹窗（07 §1.4）。"""
    row = core.Store("crafts").get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    ctx = _Ctx()
    row.update(_qt_triple(body.get("quality_type")))
    if body.get("inspection_scheme_id") is not None:
        row["inspection_scheme_id"] = body.get("inspection_scheme_id")
    _apply_linkage(row)
    _decorate_craft(row, ctx)
    core.Store("crafts").flush()
    return core.ok(row)


def _pc_import_template_header(body, session):
    return {"success": True,
            "data": {"titles": list(PUB_CRAFT_TITLES),
                     "required_titles": list(PUB_CRAFT_REQUIRED),
                     "auto_titles": []},
            "enterprise_config_update_time": core.now()}


# ========================= craft_line（工艺路线） =========================
_CRAFT_LINE_FIELDS = ("unit_money", "standard_working_seconds", "price_mode", "need_quality",
                      "quality_type", "is_open_clock", "need_scrap_rework", "need_outsource",
                      "remark", "from_unit_num", "to_unit_num", "ratio", "device_ids",
                      "reportable_user_ids", "quality_able_user_ids", "waste_item_ids",
                      "produce_line_ids", "unit_id", "item_list", "attachments", "work_code",
                      "inspection_scheme_id", "report_attach_module_id", "quality_attach_module_id")


def _cl_item_from_pub_craft(c, i, line_id):
    """工艺路线工序行：pub_craft 档案展开 + order_number=下标（08 §2.2）。"""
    pm = _as_int(c.get("price_mode"), 1) or 1
    qt = _as_int(c.get("quality_type"), 3) or 3
    um = c.get("unit_money")
    return {
        "id": _new_id(500000, i), "craft_line_id": line_id,
        "pub_craft_id": c.get("id"), "order_number": i,
        "name": c.get("name"), "code": c.get("code"),
        "workshop_id": c.get("workshop_id"),
        "workshop_path_ids": c.get("workshop_path_ids") or [],
        "workshop_path_names": c.get("workshop_path_names") or "",
        "produce_line_ids": c.get("produce_line_ids") or [],
        "produce_line_names": c.get("produce_line_names") or "",
        "price_mode": pm, "price_mode_name": PRICE_MODE[pm]["name"],
        "price_mode_color": PRICE_MODE[pm]["color"],
        "price_mode_tag_class": PRICE_MODE[pm]["tag_class"],
        "unit_money": _fmt_money(um) if um not in (None, "") else "0.00000000",
        "standard_working_seconds": c.get("standard_working_seconds"),
        "device_ids": c.get("device_ids") or [], "device_names": c.get("device_names") or "",
        "reportable_user_ids": c.get("reportable_user_ids") or [],
        "reportable_user_names": c.get("reportable_user_names") or "",
        "quality_able_user_ids": c.get("quality_able_user_ids") or [],
        "quality_able_user_names": c.get("quality_able_user_names") or "",
        "waste_item_ids": c.get("waste_item_ids") or [],
        "waste_item_names": c.get("waste_item_names") or "",
        "unit": c.get("unit"), "unit_id": c.get("unit_id"),
        "ratio": c.get("ratio") or "1.00000000000000000000",
        "from_unit_num": c.get("from_unit_num") or "1.00000000",
        "to_unit_num": c.get("to_unit_num") or "1.00000000",
        "need_quality": _as_int(c.get("need_quality"), 2) or 2,
        "quality_type": qt, "quality_type_name": QUALITY_TYPE[qt]["name"],
        "quality_type_tag_class": QUALITY_TYPE[qt]["tag_class"],
        "inspection_scheme_id": c.get("inspection_scheme_id"),
        "is_open_clock": _as_int(c.get("is_open_clock"), 2) or 2,
        "need_scrap_rework": _as_int(c.get("need_scrap_rework"), 2) or 2,
        "need_outsource": _as_int(c.get("need_outsource"), 2) or 2,
        "item_list": c.get("item_list") or [], "attachments": c.get("attachments") or [],
        "remark": c.get("remark"), "work_code": c.get("work_code"),
        "drawing_attachments": c.get("drawing_attachments") or [],
        "work_guide_attachments": c.get("work_guide_attachments") or [],
        "fieldValueList": c.get("fieldValueList") or [],
        "is_enable": _as_int(c.get("is_enable"), 1) or 1, "del": 2,
        "created_at": core.now(), "updated_at": core.now(),
        "pub_craft": {"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                      "jianpin": c.get("jianpin"), "qr_code_url": c.get("qr_code_url"),
                      "unit_money": c.get("unit_money"), "price_mode": pm,
                      "price_mode_name": PRICE_MODE[pm]["name"],
                      "workshop_path_names": c.get("workshop_path_names")},
    }


def _cl_build_craft_list(pub_craft_ids, body_craft_list, line_id, crafts_idx):
    """pub_craft_ids 顺序数组 → craft_list；body.craft_list 每行按 pub_craft_id 叠加覆盖项（08 §3.4）。"""
    overrides = {}
    for r in (body_craft_list or []):
        if isinstance(r, dict) and r.get("pub_craft_id") is not None:
            o = {}
            for f in _CRAFT_LINE_FIELDS:
                if r.get(f) not in (None, ""):
                    o[f] = r.get(f)
            overrides[str(r.get("pub_craft_id"))] = o
    out = []
    for i, pid in enumerate(_ids(pub_craft_ids)):
        c = _lookup(crafts_idx, pid)
        if not c:
            continue
        item = _cl_item_from_pub_craft(c, i, line_id)
        item.update(overrides.get(str(c.get("id")), {}))
        if "price_mode" in item and item["price_mode"] is not None:
            item.update(_pm_triple(item["price_mode"]))
        if item.get("unit_money") not in (None, ""):
            item["unit_money"] = _fmt_money(item["unit_money"]) or "0.00000000"
        _apply_linkage(item)
        out.append(item)
    for i, item in enumerate(out):
        item["order_number"] = i
    return out


def _cl_decorate(row):
    """craft_names / display_craft_names 派生（08 §2.1）。"""
    cl = row.get("craft_list") or []
    row["craft_list"] = cl
    names = ",".join([x.get("name") or "" for x in cl if x.get("name")])
    row["craft_names"] = names
    row["display_craft_names"] = names      # 样例与 craft_names 相同（08 §4.4 待确认4）
    row["pub_craft_ids"] = [x.get("pub_craft_id") for x in cl]
    row.setdefault("jianpin", _jianpin(row.get("name")))
    row.setdefault("fieldValueList", [])
    row.setdefault("type", 1)                # PRODUCE_TYPE.SINGLE
    return row


def _cl_list(body, session):
    rows = [r for r in core.Store("routings").all() if _alive(r)]
    if str(body.get("code") or "").strip():
        rows = [r for r in rows if _like(r.get("code"), body.get("code"))]
    kw = str(body.get("name") or "").strip()
    if kw:
        rows = [r for r in rows if _like(r.get("name"), kw) or _like(r.get("jianpin"), kw)]
    pid = body.get("pub_craft_id") or body.get("craft_id")
    if pid not in (None, ""):
        rows = [r for r in rows if str(pid) in [str(x) for x in (r.get("pub_craft_ids") or [])]]
    return _paged(_sort_rows(rows, body), body)


def _cl_detail(body, session):
    r = core.Store("routings").get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok(r)


def _cl_add(body, session):
    if not str(body.get("name") or "").strip():
        return core.fail("请输入工艺路线名称")
    pub_craft_ids = body.get("pub_craft_ids")
    if not pub_craft_ids and not body.get("craft_list"):
        return core.fail("请至少选择一条工序")
    crafts_idx = _by_id("crafts")
    store = core.Store("routings")
    code = str(body.get("code") or "").strip()
    if not code:
        code = core.next_code("GYLX", [r for r in store.all() if _alive(r)], width=3, datefmt="%Y%m")
    line_id = _new_id(600000)
    row = {"id": line_id, "code": code, "name": str(body.get("name")).strip(),
           "jianpin": _jianpin(body.get("name")), "type": _as_int(body.get("type"), 1) or 1,
           "enterprise_id": (session or {}).get("enterprise_id") or 16807,
           "craft_list": [], "pub_craft_ids": [], "fieldValueList": body.get("fieldValueList") or []}
    row = _set_create_user(row, session)
    row["craft_list"] = _cl_build_craft_list(pub_craft_ids, body.get("craft_list"), line_id, crafts_idx)
    if not row["craft_list"]:
        return core.fail("请至少选择一条工序")
    _cl_decorate(row)
    store.add(row)
    return core.ok(row)


def _cl_edit(body, session):
    store = core.Store("routings")
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    crafts_idx = _by_id("crafts")
    if str(body.get("name") or "").strip():
        row["name"] = str(body.get("name")).strip()
    if body.get("pub_craft_ids") is not None or body.get("craft_list") is not None:
        if not body.get("pub_craft_ids") and not body.get("craft_list"):
            return core.fail("请至少选择一条工序")
        row["craft_list"] = _cl_build_craft_list(
            body.get("pub_craft_ids") if body.get("pub_craft_ids") is not None
            else [x.get("pub_craft_id") for x in (row.get("craft_list") or [])],
            body.get("craft_list"), row.get("id"), crafts_idx)
        if not row["craft_list"]:
            return core.fail("请至少选择一条工序")
    if body.get("fieldValueList") is not None:
        row["fieldValueList"] = body.get("fieldValueList")
    # 编号修改时只读（08 §1.4），忽略 code 变更
    _cl_decorate(row)
    row["updated_at"] = core.now()
    store.flush()
    return core.ok(row)


def _cl_del(body, session):
    r = core.Store("routings").get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    core.Store("routings").update(body.get("id"), {"del": 1})
    return core.ok()


def _cl_selector(body, session):
    """选择工艺路线弹窗：keyword(名/简拼/编号)+pub_craft_id，10条/页（08 §1.9）。"""
    rows = [r for r in core.Store("routings").all() if _alive(r)]
    kw = str(body.get("keyword") or "").strip()
    if kw:
        rows = [r for r in rows if _like(r.get("name"), kw) or _like(r.get("jianpin"), kw)
                or _like(r.get("code"), kw)]
    pid = body.get("pub_craft_id")
    if pid not in (None, ""):
        rows = [r for r in rows if str(pid) in [str(x) for x in (r.get("pub_craft_ids") or [])]]
    data = [{"id": r.get("id"), "code": r.get("code"), "name": r.get("name"),
             "craft_num": len(r.get("craft_list") or []),
             "display_craft_names": r.get("display_craft_names") or r.get("craft_names") or ""}
            for r in rows]
    return _paged(data, body, default_size=10)


# ========================= goods_craft（商品工序挂接） =========================
def _gc_store():
    return core.Store("goods_crafts")


def _gc_rows(goods_id):
    """按 goods_id 取挂接行（首次访问时从 goods.craft_list 惰性迁移入库，goods 侧为一致性锚点）。
    注意：Store 每次实例化都重读磁盘——需要原地改行的调用方必须用 _gc_mutator，避免跨实例丢写。"""
    store = _gc_store()
    gid = str(goods_id)
    rows = [r for r in store.all() if str(r.get("goods_id")) == gid and _alive(r)]
    if rows:
        return sorted(rows, key=lambda r: _as_int(r.get("order_number"), 0) or 0)
    g = core.Store("goods").get(goods_id)
    if not g or not g.get("craft_list"):
        return []
    for i, r in enumerate(g["craft_list"]):
        r.setdefault("goods_id", goods_id)
        r.setdefault("order_number", i)
        r.setdefault("del", 2)
        r.setdefault("is_enable", 1)
        store.add(r)
    store.flush()
    return sorted([r for r in store.all() if str(r.get("goods_id")) == gid and _alive(r)],
                  key=lambda r: _as_int(r.get("order_number"), 0) or 0)


def _gc_migrate_all():
    """把所有内嵌 craft_list 的商品迁移入 goods_crafts 库（幂等）。"""
    for gid in _goods_ids_with_craft():
        _gc_rows(gid)


def _gc_mutator(goods_id):
    """取「迁移后重新实例化」的可写 store + 该商品排序后的行（保证旧实例写盘不被覆盖）。"""
    _gc_rows(goods_id)                      # 确保迁移已落盘
    store = _gc_store()                     # 迁移之后的新实例
    rows = [r for r in store.all()
            if str(r.get("goods_id")) == str(goods_id) and _alive(r)]
    rows.sort(key=lambda r: _as_int(r.get("order_number"), 0) or 0)
    return store, rows


def _gc_sync_goods(goods_id):
    """同步 goods.craft_list（商品列表「工序流程 管理(N)」计数联动，09 §3.6）。"""
    rows = _gc_rows(goods_id)
    g = core.Store("goods").get(goods_id)
    if g:
        g["craft_list"] = rows
        core.Store("goods").flush()
    return rows


def _gc_from_pub_craft(goods_id, c, order_number, session):
    """goods_craft 新行：商品级覆盖初值取自工序档案（09 §2.1）。"""
    pm = _as_int(c.get("price_mode"), 1) or 1
    qt = _as_int(c.get("quality_type"), 3) or 3
    g = core.Store("goods").get(goods_id) or {}
    row = {
        "goods_id": goods_id, "pub_craft_id": c.get("id"), "order_number": order_number,
        "unit_money": _fmt_money(c.get("unit_money")) if c.get("unit_money") not in (None, "") else "0.00000000",
        "standard_working_seconds": c.get("standard_working_seconds"),
        "price_mode": pm, "price_mode_name": PRICE_MODE[pm]["name"],
        "price_mode_color": PRICE_MODE[pm]["color"], "price_mode_tag_class": PRICE_MODE[pm]["tag_class"],
        "ratio": c.get("ratio") or "1.00000000000000000000",
        "from_unit_num": c.get("from_unit_num") or "1.00000000",
        "to_unit_num": c.get("to_unit_num") or "1.00000000",
        "unit_id": c.get("unit_id"), "unit": c.get("unit"),
        "need_quality": _as_int(c.get("need_quality"), 2) or 2,
        "quality_type": qt, "quality_type_name": QUALITY_TYPE[qt]["name"],
        "quality_type_tag_class": QUALITY_TYPE[qt]["tag_class"],
        "inspection_scheme_id": c.get("inspection_scheme_id"),
        "need_scrap_rework": _as_int(c.get("need_scrap_rework"), 2) or 2,
        "need_outsource": _as_int(c.get("need_outsource"), 2) or 2,
        "is_open_clock": _as_int(c.get("is_open_clock"), 2) or 2,
        "need_ai_quality": 2, "ai_quality_program_id": None, "ai_quality_program": None,
        "device_ids": c.get("device_ids") or [], "device_names": c.get("device_names") or "",
        "devices": c.get("devices") or [],
        "reportable_user_ids": c.get("reportable_user_ids") or [],
        "reportable_user_names": c.get("reportable_user_names") or "",
        "quality_able_user_ids": c.get("quality_able_user_ids") or [],
        "quality_able_user_names": c.get("quality_able_user_names") or "",
        "waste_item_ids": c.get("waste_item_ids") or [],
        "waste_item_names": c.get("waste_item_names") or "",
        "produce_line_ids": c.get("produce_line_ids") or [],
        "produce_line_names": c.get("produce_line_names") or "",
        "report_attach_module_id": c.get("report_attach_module_id"),
        "quality_attach_module_id": c.get("quality_attach_module_id"),
        "report_work_ext_field": None, "quality_ext_field": None,
        "work_code": c.get("work_code"), "item_list": [], "attachments": [],
        "drawing_attachments": [], "work_guide_attachments": [],
        "remark": c.get("remark"), "money_type": "",
        "enterprise_id": (session or {}).get("enterprise_id") or 16807,
        "is_enable": 1, "del": 2, "created_at": core.now(), "updated_at": core.now(),
        "pub_craft": {"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                      "jianpin": c.get("jianpin"), "qr_code_url": c.get("qr_code_url"),
                      "unit_money": c.get("unit_money"), "price_mode": pm,
                      "price_mode_name": PRICE_MODE[pm]["name"],
                      "price_mode_tag_class": PRICE_MODE[pm]["tag_class"],
                      "workshop": c.get("workshop"),
                      "workshop_path_names": c.get("workshop_path_names"),
                      "standard_working_name": c.get("standard_working_name")},
        "goods": {"id": g.get("id"), "name": g.get("name"), "code": g.get("code"),
                  "standard": g.get("standard"), "unit": g.get("unit")},
        "fieldValueList": c.get("fieldValueList") or [],
    }
    return row


_GC_OVERRIDE_FIELDS = _CRAFT_LINE_FIELDS + ("need_ai_quality", "ai_quality_program_id")


def _gc_apply_overrides(row, sub):
    """全量提交时行内覆盖字段（09 §3.3：retain_id 旧行保 id，新行无 id 插入）。"""
    for f in _GC_OVERRIDE_FIELDS:
        if isinstance(sub, dict) and sub.get(f) not in (None, ""):
            row[f] = sub.get(f)
    return row


def _gc_get_list(body, session):
    """弹窗A打开/商品详情 Tab：{goods_id} → 全量工序列表（09 §3.4）。"""
    goods_id = body.get("goods_id")
    if goods_id in (None, ""):
        return core.ok([])
    return core.ok(_gc_rows(goods_id))


def _gc_update(body, session):
    """整表全量提交：order_number=数组下标(0起)；带 id 更新、无 id 新插、缺席旧行删除（09 §3.3）。"""
    goods_id = body.get("goods_id")
    if goods_id in (None, ""):
        return core.fail("参数校验错误")
    g = core.Store("goods").get(goods_id)
    if not g:
        return core.fail("数据不存在")
    store, _ = _gc_mutator(goods_id)
    crafts_idx = _by_id("crafts")
    submitted = [r for r in (body.get("craft_list") or []) if isinstance(r, dict)]
    old = {str(r.get("id")): r for r in store.all()
           if str(r.get("goods_id")) == str(goods_id) and _alive(r)}
    kept = set()
    for idx, sub in enumerate(submitted):
        pid = sub.get("pub_craft_id")
        c = _lookup(crafts_idx, pid)
        if not c:
            continue
        rid = sub.get("id") if str(sub.get("id")) in old else None
        if rid is not None:
            row = old[str(rid)]
            kept.add(str(rid))
        else:
            row = _gc_from_pub_craft(goods_id, c, idx, session)
        _gc_apply_overrides(row, sub)
        row["order_number"] = idx
        _apply_linkage(row)
        if row.get("unit_money") not in (None, ""):
            row["unit_money"] = _fmt_money(row["unit_money"]) or "0.00000000"
        row["updated_at"] = core.now()
        if rid is None:
            store.add(row)
    for rid, r in old.items():
        if rid not in kept:
            store.remove(r.get("id"))
    store.flush()
    _gc_sync_goods(goods_id)
    return core.ok()


def _gc_batch_add_by_pub_craft_ids(body, session):
    """按 pub_craft_ids 批量追加挂接到商品尾部（/goods_craft/batch_add 族语义，B4 任务口径）。"""
    goods_id = body.get("goods_id")
    pids = _ids(body.get("pub_craft_ids"))
    if goods_id in (None, "") or not pids:
        return core.fail("参数校验错误")
    g = core.Store("goods").get(goods_id)
    if not g:
        return core.fail("数据不存在")
    crafts_idx = _by_id("crafts")
    store, rows = _gc_mutator(goods_id)
    n = len(rows)
    added = 0
    for pid in pids:
        c = _lookup(crafts_idx, pid)
        if not c:
            continue
        store.add(_gc_from_pub_craft(goods_id, c, n + added, session))
        added += 1
    if not added:
        return core.fail("工序不存在")
    store.flush()
    _gc_sync_goods(goods_id)
    return core.ok({"count": added})


def _gc_list(body, session):
    """商品工序管理全局弹窗（09 §1.4）：goods_keyword/name/code/workshop/produce_line_id 筛选。"""
    goods_idx = _by_id("goods")
    rows = []
    for gid in {str(r.get("goods_id")) for r in _gc_store().all()} | _goods_ids_with_craft():
        rows.extend(_gc_rows(gid))
    kw = str(body.get("goods_keyword") or "").strip()
    if kw:
        def _goods_of(r):
            g = r.get("goods")
            if isinstance(g, dict) and (g.get("name") or g.get("code")):
                return g
            return _lookup(goods_idx, r.get("goods_id")) or {}
        out = []
        for r in rows:
            g = _goods_of(r)
            if _like(g.get("name"), kw) or _like(g.get("code"), kw) or _like(g.get("standard"), kw):
                out.append(r)
        rows = out
    if str(body.get("name") or "").strip():
        rows = [r for r in rows if _like((r.get("pub_craft") or {}).get("name"), body.get("name"))]
    if str(body.get("code") or "").strip():
        rows = [r for r in rows if _like((r.get("pub_craft") or {}).get("code"), body.get("code"))]
    if body.get("workshop_id") not in (None, "", []):
        rows = [r for r in rows if str(body.get("workshop_id")) in
                [str(x) for x in (((r.get("pub_craft") or {}).get("workshop_path_ids")) or [])]]
    if body.get("produce_line_id") not in (None, ""):
        rows = [r for r in rows if str(body.get("produce_line_id")) in
                [str(x) for x in (r.get("produce_line_ids") or [])]]
    rows.sort(key=lambda r: (str(r.get("goods_id")), _as_int(r.get("order_number"), 0) or 0))
    return _paged(rows, body)


def _goods_ids_with_craft():
    return {str(g.get("id")) for g in core.Store("goods").all() if g.get("craft_list")}


def _gc_edit(body, session):
    """全局弹窗行内修改（09 §1.4：逐项即时提交）。"""
    _gc_migrate_all()                       # 行 id 可能尚未惰性迁移入库
    store = _gc_store()
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    sub = {k: v for k, v in body.items() if k in _GC_OVERRIDE_FIELDS and v not in (None, "")}
    _gc_apply_overrides(row, sub)
    if row.get("unit_money") not in (None, ""):
        row["unit_money"] = _fmt_money(row["unit_money"]) or "0.00000000"
    _apply_linkage(row)
    row["updated_at"] = core.now()
    store.flush()
    _gc_sync_goods(row.get("goods_id"))
    return core.ok(row)


def _gc_batch_del(body, session):
    """全局弹窗批量删除（09 §1.4）。"""
    _gc_migrate_all()                       # 行 id 可能尚未惰性迁移入库
    store = _gc_store()
    touched = set()
    for i in _ids(body.get("ids")):
        row = store.get(i)
        if row:
            touched.add(str(row.get("goods_id")))
            store.remove(i)
    for gid in touched:
        _gc_sync_goods(gid)
    return core.ok()


def _goods_batch_insert(body, session):
    """批量插入工序（简化版，09 §1.5）：前/后工序定位，均不指定→插到末尾。"""
    goods_ids = _ids(body.get("goods_ids"))
    pid = body.get("pub_craft_id")
    prev = body.get("prev_pub_craft_id")
    nxt = body.get("next_pub_craft_id")
    if not goods_ids:
        return core.fail("请勾选商品")
    if pid in (None, ""):
        return core.fail("请选择要插入的工序")
    if prev not in (None, "") and nxt not in (None, ""):
        return core.fail("前面的工序和后面的工序最多只能选择一个。")
    crafts_idx = _by_id("crafts")
    c = _lookup(crafts_idx, pid)
    if not c:
        return core.fail("工序不存在")
    success_count, fail_array = 0, []
    for gid in goods_ids:
        g = core.Store("goods").get(gid)
        if not g:
            fail_array.append({"goods_name": "", "fail_reason": "商品不存在"})
            continue
        store, rows = _gc_mutator(gid)
        pos = None
        if prev not in (None, ""):
            hit = [i for i, r in enumerate(rows) if str(r.get("pub_craft_id")) == str(prev)]
            if not hit:
                fail_array.append({"goods_name": g.get("name"), "fail_reason": "商品不存在指定的前后工序"})
                continue
            pos = hit[-1] + 1
        elif nxt not in (None, ""):
            hit = [i for i, r in enumerate(rows) if str(r.get("pub_craft_id")) == str(nxt)]
            if not hit:
                fail_array.append({"goods_name": g.get("name"), "fail_reason": "商品不存在指定的前后工序"})
                continue
            pos = hit[0]
        else:
            pos = len(rows)
        new_row = _gc_from_pub_craft(gid, c, pos, session)
        store.add(new_row)
        # 重排 order_number（store 实例内行，落盘不丢）
        for i, r in enumerate(sorted(
                [r2 for r2 in store.all()
                 if str(r2.get("goods_id")) == str(gid) and _alive(r2)],
                key=lambda r2: _as_int(r2.get("order_number"), 0) or 0)):
            r["order_number"] = i
        store.flush()
        _gc_sync_goods(gid)
        success_count += 1
    return core.ok({"success_count": success_count, "fail_array": fail_array})


# ========================= waste_item（不良品项） =========================
def _wi_list(body, session):
    rows = [r for r in core.Store("wastes").all() if _alive(r)]
    if str(body.get("code") or "").strip():
        rows = [r for r in rows if _like(r.get("code"), body.get("code"))]
    if str(body.get("name") or "").strip():
        rows = [r for r in rows if _like(r.get("name"), body.get("name"))]
    return _paged(rows, body)


def _wi_add(body, session):
    name = str(body.get("name") or "").strip()
    if not name:
        return core.fail("请输入不良品项名称")
    store = core.Store("wastes")
    for r in store.all():
        if _alive(r) and str(r.get("name")) == name:
            return core.fail("不良品项已存在")
    row = {"code": str(body.get("code") or "").strip() or name, "name": name,
           "enterprise_id": (session or {}).get("enterprise_id") or 16807,
           "del": 2, "fieldValueList": []}
    store.add(row)
    return core.ok(row)


def _wi_edit(body, session):
    store = core.Store("wastes")
    row = store.get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    if str(body.get("name") or "").strip():
        row["name"] = str(body.get("name")).strip()
    if body.get("code") is not None:
        row["code"] = str(body.get("code")).strip() or row.get("name")
    row["updated_at"] = core.now()
    store.flush()
    return core.ok(row)


def _wi_del(body, session):
    row = core.Store("wastes").get(body.get("id"))
    if not row:
        return core.fail("数据不存在")
    core.Store("wastes").update(body.get("id"), {"del": 1})
    return core.ok()


# ========================= 路由注册 =========================
ROUTES = {
    # ---- pub_craft 公共工序（07 §4.1）----
    ("pub_craft", "list"): _pc_list,
    ("pub_craft", "detail"): _pc_detail,
    ("pub_craft", "add"): _pc_add,
    ("pub_craft", "edit"): _pc_edit,
    ("pub_craft", "del"): _pc_del,
    ("pub_craft", "batch_del"): _pc_batch_del,
    ("pub_craft", "batch_edit"): _pc_batch_edit,
    ("pub_craft", "enable"): lambda b, s: _pc_switch_enable(b, 1),
    ("pub_craft", "disable"): lambda b, s: _pc_switch_enable(b, 2),
    ("pub_craft", "set_need_clock"): _pc_toggle("is_open_clock"),
    ("pub_craft", "set_need_scrap_rework"): _pc_toggle("need_scrap_rework"),
    ("pub_craft", "set_need_outsource"): _pc_toggle("need_outsource"),
    ("pub_craft", "update_quality_type"): _pc_update_quality_type,
    ("pub_craft", "import_template_header"): _pc_import_template_header,
    # ---- craft_line 工艺路线（08 §3.5）----
    ("craft_line", "list"): _cl_list,
    ("craft_line", "detail"): _cl_detail,
    ("craft_line", "add"): _cl_add,
    ("craft_line", "edit"): _cl_edit,
    ("craft_line", "del"): _cl_del,
    ("craft_line", "craft_line_selector"): _cl_selector,
    # ---- goods_craft 商品工序挂接（09）----
    ("goods_craft", "get_goods_craft_list"): _gc_get_list,
    ("goods_craft", "update_goods_craft"): _gc_update,
    ("goods_craft", "batch_add_by_pub_craft_ids"): _gc_batch_add_by_pub_craft_ids,
    ("goods_craft", "list"): _gc_list,
    ("goods_craft", "edit"): _gc_edit,
    ("goods_craft", "batch_del"): _gc_batch_del,
    # ---- goods 批量插入工序（09 §1.5，原站挂在 goods 模块）----
    ("goods", "batch_insert_goods_craft"): _goods_batch_insert,
    # ---- waste_item 不良品项 ----
    ("waste_item", "list"): _wi_list,
    ("waste_item", "add"): _wi_add,
    ("waste_item", "edit"): _wi_edit,
    ("waste_item", "del"): _wi_del,
}
