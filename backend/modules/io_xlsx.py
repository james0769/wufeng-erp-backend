# -*- coding: utf-8 -*-
r"""B10 批量导入/导出（xlsx IO）。

证据来源（复刻依据）：
- 真实模板/导出文件：`快工单批量导入模板与导出数据\01~04\*.xlsx`（原件已拷入 backend\templates\，逐字节返回）
- 表头/必填列 JSON：goods_craft(模块29)/pub_craft(10)/craft_line(17)/waste_item(35) 四个真实 import_template_header 抓包
- 参数依据：各域 _清单.md + deep\06-导入解析与端点攻坚.md（import_model 1=按商品编号/2=按商品名；web_import 与 import 两种动词按 §1.6 矩阵）
- 原站语义要点：
  * download_import_template → xlsx 二进制；import_template_header → {titles, required_titles, auto_titles}
  * import/web_import 入参 {rows:[标题行, ...数据行], swap_column_map:{系统字段:Excel表头}, import_model, import_type}
    （exceljs 解析在浏览器端完成，后端收到的就是解析后的行——本模块不做文件解析）
  * 响应 data = 失败行集合，每行首元素为错误原因（前端 downErrorFile 据此生成错误文件）；成功行落库
  * craft_line/export、waste_item/export 原站无服务端路由（404）；waste_item 导出为前端 feExport 行为复刻
    （列=表格显示列 code/name/created_at，仅表头加粗，sheet 名"不良品项导出"）
  * purchase_bill/export 原站 404，按任务要求实现（列头为推断，无真实导出可对照，已在列定义处标注）
"""
import os, json, traceback
from io import BytesIO

import core
from openpyxl import Workbook
from openpyxl.styles import Font

TPL_DIR = os.path.join(core.ROOT, "templates")

# ---- 枚举（11-数据字段词典.md 实证） ----
SOURCE_CN = {"自产": 1, "外协加工": 2, "外购": 3}
SOURCE_ID = {1: "自产", 2: "外协加工", 3: "外购"}
PRICE_MODE = {"计件": 1, "计时": 2}
QUALITY_TYPE = {"全检": 1, "抽检": 2, "不需检验": 3}
QUALITY_NAME = {1: "全检", 2: "抽检", 3: "不需检验"}
YESNO = {"是": 1, "否": 2}
YESNO_NAME = {1: "是", 2: "否"}
ENABLE_NAME = {1: "启用", 2: "禁用"}
UNIT_SECONDS = {"小时": 3600, "分钟": 60, "秒": 1}


class RowError(Exception):
    """行级业务错误（导入时转为失败行，首元素=错误原因）。"""


# ---------------------------------------------------------------- 基础工具
def _s(v):
    """单元格值 → 去空格字符串或 None。"""
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _n(v, default="0"):
    """数值型 → 原样字符串（原站金额为定点数字符串），空取 default。"""
    t = _s(v)
    return t if t is not None else default


def _num(x):
    """导出用：任意值 → 字符串或 None（原站导出数字列为文本）。"""
    if x is None or x == "":
        return None
    return str(x)


def _d(r, *keys):
    """逐键取值，返回第一个非空（导出兼容多种种子字段名）。"""
    for k in keys:
        v = r.get(k)
        if v is not None and v != "":
            return v
    return None


def _obj_name(r, *keys):
    """嵌套对象取名：unit.name / create_user.real_name 等。"""
    for k in keys:
        v = r.get(k)
        if isinstance(v, dict):
            for f in ("name", "real_name", "title", "code"):
                if v.get(f):
                    return v[f]
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            return ",".join(str(x.get("name") or x.get("real_name") or "") for x in v)
        elif v:
            return v
    return None


def _find(store, value, fields=("name",)):
    """按名称等字段在 Store 中找行。"""
    if not value:
        return None
    for r in store.all():
        for f in fields:
            if str(r.get(f) or "") == str(value):
                return r
    return None


def _split_names(v):
    return [t for t in (x.strip() for x in str(v or "").replace("，", ",").replace("、", ",").split(",")) if t]


def _next_seq(prefix, rows, width=6):
    """无日期前缀的编号（客户 KH000002 / 供应商 GYS000002 样式）。"""
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(prefix):
            try:
                mx = max(mx, int(c[len(prefix):]))
            except ValueError:
                pass
    return prefix + str(mx + 1).zfill(width)


def _to_seconds(num, unit):
    try:
        return int(float(num) * UNIT_SECONDS.get(unit, 1))
    except (TypeError, ValueError):
        return None


def _resolve_goods(v, by_name=False):
    """按 编号(+规格) 或 商品名 找商品；找不到自动建（模板说明"找不到商品按照填写的商品信息自动添加"）。"""
    store = core.Store("goods")
    g = None
    if not by_name:
        code = v("商品编号")
        if code:
            g = _find(store, code, ("code",))
    else:
        nm = v("商品名")
        if nm:
            g = _find(store, nm, ("name",))
    if g is None and not by_name and v("商品名"):
        g = _find(store, v("商品名"), ("name",))
    if g is None:
        nm = v("商品名") or v("商品编号")
        if not nm:
            raise RowError("商品不存在")
        g = store.add({
            "code": v("商品编号") or core.next_code("SP", store.all()),
            "name": nm, "standard": v("商品规格"),
            "source": SOURCE_CN.get(v("商品来源") or "", 3), "is_enable": 1,
        })
    return g


def _goods_mini(g):
    if not g:
        return None
    return {"id": g.get("id"), "name": g.get("name"), "code": g.get("code"), "standard": g.get("standard")}


# ---------------------------------------------------------------- 导入 builder（合法行 → Store）
def _imp_goods(v, ctx, session, body):
    store = core.Store("goods")
    name = v("商品名")
    code = v("商品编号") or core.next_code("SP", store.all())
    src = SOURCE_CN.get(v("来源") or "", None)
    row = {
        "code": code, "name": name, "standard": v("规格"), "remark": v("备注"),
        "selling_money": _n(v("销售单价"), "0.00000000"), "cost_money": _n(v("成本单价"), "0.00000000"),
        "outsource_money": _n(v("外协单价"), "0.00000000"),
        "source": src, "source_name": SOURCE_ID.get(src), "is_enable": 1,
        "enterprise_id": session.get("enterprise_id"), "stock_total_num": 0, "craft_list": [],
    }
    u = _find(core.Store("units"), v("单位"), ("name",))
    if u:
        row["unit_id"], row["unit"] = u.get("id"), {"id": u.get("id"), "name": u.get("name")}
    cat = v("类别")
    if cat:
        row["category_path_names"] = cat
        c = _find(core.Store("categories"), cat.split("/")[-1], ("name",))
        if c:
            row["category_id"] = c.get("id")
    sup_names = _split_names(v("供应商"))
    if sup_names:
        row["supplier_names"] = ",".join(sup_names)
        s0 = _find(core.Store("suppliers"), sup_names[0], ("name",))
        if s0:
            row["supplier_id"], row["supplier"] = s0.get("id"), {"id": s0.get("id"), "name": s0.get("name")}
    # 工序名称/工艺路线名称二选一，都填以工序名称为准（模板说明 8）
    craft_names = _split_names(v("工序名称"))
    if not craft_names and v("工艺路线名称"):
        rl = _find(core.Store("routings"), v("工艺路线名称"), ("name",))
        craft_names = _split_names((rl or {}).get("craft_names"))
    for nm in craft_names:
        c = _find(core.Store("crafts"), nm, ("name",))
        if c:
            row["craft_list"].append({"pub_craft_id": c.get("id"), "pub_craft": {"id": c.get("id"), "name": nm}})
    if v("库存下限警告值"):
        row["low_limit"] = v("库存下限警告值")
    if v("库存上限警告值"):
        row["upper_limit"] = v("库存上限警告值")
    if v("呆滞警告值"):
        row["stagnant_warning_day"] = v("呆滞警告值")
    return store.add(row)


