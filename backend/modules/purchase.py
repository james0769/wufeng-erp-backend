# -*- coding: utf-8 -*-
r"""B8 供应/采购域（依据 deep\13-供应采购域.md + api_dump\dd_supplier.json / deep_m5 实测）。

覆盖：
- supplier：list（54 条种子，B11 生成 data\suppliers.json）/ add / edit / del / detail / status_count
  + supplier_linkman / supplier_address 子表 list + dropdown / autocomplete（采购单表单数据源）
- purchase_bill：list / add / edit / del / detail / audit（7 值状态机）/ withdrawal / status_count /
  money_statistics / purchase_bill_item_money（明细金额公式试算）/ formula_support_field_select
- purchase_plan：list（缺料自动生成记录：BOM 一层展开的物料需求 − 库存）
- goods_stock_out_analysis：get_month_prepared_suggestion（备料建议 = 需求 − 库存，下限 0）

核心业务规则（出处 deep\13 报告）：
- 明细金额公式 purchase_bill_item_money_formula = "${明细.采购单价}*${明细.采购数量}"，
  小数位 8（purchase_bill_item_money_decimal_num）；无效公式 → "明细金额公式计算无效"；
  折后金额 after_discount_money = 金额 − 优惠金额；单据 money = Σ 折后金额（8 位），purchase_num = Σ num。
- 审核状态机 7 值 WAREHOUSE_AUDIT_STATUS：1 不需审核 / 2 待审 / 3 通过 / 4 退回 / 5 草稿 /
  6 撤回 / 7 审核中。审核 2|7 → 3|4；撤回 2|7 → 6；2|7（待审/审核中）不可改不可删。
- 编号：供应商 GYS+6 位流水；采购单 CG+YYYYMMDD+3 位（CG20261026001）；
  采购计划 CGJH+YYYYMMDD+3 位（CGJH20261006001）。
"""
import json
import re
from decimal import Decimal, ROUND_HALF_UP

import core

# ---- 采购金额公式（enterprise_config 实测值，D:ecfg_enterprise.json）----
ITEM_MONEY_FORMULA = "${明细.采购单价}*${明细.采购数量}"
ITEM_MONEY_DECIMAL = 8
# 公式可选变量（D:purchase/formula_fields.json 逐字）
FORMULA_FIELDS = {
    "采购数量": "明细.采购数量",
    "采购单价": "明细.采购单价",
    "商品名": "商品.商品名",
    "商品编号": "商品.商品编号",
    "商品规格": "商品.商品规格",
    "商品单位": "商品.商品单位",
}

# ---- 7 值审核状态机（WAREHOUSE_AUDIT_STATUS；tag_class 取自真实数据：3通过→5 / 2待审→3 / 1不需→4 / 4退回→2）----
AUDIT_STATUS_NAME = {1: "不需审核", 2: "待审", 3: "通过", 4: "退回", 5: "草稿", 6: "撤回", 7: "审核中"}
AUDIT_STATUS_TAG = {1: "4", 2: "3", 3: "5", 4: "2", 5: "1", 6: "6", 7: "3"}
LIFE_STAGE_NAME = {1: "注册", 2: "淘汰", 3: "潜在", 4: "准入"}   # SUPPLIER_LIFE_STAGE

_Q8 = Decimal("0.00000001")


def _dec(v):
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except Exception:
        return Decimal(0)


def _d8(v):
    """8 位小数字符串（与原站 "105.00000000" 同款；format f 避免 Decimal 的 0E-8 科学计数）。"""
    return format(_dec(v).quantize(_Q8, rounding=ROUND_HALF_UP), "f")


def _manual_page(rows, body, total, default_size=30):
    """对内存过滤后的列表分页（契约字段 pageNo/pageSize）。"""
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return {"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize], "total": total,
            "pageNo": pageNo, "pageSize": pageSize}


def _user_brief(uid):
    if uid is None:
        return None
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "name": u.get("name") or u.get("real_name"),
                    "real_name": u.get("real_name") or u.get("name"), "type_name": "普通用户",
                    "has_password": False, "claw_fun_type_name": "", "qr_code_url": None}
    return {"id": uid, "name": "", "real_name": "", "type_name": "普通用户",
            "has_password": False, "claw_fun_type_name": "", "qr_code_url": None}


def _goods_brief(gid):
    if gid is None:
        return None
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(gid):
            return {"id": g.get("id"), "name": g.get("name"), "code": g.get("code"),
                    "standard": g.get("standard"), "unit_id": g.get("unit_id"),
                    "unit": g.get("unit"), "source": g.get("source"),
                    "source_name": g.get("source_name"), "avatars": g.get("avatars") or [],
                    "cost_money": g.get("cost_money"), "category_path_names": g.get("category_path_names")}
    return None


def _supplier_brief(sid):
    if sid is None:
        return None
    for s in core.Store("suppliers").all():
        if str(s.get("id")) == str(sid):
            return {"id": s.get("id"), "name": s.get("name"), "code": s.get("code"),
                    "life_stage": s.get("life_stage")}
    return None