def _imp_goods_craft(v, ctx, session, body):
    goods = _resolve_goods(v, by_name=bool(v("商品名") and not v("商品编号")))
    craft = _find(core.Store("crafts"), v("工序名称"), ("name",))
    if not craft:
        raise RowError("工序不存在")
    entry = {
        "goods_id": goods.get("id"), "pub_craft_id": craft.get("id"),
        "pub_craft": {"id": craft.get("id"), "name": craft.get("name")},
        "price_mode": PRICE_MODE.get(v("计价方式") or "计件", 1),
        "unit_money": _n(v("工资单价"), craft.get("unit_money") or "0.00000000"),
        "quality_type": QUALITY_TYPE.get(v("检验方案") or "不需检验", 3),
        "need_scrap_rework": YESNO.get(v("是否需要返工/报废") or "否", 2),
        "need_outsource": YESNO.get(v("是否需外协") or "否", 2),
        "is_open_clock": YESNO.get(v("是否需生产任务计时") or "否", 2),
        "standard_working_seconds": _to_seconds(v("标准工时"), v("工时单位") or "小时"),
        "remark": v("备注"),
    }
    if v("抽检方案"):
        entry["inspection_scheme_name"] = v("抽检方案")
    # import_type：2=覆盖导入（先清空该商品挂接），1=追加导入
    crafts = goods.get("craft_list")
    if not isinstance(crafts, list):
        crafts = []
    if str(body.get("import_type") or "1") == "2":
        crafts = []
    crafts.append(entry)
    core.Store("goods").update(goods.get("id"), {"craft_list": crafts})
    return entry


def _imp_goods_bom(v, ctx, session, body):
    """深度 0 行=新建 BOM；>0 行=挂到最近一个父 BOM 的结构（模板说明 3/11/12）。"""
    try:
        depth = int(float(v("深度") or 0))
    except (TypeError, ValueError):
        depth = 0
    boms, trees = core.Store("boms"), core.Store("bom_trees")
    if depth <= 0:
        name = v("BOM名称")
        if not name:
            raise RowError("深度为0时，BOM名称必须填写")
        g = _resolve_goods(v)
        code = v("BOM编号") or core.next_code("BOM", boms.all())
        row = boms.add({
            "code": code, "name": name, "goods_id": g.get("id"), "goods": _goods_mini(g),
            "is_default": YESNO.get(v("设为默认BOM") or "否", 2),
            "is_enable": {"启用": 1, "禁用": 2}.get(v("启用/禁用") or "启用", 1),
            "remark": v("备注"), "bom_cost_money_total": "0",
            "enterprise_id": session.get("enterprise_id"),
        })
        ctx["last_bom"] = row
        return row
    g = _resolve_goods(v, by_name=bool(v("商品名") and not v("商品编号")))
    parent = ctx.get("last_bom")
    if not parent:
        raise RowError("找不到上级BOM，请先导入深度为0的行")
    num = _n(v("单位用量"), "1")
    cost = _n(_d(g, "cost_money"), "0")
    try:
        total = "%.8f" % (float(cost) * float(num))
    except (TypeError, ValueError):
        total = "0.00000000"
    pc = _find(core.Store("crafts"), v("投料工序"), ("name",))
    trees.add({
        "goods_bom_id": parent.get("id"), "goods_id": g.get("id"), "goods": _goods_mini(g),
        "level": depth, "num": num,
        "pub_craft_id": (pc or {}).get("id"),
        "pub_craft": {"id": (pc or {}).get("id"), "name": v("投料工序")} if pc else None,
        "goods_cost_money": cost, "goods_cost_money_total": total,
        "remark": v("结构备注"), "reference_bom": v("引用BOM"),
    })
    try:
        boms.update(parent.get("id"), {"bom_cost_money_total": "%.8f" % (float(parent.get("bom_cost_money_total") or 0) + float(total))})
        parent["bom_cost_money_total"] = "%.8f" % (float(parent.get("bom_cost_money_total") or 0))
    except (TypeError, ValueError):
        pass
    return parent


def _imp_pub_craft(v, ctx, session, body):
    store = core.Store("crafts")
    row = {
        "name": v("工序名"), "code": v("工序编号"),
        "unit_money": _n(v("工资单价"), "2.30000000"),
        "price_mode": PRICE_MODE.get(v("计价方式") or "计件", 1),
        "quality_type": QUALITY_TYPE.get(v("检验方案") or "不需检验", 3),
        "need_outsource": YESNO.get(v("是否需外协") or "否", 2),
        "need_scrap_rework": YESNO.get(v("是否需要返工/报废") or "否", 2),
        "is_open_clock": YESNO.get(v("是否开启生产任务计时") or "否", 2),
        "standard_working_seconds": _to_seconds(v("标准工时"), v("工时单位") or "小时"),
        "remark": v("备注"), "is_enable": 1, "enterprise_id": session.get("enterprise_id"),
    }
    if row["price_mode"] == 1:
        row["price_mode_name"] = "计件"
    elif row["price_mode"] == 2:
        row["price_mode_name"] = "计时"
    row["quality_type_name"] = QUALITY_NAME.get(row["quality_type"])
    if v("抽检方案"):
        row["inspection_scheme_name"] = v("抽检方案")
    ws = _find(core.Store("workshops"), v("车间"), ("name",))
    if ws:
        row["workshop_id"], row["workshop"] = ws.get("id"), {"id": ws.get("id"), "name": ws.get("name")}
    pl_names = _split_names(v("产线"))
    if pl_names:
        ids, names = [], []
        for nm in pl_names:
            p = _find(core.Store("produce_lines"), nm, ("name",))
            if p:
                ids.append(p.get("id"))
                names.append(nm)
        row["produce_line_ids"], row["produce_line_names"] = ids, ",".join(names)
    u = _find(core.Store("units"), v("报工单位"), ("name",))
    if u:
        row["unit_id"], row["unit"] = u.get("id"), {"id": u.get("id"), "name": u.get("name")}
    dev_ids, dev_names = [], []
    for c in _split_names(v("机台编号")):
        m = _find(core.Store("machines"), c, ("code", "name"))
        if m:
            dev_ids.append(m.get("id"))
            dev_names.append("%s-%s" % (m.get("code"), m.get("name")))
    if dev_ids:
        row["device_ids"], row["device_names"] = dev_ids, ",".join(dev_names)
    rep_ids, rep_names = [], []
    for c in _split_names(v("生产人员姓名")):
        usr = _find(core.Store("users"), c, ("real_name", "name"))
        if usr:
            rep_ids.append(usr.get("id"))
            rep_names.append(c)
    if rep_ids:
        row["reportable_user_ids"], row["reportable_user_names"] = rep_ids, ",".join(rep_names)
    qc_ids, qc_names = [], []
    for c in _split_names(v("质检人员姓名")):
        usr = _find(core.Store("users"), c, ("real_name", "name"))
        if usr:
            qc_ids.append(usr.get("id"))
            qc_names.append(c)
    if qc_ids:
        row["quality_able_user_ids"], row["quality_able_user_names"] = qc_ids, ",".join(qc_names)
    w_ids, w_names = [], []
    for c in _split_names(v("不良品项名称")):
        w = _find(core.Store("wastes"), c, ("name", "code"))
        if w:
            w_ids.append(w.get("id"))
            w_names.append(c)
    if w_ids:
        row["waste_item_ids"], row["waste_item_names"] = w_ids, ",".join(w_names)
    if v("工艺要求"):
        row["craft_requirement"] = v("工艺要求")
    return store.add(row)


def _imp_craft_line(v, ctx, session, body):
    store = core.Store("routings")
    crafts = core.Store("crafts")
    ids, names = [], []
    for nm in _split_names(v("工序名称")):
        c = _find(crafts, nm, ("name",))
        if not c:
            raise RowError("工序「%s」不存在" % nm)
        ids.append(c.get("id"))
        names.append(nm)
    row = {
        "code": v("工艺路线编号") or core.next_code("GYLX", store.all(), width=3, datefmt="%Y%m"),
        "name": v("工艺路线名称"), "pub_craft_ids": ids,
        "craft_names": ",".join(names), "display_craft_names": ",".join(names),
        "enterprise_id": session.get("enterprise_id"),
    }
    return store.add(row)


def _imp_waste_item(v, ctx, session, body):
    name = v("不良品项名称")
    return core.Store("wastes").add({
        "code": v("不良品项编号") or name, "name": name,
        "enterprise_id": session.get("enterprise_id"),
    })


def _bill_common(v, session, body, store_name, code_prefix):
    """加工单/外协/生产计划共用的商品解析与编号。"""
    model = str(body.get("import_model") or "1")
    g = _resolve_goods(v, by_name=(model == "2"))
    store = core.Store(store_name)
    code = v("加工单号") or v("外协工单号") or v("计划编号") or core.next_code(code_prefix, store.all(), width=3, datefmt="%Y%m%d")
    return g, store, code


def _imp_produce_bill(v, ctx, session, body):
    g, store, code = _bill_common(v, session, body, "produce_bills", "JG")
    crafts = [_find(core.Store("crafts"), nm, ("name",)) for nm in _split_names(v("工序名"))]
    row = {
        "code": code, "goods_id": g.get("id"), "goods": _goods_mini(g),
        "plan_num": _n(v("计划数"), "1"), "num": _n(v("计划数"), "1"),
        "is_use_default_bom": YESNO.get(v("是否使用默认BOM") or "是", 1),
        "bom_name": v("BOM名称"), "delivery_date": v("交货日期"),
        "priority": v("优先级") or 2, "plan_start_time": v("计划开始"), "plan_end_time": v("计划结束"),
        "remark": v("备注"), "status": 1, "status_name": "未开始", "type": body.get("type") or 1,
        "enterprise_id": session.get("enterprise_id"),
    }
    cl = [{"pub_craft_id": c.get("id"), "pub_craft": {"id": c.get("id"), "name": c.get("name")}} for c in crafts if c]
    if cl:
        row["craft_list"] = cl
    return store.add(row)


def _imp_outsource_bill(v, ctx, session, body):
    g, store, code = _bill_common(v, session, body, "outsource_produce_bills", "WX")
    sup = _find(core.Store("suppliers"), v("供应商名称") or v("供应商编号"), ("name", "code"))
    row = {
        "code": code, "goods_id": g.get("id"), "goods": _goods_mini(g),
        "plan_num": _n(v("计划数"), "1"), "num": _n(v("计划数"), "1"),
        "is_use_default_bom": YESNO.get(v("是否使用默认BOM") or "是", 1),
        "bom_name": v("BOM名称"), "delivery_date": v("交货日期"),
        "priority": v("优先级") or 2, "outsource_money": _n(v("外协单价"), "0.00000000"),
        "plan_start_time": v("计划开始"), "plan_end_time": v("计划结束"),
        "remark": v("备注"), "status": 1, "status_name": "未开始",
        "enterprise_id": session.get("enterprise_id"),
    }
    if sup:
        row["supplier_id"], row["supplier"] = sup.get("id"), {"id": sup.get("id"), "name": sup.get("name")}
    return store.add(row)


def _imp_produce_schedule(v, ctx, session, body):
    g, store, code = _bill_common(v, session, body, "produce_schedules", "JH")
    return store.add({
        "code": code, "name": v("计划名称") or code, "delivery_date": v("交货日期"), "remark": v("备注"),
        "goods_id": g.get("id"), "goods": _goods_mini(g),
        "plan_num": _n(v("计划数量"), "1"), "num": _n(v("计划数量"), "1"),
        "is_use_default_bom": YESNO.get(v("是否使用默认BOM") or "是", 1), "bom_name": v("BOM名称"),
        "enterprise_id": session.get("enterprise_id"),
    })


def _imp_purchase_bill(v, ctx, session, body):
    """同采购单号的多行合并为一张采购单（前端 rowspan 分组语义，服务端按编号聚合）。"""
    model = str(body.get("import_model") or "1")
    g = _resolve_goods(v, by_name=(model == "2"))
    sup = _find(core.Store("suppliers"), v("供应商"), ("name", "code"))
    store = core.Store("purchase_bills")
    code = v("采购单号")
    bill = ctx.get("purchase", {}).get(code) if code else None
    item = {
        "goods_id": g.get("id"), "goods": _goods_mini(g),
        "num": _n(v("商品数量"), "1"), "unit_price": _n(v("商品单价"), "0"),
        "discount_money": _n(v("商品优惠金额"), "0"), "remark": v("商品备注"),
    }
    try:
        item["money"] = "%.8f" % max(0.0, float(item["num"]) * float(item["unit_price"]) - float(item["discount_money"]))
    except (TypeError, ValueError):
        item["money"] = "0.00000000"
    if bill:
        bill["item_list"].append(item)
        bill["money"] = "%.8f" % (float(bill.get("money") or 0) + float(item["money"]))
        bill["purchase_num"] = str(int(float(bill.get("purchase_num") or 0)) + int(float(item["num"])))
        store.update(bill.get("id"), {"item_list": bill["item_list"], "money": bill["money"], "purchase_num": bill["purchase_num"]})
        return bill
    if not sup:
        raise RowError("供应商不存在")
    row = store.add({
        "code": code or core.next_code("CG", store.all(), width=3, datefmt="%Y%m%d"),
        "supplier_id": sup.get("id"), "supplier": {"id": sup.get("id"), "name": sup.get("name"), "code": sup.get("code")},
        "purchase_user": v("采购人"), "delivery_date": v("交货日期"), "remark": v("备注"),
        "audit_status": 1, "audit_status_name": "待审核",
        "money": item["money"], "purchase_num": item["num"], "item_list": [item],
        "enterprise_id": session.get("enterprise_id"),
    })
    ctx.setdefault("purchase", {})[row["code"]] = row
    return row