# ================= 供应商 =================

def _next_supplier_code(rows):
    mx = 0
    for r in rows:
        m = re.match(r"^GYS(\d+)$", str(r.get("code") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return "GYS" + str(mx + 1).zfill(6)


def _supplier_children(sid):
    linkmen = [r for r in core.Store("supplier_linkmans").all()
               if str(r.get("supplier_id")) == str(sid) and str(r.get("del", 2)) != "1"]
    addrs = [r for r in core.Store("supplier_addresses").all()
             if str(r.get("supplier_id")) == str(sid) and str(r.get("del", 2)) != "1"]
    linkmen.sort(key=lambda r: (r.get("is_default") != 1, r.get("id") or 0))
    addrs.sort(key=lambda r: (r.get("is_default") != 1, r.get("id") or 0))
    return linkmen, addrs


def _decorate_supplier(row):
    row = dict(row)   # 不污染存储行
    linkmen, addrs = _supplier_children(row.get("id"))
    row.setdefault("enterprise_id", 16807)
    row.setdefault("life_stage", 4)
    row.setdefault("source_type", 1)
    row.setdefault("is_enable", 1)
    row.setdefault("tag_ids", [])
    row["supplier_level_name"] = row.get("supplier_level_name") or ""
    row["tag_names"] = row.get("tag_names") or ""
    row["km"] = row.get("km")
    row["editAccessible"] = True
    row["resp_user"] = _user_brief(row.get("resp_user_id"))
    row["create_user"] = _user_brief(row.get("create_user_id"))
    row["default_linkman"] = row.get("default_linkman") or (linkmen[0] if linkmen else None)
    row["default_address"] = row.get("default_address") or (addrs[0] if addrs else None)
    row["default_receipt_account"] = row.get("default_receipt_account")
    row["linkman_count"] = len(linkmen)
    row["address_count"] = len(addrs)
    row["attachments"] = row.get("attachments") or []
    row["fieldValueList"] = row.get("fieldValueList") or []
    return row


def _supplier_list(body, session):
    rows = [r for r in core.Store("suppliers").all() if str(r.get("del", 2)) != "1"]
    for param, field in {"code": "code", "name": "name",
                         "supplier_enterprise_name": "supplier_enterprise_name",
                         "credit_code": "credit_code"}.items():
        kw = str(body.get(param) or "").strip()
        if kw:
            rows = [r for r in rows if kw in str(r.get(field) or "")]
    # 种子行（dd_supplier 格式）缺省 life_stage=4 准入 / source_type=1 内部（D:supplier_list_p1.json 实测全量）
    defaults = {"life_stage": 4, "source_type": 1, "is_enable": 1}
    for param, dft in defaults.items():
        if body.get(param) not in (None, "", "null"):
            rows = [r for r in rows if str(r.get(param, dft)) == str(body.get(param))]
    rows = [_decorate_supplier(r) for r in rows]
    return core.paged_ok(_manual_page(rows, body, len(rows)))


def _supplier_add(body, session):
    store = core.Store("suppliers")
    name = str(body.get("name") or "").strip()
    if not name:
        return core.fail("请输入供应商名称")
    row = {
        "enterprise_id": 16807,
        "name": name,
        "code": str(body.get("code") or "").strip() or _next_supplier_code(store.all()),
        "jianpin": body.get("jianpin"),
        "supplier_enterprise_name": body.get("supplier_enterprise_name"),
        "credit_code": body.get("credit_code"),
        "resp_user_id": body.get("resp_user_id") or (session or {}).get("id"),
        "create_user_id": (session or {}).get("id"),
        "life_stage": body.get("life_stage") or 4,
        "supplier_level_id": body.get("supplier_level_id"),
        "source_type": body.get("source_type") or 1,
        "is_enable": 1,
        "tag_ids": body.get("tag_ids") or [],
        "remark": body.get("remark"),
        "attachments": body.get("attachments") or [],
    }
    row = store.add(row, code_field="code")
    # 联系人/地址随表单一并落子表（共享表单 C:69549 行为）
    linkman = body.get("linkman") or {}
    if isinstance(linkman, dict) and (linkman.get("name") or linkman.get("mobile")):
        lm = core.Store("supplier_linkmans").add({
            "supplier_id": row["id"], "enterprise_id": 16807,
            "name": linkman.get("name"), "mobile": linkman.get("mobile"), "is_default": 1})
        row["default_linkman"] = lm
    addr = body.get("address") or {}
    if isinstance(addr, dict) and (addr.get("address") or addr.get("region_address")):
        ad = core.Store("supplier_addresses").add({
            "supplier_id": row["id"], "enterprise_id": 16807,
            "province_code": addr.get("province_code"), "city_code": addr.get("city_code"),
            "area_code": addr.get("area_code"), "address": addr.get("address"),
            "region_address": addr.get("region_address"),
            "full_address": addr.get("full_address")
            or ((addr.get("region_address") or "") + (addr.get("address") or "")),
            "location": addr.get("location"), "is_accurate": addr.get("is_accurate", 2),
            "is_default": 1})
        row["default_address"] = ad
    store.flush()
    return core.ok(_decorate_supplier(row))


def _supplier_edit(body, session):
    store = core.Store("suppliers")
    if not store.get(body.get("id")):
        return core.fail("供应商不存在")
    if body.get("name") is not None and not str(body.get("name")).strip():
        return core.fail("请输入供应商名称")
    patch = {f: body.get(f) for f in
             ("name", "code", "jianpin", "supplier_enterprise_name", "credit_code", "resp_user_id",
              "life_stage", "supplier_level_id", "source_type", "is_enable", "tag_ids", "remark",
              "stage_change_remark", "tag_names")
             if f in body}
    # 生命阶段降级（新值<当前值）时备注必填（C:65687 规则）
    if "life_stage" in patch:
        cur = store.get(body.get("id"))
        old = cur.get("life_stage") if cur else None
        try:
            if old is not None and int(patch["life_stage"]) < int(old) and not str(patch.get("stage_change_remark") or body.get("stage_change_remark") or "").strip():
                return core.fail("降级生命阶段时必须填写修改备注")
        except (TypeError, ValueError):
            pass
    row = store.update(body.get("id"), patch)
    return core.ok(_decorate_supplier(row))


def _supplier_del(body, session):
    store = core.Store("suppliers")
    ids = [str(i) for i in (body.get("ids") or ([body.get("id")] if body.get("id") else []))]
    if not ids:
        return core.fail("请选择要删除的供应商")
    n = 0
    for rid in ids:
        if store.get(rid):
            store.remove(rid)
            n += 1
    return core.ok({"deleted": n})


def _supplier_detail(body, session):
    row = core.Store("suppliers").get(body.get("id"))
    if not row:
        return core.fail("供应商不存在")
    row = _decorate_supplier(row)
    row["life_stage_name"] = LIFE_STAGE_NAME.get(row.get("life_stage"))
    row["source_type_name"] = "内部创建" if str(row.get("source_type")) == "1" else "外部注册"
    row["goods_count"] = len([g for g in core.Store("goods").all()
                              if str(g.get("supplier_id")) == str(row.get("id"))])
    row["purchase_bill_count"] = len([b for b in core.Store("purchase_bills").all()
                                      if str(b.get("supplier_id")) == str(row.get("id"))])
    return core.ok(row)


def _supplier_status_count(body, session):
    """快捷筛计数（实测 shape：deep_m5/supplier_status_count.json；种子行缺省=准入/内部）。"""
    rows = core.Store("suppliers").all()
    return core.ok({
        "life_stage_count": {
            "register": len([r for r in rows if str(r.get("life_stage", 4)) == "1"]),
            "eliminated": len([r for r in rows if str(r.get("life_stage", 4)) == "2"]),
            "potential": len([r for r in rows if str(r.get("life_stage", 4)) == "3"]),
            "approved": len([r for r in rows if str(r.get("life_stage", 4)) == "4"]),
        },
        "source_type_count": {
            "internal": len([r for r in rows if str(r.get("source_type", 1)) == "1"]),
            "external": len([r for r in rows if str(r.get("source_type", 1)) == "2"]),
        },
        "supplier_level_count": [],
    })


def _linkman_list(body, session):
    rows = [r for r in core.Store("supplier_linkmans").all() if str(r.get("del", 2)) != "1"]
    if body.get("supplier_id") not in (None, ""):
        rows = [r for r in rows if str(r.get("supplier_id")) == str(body.get("supplier_id"))]
    out = []
    for r in rows:
        r = dict(r)
        r["supplier"] = _supplier_brief(r.get("supplier_id"))
        r["fieldValueList"] = r.get("fieldValueList") or []
        r["has_password"] = False
        out.append(r)
    return core.paged_ok(_manual_page(out, body, len(out)))


def _address_list(body, session):
    rows = [r for r in core.Store("supplier_addresses").all() if str(r.get("del", 2)) != "1"]
    if body.get("supplier_id") not in (None, ""):
        rows = [r for r in rows if str(r.get("supplier_id")) == str(body.get("supplier_id"))]
    out = []
    for r in rows:
        r = dict(r)
        r["supplier"] = _supplier_brief(r.get("supplier_id"))
        r["fieldValueList"] = r.get("fieldValueList") or []
        out.append(r)
    return core.paged_ok(_manual_page(out, body, len(out)))


def _dropdown_supplier(body, session):
    """dd_supplier 同款（/dropdown/supplier：{id,name,code,del:false}）。"""
    kw = str(body.get("keyword") or body.get("name") or "").strip()
    rows = [{"id": s.get("id"), "name": s.get("name"), "code": s.get("code"), "del": False}
            for s in core.Store("suppliers").all()
            if str(s.get("del", 2)) != "1"
            and (not kw or kw in str(s.get("name") or "") or kw in str(s.get("code") or ""))]
    return core.ok(rows)


def _autocomplete_supplier(body, session):
    """采购单表单供应商自动补全（选中展示 code-name）。"""
    kw = str(body.get("keyword") or "").strip()
    out = []
    for s in core.Store("suppliers").all():
        label = "%s-%s" % (s.get("code"), s.get("name"))
        if kw and kw not in label:
            continue
        out.append({"id": s.get("id"), "name": s.get("name"), "code": s.get("code"), "value": label})
    try:
        limit = int(body.get("pageSize") or 20)
    except Exception:
        limit = 20
    return core.ok(out[:limit])


# ================= 采购单 purchase_bill =================

def _calc_item_money(num, unit_price, goods=None, formula=ITEM_MONEY_FORMULA):
    """按 enterprise_config 公式计算明细金额（返回 Decimal；None=公式无效）。
    数值变量（采购数量/采购单价）可参与运算；文本变量（商品名等）进入公式会令表达式
    含非法字符 → 无效（对应原站"明细金额公式计算无效"）。"""
    values = {"明细.采购数量": _dec(num), "明细.采购单价": _dec(unit_price)}
    if goods:
        unit = goods.get("unit")
        values["商品.商品名"] = goods.get("name")
        values["商品.商品编号"] = goods.get("code")
        values["商品.商品规格"] = goods.get("standard")
        values["商品.商品单位"] = unit.get("name") if isinstance(unit, dict) else unit

    def rep(m):
        v = values.get(m.group(1))
        if v is None or v == "":
            return "@@INVALID@@"        # 未知/空变量 → 表达式非法
        return "(" + str(v) + ")"

    expr = re.sub(r"\$\{([^}]+)\}", rep, formula)
    if not expr.strip() or not re.fullmatch(r"[0-9+\-*/().\s]+", expr):
        return None
    py = re.sub(r"(\d+(?:\.\d+)?)", r'D("\1")', expr)
    try:
        val = eval(py, {"__builtins__": {}}, {"D": Decimal})   # demo 级：字符集已白名单
    except Exception:
        return None
    return val.quantize(_Q8, rounding=ROUND_HALF_UP)


def _resolve_linkman_text(body):
    """表单 supplier_linkman_id / supplier_address_id → 行上文本（切换供应商自动选第一个）。"""
    sid = body.get("supplier_id")
    lman, addr = body.get("supplier_linkman"), body.get("supplier_address")
    if body.get("supplier_linkman_id"):
        for r in core.Store("supplier_linkmans").all():
            if str(r.get("id")) == str(body.get("supplier_linkman_id")) \
                    and (not sid or str(r.get("supplier_id")) == str(sid)):
                lman = r.get("name")
                break
    if body.get("supplier_address_id"):
        for r in core.Store("supplier_addresses").all():
            if str(r.get("id")) == str(body.get("supplier_address_id")) \
                    and (not sid or str(r.get("supplier_id")) == str(sid)):
                addr = r.get("full_address")
                break
    return lman, addr


def _build_items(body_items):
    """明细金额三级公式：money=公式 → after_discount_money=money−优惠金额 → 单据 money=Σ折后。
    校验文案逐字取自表单 chunk C:96563。"""
    if not body_items:
        return None, "列表不可为空"
    items = []
    for it in body_items:
        gid = it.get("goods_id")
        goods = _goods_brief(gid)
        gname = (goods or {}).get("name") or str(gid)
        num, price = _dec(it.get("num")), _dec(it.get("unit_price"))
        if num <= 0 or num > 99999999:
            return None, "%s的数量填写错误" % gname
        if price < 0 or price > 99999999:
            return None, "%s的单价填写错误" % gname
        discount_money = _dec(it.get("discount_money"))
        money = _calc_item_money(num, price, goods)
        if money is None:
            return None, "存在金额无法计算的商品，请核对后提交！"
        if discount_money < 0 or discount_money > 99999999:
            return None, "%s的优惠金额填写错误" % gname
        if discount_money > money:
            return None, "优惠金额不能大于金额"
        after = money - discount_money
        if after > 99999999:
            return None, "%s的优惠后金额不能超过99999999" % gname
        discount = it.get("discount")
        item = dict(it)   # 保留来源外键等透传字段
        item.update({
            "enterprise_id": 16807,
            "goods_id": gid,
            "num": _d8(num),
            "unit_price": _d8(price),
            "discount": "10.00" if discount in (None, "") else str(discount),
            "discount_money": _d8(discount_money),
            "money": _d8(money),
            "after_discount_money": _d8(after),
            "already_stock_in_num": _d8(it.get("already_stock_in_num") or 0),
            "delivery_num": _d8(it.get("delivery_num") or 0),
            "goods": goods,
            "del": 2,
        })
        items.append(item)
    return items, None


def _bill_totals(items):
    money = sum((Decimal(str(i["after_discount_money"])) for i in items), Decimal(0))
    num = sum((Decimal(str(i["num"])) for i in items), Decimal(0))
    return _d8(money), _d8(num)


def _decorate_bill(row):
    row = dict(row)   # 不污染存储行
    row["item_list"] = [dict(it) for it in (row.get("item_list") or [])]
    row["audit_status_name"] = AUDIT_STATUS_NAME.get(row.get("audit_status"), "")
    row["audit_status_tag_class"] = AUDIT_STATUS_TAG.get(row.get("audit_status"), "")
    row["auditAccessible"] = row.get("audit_status") in (2, 7)
    row["supplier"] = _supplier_brief(row.get("supplier_id"))
    row["create_user"] = _user_brief(row.get("create_user_id"))
    row["purchase_user"] = _user_brief(row.get("purchase_user_id"))
    for it in row["item_list"]:
        if not it.get("goods"):
            it["goods"] = _goods_brief(it.get("goods_id"))
    row.setdefault("already_stock_in_num", "0.00000000")
    row.setdefault("already_payment_money", 0)
    row.setdefault("already_invoice_money", 0)
    row.setdefault("debt_money", "0.00000000")
    row.setdefault("fieldValueList", [])
    row.setdefault("purchase_total_num", row.get("purchase_num"))
    return row


def _is_overdue(row):
    dd = str(row.get("delivery_date") or "")
    return bool(dd) and dd < core.now("%Y-%m-%d")


def _filtered_bills(body):
    rows = [r for r in core.Store("purchase_bills").all() if str(r.get("del", 2)) != "1"]
    code = str(body.get("code") or "").strip()
    if code:
        rows = [r for r in rows if code in str(r.get("code") or "")]
    if body.get("supplier_id") not in (None, ""):
        rows = [r for r in rows if str(r.get("supplier_id")) == str(body.get("supplier_id"))]
    sname = str(body.get("supplier") or "").strip()
    if sname:
        rows = [r for r in rows if sname in str((r.get("supplier") or {}).get("name") or "")
                or sname in str(r.get("supplier_linkman") or "")]
    if body.get("audit_status") not in (None, "", "null"):
        rows = [r for r in rows if str(r.get("audit_status")) == str(body.get("audit_status"))]
    return rows


def _bill_list(body, session):
    rows = [_decorate_bill(r) for r in _filtered_bills(body)]
    return core.paged_ok(_manual_page(rows, body, len(rows)))


def _bill_add(body, session):
    store = core.Store("purchase_bills")
    items, err = _build_items(body.get("item_list"))
    if err:
        return core.fail(err)
    money, num = _bill_totals(items)
    if Decimal(money) > 999999999:
        return core.fail("金额最大不能超过999999999")
    lman, addr = _resolve_linkman_text(body)
    try:
        audit_status = int(body.get("audit_status") or 2)   # 默认待审；可传 1/5 等直接落状态
    except (TypeError, ValueError):
        return core.fail("审核状态无效")
    if audit_status not in AUDIT_STATUS_NAME:
        return core.fail("审核状态无效")
    row = {
        "enterprise_id": 16807,
        "code": str(body.get("code") or "").strip() or core.next_code("CG", store.all(), width=3),
        "supplier_id": body.get("supplier_id"),
        "supplier_linkman": lman,
        "supplier_address": addr,
        "purchase_user_id": body.get("purchase_user_id"),
        "create_user_id": (session or {}).get("id"),
        "delivery_date": body.get("delivery_date"),
        "produce_schedule_id": body.get("produce_schedule_id"),
        "enquiry_price_id": body.get("enquiry_price_id"),
        "remark": body.get("remark"),
        "attachments": body.get("attachments") or [],
        "audit_status": audit_status,
        "audit_config_json": body.get("audit_config_json")
        or json.dumps([{"audit_user_ids": str((session or {}).get("id")), "audit_type": 1}],
                      ensure_ascii=False),
        "current_audit_level": 0,
        "allow_withdrawal": 2,
        "money": money,
        "purchase_num": num,
        "already_stock_in_num": "0.00000000",
        "stock_in_bill_num": 0, "stock_in_bill_codes": "", "stock_in_bill_money": "0",
        "stock_out_bill_num": 0, "stock_out_bill_codes": "", "stock_out_bill_money": "0",
        "already_payment_money": 0, "already_invoice_money": 0, "debt_money": "0.00000000",
        "delivery_num": "0", "net_delivery_num": "0",
        "item_list": items,
    }
    row = store.add(row, code_field="code")
    return core.ok(_decorate_bill(row))


def _bill_edit(body, session):
    store = core.Store("purchase_bills")
    row = store.get(body.get("id"))
    if not row:
        return core.fail("采购单不存在")
    if row.get("audit_status") in (2, 7):
        return core.fail("该单据待审核中，不可修改，请先撤回")
    if "item_list" in body:
        items, err = _build_items(body.get("item_list"))
        if err:
            return core.fail(err)
        money, num = _bill_totals(items)
        if Decimal(money) > 999999999:
            return core.fail("金额最大不能超过999999999")
    else:
        items, money, num = row.get("item_list") or [], row.get("money"), row.get("purchase_num")
    lman, addr = _resolve_linkman_text(body)
    patch = {"item_list": items, "money": money, "purchase_num": num}
    if "supplier_linkman" in body or body.get("supplier_linkman_id"):
        patch["supplier_linkman"] = lman
    if "supplier_address" in body or body.get("supplier_address_id"):
        patch["supplier_address"] = addr
    for f in ("code", "supplier_id", "purchase_user_id", "delivery_date", "produce_schedule_id",
              "enquiry_price_id", "remark", "attachments", "audit_status", "audit_config_json"):
        if f in body:
            patch[f] = body.get(f)
    if patch.get("audit_status") is not None:
        try:
            if int(patch["audit_status"]) not in AUDIT_STATUS_NAME:
                return core.fail("审核状态无效")
        except (TypeError, ValueError):
            return core.fail("审核状态无效")
    row = store.update(body.get("id"), patch)
    return core.ok(_decorate_bill(row))


def _bill_del(body, session):
    store = core.Store("purchase_bills")
    ids = [str(i) for i in (body.get("ids") or ([body.get("id")] if body.get("id") else []))]
    if not ids:
        return core.fail("请选择要删除的采购单")
    skipped, n = [], 0
    for rid in ids:
        row = store.get(rid)
        if not row:
            continue
        if row.get("audit_status") in (2, 7):   # 待审/审核中不可删（先撤回）
            skipped.append(row.get("code"))
            continue
        store.remove(rid)
        n += 1
    if skipped and not n:
        return core.fail("单据【%s】待审核中，不可删除，请先撤回" % "、".join(skipped))
    if not n and not skipped:
        return core.fail("采购单不存在")
    return core.ok({"deleted": n, "skipped": skipped})


def _bill_detail(body, session):
    row = core.Store("purchase_bills").get(body.get("id"))
    if not row:
        return core.fail("采购单不存在")
    return core.ok(_decorate_bill(row))


def _audit_target(body):
    """解析审核结果 → 3 通过 / 4 退回（result: pass|reject|通过|退回|3|4；或 reject/pass 布尔）。"""
    res = str(body.get("result") or "").strip().lower()
    if res in ("pass", "通过", "3"):
        return 3
    if res in ("reject", "退回", "4"):
        return 4
    if body.get("reject") in (1, "1", True):
        return 4
    if body.get("pass") in (0, "0", False):
        return 4
    return 3


def _bill_audit(body, session):
    """审核状态机：2 待审 | 7 审核中 → 3 通过 / 4 退回；支持 ids 批量。"""
    store = core.Store("purchase_bills")
    ids = body.get("ids") or ([body.get("id")] if body.get("id") else [])
    if not ids:
        return core.fail("请选择要审核的采购单")
    target = _audit_target(body)
    passed, skipped = [], []
    for rid in ids:
        row = store.get(rid)
        if not row:
            continue
        if row.get("audit_status") not in (2, 7):
            skipped.append("%s(%s)" % (row.get("code"), AUDIT_STATUS_NAME.get(row.get("audit_status"), "")))
            continue
        store.update(rid, {"audit_status": target, "current_audit_level": 0})
        passed.append(row.get("code"))
    if not passed:
        return core.fail("单据【%s】当前状态不可审核" % "、".join(skipped))
    return core.ok({"audit_status": target, "audit_status_name": AUDIT_STATUS_NAME[target],
                    "pass_count": len(passed), "skipped": skipped})


def _bill_withdrawal(body, session):
    """撤回：2 待审 | 7 审核中 → 6 撤回。"""
    store = core.Store("purchase_bills")
    row = store.get(body.get("id"))
    if not row:
        return core.fail("采购单不存在")
    if row.get("audit_status") not in (2, 7):
        return core.fail("单据【%s】当前状态不可撤回" % row.get("code"))
    row = store.update(body.get("id"), {"audit_status": 6, "current_audit_level": 0})
    return core.ok(_decorate_bill(row))


def _bill_status_count(body, session):
    """快捷筛计数（status_count 键名取自 C:60907 statistics）。"""
    rows = _filtered_bills(body)
    keys = {"no_need": 1, "wait": 2, "pass": 3, "reject": 4, "withdrawal": 6, "auditing": 7}
    return core.ok({
        "status_count": {k: len([r for r in rows if str(r.get("audit_status")) == str(v)])
                         for k, v in keys.items()},
        "overdue_count": {"is_overdue": len([r for r in rows if _is_overdue(r)]),
                          "not_overdue": len([r for r in rows if not _is_overdue(r)])},
        "have_debt_count": {"have_debt": len([r for r in rows if _dec(r.get("debt_money")) > 0]),
                            "not_debt": len([r for r in rows if _dec(r.get("debt_money")) <= 0])},
    })


def _bill_money_statistics(body, session):
    """统计条【采购总数】【采购金额】【入库金额】【退货金额】【欠款】（deep_m5 实测 shape）。"""
    rows = _filtered_bills(body)

    def total(field):
        return _d8(sum((_dec(r.get(field)) for r in rows), Decimal(0)))

    return core.ok({
        "purchase_total_num": total("purchase_num"),
        "purchase_total_money": total("money"),
        "stock_in_bill_money": total("stock_in_bill_money"),
        "stock_out_bill_money": total("stock_out_bill_money"),
        "already_payment_money": total("already_payment_money"),
        "debt_money": total("debt_money"),
    })


def _bill_item_money(body, session):
    """单行试算（/purchase_bill/purchase_bill_item_money；无效公式 → 明细金额公式计算无效）。"""
    goods = _goods_brief(body.get("goods_id"))
    money = _calc_item_money(body.get("num"), body.get("unit_price"), goods)
    if money is None:
        return core.fail("明细金额公式计算无效")
    return core.ok({"money": _d8(money)})


def _formula_fields(body, session):
    """公式可选变量（D:purchase/formula_fields.json 逐字）。"""
    return core.ok(dict(FORMULA_FIELDS))


# ================= 采购计划 purchase_plan（缺料自动生成）=================

# 加工单五态（produce 域同款）：1 未开始 / 2 生产中 / 3 已完成 / 4 已取消 / 5 已暂停
_BILL_STATUS_FINISHED = (3, 4)   # 已完成/已取消的加工单不再产生物料需求


def _bom_default(goods_id):
    """商品的默认 BOM（is_default=1 优先，否则第一条）；子件树取自 bom_trees 种子
    （B11 布局：boms=表头，bom_trees={goods_bom_id, goods_id, structure_tree}）。"""
    boms = [b for b in core.Store("boms").all()
            if str(b.get("goods_id")) == str(goods_id) and str(b.get("del", 2)) != "1"]
    if not boms:
        return None
    bom = boms[0]
    for b in boms:
        if str(b.get("is_default")) == "1":
            bom = b
            break
    bom = dict(bom)
    if not (bom.get("structure_tree") or bom.get("item_list")):
        for t in core.Store("bom_trees").all():
            if str(t.get("goods_bom_id")) == str(bom.get("id")):
                bom["structure_tree"] = t.get("structure_tree") or []
                break
    return bom


def _bill_goods_id(bill):
    """加工单行无 goods_id（按 goods_code/goods_name 记）→ 回查商品表。"""
    if bill.get("goods_id"):
        return bill.get("goods_id")
    goods = core.Store("goods").all()
    for g in goods:
        if bill.get("goods_code") and str(g.get("code")) == str(bill.get("goods_code")):
            return g.get("id")
    for g in goods:
        if bill.get("goods_name") and str(g.get("name")) == str(bill.get("goods_name")):
            return g.get("id")
    return None


def _demand_map():
    """物料需求（简化）：未完工加工单 × 默认 BOM 一层子件用量（加工单 num × 子件单耗）。"""
    demand = {}

    def add(gid, n):
        if gid is None or n <= 0:
            return
        demand[gid] = demand.get(gid, Decimal(0)) + n

    for bill in core.Store("produce_bills").all():
        if str(bill.get("del", 2)) == "1" or bill.get("status") in _BILL_STATUS_FINISHED:
            continue   # 已完成/已取消的加工单不再产生物料需求
        pairs = []
        gid = _bill_goods_id(bill)
        if gid:
            pairs.append((gid, _dec(bill.get("num"))))
        for it in bill.get("item_list") or []:   # 兼容带明细结构的加工单
            pairs.append((it.get("goods_id"), _dec(it.get("num"))))
        for gid, bnum in pairs:
            bom = _bom_default(gid)
            if not bom:
                continue
            for it in (bom.get("structure_tree") or bom.get("item_list") or []):
                if it.get("parent_id"):          # 只做一层展开（简化）
                    continue
                add(it.get("goods_id"), bnum * _dec(it.get("num")))
    return demand


def _stock_map():
    """库存（简化）：goods_stocks 按商品汇总（可用库存 stock_available_num 优先，
    回退 stock_num/num）；无库存行时回退商品维 stock_total_available_num/stock_total_num。"""
    stock = {}
    for r in core.Store("goods_stocks").all():
        gid = r.get("goods_id")
        if gid is None:
            continue
        v = r.get("stock_available_num")
        if v in (None, ""):
            v = r.get("stock_num") if r.get("stock_num") not in (None, "") else r.get("num")
        stock[gid] = stock.get(gid, Decimal(0)) + _dec(v)
    for g in core.Store("goods").all():
        gid = g.get("id")
        if gid not in stock:
            v = g.get("stock_total_available_num")
            if v in (None, ""):
                v = g.get("stock_total_num")
            if v not in (None, ""):
                stock[gid] = stock.get(gid, Decimal(0)) + _dec(v)
    return stock


def _shortage_rows():
    """缺料行：num = 需求 − 库存（下限 0；与备料建议公式同式）。"""
    demand, stock = _demand_map(), _stock_map()
    goods_map = {g.get("id"): g for g in core.Store("goods").all()}
    rows = []
    for gid, need in demand.items():
        g = goods_map.get(gid)
        if g is None:
            continue
        gap = need - stock.get(gid, Decimal(0))
        if gap > 0:
            rows.append({"goods_id": gid, "need": need,
                         "stock": stock.get(gid, Decimal(0)), "num": gap, "goods": g})
    rows.sort(key=lambda r: -r["num"])
    return rows


def _plan_list(body, session):
    """采购计划列表：缺料自动生成记录（= 物料需求页"采购申请"链路：缺料行 num−库存 进同一张计划）。"""
    shortages = _shortage_rows()
    rows = []
    if shortages:
        items = []
        for idx, s in enumerate(shortages):
            price = _dec((s["goods"] or {}).get("cost_money"))   # 补货逻辑：unit_price=成本单价
            items.append({
                "id": 990100 + idx, "purchase_plan_id": 990101, "goods_id": s["goods_id"],
                "num": _d8(s["num"]), "unit_price": _d8(price), "money": _d8(price * s["num"]),
                "remark": "缺料自动生成", "supplier_id": None, "supplier_linkman": None,
                "supplier_address": None, "already_purchase_num": "0.00000000",
                "already_stock_in_num": "0.00000000", "goods": _goods_brief(s["goods_id"]),
                "del": 2,
            })
        money = _d8(sum((_dec(i["money"]) for i in items), Decimal(0)))
        num = _d8(sum((_dec(i["num"]) for i in items), Decimal(0)))
        rows.append({
            "id": 990101,
            "code": "CGJH" + core.now("%Y%m%d") + "001",
            "name": "缺料自动生成（%s）" % core.now("%Y-%m"),
            "money": money, "purchase_num": num, "plan_purchase_total_num": num,
            "already_purchase_num": "0.00000000", "already_stock_in_num": "0.00000000",
            "finish_purchase_percent": "0", "finish_stock_percent": "0",
            "audit_status": 1, "audit_status_name": AUDIT_STATUS_NAME[1],
            "audit_status_tag_class": AUDIT_STATUS_TAG[1],
            "audit_config_json": None, "current_audit_level": 0, "allow_withdrawal": 2,
            "supplier": None, "supplier_linkman": None, "supplier_address": None,
            "delivery_date": None, "remark": "物料需求缺料自动生成（可用库存不足部分）",
            "create_user": _user_brief((session or {}).get("id")),
            "created_at": core.now("%Y-%m-01 09:00:00"),
            "item_list": items, "fieldValueList": [],
        })
    return core.paged_ok(_manual_page(rows, body, len(rows)))


# ================= 智能补货 goods_stock_out_analysis =================

def _month_prepared(body, session):
    """月度备料建议（简化）：需求 − 库存（下限 0），返回 {goods_id, num}。
    真实响应键 need_prepared_goods_count / need_prepared_total_num（D:purchase/month_prepared.json）。"""
    shortages = _shortage_rows()
    total = sum((s["num"] for s in shortages), Decimal(0))
    return core.ok({
        "need_prepared_goods_count": len(shortages),
        "need_prepared_total_num": _d8(total),
        "list": [{"goods_id": s["goods_id"], "num": _d8(s["num"]),
                  "goods_name": (s["goods"] or {}).get("name"),
                  "predict_stock_out_num": _d8(s["need"]),
                  "initial_stock_num": _d8(s["stock"])} for s in shortages],
    })


ROUTES = {
    # ---- 供应商 ----
    ("supplier", "list"): _supplier_list,
    ("supplier", "list_by_filter_view"): _supplier_list,
    ("supplier", "add"): _supplier_add,
    ("supplier", "edit"): _supplier_edit,
    ("supplier", "del"): _supplier_del,
    ("supplier", "batch_del"): _supplier_del,
    ("supplier", "detail"): _supplier_detail,
    ("supplier", "status_count"): _supplier_status_count,
    # ---- 供应商子表 / 下拉 ----
    ("supplier_linkman", "list"): _linkman_list,
    ("supplier_address", "list"): _address_list,
    ("dropdown", "supplier"): _dropdown_supplier,
    ("dropdown", "supplier_linkman"): _linkman_list,
    ("dropdown", "supplier_address"): _address_list,
    ("autocomplete", "supplier"): _autocomplete_supplier,
    # ---- 采购单 ----
    ("purchase_bill", "list"): _bill_list,
    ("purchase_bill", "list_by_filter_view"): _bill_list,
    ("purchase_bill", "add"): _bill_add,
    ("purchase_bill", "edit"): _bill_edit,
    ("purchase_bill", "del"): _bill_del,
    ("purchase_bill", "detail"): _bill_detail,
    ("purchase_bill", "audit"): _bill_audit,
    ("purchase_bill", "withdrawal"): _bill_withdrawal,
    ("purchase_bill", "status_count"): _bill_status_count,
    ("purchase_bill", "money_statistics_by_filter_view"): _bill_money_statistics,
    ("purchase_bill", "purchase_bill_item_money"): _bill_item_money,
    ("purchase_bill", "formula_support_field_select"): _formula_fields,
    # ---- 采购计划（缺料自动生成）----
    ("purchase_plan", "list"): _plan_list,
    ("purchase_plan", "list_by_filter_view"): _plan_list,
    # ---- 智能补货 ----
    ("goods_stock_out_analysis", "get_month_prepared_suggestion"): _month_prepared,
}