def _imp_customer_contract(v, ctx, session, body):
    """同合同编号多行合并一张合同；商品不存在自动新建（模板说明 8）。"""
    model = str(body.get("import_model") or "1")
    g = _resolve_goods(v, by_name=(model == "2"))
    store = core.Store("contracts")
    code = v("合同编号")
    bill = ctx.get("contract", {}).get(code) if code else None
    item = {
        "goods_id": g.get("id"), "goods": _goods_mini(g),
        "unit_price": _n(v("商品单价"), "0"), "num": _n(v("商品数量"), "1"),
        "discount_money": _n(v("商品优惠金额"), "0"), "remark": v("商品备注"),
    }
    try:
        item["money"] = "%.8f" % max(0.0, float(item["num"]) * float(item["unit_price"]) - float(item["discount_money"]))
    except (TypeError, ValueError):
        item["money"] = "0.00000000"
    if bill:
        bill["item_list"].append(item)
        bill["money"] = "%.8f" % (float(bill.get("money") or 0) + float(item["money"]))
        bill["sale_total"] = str(int(float(bill.get("sale_total") or 0)) + int(float(item["num"])))
        store.update(bill.get("id"), {"item_list": bill["item_list"], "money": bill["money"], "sale_total": bill["sale_total"]})
        return bill
    cust = _find(core.Store("customers"), v("客户"), ("name", "code"))
    row = store.add({
        "code": code or core.next_code("HT", store.all(), width=3, datefmt="%Y%m%d"),
        "customer_id": (cust or {}).get("id"),
        "customer": {"id": (cust or {}).get("id"), "name": v("客户"), "code": (cust or {}).get("code")},
        "linkman_name": v("联系人"), "province": v("省"), "city": v("市"), "area": v("区"), "address": v("地址"),
        "advance": _n(v("预付款"), "0"), "delivery_date": v("交货日期"), "collection_date": v("收款日期"),
        "has_tax": YESNO.get(v("是否含税") or "是", 1), "remark": v("备注"), "sales_user_name": v("业务员"),
        "audit_status": 1, "audit_status_name": "待审核", "business_status": 1, "business_status_name": "履约中",
        "money": item["money"], "sale_total": item["num"], "income_money": "0", "item_list": [item],
        "enterprise_id": session.get("enterprise_id"),
    })
    ctx.setdefault("contract", {})[row["code"]] = row
    return row


def _imp_customer(v, ctx, session, body):
    store = core.Store("customers")
    return store.add({
        "code": v("编号") or _next_seq("KH", store.all()),
        "name": v("名称"), "tag_names": ",".join(_split_names(v("客户标签"))),
        "default_linkman": {"name": v("联系人"), "mobile": v("联系人电话")},
        "province": v("省"), "city": v("市"), "area": v("区"), "address": v("地址"),
        "is_enable": 1, "enterprise_id": session.get("enterprise_id"),
    })


def _imp_supplier(v, ctx, session, body):
    store = core.Store("suppliers")
    return store.add({
        "code": v("编号") or _next_seq("GYS", store.all()),
        "name": v("名称"), "enterprise_name": v("供应商企业名称"),
        "credit_code": v("统一社会信用代码"), "linkman": v("联系人"), "charge_user": v("负责人"),
        "mobile": v("联系人电话"), "province": v("省"), "city": v("市"), "area": v("区"), "address": v("地址"),
        "tag_names": ",".join(_split_names(v("供应商标签"))), "level_name": v("供应商级别"),
        "remark": v("备注"), "account_name": v("收款账户名"), "bank_name": v("开户银行"), "bank_code": v("银行卡号"),
        "is_enable": 1, "enterprise_id": session.get("enterprise_id"),
    })


def _imp_stock_in_bill(v, ctx, session, body):
    store = core.Store("stock_in_bills")
    code = v("其他入库单号")
    bill = ctx.get("stock_in", {}).get(code) if code else None
    g = _resolve_goods(v)
    item = {"goods_id": g.get("id"), "goods": _goods_mini(g), "num": _n(v("商品数量"), "1"),
            "selling_money": _n(v("销售单价(元)"), "0"), "cost_money": _n(v("成本单价(元)"), "0"),
            "remark": v("商品备注")}
    if bill:
        bill["item_list"].append(item)
        store.update(bill.get("id"), {"item_list": bill["item_list"]})
        return bill
    sup = _find(core.Store("suppliers"), v("供应商"), ("name", "code"))
    row = store.add({
        "code": code or core.next_code("RK", store.all(), width=3, datefmt="%Y%m%d"),
        "type": 2, "type_name": "其它入库", "supplier_id": (sup or {}).get("id"),
        "supplier": {"id": (sup or {}).get("id"), "name": v("供应商")},
        "ware_name": v("仓库"), "in_date": v("入库日期"), "receiver": v("收货人"),
        "remark": v("备注"), "audit_status": 1, "audit_status_name": "待审核", "item_list": [item],
        "enterprise_id": session.get("enterprise_id"),
    })
    ctx.setdefault("stock_in", {})[row["code"]] = row
    return row


def _imp_stock_out_bill(v, ctx, session, body):
    store = core.Store("stock_out_bills")
    code = v("其他出库单号")
    bill = ctx.get("stock_out", {}).get(code) if code else None
    g = _resolve_goods(v)
    item = {"goods_id": g.get("id"), "goods": _goods_mini(g), "num": _n(v("商品数量"), "1"),
            "selling_money": _n(v("销售单价(元)"), "0"), "cost_money": _n(v("成本单价(元)"), "0"),
            "remark": v("商品备注")}
    if bill:
        bill["item_list"].append(item)
        store.update(bill.get("id"), {"item_list": bill["item_list"]})
        return bill
    row = store.add({
        "code": code or core.next_code("CK", store.all(), width=3, datefmt="%Y%m%d"),
        "type": 2, "type_name": "其它出库", "ware_name": v("仓库"), "out_date": v("出库日期"),
        "sender": v("发货人"), "remark": v("备注"), "audit_status": 1, "audit_status_name": "待审核",
        "item_list": [item], "enterprise_id": session.get("enterprise_id"),
    })
    ctx.setdefault("stock_out", {})[row["code"]] = row
    return row


# ---------------------------------------------------------------- 模块注册表
# file/variants: backend\templates 下真实模板原件（原站下载字节）
# titles: 真实模板首行（逐字）；required: 真实 header JSON required_titles（未捕获者按模板"红底必填"推断）
# verb: 原站导入动词（import=服务端直传族 / web_import=浏览器解析族，deep\06 §1.6 矩阵）
# max_rows: 模板"导入说明"单次上限
MODULES = {
    "goods": {
        "file": "goods.xlsx", "name": "商品", "verb": "import", "max_rows": 5000,
        "titles": ["商品编号", "商品名", "类别", "单位", "销售单价", "成本单价", "来源", "规格", "供应商",
                   "检验规范", "外协单价", "备注", "工序名称", "工艺路线名称", "库存下限警告值", "库存上限警告值", "呆滞警告值"],
        "required": ["商品名"], "builder": _imp_goods,
    },
    "goods_craft": {
        "file": "goods_craft.xlsx", "name": "商品工序明细", "verb": "import", "max_rows": 5000,
        "titles": ["商品名", "商品编号", "商品规格", "工序名称", "检验方案", "抽检方案", "是否需要返工/报废",
                   "是否需外协", "是否需生产任务计时", "报工附加组件", "质检附加组件", "单位关系(生产1个计划数需要多少)",
                   "计价方式", "工资单价", "标准工时", "工时单位", "备注"],
        "required": ["商品名", "工序名称"], "builder": _imp_goods_craft,   # 真实 header JSON 实证
    },
    "goods_bom": {
        "file": "goods_bom.xlsx", "name": "商品BOM", "verb": "import", "max_rows": 1000,
        "titles": ["BOM编号", "BOM名称", "类别", "启用/禁用", "设为默认BOM", "备注", "深度", "单位用量", "投料工序",
                   "商品编号", "商品名", "商品规格", "商品来源", "商品单位", "销售单价", "成本单价", "商品供应商",
                   "商品分类", "结构备注", "引用BOM"],
        "required": [], "builder": _imp_goods_bom,   # 条件必填：深度0→BOM名称；否则→商品名（模板说明 11/12）
    },
    "pub_craft": {
        "file": "pub_craft.xlsx", "name": "工序", "verb": "import", "max_rows": 5000,
        "titles": ["工序名", "工序编号", "检验方案", "抽检方案", "是否需要返工/报废", "是否需外协", "报工附加组件",
                   "质检附加组件", "车间", "产线", "单位关系(生产1个计划数需要多少)", "计价方式", "工资单价", "报工单位",
                   "标准工时", "工时单位", "机台编号", "生产人员姓名", "质检人员姓名", "不良品项名称", "备注",
                   "是否开启生产任务计时", "工艺要求"],
        "required": ["工序名", "检验方案", "是否需外协", "计价方式"], "builder": _imp_pub_craft,   # 真实 header JSON 实证
    },
    "craft_line": {
        "file": "craft_line.xlsx", "name": "工艺路线", "verb": "import", "max_rows": 5000,
        "titles": ["工艺路线编号", "工艺路线名称", "工序名称"],
        "required": ["工艺路线名称", "工序名称"], "builder": _imp_craft_line,   # 真实 header JSON 实证
    },
    "waste_item": {
        "file": "waste_item.xlsx", "name": "不良品项", "verb": "web_import", "max_rows": 1000,
        "titles": ["不良品项编号", "不良品项名称"],
        "required": ["不良品项名称"], "builder": _imp_waste_item,   # 真实 header JSON 实证
    },
    "produce_bill": {
        "file": "produce_bill.xlsx", "variants": {"1": "produce_bill.xlsx", "2": "produce_bill_by_name.xlsx"},
        "name": "加工单", "verb": "web_import", "max_rows": 1000,
        "titles": ["加工单号", "商品编号", "计划数", "是否使用默认BOM", "BOM名称", "工序名", "交货日期", "优先级", "计划开始", "计划结束", "备注"],
        "titles2": ["加工单号", "商品名", "商品编号", "商品规格", "计划数", "是否使用默认BOM", "BOM名称", "工序名", "交货日期", "优先级", "计划开始", "计划结束", "备注"],
        "required": ["商品编号", "计划数"], "required2": ["商品名", "计划数"],   # import_model 1/2（推断）
        "builder": _imp_produce_bill,
    },
    "outsource_produce_bill": {
        "file": "outsource_produce_bill.xlsx", "name": "外协工单", "verb": "web_import", "max_rows": 1000,
        "titles": ["外协工单号", "商品编号", "供应商编号", "供应商名称", "计划数", "是否使用默认BOM", "BOM名称",
                   "交货日期", "优先级", "外协单价", "计划开始", "计划结束", "备注"],
        "required": ["商品编号", "计划数"], "builder": _imp_outsource_bill,
    },
    "produce_schedule": {
        "file": "produce_schedule.xlsx", "name": "生产计划", "verb": "web_import", "max_rows": 1000,
        "titles": ["计划编号", "计划名称", "交货日期", "备注", "商品编号", "商品名", "商品规格", "计划数量", "是否使用默认BOM", "BOM名称"],
        "required": ["商品编号", "计划数量"], "builder": _imp_produce_schedule,
    },
    "purchase_bill": {
        "file": "purchase_bill.xlsx", "variants": {"1": "purchase_bill.xlsx", "2": "purchase_bill_by_name.xlsx"},
        "name": "采购单", "verb": "web_import", "max_rows": 1000,
        "titles": ["采购单号", "供应商", "采购人", "交货日期", "备注", "商品编号", "商品数量", "商品单价", "商品优惠金额", "商品备注"],
        "titles2": ["采购单号", "供应商", "采购人", "交货日期", "备注", "商品编号", "商品名", "商品规格", "商品数量", "商品单价", "商品优惠金额", "商品备注"],
        "required": ["供应商", "商品编号"], "required2": ["供应商", "商品名"],   # import_model 1/2（推断）
        "builder": _imp_purchase_bill,
    },
    "customer_contract": {
        "file": "customer_contract.xlsx", "variants": {"1": "customer_contract.xlsx", "2": "customer_contract_by_name.xlsx"},
        "name": "合同订单", "verb": "web_import", "max_rows": 1000,
        "titles": ["合同编号", "客户", "联系人", "省", "市", "区", "地址", "预付款", "交货日期", "收款日期", "是否含税",
                   "备注", "商品编号", "商品单价", "商品数量", "商品优惠金额", "商品备注", "业务员", "原材料", "胶条", "舌头", "透气孔", "制袋规格"],
        "titles2": ["合同编号", "客户", "联系人", "省", "市", "区", "地址", "预付款", "交货日期", "收款日期", "是否含税",
                    "备注", "商品编号", "商品名", "商品规格", "商品单价", "商品数量", "商品优惠金额", "商品类别", "商品单位",
                    "商品来源", "商品备注", "业务员", "原材料", "胶条", "舌头", "透气孔", "制袋规格"],
        "required": ["客户", "商品编号"], "required2": ["客户", "商品名"],   # import_model 1/2（推断）
        "builder": _imp_customer_contract,
    },
    "customer": {
        "file": "customer.xlsx", "name": "客户", "verb": "import", "max_rows": 5000,
        "titles": ["编号", "名称", "客户标签", "联系人", "联系人电话", "省", "市", "区", "地址"],
        "required": ["名称"], "builder": _imp_customer,
    },
    "supplier": {
        "file": "supplier.xlsx", "name": "供应商", "verb": "import", "max_rows": 5000,
        "titles": ["编号", "名称", "供应商企业名称", "统一社会信用代码", "联系人", "负责人", "联系人电话", "省", "市",
                   "区", "地址", "供应商标签", "供应商级别", "备注", "收款账户名", "开户银行", "银行卡号"],
        "required": ["名称"], "builder": _imp_supplier,
    },
    "stock_in_bill": {
        "file": "stock_in_bill.xlsx", "name": "其他入库单", "verb": "web_import", "max_rows": 1000,
        "titles": ["其他入库单号", "供应商", "仓库", "入库类型", "入库日期", "收货人", "备注", "商品编号", "商品名",
                   "商品规格", "商品数量", "销售单价(元)", "成本单价(元)", "商品备注", "车号"],
        "required": ["仓库", "商品编号", "商品数量"], "builder": _imp_stock_in_bill,
    },
    "stock_out_bill": {
        "file": "stock_out_bill.xlsx", "name": "其他出库单", "verb": "web_import", "max_rows": 1000,
        "titles": ["其他出库单号", "仓库", "出库类型", "出库日期", "发货人", "备注", "商品编号", "商品名", "商品规格",
                   "商品数量", "销售单价(元)", "成本单价(元)", "商品备注"],
        "required": ["仓库", "商品编号", "商品数量"], "builder": _imp_stock_out_bill,
    },
}


def _variant_of(spec, body):
    return "2" if str(body.get("import_model") or "1") == "2" else "1"


# ---------------------------------------------------------------- download_import_template
def _make_download(mod):
    spec = MODULES[mod]

    def h(body, session):
        fname = spec.get("file")
        if spec.get("variants"):
            fname = spec["variants"].get(_variant_of(spec, body), spec["variants"]["1"])
        path = os.path.join(TPL_DIR, fname)
        if not os.path.exists(path):
            return core.fail("404")
        with open(path, "rb") as f:
            data = f.read()
        return {"__binary__": data, "__filename__": "%s导入模板.xlsx" % spec["name"]}
    return h


# ---------------------------------------------------------------- import_template_header
def _make_header(mod):
    spec = MODULES[mod]

    def h(body, session):
        v2 = spec.get("variants") and _variant_of(spec, body) == "2"
        return {
            "success": True,
            "data": {
                "titles": list(spec["titles2"] if v2 else spec["titles"]),
                "required_titles": list(spec["required2"] if v2 else spec["required"]),
                "auto_titles": [],
            },
            "enterprise_config_update_time": core.now(),
        }
    return h


# ---------------------------------------------------------------- import / web_import
def _make_import(mod):
    spec = MODULES[mod]

    def h(body, session):
        rows = body.get("rows")
        if not isinstance(rows, list) or not rows:
            return core.fail("参数校验错误")
        if len(rows) - 1 > spec["max_rows"]:
            return core.fail("一次导入不能大于%d条记录。" % spec["max_rows"])
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        scm = body.get("swap_column_map")
        if isinstance(scm, str):
            try:
                scm = json.loads(scm)
            except ValueError:
                scm = {}
        scm = scm or {}
        idx = {}
        for i, t in enumerate(header):
            if t and t not in idx:
                idx[t] = i
        v2 = spec.get("variants") and _variant_of(spec, body) == "2"
        required = spec["required2"] if v2 else spec["required"]
        errors, ctx = [], {}

        def make_v(r):
            def v(field):
                col = scm.get(field, field)          # swap_column_map：{系统字段: Excel表头}
                i = idx.get(col)
                if i is None or i >= len(r):
                    return None
                return _s(r[i])
            return v

        for n, r in enumerate(rows[1:], start=2):
            cells = ["" if c is None else str(c).strip() for c in r]
            if not any(cells):
                continue
            v = make_v(r)
            missing = [f for f in required if not v(f)]
            if missing:
                errors.append(["第%d行：%s不能为空" % (n, "、".join(missing))] + cells)
                continue
            try:
                spec["builder"](v, ctx, session, body)
            except RowError as e:
                errors.append(["第%d行：%s" % (n, e)] + cells)
            except Exception:
                traceback.print_exc()
                errors.append(["第%d行：数据异常" % n] + cells)
        # 原站语义：data=失败行集合（首元素=错误原因），成功行已落库
        return core.ok(errors)
    return h


# ---------------------------------------------------------------- export
def _filter_rows(rows, body):
    """按列表筛选参数过滤（空值跳过；ids=勾选行；keyword 跨编号/名称/规格模糊）。"""
    paged = {"token", "enterprise_id", "pageNo", "pageSize", "timestamp", "sign", "channel", "module",
             "import_model", "import_type", "swap_column_map", "filter_view_id", "sortField", "sortOrder",
             "ids", "keyword", "keywords"}
    ids = body.get("ids")
    if isinstance(ids, list) and ids:
        want = {str(x) for x in ids}
        rows = [r for r in rows if str(r.get("id")) in want]
    kw = str(body.get("keyword") or body.get("keywords") or "")
    if kw:
        rows = [r for r in rows if kw in ("%s%s%s" % (r.get("code") or "", r.get("name") or "", r.get("standard") or ""))]
    for k, val in body.items():
        if k in paged or val is None or val == "":
            continue
        rows = [r for r in rows if str(val) in str(r.get(k) if r.get(k) is not None else "")]
    return rows


def _build_xlsx(sheet, headers, data_rows):
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet or "Worksheet")[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in data_rows:
        ws.append([x if isinstance(x, (int, float)) and not isinstance(x, bool) else (_num(x) if not isinstance(x, str) else x) for x in r])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _linkman_text(r):
    lm = r.get("linkman") or r.get("default_linkman") or {}
    if isinstance(lm, dict) and (lm.get("name") or lm.get("mobile")):
        return "%s(%s)" % (lm.get("name") or "", lm.get("mobile") or "") if lm.get("mobile") else lm.get("name")
    return r.get("linkman_name") or r.get("linkman") or None


def _addr(r):
    parts = [r.get(x) for x in ("province", "city", "area", "address")]
    t = " ".join(str(p) for p in parts if p)
    return t or None


def _pct(r, *keys):
    v = _d(r, *keys)
    return v if v is not None else None


# ---- 各模块导出行构造（列头逐字对照真实导出 xlsx） ----
def _ex_goods(r):
    crafts = r.get("craft_list") or []
    cnames = ",".join(_obj_name({"x": c.get("pub_craft")}, "x") or "" for c in crafts if c.get("pub_craft"))
    return [[
        _num(r.get("code")), _num(r.get("name")),
        _num(_d(r, "category_path_names") or _obj_name(r, "category")),
        _num(_d(r, "stock_total_num", "stock_num")), _num(_d(r, "stock_total_available_num", "stock_available_num")),
        _num(r.get("standard")), _num(cnames or r.get("craft_names")),
        _num(r.get("qr_code_url") or ("https://single.kgd.ltd/#/g?qr_id=%s" % r.get("id"))),
        _num(_d(r, "selling_money")), _num(_d(r, "cost_money", "purchase_money")),
        _num(r.get("remark")), ENABLE_NAME.get(r.get("is_enable") or 1, "启用"),
        _num(_obj_name(r, "unit")), _num(_d(r, "source_name") or SOURCE_ID.get(r.get("source"))),
        _num(_d(r, "supplier_names") or _obj_name(r, "supplier")),
        _num(_d(r, "material_inspection_content_title") or _obj_name(r, "material_inspection_content")),
        _num(r.get("outsource_money")), _num(r.get("low_limit")), _num(r.get("upper_limit")),
        _num(r.get("stagnant_warning_day")),
        _num(_obj_name(r, "create_user")), _num(r.get("created_at")),
    ]]


def _ex_goods_bom(r):
    """父行(BOM 头) + 结构行展开（对照 BOM_全量导出.xlsx 真实行形状）。"""
    goods = r.get("goods") or {}
    out = [[
        _num(r.get("code")), _num(r.get("name")),
        _num(_obj_name(r, "big_category") or _d(r, "category_path_names") or "/"),
        _num(r.get("bom_cost_money_total")), ENABLE_NAME.get(r.get("is_enable") or 1, "启用"),
        YESNO_NAME.get(r.get("is_default") or 2, "否"), _num(r.get("remark")),
        "0", "1", None,
        _num(goods.get("code")), _num(goods.get("name")), _num(goods.get("standard")),
        _num(SOURCE_ID.get(goods.get("source")) or goods.get("source_name")),
        _num(_obj_name(goods, "unit") or goods.get("unit_name")),
        _num(goods.get("cost_money")), _num(goods.get("cost_money")), None,
        _num(goods.get("category_path_names")), None, None,
        _num(r.get("created_at")),
    ]]
    for t in core.Store("bom_trees").all():
        if str(t.get("goods_bom_id")) != str(r.get("id")):
            continue
        g = t.get("goods") or {}
        out.append([
            None, None, None, None, None, None, None,
            _num(t.get("level")), _num(t.get("num")), _num(_obj_name(t, "pub_craft")),
            _num(g.get("code")), _num(g.get("name")), _num(g.get("standard")),
            _num(SOURCE_ID.get(g.get("source")) or g.get("source_name")),
            _num(_obj_name(g, "unit") or g.get("unit_name")),
            _num(_d(t, "goods_cost_money")), _num(_d(t, "goods_cost_money_total")),
            _num(_obj_name(g, "supplier") or g.get("supplier_names")),
            _num(g.get("category_path_names")), _num(t.get("remark")),
            _num(_obj_name(t, "reference_bom")), None,
        ])
    return out


def _ex_pub_craft(r):
    sw = r.get("standard_working_seconds")
    try:
        sw = int(float(sw)) if sw is not None else None
    except (TypeError, ValueError):
        sw = None
    std = r.get("standard_working_name")
    if not std and sw:
        h, m, s = sw // 3600, sw % 3600 // 60, sw % 60
        std = "%d小时%d分钟%d秒" % (h, m, s) if h or m or s else None
    money = _d(r, "unit_money")
    if money is not None:
        money = "%s元" % money
    return [[
        _num(r.get("name")), _num(r.get("code")),
        _num(_d(r, "workshop_path_names") or _obj_name(r, "workshop")), _num(r.get("produce_line_names")),
        _num(_d(r, "price_mode_name") or ({1: "计件", 2: "计时"}.get(r.get("price_mode")))),
        _num(money), _num(r.get("device_names")), _num(r.get("reportable_user_names")),
        _num(r.get("quality_able_user_names")), _num(r.get("waste_item_names")),
        _num(_obj_name(r, "unit")), _num(r.get("unit_relation") or "生产1个计划数，需要1"),
        _num(std),
        _num(_d(r, "quality_type_name") or QUALITY_NAME.get(r.get("quality_type") or 3, "不需检验")),
        YESNO_NAME.get(r.get("need_scrap_rework") or 2, "否"), YESNO_NAME.get(r.get("need_outsource") or 2, "否"),
        _num(_obj_name(r, "report_attach_module")), _num(_obj_name(r, "quality_attach_module")),
        ENABLE_NAME.get(r.get("is_enable") or 1, "启用"), _num(r.get("remark")),
        YESNO_NAME.get(r.get("is_open_clock") or 2, "否"), _num(r.get("craft_requirement") or "-"),
        _num(r.get("created_at")), _num(_obj_name(r, "create_user")),
    ]]


def _ex_produce_bill(r):
    goods = r.get("goods") or {}
    crafts = r.get("craft_list") or []
    cur = r.get("current_craft_name") or (_obj_name({"x": crafts[-1].get("pub_craft")}, "x") if crafts else None)
    total_s = _d(r, "total_working_seconds", "sum_working_seconds")
    return [[
        _num(r.get("sort_num")), _num(r.get("code")),
        _num(r.get("qr_code_url") or ("https://single.kgd.ltd/#/p?qr_id=%s" % r.get("id"))),
        _num("是" if r.get("is_overdue") in (1, "1", True) else "否"),
        _num(_obj_name(r, "produce_schedule") or r.get("produce_schedule_code")),
        _num(_d(r, "produce_schedule_name")),
        _num(_obj_name(r, "project") or r.get("project_code")), _num(_d(r, "project_name")),
        _num(_obj_name(r, "customer_contract") or r.get("contract_code")),
        _num(_d(r, "sales_user_name") or _obj_name(r, "sales_user")),
        _num(goods.get("name")), _num(goods.get("code")), _num(goods.get("standard")),
        _num(r.get("bom_name")), _num(goods.get("category_path_names")),
        _num(_d(r, "plan_num", "num")),
        _num(_d(goods, "stock_total_num") or r.get("goods_stock_num")),
        _num(_d(r, "finish_num", "real_num", "produce_num")),
        _num(_d(r, "status_name") or {1: "未开始", 2: "生产中", 3: "已完成", 4: "已取消", 5: "已暂停"}.get(r.get("status"), "")),
        _num(_d(r, "type_name") or ("单件生产" if str(r.get("type") or 1) == "1" else "批量生产")),
        _num(r.get("material_status_name") or r.get("picking_status_name")),
        _num(r.get("pause_reason")), _num(r.get("cancel_reason")), _num(r.get("priority")),
        _num(cur), _num(r.get("craft_progress")), _num(r.get("task_progress")),
        _num(r.get("stock_out_bill_code") or r.get("production_stock_out_bill_code")),
        _num(r.get("stock_in_bill_code") or r.get("produce_stock_in_bill_code")),
        _num(r.get("stock_in_progress") or r.get("in_stock_progress")),
        _num(r.get("remark")), _num(r.get("drawing")), _num(r.get("delivery_countdown")),
        _num(r.get("delivery_date")), _num(r.get("plan_start_time")), _num(r.get("plan_end_time")),
        _num(total_s), _num(r.get("start_time")), _num(r.get("finish_time")),
        _num(_obj_name(r, "create_user")), _num(r.get("created_at")),
    ]]


def _ex_waste_item(r):
    return [[_num(r.get("code")), _num(r.get("name")), _num(r.get("created_at"))]]


def _ex_customer_contract(r):
    cust = r.get("customer") or {}
    items = r.get("item_list") or (r.get("purchase_bill_item_list") or [])
    base_head = lambda item: [
        _num(r.get("code")), _num(_obj_name(r, "contract_quotation_bill") or r.get("contract_quotation_bill_code")),
        _num(cust.get("code")), _num(cust.get("name") or r.get("customer_name")),
        _num(_linkman_text(r)), _num(_d(r, "sale_expense_money", "sale_money")),
        _num(_addr(r)),
        _num(_d(r, "audit_status_name") or "不需审核"), _num(_d(r, "business_status_name") or "履约中"),
        _num(_d(r, "finish_date", "last_finish_time")), _num(r.get("audit_pass_time")),
    ]
    base_tail = lambda item: [
        _num(_d(item or {}, "remark")),
        _num(_d(r, "sale_total")), _num(r.get("money")), _num(r.get("advance")),
        _num(r.get("income_money")), _num(_pct(r, "collection_progress")),
        _num(r.get("expend_money")), _num(r.get("already_invoice_money")), _num(r.get("debt_money")),
        _num(r.get("delivery_date")), _num(r.get("collection_date")),
        YESNO_NAME.get(r.get("has_tax") or 1, "是"), _num(r.get("remark")),
        _num(_d(r, "sales_user_name") or _obj_name(r, "sales_user")),
        _num(_obj_name(r, "create_user")), _num(r.get("stock_out_finish_date")), _num(r.get("created_at")),
    ]
    if not items:
        return [base_head(None) + [None] * 11 + base_tail(None)]
    out = []
    for item in items:
        g = item.get("goods") or {}
        mid = [
            _num(g.get("name")), _num(g.get("code")), _num(g.get("standard")),
            _num(g.get("category_path_names")), _num(item.get("unit_price")),
            _num(_obj_name(g, "unit") or g.get("unit_name")), _num(item.get("num")),
            _num(_d(g, "stock_total_num")),
            _num(_pct(item, "put_into_produce_percent", "put_into_percent") or _pct(r, "put_into_produce_progress")),
            _num(_pct(item, "stock_out_percent") or _pct(r, "stock_out_progress")),
            _num(_pct(item, "purchase_percent") or _pct(r, "purchase_progress")),
        ]
        out.append(base_head(item) + mid + base_tail(item))
    return out


def _ex_purchase_bill(r):
    """原站 /purchase_bill/export 404，本表列为推断（按列表主列+导入模板字段组合，无真实导出对照）。"""
    sup = r.get("supplier") or {}
    return [[
        _num(r.get("code")), _num(sup.get("name") or r.get("supplier_name")),
        _num(_d(r, "supplier_linkman") or r.get("linkman")), _num(r.get("supplier_address")),
        _num(r.get("money")), _num(_d(r, "purchase_num", "purchase_total_num")),
        _num(r.get("stock_in_bill_money")), _num(r.get("stock_out_bill_money")),
        _num(r.get("already_payment_money")), _num(r.get("debt_money")),
        _num(_d(r, "purchase_user") or _obj_name(r, "purchase_user_obj")),
        _num(_d(r, "audit_status_name")), _num(r.get("delivery_date")),
        _num(r.get("created_at")), _num(r.get("remark")),
    ]]


def _ex_customer(r):
    lm = r.get("default_linkman") or {}
    return [[
        _num(r.get("name")), _num(_d(r, "tag_names")), _num(lm.get("name")), _num(lm.get("mobile") or lm.get("phone")),
        _num(_d(r, "location") or _addr(r)), ENABLE_NAME.get(r.get("is_enable") or 1, "启用"),
        _num(_obj_name(r, "resp_user")), _num(_obj_name(r, "create_user")),
        _num(r.get("code")), _num(r.get("created_at")),
    ]]


def _ex_supplier(r):
    return [[
        _num(r.get("code")), _num(r.get("name")), _num(_d(r, "credit_code", "unified_credit_code")),
        _num(_d(r, "level_name", "supplier_level_name")), _num(_d(r, "source_type_name")),
        _num(_d(r, "life_stage_name")), _num(_d(r, "linkman")), _num(_d(r, "mobile", "linkman_mobile")),
        _num(_d(r, "account_name")), _num(_d(r, "bank_name")), _num(_d(r, "bank_code", "bank_card")),
        _num(_d(r, "address") or _addr(r)), _num(_d(r, "tag_names")), _num(r.get("remark")),
        _num(_d(r, "charge_user") or _obj_name(r, "charge_user_obj")), _num(_obj_name(r, "create_user")),
        _num(r.get("created_at")),
    ]]


EXPORTS = {
    "goods": {"store": "goods", "sheet": "Worksheet", "file": "商品导出.xlsx",
              "headers": ["商品编号", "商品名", "类别", "总库存", "可用库存", "规格", "工序名", "商品二维码链接",
                          "销售单价", "成本单价", "备注", "状态", "单位", "商品来源", "供应商", "检验规范", "外协单价",
                          "下限警告值", "上限警告值", "呆滞警告值", "创建人", "创建时间"],
              "rows_of": _ex_goods},
    "goods_bom": {"store": "boms", "sheet": "Worksheet", "file": "BOM导出.xlsx",
                  "headers": ["BOM编号", "BOM名称", "类别", "成本总额（元）", "启用/禁用", "默认BOM", "备注", "深度",
                              "单位用量", "投料工序", "商品编号", "商品名", "商品规格", "商品来源", "商品单位",
                              "成本单价（元）", "成本金额（元）", "商品供应商", "商品分类", "结构备注", "引用BOM", "创建时间"],
                  "rows_of": _ex_goods_bom},
    "pub_craft": {"store": "crafts", "sheet": "Worksheet", "file": "工序导出.xlsx",
                  "headers": ["工序名", "工序编号", "车间", "产线", "计价方式", "工资单价", "机台", "生产人员", "质检人员",
                              "不良品项", "报工单位", "单位关系", "标准工时", "检验方案", "需返工/报废", "需外协",
                              "报工附加组件", "质检附加组件", "状态", "备注", "是否开启生产任务计时", "工艺要求",
                              "创建时间", "创建人"],
                  "rows_of": _ex_pub_craft},
    "produce_bill": {"store": "produce_bills", "sheet": "Worksheet", "file": "加工单导出.xlsx",
                     "headers": ["排单序号", "加工单号", "二维码信息", "是否逾期", "生产计划编号", "生产计划名", "项目编号",
                                 "项目名称", "合同编号", "业务员", "商品", "商品编号", "商品规格", "BOM名称", "商品类别",
                                 "计划数", "商品库存", "实际生产数", "状态", "工单类型", "领料状态", "暂停原因", "取消原因",
                                 "优先级", "当前工序", "工序进度", "任务进度", "生产出库单号", "成品入库单号", "入库进度",
                                 "备注", "图纸", "交货倒计时", "交货日期", "计划开始", "计划结束", "加工单总工时", "开始时间",
                                 "完成时间", "创建人", "创建时间"],
                     "rows_of": _ex_produce_bill},
    # 复刻前端 feExport（原站无服务端导出路由）：列=表格显示列，仅表头加粗
    "waste_item": {"store": "wastes", "sheet": "不良品项导出", "file": "不良品项导出.xlsx",
                   "headers": ["不良品项编号", "不良品项名称", "创建时间"],
                   "rows_of": _ex_waste_item},
    "customer_contract": {"store": "contracts", "sheet": "Worksheet", "file": "合同订单导出.xlsx",
                          "headers": ["合同编号", "合同报价单号", "客户编号", "客户名", "联系人", "销售费用(元)", "客户地址",
                                      "审核状态", "业务状态", "合同完成时间", "审核通过时间", "商品名", "商品编号", "商品规格",
                                      "商品类别", "商品单价", "商品单位", "销售数量", "商品库存", "投产进度", "出库进度",
                                      "采购进度", "商品备注", "销售总数", "合同金额", "预付款", "累计收款", "收款进度",
                                      "累计支出", "已开票金额", "欠款", "交货日期", "收款日期", "是否含税", "备注", "业务员",
                                      "创建人", "出库完成日期", "创建时间"],
                          "rows_of": _ex_customer_contract},
    "purchase_bill": {"store": "purchase_bills", "sheet": "Worksheet", "file": "采购单导出.xlsx",
                      "headers": ["采购单号", "供应商", "联系人", "地址", "金额(元)", "采购数量", "入库金额(元)",
                                  "退货金额(元)", "已付款(元)", "欠款(元)", "采购人", "审核状态", "交货日期", "创建时间", "备注"],
                      "rows_of": _ex_purchase_bill},
    "customer": {"store": "customers", "sheet": "Worksheet", "file": "客户导出.xlsx",
                 "headers": ["客户名", "客户标签", "联系人", "联系人电话", "地址", "启用/禁用", "负责人", "创建人", "编号", "创建时间"],
                 "rows_of": _ex_customer},
    "supplier": {"store": "suppliers", "sheet": "Worksheet", "file": "供应商导出.xlsx",
                 "headers": ["供应商编号", "供应商名称", "统一社会信用代码", "供应商级别", "来源类型", "生命阶段", "联系人",
                             "联系人电话", "收款账户名", "开户银行", "银行卡号", "地址", "供应商标签", "备注", "负责人",
                             "创建人", "创建时间"],
                 "rows_of": _ex_supplier},
}


def _make_export(mod):
    spec = EXPORTS[mod]

    def h(body, session):
        rows = _filter_rows(core.Store(spec["store"]).all(), body)
        data = []
        for r in rows:
            data.extend(spec["rows_of"](r))
        blob = _build_xlsx(spec["sheet"], spec["headers"], data)
        return {"__binary__": blob, "__filename__": spec["file"]}
    return h


# ---------------------------------------------------------------- 路由注册
ROUTES = {}

for _mod, _spec in MODULES.items():
    ROUTES[(_mod, "download_import_template")] = _make_download(_mod)
    ROUTES[(_mod, "import_template_header")] = _make_header(_mod)
    ROUTES[(_mod, _spec["verb"])] = _make_import(_mod)

for _mod in EXPORTS:
    ROUTES[(_mod, "export")] = _make_export(_mod)

# 原站无服务端导出路由（02 清单 FAIL 记录），保持 404 语义
ROUTES[("craft_line", "export")] = lambda b, s: core.fail("404")
