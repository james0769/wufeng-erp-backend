# -*- coding: utf-8 -*-
"""B12 质检中心域：全检 + 抽检 + 抽检方案。
依据：deep\\19-质检体系.md（§1 三页面/§2 抽检链路/§3 检验方案/§8 接口清单与枚举）、
      deep\\01-加工单全流程链路.md §3（quality_type 三态、全检质检 payload、返工自动化）。
字段对齐 api_dump/deep_m11 实测（random_list/random_detail/scheme_list/quality_users/inspect_fields）。

关键定论（deep\\19 §8.2，复刻易错点：两套状态编码【相反】）：
  全检·报工记录 quality_status：1 待质检 / 2 已质检 / 3 不需质检（QUALITY_STATUS）；
  抽检单 produce_inspection_random.status：1 已质检（tag"5"绿）/ 2 待质检（tag"3"橙）；
  抽检单 type：1 手动（type_name"手动"实测）/ 2 抽检方案自动生成（数量/工序锁定且不可删）。

范围说明：report_work_analysis 归 stats.py（已实现）；来料质检 incoming_quality_control 族、
AI质检 ai_quality_program 族、打印导出族（print_preview/batch_template_print/export）不在本卡。
"""
import time
from decimal import Decimal, ROUND_DOWN

import core

# ---------------- 枚举（deep\19 §8.2 app 枚举逐字） ----------------
QUALITY_STATUS = {1: "待质检", 2: "已质检", 3: "不需质检"}            # 全检（报工记录质检状态）
QUALITY_STATUS_KEY = {1: "wait_quality", 2: "finish_quality", 3: "not_quality"}
QUALITY_CHECK_TYPE = {1: "全检", 2: "抽检", 3: "不需质检"}            # 工序检验方案（quality_type）
# 抽检单状态：与全检相反！（deep\19 §1.2 实测：status2→待质检 tag3、status1→已质检 tag5）
RANDOM_STATUS = {1: "已质检", 2: "待质检"}
RANDOM_STATUS_TAG = {1: "5", 2: "3"}
RANDOM_TYPE_NAME = {1: "手动", 2: "自动"}  # type=2 的 type_name 无实测样例，"自动"为推断
# 后端 scheme_type_name 实测文案：type=1 少"量"字（deep\19 §3，与前端枚举存在差异，照抄后端）
SCHEME_TYPE_NAME = {1: "按报工数抽检", 2: "按生产时长抽检", 3: "定时抽检"}
BOOL_NAME = {1: "是", 2: "否"}
MAX_NUM = 99999999
# 全检页排序枚举（deep\19 §1.1 QUALITY_RECORD_SORT_ENUM）/ 抽检页（SPOT_CHECK_SORT_ENUM）
_RWQ_SORT = {"quality_status", "last_quality_time", "report_time", "priority_configure"}
_RND_SORT = {"inspect_random_code": "code", "inspect_time": "inspect_time", "created_at": "created_at"}

# ---------------- 存取（单例，避免多实例 flush 互相覆盖） ----------------
_RWQ = _RECORDS = _RNDS = _SCHEMES = _CRAFTS = _PC = _BILLS = None

def _rwq():
    global _RWQ
    if _RWQ is None:
        _RWQ = core.Store("report_work_quality")      # 本卡新建种子（全检视图种子行）
    return _RWQ

def _records():
    global _RECORDS
    if _RECORDS is None:
        _RECORDS = core.Store("report_work_records")  # produce.report_work 的运行时上游（只在其上回写判定结果）
    return _RECORDS

def _randoms():
    global _RNDS
    if _RNDS is None:
        _RNDS = core.Store("produce_inspection_randoms")
    return _RNDS

def _schemes():
    global _SCHEMES
    if _SCHEMES is None:
        _SCHEMES = core.Store("inspection_schemes")
    return _SCHEMES

def _crafts():
    global _CRAFTS
    if _CRAFTS is None:
        _CRAFTS = core.Store("produce_bill_crafts")
    return _CRAFTS

def _pc():
    """pub_craft 主档（crafts 库，quality_type/可质检人/不良品项在此）。"""
    global _PC
    if _PC is None:
        _PC = core.Store("crafts")
    return _PC

def _bills():
    global _BILLS
    if _BILLS is None:
        _BILLS = core.Store("produce_bills")
    return _BILLS

# ---------------- 小工具 ----------------
def _num(x, w=8):
    try:
        return ("%." + str(w) + "f") % float(x or 0)
    except Exception:
        return ("%.8f" % 0) if w == 8 else ("%.2f" % 0)

def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0

def _s(body, k):
    return str(body.get(k) or "").strip()

def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name") or u.get("name") or str(uid),
                    "mobile": u.get("mobile"), "type": u.get("type")}
    return {"id": uid, "real_name": "用户%s" % uid}

def _waste(id_):
    for w in core.Store("wastes").all():
        if str(w.get("id")) == str(id_):
            return {"id": w.get("id"), "code": w.get("code"), "name": w.get("name")}
    return None

def _waste_names(pairs):
    return ",".join(((p.get("waste_item") or {}).get("name") or "") for p in pairs if p.get("waste_item"))

def _pc_of(craft):
    """任务行 → pub_craft 主档（种子行为 B11 导出扁平行，quality_type 等在主档上）。"""
    if not craft:
        return None
    return _pc().get(craft.get("pub_craft_id"))

def _bill_of(craft):
    if not craft:
        return None
    return _bills().get(craft.get("produce_bill_id"))

def _page(rows, body, default_size=30):
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or default_size)
    except Exception:
        pageSize = default_size
    return core.paged_ok({"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize], "total": len(rows),
                          "pageNo": pageNo, "pageSize": pageSize})

def _sorted_rows(rows, body, allowed, alias=None):
    f = _s(body, "sortField")
    if f and (f in allowed or (alias and f in alias)):
        key = (alias or {}).get(f, f)
        desc = str(body.get("sortOrder") or "") == "descend"
        return sorted(rows, key=lambda r: (str(r.get(key) or "")), reverse=desc)
    return rows

def _next_random_code(rows):
    """抽检单号：JY + yyyyMMdd + 4 位流水（deep\19 §1.2 实测 3 样本归纳，推断规则）。"""
    stamp = time.strftime("%Y%m%d")
    prefix = "JY" + stamp
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(prefix):
            try:
                mx = max(mx, int(c[len(prefix):]))
            except ValueError:
                pass
    return prefix + str(mx + 1).zfill(4)

def _next_bill_code(rows):
    """返工工单编号：YYYYMMDDNNN-n（deep\01 §1.6 同款）。"""
    stamp = time.strftime("%Y%m%d")
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(stamp) and len(c) >= len(stamp) + 3:
            try:
                mx = max(mx, int(c[len(stamp):len(stamp) + 3]))
            except ValueError:
                pass
    return stamp + str(mx + 1).zfill(3) + "-1"

# ================================================================
# 全检族：report_work_quality（报工记录质检视图）+ report_work_record 质检动作
# ================================================================
def _rwq_units():
    """全检视图 = 本卡种子(report_work_quality) ∪ 运行时报工记录(report_work_records)。
    返回 [(store, row)]，写回时按所属 store flush。"""
    return [(_rwq(), r) for r in _rwq().all()] + [(_records(), r) for r in _records().all()]

def _find_rwq(rid):
    for st, r in _rwq_units():
        if str(r.get("id")) == str(rid):
            return st, r
    return None, None

def _rwq_craft_info(rec):
    """行上的任务/工序冗余（§1.1 列组：produce_bill_craft + produce_bill）。"""
    craft = _crafts().get(rec.get("produce_bill_craft_id"))
    pc = _pc_of(craft)
    bill = _bills().get(rec.get("produce_bill_id"))
    qt = (pc or {}).get("quality_type") or craft.get("quality_type") or 3
    cinfo = None
    if craft:
        cinfo = {
            "id": craft.get("id"), "produce_bill_id": craft.get("produce_bill_id"),
            "pub_craft_id": craft.get("pub_craft_id"),
            "craft_name": craft.get("craft_name") or (pc or {}).get("name"),
            "create_type": craft.get("create_type") or 1, "create_type_name": "生产任务",
            "num": _num(craft.get("num")), "status": craft.get("status"),
            "quality_type": qt, "quality_type_name": QUALITY_CHECK_TYPE.get(qt, ""),
            "waste_item_ids": (pc or {}).get("waste_item_ids") or [],
            "quality_able_user_ids": (pc or {}).get("quality_able_user_ids") or [],
            "quality_able_user_names": (pc or {}).get("quality_able_user_names") or "",
            "workshop_id": craft.get("workshop_id") or (pc or {}).get("workshop_id"),
            "workshop_path_names": craft.get("workshop_path_names") or (pc or {}).get("workshop_path_names") or "",
            "produce_line_names": craft.get("produce_line_names") or (pc or {}).get("produce_line_names") or "",
            "unit": craft.get("unit") or (pc or {}).get("unit"),
            "pub_craft": {"id": (pc or {}).get("id"), "name": (pc or {}).get("name"),
                          "code": (pc or {}).get("code")} if pc else None,
        }
    binfo = None
    if bill:
        g = bill.get("goods")
        binfo = {
            "id": bill.get("id"), "code": bill.get("code"), "num": _num(bill.get("num")),
            "status": bill.get("status"), "status_name": bill.get("status_name"),
            "delivery_date": bill.get("delivery_date"), "work_type": bill.get("work_type") or 1,
            "goods": g if isinstance(g, dict) else {
                "id": bill.get("goods_id"), "code": bill.get("goods_code"), "name": bill.get("goods_name"),
                "standard": bill.get("goods_standard"), "category_path_names": bill.get("category_path_names")},
        }
    return craft, pc, cinfo, binfo

def _decorate_rwq(rec):
    d = dict(rec)
    craft, pc, cinfo, binfo = _rwq_craft_info(rec)
    qt = (cinfo or {}).get("quality_type") or 3
    qs = d.get("quality_status") or (3 if qt == 3 else 1)
    d.setdefault("type", 1)
    d.setdefault("type_name", "生产报工")
    d.setdefault("valid_num", "0.00000000")
    d.setdefault("waste_num", "0.00000000")
    d.setdefault("report_num", _num(_f(d.get("valid_num")) + _f(d.get("waste_num"))))
    d.setdefault("report_waste_list", [])
    d.setdefault("waste_item_sum", len(d.get("report_waste_list") or []))
    d.setdefault("working_minutes", 0)
    d.setdefault("rework_num", "0.00000000")
    d.setdefault("scrap_num", "0.00000000")
    d.setdefault("quality_remark", None)
    d.setdefault("start_time", None)
    d.setdefault("end_time", None)
    d.setdefault("settle_status", 1)
    d.setdefault("settle_status_name", "未结算")
    d["report_time"] = d.get("report_time") or d.get("created_at")
    d["quality_status"] = qs
    d["quality_status_name"] = QUALITY_STATUS.get(qs, "")
    d["quality_status_key"] = QUALITY_STATUS_KEY.get(qs, "")
    d["need_rework"] = d.get("need_rework") or 2
    d["need_rework_name"] = BOOL_NAME.get(d["need_rework"], "")
    d["last_quality_user"] = d.get("last_quality_user") or (
        _user(d.get("last_quality_user_id")) if d.get("last_quality_user_id") else None)
    if not d.get("report_user") and d.get("report_user_id"):
        d["report_user"] = _user(d.get("report_user_id"))
    if not d.get("create_user") and d.get("create_user_id"):
        d["create_user"] = _user(d.get("create_user_id"))
    if cinfo:
        d["produce_bill_craft"] = cinfo
        d["craft_name"] = cinfo["craft_name"]
        d["quality_type"] = qt
        d["quality_type_name"] = QUALITY_CHECK_TYPE.get(qt, "")
    if binfo:
        d["produce_bill"] = binfo
    # 行加载后置标志（§1.1）：全检工序行走质检弹窗，其余走编辑报工弹窗
    d["__is_edit_accessible"] = qt != 1
    return d

def _filter_rwq(units, body):
    out = []
    st_f = body.get("quality_status")
    rw_f = body.get("need_rework")
    sts = None if st_f in (None, "", "all") else (st_f if isinstance(st_f, list) else [st_f])
    rws = None if rw_f in (None, "", "all") else [rw_f]
    ruids = [str(x) for x in (body.get("report_user_ids") or [])]
    quids = [str(x) for x in (body.get("last_quality_user_ids") or [])]
    for st, r in units:
        craft, pc, cinfo, binfo = _rwq_craft_info(r)
        qt = (cinfo or {}).get("quality_type") or 3
        qs = r.get("quality_status") or (3 if qt == 3 else 1)
        if sts is not None and str(qs) not in [str(x) for x in sts]:
            continue
        if rws is not None and str(r.get("need_rework") or 2) not in [str(x) for x in rws]:
            continue
        if _s(body, "produce_bill_code") and _s(body, "produce_bill_code") not in str((binfo or {}).get("code") or ""):
            continue
        if _s(body, "craft_name") and _s(body, "craft_name") not in str((cinfo or {}).get("craft_name") or ""):
            continue
        if _s(body, "craft_code") and _s(body, "craft_code") not in str(((cinfo or {}).get("pub_craft") or {}).get("code") or ""):
            continue
        gk = _s(body, "goods_keyword")
        g = (binfo or {}).get("goods") or {}
        if gk and gk not in "%s %s %s" % (g.get("name") or "", g.get("code") or "", g.get("standard") or ""):
            continue
        if _s(body, "traceability_code_code") and _s(body, "traceability_code_code") not in str(r.get("traceability_codes") or ""):
            continue
        if _s(body, "quality_remark") and _s(body, "quality_remark") not in str(r.get("quality_remark") or ""):
            continue
        if ruids and str(r.get("report_user_id")) not in ruids:
            continue
        if quids and str(r.get("last_quality_user_id")) not in quids:
            continue
        rt = str(r.get("report_time") or r.get("created_at") or "")
        if _s(body, "report_time_start") and rt < _s(body, "report_time_start"):
            continue
        if _s(body, "report_time_end") and rt > _s(body, "report_time_end") + " 23:59:59":
            continue
        out.append((st, r))
    return out

def _rwq_list(body, session):
    units = _filter_rwq(_rwq_units(), body)
    rows = [_decorate_rwq(r) for _, r in units]
    rows = _sorted_rows(rows, body, _RWQ_SORT)
    return _page(rows, body)

def _rwq_detail(body, session):
    st, r = _find_rwq(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok(_decorate_rwq(r))

def _rwq_status_count(body, session):
    units = _filter_rwq(_rwq_units(), body)
    sc = {1: 0, 2: 0, 3: 0}
    rc = {"already_rework": 0, "not_rework": 0}
    for _, r in units:
        craft, pc, cinfo, _ = _rwq_craft_info(r)
        qt = (cinfo or {}).get("quality_type") or 3
        qs = r.get("quality_status") or (3 if qt == 3 else 1)
        if qs in sc:
            sc[qs] += 1
        rc["already_rework" if str(r.get("need_rework") or 2) == "1" else "not_rework"] += 1
    return core.ok({
        "quality_status_count": {QUALITY_STATUS_KEY[k]: v for k, v in sc.items()},
        "rework_count": rc,
    })

def _rebuild_report_waste(rec, arr):
    lst = []
    for it in arr or []:
        w = _waste(it.get("waste_item_id"))
        if w:
            lst.append({"waste_item": w, "num": _num(it.get("num"))})
    rec["report_waste_list"] = lst
    rec["waste_item_sum"] = len(lst)

def _wage(price_mode, valid_num, unit_money, minutes):
    dn = 0  # 工资=单价×良品数（计件）/时长秒÷3600×单价（计时），0 位截断（deep\18 §4.3）
    if str(price_mode) == "2":
        base = (Decimal(str(_f(minutes))) * 60 / Decimal(3600)) * Decimal(str(_f(unit_money)))
    else:
        base = Decimal(str(_f(valid_num))) * Decimal(str(_f(unit_money)))
    val = base.quantize(Decimal(1), rounding=ROUND_DOWN)
    return str(int(val))

def _qualityable_user_ids(rec):
    """记录的质检人员：任务质检派工优先，空则回退工序可质检人（deep\19 §1.1/§4.3）。"""
    craft = _crafts().get(rec.get("produce_bill_craft_id"))
    ids = (craft or {}).get("quality_dispatch_user_ids") or []
    if not ids:
        ids = (_pc_of(craft) or {}).get("quality_able_user_ids") or []
    return [str(x) for x in ids]

def _create_rework(rec, rework_craft_id, session):
    """返工自动化（deep\01 §3.2，确定）：返工工序=当前工序→同加工单返工任务；
    返工工序≠当前工序→返工工单（produce_bill, work_type=2）+ 其下返工任务。返回 (类型, 新行)。"""
    craft = _crafts().get(rec.get("produce_bill_craft_id"))
    pc = _pc_of(craft) or {}
    cur_pc = (craft or {}).get("pub_craft_id")
    target = rework_craft_id or cur_pc
    num = rec.get("rework_num")
    base_craft = {
        "enterprise_id": session.get("enterprise_id") or rec.get("enterprise_id"),
        "pub_craft_id": target,
        "craft_name": (pc or {}).get("name") or (craft or {}).get("craft_name"),
        "num": num, "produce_bill_craft_num": num,
        "status": 1, "price_mode": (pc or {}).get("price_mode") or 1,
        "unit_money": _num((pc or {}).get("unit_money"), 2),
        "quality_type": (pc or {}).get("quality_type") or 3,
        "waste_item_ids": (pc or {}).get("waste_item_ids") or [],
        "is_rework": 1, "create_type": 1,
        "valid_num": "0.00000000", "total_valid_num": "0.00000000",
        "waste_num": "0.00000000", "rework_num": "0.00000000", "scrap_num": "0.00000000",
        "report_num": "0.00000000", "working_minutes": "0.00",
        "dispatch_user_ids": None, "dispatch_list": [],
        "is_open_clock": 2, "remark": "质检返工自动创建",
        "create_user_id": session.get("id"),
    }
    if str(target) == str(cur_pc):
        row = dict(base_craft, produce_bill_id=craft.get("produce_bill_id"))
        return "rework_task", _crafts().add(row)
    # 返工工单（work_type=2）
    src = _bills().get(craft.get("produce_bill_id")) or {}
    g = src.get("goods") if isinstance(src.get("goods"), dict) else None
    bill = _bills().add({
        "enterprise_id": base_craft["enterprise_id"], "code": _next_bill_code(_bills().all()),
        "goods_id": src.get("goods_id") or (g or {}).get("id"),
        "goods": g or {"id": src.get("goods_id"), "code": src.get("goods_code"), "name": src.get("goods_name"),
                       "standard": src.get("goods_standard")},
        "goods_name": src.get("goods_name"), "goods_code": src.get("goods_code"),
        "goods_standard": src.get("goods_standard"),
        "num": num, "status": 1, "work_type": 2, "produce_type": src.get("produce_type") or 1,
        "create_mode": 1, "requisition_status": 2,
        "actual_num": "0.00000000", "stock_in_num": "0.00000000",
        "remark": "质检返工自动创建（源报工记录 %s）" % rec.get("id"),
        "create_user_id": session.get("id"),
    })
    row = dict(base_craft, produce_bill_id=bill["id"])
    task = _crafts().add(row)
    return "rework_bill", task

def _rwq_quality(body, session):
    """全检质检判定（deep\01 §3.2 payload）：id/valid_num/waste_num/report_waste_array/
    rework_num/scrap_num/quality_status/quality_remark/rework_craft_id/is_finish/start_time/end_time。"""
    st, rec = _find_rwq(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    craft, pc, cinfo, _ = _rwq_craft_info(rec)
    qt = (cinfo or {}).get("quality_type") or 3
    if qt == 3 and str(rec.get("quality_status") or 3) == "3":
        return core.fail("不需质检的报工记录不能质检")
    valid = _f(body.get("valid_num") if body.get("valid_num") is not None else rec.get("valid_num"))
    waste = _f(body.get("waste_num") if body.get("waste_num") is not None else rec.get("waste_num"))
    if valid < 0 or waste < 0 or valid > MAX_NUM or waste > MAX_NUM:
        return core.fail("数量填写错误")
    rework = _f(body.get("rework_num") or 0)
    scrap = _f(body.get("scrap_num") or 0)
    if rework < 0 or scrap < 0 or rework > MAX_NUM or scrap > MAX_NUM:
        return core.fail("数量填写错误")
    if rework + scrap > waste + 1e-9:
        return core.fail("返工数与报废数不能大于不良品数")
    remark = body.get("quality_remark")
    if remark is not None and len(str(remark)) > 255:
        return core.fail("长度超出限制")
    stv, etv = body.get("start_time"), body.get("end_time")
    if stv and etv and str(stv) > str(etv):
        return core.fail("开始时间不能大于结束时间")
    # ---- 落判定 ----
    rec["valid_num"] = _num(valid)
    rec["waste_num"] = _num(waste)
    rec["report_num"] = _num(valid + waste)
    if body.get("report_waste_array") is not None:
        _rebuild_report_waste(rec, body.get("report_waste_array"))
    rec["rework_num"] = _num(rework)
    rec["scrap_num"] = _num(scrap)
    rec["quality_remark"] = remark
    if stv:
        rec["start_time"] = stv
    if etv:
        rec["end_time"] = etv
    um = (pc or {}).get("unit_money") or rec.get("craft_unit_money")
    rec["price_mode"] = rec.get("price_mode") or (pc or {}).get("price_mode") or 1
    rec["craft_unit_money"] = _num(um)
    rec["valid_money"] = _wage(rec["price_mode"], valid, um, rec.get("working_minutes") or 0)
    try:
        qs = int(body.get("quality_status"))
        if qs not in (1, 2, 3):
            qs = 2
    except Exception:
        qs = 2
    rec["quality_status"] = qs
    rec["last_quality_user_id"] = session.get("id")
    rec["last_quality_user"] = _user(session.get("id"))
    rec["last_quality_time"] = core.now()
    created = None
    if rework > 0 and qt == 1:  # 允许报废返工仅全检工序可开（deep\19 §4.4）
        rec["need_rework"] = 1
        created = _create_rework(rec, body.get("rework_craft_id"), session)
    elif rec.get("need_rework") != 1:
        rec["need_rework"] = 2
    st.flush()
    d = _decorate_rwq(rec)
    if created:
        # toast 模板（deep\01 §3.2 逐字）：提交成功，系统已自动创建${返工工单|返工任务}。
        d["auto_create_rework"] = created[0]
        d["auto_create_rework_id"] = created[1].get("id")
    return core.ok(d)

def _rwq_batch_quality(body, session):
    """批量质检（deep\19 §1.1 通知四段逐字）；只作用于"质检人员=当前用户"的记录。"""
    ids = body.get("id_array") or []
    if not ids:
        return core.fail("请选择报工记录")
    total = success = already = no_need = not_able = 0
    for i in ids:
        st, rec = _find_rwq(i)
        if not rec:
            continue
        total += 1
        craft, pc, cinfo, _ = _rwq_craft_info(rec)
        qt = (cinfo or {}).get("quality_type") or 3
        qs = rec.get("quality_status") or (3 if qt == 3 else 1)
        if qs == 3:
            no_need += 1
            continue
        if qs == 2:
            already += 1
            continue
        able = _qualityable_user_ids(rec)
        if able and str(session.get("id")) not in able:
            not_able += 1
            continue
        rec["quality_status"] = 2
        rec["last_quality_user_id"] = session.get("id")
        rec["last_quality_user"] = _user(session.get("id"))
        rec["last_quality_time"] = core.now()
        if rec.get("need_rework") != 1:
            rec["need_rework"] = 2
        st.flush()
        success += 1
    return core.ok({"total": total, "success_num": success,
                    "already_quality_finish_num": already, "no_need_quality_num": no_need,
                    "not_qualityable_user_num": not_able})

def _rwr_edit_quality_status(body, session):
    """report_work_record/edit_quality_status（§8.1 有接口、无样例；按状态编辑实现，推断）。"""
    st, rec = _find_rwq(body.get("id"))
    if not rec:
        return core.fail("数据不存在")
    try:
        qs = int(body.get("quality_status"))
        assert qs in (1, 2, 3)
    except Exception:
        return core.fail("参数错误")
    rec["quality_status"] = qs
    if qs == 2:
        rec["last_quality_user_id"] = session.get("id")
        rec["last_quality_user"] = _user(session.get("id"))
        rec["last_quality_time"] = core.now()
    else:
        rec["last_quality_user_id"] = None
        rec["last_quality_user"] = None
        rec["last_quality_time"] = None
    st.flush()
    return core.ok()

# ================================================================
# 抽检族：produce_inspection_random（状态与全检相反：1 已质检 / 2 待质检）
# ================================================================
def _decorate_random(r):
    d = dict(r)
    craft = _crafts().get(d.get("produce_bill_craft_id"))
    pc = _pc_of(craft)
    bill = _bills().get(d.get("produce_bill_id"))
    st = d.get("status") or 2
    d.setdefault("type", 1)
    d.setdefault("num", "0.00000000")
    d.setdefault("valid_num", "0.00000000")
    d.setdefault("waste_num", "0.00000000")
    d.setdefault("fieldValueList", [])
    d.setdefault("inspection_random_waste_list", [])
    d["type_name"] = RANDOM_TYPE_NAME.get(d.get("type") or 1, "")
    d["status_name"] = RANDOM_STATUS.get(st, "")
    d["status_tag_class"] = RANDOM_STATUS_TAG.get(st, "")
    if not d.get("waste_item_names"):
        d["waste_item_names"] = _waste_names(d.get("inspection_random_waste_list") or [])
    if d.get("inspect_user_id") and not d.get("inspect_user"):
        d["inspect_user"] = _user(d.get("inspect_user_id"))
    else:
        d.setdefault("inspect_user", None)
    if d.get("produce_user_id") and not d.get("produce_user"):
        d["produce_user"] = _user(d.get("produce_user_id"))
    else:
        d.setdefault("produce_user", None)
    if not d.get("create_user") and d.get("create_user_id"):
        d["create_user"] = _user(d.get("create_user_id"))
    if bill:
        g = bill.get("goods")
        d["produce_bill"] = {
            "id": bill.get("id"), "code": bill.get("code"),
            "goods_id": bill.get("goods_id"),
            "goods": g if isinstance(g, dict) else {
                "id": bill.get("goods_id"), "code": bill.get("goods_code"), "name": bill.get("goods_name"),
                "standard": bill.get("goods_standard"), "category_path_names": bill.get("category_path_names")},
        }
    if craft:
        scheme = None
        if d.get("inspection_scheme_id"):
            scheme = _schemes().get(d.get("inspection_scheme_id"))
        elif craft.get("inspection_scheme_id"):
            scheme = _schemes().get(craft.get("inspection_scheme_id"))
        d["produce_bill_craft"] = {
            "id": craft.get("id"), "produce_bill_id": craft.get("produce_bill_id"),
            "pub_craft_id": craft.get("pub_craft_id"),
            "status": craft.get("status"),
            "num": _num(craft.get("num")), "produce_bill_craft_num": _num(craft.get("num")),
            "quality_type": (pc or {}).get("quality_type") or craft.get("quality_type") or 3,
            "quality_type_name": QUALITY_CHECK_TYPE.get((pc or {}).get("quality_type") or 3, ""),
            "workshop_path_names": craft.get("workshop_path_names") or (pc or {}).get("workshop_path_names") or "",
            "produce_line_names": craft.get("produce_line_names") or (pc or {}).get("produce_line_names") or "",
            "waste_item_ids": (pc or {}).get("waste_item_ids") or [],
            "waste_item_list": [w for w in (_waste(x) for x in ((pc or {}).get("waste_item_ids") or [])) if w],
            "unit": craft.get("unit") or (pc or {}).get("unit"),
            "pub_craft": {"id": (pc or {}).get("id"), "name": (pc or {}).get("name"),
                          "code": (pc or {}).get("code"),
                          "quality_able_user_ids": (pc or {}).get("quality_able_user_ids") or [],
                          "quality_able_user_names": (pc or {}).get("quality_able_user_names") or ""},
            "inspection_scheme": ({"id": scheme.get("id"), "name": scheme.get("name"),
                                   "is_self_build": scheme.get("is_self_build"),
                                   "scheme_type_name": scheme.get("scheme_type_name")}
                                  if scheme else None),
        }
    return d

def _filter_randoms(rows, body):
    out = []
    for r in rows:
        d = _decorate_random(r)
        if _s(body, "code") and _s(body, "code") not in str(d.get("code") or ""):
            continue
        if _s(body, "produce_bill_code") and _s(body, "produce_bill_code") not in str((d.get("produce_bill") or {}).get("code") or ""):
            continue
        pc = ((d.get("produce_bill_craft") or {}).get("pub_craft") or {})
        if _s(body, "pub_craft_name") and _s(body, "pub_craft_name") not in str(pc.get("name") or ""):
            continue
        if _s(body, "pub_craft_code") and _s(body, "pub_craft_code") not in str(pc.get("code") or ""):
            continue
        g = (d.get("produce_bill") or {}).get("goods") or {}
        if _s(body, "goods_name") and _s(body, "goods_name") not in str(g.get("name") or ""):
            continue
        if _s(body, "goods_standard") and _s(body, "goods_standard") not in str(g.get("standard") or ""):
            continue
        if _s(body, "goods_category_id") and _s(body, "goods_category_id") not in str(g.get("category_path_names") or ""):
            continue
        if _s(body, "workshop_id") and _s(body, "workshop_id") not in [str(x) for x in ((d.get("produce_bill_craft") or {}).get("workshop_path_ids") or [])] + [str((d.get("produce_bill_craft") or {}).get("workshop_id") or "")]:
            continue
        st_f = body.get("status")
        if st_f not in (None, "", "all") and str(d.get("status")) != str(st_f):
            continue
        if _s(body, "inspect_user_id") and str(d.get("inspect_user_id")) != _s(body, "inspect_user_id"):
            continue
        puids = [str(x) for x in (body.get("produce_user_ids") or [])]
        if puids and str(d.get("produce_user_id")) not in puids:
            continue
        cuids = [str(x) for x in (body.get("create_user_ids") or [])]
        if cuids and str(d.get("create_user_id")) not in cuids:
            continue
        it = str(d.get("inspect_time") or "")
        if _s(body, "inspect_time_start") and it and it < _s(body, "inspect_time_start"):
            continue
        if _s(body, "inspect_time_end") and it and it > _s(body, "inspect_time_end") + " 23:59:59":
            continue
        ca = str(d.get("created_at") or "")
        if _s(body, "created_at_start") and ca < _s(body, "created_at_start"):
            continue
        if _s(body, "created_at_end") and ca > _s(body, "created_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out

def _random_list(body, session):
    rows = _filter_randoms(_randoms().all(), body)
    rows = _sorted_rows(rows, body, set(_RND_SORT.values()), _RND_SORT)
    return _page([_decorate_random(r) for r in rows], body)

def _random_status_count(body, session):
    rows = _filter_randoms(_randoms().all(), body)
    fin = len([r for r in rows if str(r.get("status")) == "1"])
    wait = len([r for r in rows if str(r.get("status")) == "2"])
    return core.ok({"inspect_status_count": {"total_count": len(rows),
                                             "finish_status_count": fin, "wait_status_count": wait}})

def _random_detail(body, session):
    r = _randoms().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok(_decorate_random(r))

def _craft_pickable(craft):
    """可抽检范围（deep\19 §2.1）：任务 未开始(1)/进行中(2)；创建时加工单须生产中（§3.3 tooltip）。"""
    if not craft or str(craft.get("status")) not in ("1", "2"):
        return False
    bill = _bills().get(craft.get("produce_bill_id"))
    return bool(bill) and str(bill.get("status")) == "2"

def _random_add(body, session):
    craft = _crafts().get(body.get("produce_bill_craft_id"))
    if not craft:
        return core.fail("请选择工序")
    if not _craft_pickable(craft):
        # tooltip 逐字（deep\19 §3）：加工单暂停、已取消、未开始和完成时不允许创建
        return core.fail("加工单暂停、已取消、未开始和完成时不允许创建")
    try:
        num = _f(body.get("num"))
    except Exception:
        return core.fail("参数错误")
    if not str(body.get("num") or "").strip():
        return core.fail("请输入抽检数量")
    if num <= 0:
        return core.fail("抽检数量不能为0")
    if num > MAX_NUM:
        return core.fail("抽检数量最大为99999999")
    code = _s(body, "code") or _next_random_code(_randoms().all())
    if len(code) > 50:
        return core.fail("长度超出限制")
    remark = body.get("remark")
    if remark and len(str(remark)) > 255:
        return core.fail("长度超出限制")
    row = _randoms().add({
        "enterprise_id": session.get("enterprise_id"),
        "code": code,
        "produce_bill_craft_id": craft.get("id"),
        "produce_bill_id": craft.get("produce_bill_id"),
        "num": _num(num),
        "status": 2,              # 抽检：2 待质检（与全检相反！）
        "type": body.get("type") or 1,   # 人工创建恒 1（deep\19 §2.2）
        "valid_num": "0.00000000", "waste_num": "0.00000000",
        "inspect_user_id": None, "inspect_time": None, "produce_user_id": None,
        "field_ids": [], "remark": remark, "quality_remark": None,
        "inspection_random_waste_list": [],
        "inspection_scheme_id": craft.get("inspection_scheme_id"),
        "create_user_id": session.get("id"),
    })
    return core.ok(_decorate_random(row))

def _random_edit(body, session):
    r = _randoms().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    locked = str(r.get("status")) == "1" or str(r.get("type")) == "2"  # 已质检/方案生成：数量与工序锁定（§2.2）
    patch = {}
    if "code" in body:
        code = _s(body, "code") or r.get("code")
        if len(code) > 50:
            return core.fail("长度超出限制")
        patch["code"] = code
    if "remark" in body:
        if body.get("remark") and len(str(body.get("remark"))) > 255:
            return core.fail("长度超出限制")
        patch["remark"] = body.get("remark")
    if body.get("num") is not None or body.get("produce_bill_craft_id") is not None:
        if locked:
            return core.fail("已质检或方案自动生成的抽检单，抽检数量与工序不可修改")
        if body.get("num") is not None and str(body.get("num")).strip():
            num = _f(body.get("num"))
            if num <= 0:
                return core.fail("抽检数量不能为0")
            if num > MAX_NUM:
                return core.fail("抽检数量最大为99999999")
            patch["num"] = _num(num)
        if body.get("produce_bill_craft_id") and str(body.get("produce_bill_craft_id")) != str(r.get("produce_bill_craft_id")):
            craft = _crafts().get(body.get("produce_bill_craft_id"))
            if not craft:
                return core.fail("请选择工序")
            if not _craft_pickable(craft):
                return core.fail("加工单暂停、已取消、未开始和完成时不允许创建")
            patch["produce_bill_craft_id"] = craft.get("id")
            patch["produce_bill_id"] = craft.get("produce_bill_id")
            patch["inspection_scheme_id"] = craft.get("inspection_scheme_id")
    _randoms().update(r["id"], patch)
    return core.ok()

def _random_del(body, session):
    r = _randoms().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if str(r.get("type")) == "2":  # 方案自动生成的抽检单不可删（deep\19 §1.2/§2.2）
        return core.fail("方案自动生成的抽检单不可删除")
    _randoms().remove(r["id"])
    return core.ok()

def _random_quality(body, session):
    """抽检质检执行（deep\19 §2.3）：良品数+不良品数必须等于抽检数（逐字）。"""
    r = _randoms().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if str(r.get("status")) == "1":
        return core.fail("该抽检单已质检")
    if not body.get("produce_user_id"):
        return core.fail("请选择生产人员")
    num = _f(r.get("num"))
    valid = _f(body.get("valid_num"))
    waste = _f(body.get("waste_num"))
    if valid < 0 or waste < 0 or valid > MAX_NUM or waste > MAX_NUM:
        return core.fail("数量填写错误")
    if abs(valid + waste - num) > 1e-9:
        return core.fail("良品数 + 不良品数必须等于抽检数")
    qremark = body.get("quality_remark")
    if qremark and len(str(qremark)) > 255:
        return core.fail("长度超出限制")
    craft = _crafts().get(r.get("produce_bill_craft_id"))
    pc = _pc_of(craft)
    allowed = [str(x) for x in ((pc or {}).get("waste_item_ids") or [])]
    wl = []
    for it in body.get("waste_array") or []:
        wid = str(it.get("waste_item_id"))
        if allowed and wid not in allowed:
            return core.fail("不良品项不属于当前工序配置")
        w = _waste(wid)
        if w:
            wl.append({"waste_item": w, "num": _num(it.get("num"))})
    patch = {
        "status": 1,  # 抽检：1 已质检（与全检相反！tag"5"绿）
        "produce_user_id": body.get("produce_user_id"),
        "valid_num": _num(valid), "waste_num": _num(waste),
        "inspection_random_waste_list": wl,
        "quality_remark": qremark,
        "inspect_user_id": body.get("inspect_user_id") or session.get("id"),
        "inspect_time": core.now(),
        "quality_random_attach_module_ext_field_list": body.get("quality_random_attach_module_ext_field_list") or {},
    }
    r = _randoms().update(r["id"], patch)
    return core.ok(_decorate_random(r))

def _random_inspect_fields(body, session):
    """质检附加组件检查项（实测 api_dump/deep_m11/inspect_fields.json：空）。"""
    r = _randoms().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok([])

# ---------------- 可抽检任务选择器 / 生产人员选择器 ----------------
def _inspect_random_craft_list(body, session):
    """选择生产任务弹窗（deep\19 §2.1）：仅 未开始(1)/进行中(2) 任务，行点击选中。"""
    rows = [c for c in _crafts().all() if str(c.get("status")) in ("1", "2")]
    st_f = body.get("status")
    if st_f not in (None, "", "all"):
        rows = [c for c in rows if str(c.get("status")) == str(st_f)]
    out = []
    for c in rows:
        pc = _pc_of(c)
        bill = _bill_of(c)
        if not bill:
            continue
        if _s(body, "craft_name") and _s(body, "craft_name") not in str((pc or {}).get("name") or c.get("craft_name") or ""):
            continue
        if _s(body, "produce_bill_code") and _s(body, "produce_bill_code") not in str(bill.get("code") or ""):
            continue
        if _s(body, "pub_craft_code") and _s(body, "pub_craft_code") not in str((pc or {}).get("code") or ""):
            continue
        g = bill.get("goods")
        g = g if isinstance(g, dict) else {"code": bill.get("goods_code"), "name": bill.get("goods_name"),
                                           "standard": bill.get("goods_standard")}
        gk = _s(body, "goods_keyword")
        if gk and gk not in "%s %s %s" % (g.get("name") or "", g.get("code") or "", g.get("standard") or ""):
            continue
        plan_num = _f(c.get("num"))
        valid = _f(c.get("total_valid_num"))
        pct = int(valid / plan_num * 100) if plan_num else 0
        out.append({
            "id": c.get("id"), "produce_bill_id": c.get("produce_bill_id"),
            "produce_bill_code": bill.get("code"),
            "pub_craft_id": c.get("pub_craft_id"),
            "craft_name": (pc or {}).get("name") or c.get("craft_name"),
            "pub_craft": {"id": (pc or {}).get("id"), "name": (pc or {}).get("name"), "code": (pc or {}).get("code")},
            "plan_start": c.get("plan_start"), "plan_end": c.get("plan_end"),
            "production_quantity": _num(c.get("num")),
            "produce_bill_craft_num": _num(c.get("num")),
            "total_valid_num": _num(valid),
            "production_schedule": "%d%%(%d/%d)" % (pct, int(valid), int(plan_num)) if plan_num else "",
            "production_schedule_percent": pct,
            "status": c.get("status"), "status_name": c.get("status_name") or {1: "未开始", 2: "进行中"}.get(c.get("status"), ""),
            "goods": g,
            "workshop": (pc or {}).get("workshop"),
            "workshop_path_names": c.get("workshop_path_names") or (pc or {}).get("workshop_path_names") or "",
            "produce_line_names": c.get("produce_line_names") or (pc or {}).get("produce_line_names") or "",
            "device_names": (pc or {}).get("device_names") or "",
            "reportable_user_names": (pc or {}).get("reportable_user_names") or "",
            "dispatch_user_names": c.get("dispatch_user_names") or "",
        })
    return _page(out, body)

def _get_produce_user_list(body, session):
    """抽检质检弹窗·生产人员选择器（deep\19 §2.3：/produce_bill_craft/get_produce_user_list {id}）。"""
    c = _crafts().get(body.get("id"))
    pc = _pc_of(c)
    ids = (pc or {}).get("reportable_user_ids") or (c or {}).get("dispatch_user_ids") or []
    lst = []
    for uid in ids:
        u = core.Store("users").get(uid)
        if u:
            lst.append({"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name"),
                        "mobile": u.get("mobile"), "type": u.get("type")})
    if not lst:  # 种子任务未配置可报工人时回退全量用户（demo 兜底）
        for u in core.Store("users").all()[:20]:
            lst.append({"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name"),
                        "mobile": u.get("mobile"), "type": u.get("type")})
    return core.ok(lst)

def _quality_able_user_list(body, session):
    """可质检人（deep\19 §4.3，实测 count=17：id/name/real_name/mobile/fun_ids…）。"""
    return core.ok([{"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name"),
                     "mobile": u.get("mobile"), "type": u.get("type"), "fun_ids": u.get("fun_ids") or ""}
                    for u in core.Store("users").all()])

# ================================================================
# 抽检方案 inspection_scheme（deep\19 §3，参数互斥）
# ================================================================
def _scheme_validate(body):
    name = _s(body, "name")
    if not name:
        return None, core.fail("请输入方案名称")
    if len(name) > 100:
        return None, core.fail("长度超出限制")
    try:
        stype = int(body.get("scheme_type"))
        assert stype in (1, 2, 3)
    except Exception:
        return None, core.fail("参数错误")
    if not str(body.get("num") or "").strip():
        return None, core.fail("请输入抽检数量")
    if _f(body.get("num")) <= 0:
        return None, core.fail("抽检数量不能为0")
    if _f(body.get("num")) > MAX_NUM:
        return None, core.fail("抽检数量最大为99999999")
    row = {"name": name, "scheme_type": stype, "num": _num(body.get("num")),
           "is_self_build": body.get("is_self_build") or 2,
           # 参数互斥（deep\19 §3）：按类型删除无关字段
           "report_count": None, "produce_minutes": None, "timed_inspection_time": []}
    if stype == 1:
        if not str(body.get("report_count") or "").strip():
            return None, core.fail("请输入报工数量")
        if _f(body.get("report_count")) <= 0:
            return None, core.fail("报工数量必须大于0")
        row["report_count"] = _num(body.get("report_count"))
    elif stype == 2:
        pm = body.get("produce_minutes")
        if pm in (None, "") or str(pm).strip() == "":
            return None, core.fail("请输入生产时长")
        try:
            pm = int(pm)
            assert pm > 0
        except Exception:
            return None, core.fail("生产时长必须为大于0的分钟数")
        row["produce_minutes"] = pm
    else:
        ts = body.get("timed_inspection_time") or []
        ts = [str(t).strip() for t in ts if str(t).strip()]
        if not ts:
            return None, core.fail("请添加抽检时间")
        if len(set(ts)) != len(ts):
            return None, core.fail("执行时间不能重复")
        row["timed_inspection_time"] = ts
    row["scheme_type_name"] = SCHEME_TYPE_NAME[stype]  # 后端实测文案（type1 少"量"字，照抄）
    return row, None

def _scheme_list(body, session):
    rows = _schemes().all()
    if _s(body, "name"):
        rows = [r for r in rows if _s(body, "name") in str(r.get("name") or "")]
    if body.get("scheme_type") not in (None, ""):
        rows = [r for r in rows if str(r.get("scheme_type")) == str(body.get("scheme_type"))]
    return _page([dict(r) for r in rows], body, default_size=1000)  # 下拉加载用大页（§3）

def _scheme_detail(body, session):
    r = _schemes().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok(dict(r))

def _scheme_add(body, session):
    row, err = _scheme_validate(body)
    if err:
        return err
    row["enterprise_id"] = session.get("enterprise_id")
    row["create_user_id"] = session.get("id")
    return core.ok(dict(_schemes().add(row)))

def _scheme_edit(body, session):
    r = _schemes().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    row, err = _scheme_validate(body)
    if err:
        return err
    _schemes().update(r["id"], row)
    return core.ok()

def _scheme_del(body, session):
    r = _schemes().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    _schemes().remove(r["id"])
    return core.ok()

# ================================================================
# 种子（惰性：仅当对应库为空时落；合成行带 "_seed":1，不改已有种子文件本体）
# ================================================================
def _seed_schemes():
    """抽检方案 3 条：api_dump/deep_m11/scheme_list.json 实测原文。"""
    if _schemes().rows:
        return
    for sid, name, stype, extra, cat in (
        (70197, "定时抽检", 3, {"timed_inspection_time": ["10:00", "11:00", "15:00", "16:00"]}, "2026-04-16 23:14:11"),
        (70196, "按生产时长抽检", 2, {"produce_minutes": 60}, "2026-04-16 23:14:11"),
        (70195, "按报工数量抽检", 1, {"report_count": "10.00000000"}, "2026-05-27 10:41:52"),
    ):
        _schemes().add({"id": sid, "_seed": 1, "enterprise_id": 16807, "name": name, "scheme_type": stype,
                        "num": "5.00000000" if stype in (2, 3) else "2.00000000",
                        "report_count": extra.get("report_count"), "produce_minutes": extra.get("produce_minutes"),
                        "timed_inspection_time": extra.get("timed_inspection_time", []),
                        "is_self_build": 1, "create_user_id": 63097,
                        "scheme_type_name": SCHEME_TYPE_NAME[stype],
                        "created_at": cat, "updated_at": cat, "fieldValueList": []})

def _pick_random_seed_craft():
    """抽检种子挂本地任务：生产中加工单 上的 进行中/未开始 任务（保证 quality 执行可演示）。"""
    bills = {str(b.get("id")): b for b in _bills().all() if str(b.get("status")) == "2"}
    for c in _crafts().all():
        if str(c.get("produce_bill_id")) in bills and str(c.get("status")) in ("1", "2"):
            return c
    return None

def _seed_randoms():
    """抽检单 3 条：状态组合对齐 random_list.json 实测（1 已质检 / 2 待质检 ×2）。"""
    if _randoms().rows:
        return
    craft = _pick_random_seed_craft()
    if not craft:
        return
    users = [u.get("id") for u in core.Store("users").all()]
    base = {"_seed": 1, "enterprise_id": 16807,
            "produce_bill_craft_id": craft.get("id"), "produce_bill_id": craft.get("produce_bill_id"),
            "type": 1, "field_ids": [], "inspection_scheme_id": craft.get("inspection_scheme_id"),
            "inspection_random_waste_list": [], "del": 2}
    # 1 条已质检（status=1，注意与全检相反）+ 2 条待质检（status=2）
    _randoms().add(dict(base, id=696001, code="JY202607020001", num="100.00000000", status=1,
                        valid_num="100.00000000", waste_num="0.00000000",
                        inspect_user_id=users[1] if len(users) > 1 else None,
                        inspect_time="2026-08-11 00:12:38", produce_user_id=users[2] if len(users) > 2 else None,
                        remark=None, quality_remark=None, create_user_id=users[0],
                        created_at="2026-07-02 09:05:20", updated_at="2026-08-11 00:12:38"))
    for rid, code, num, cat in ((696002, "JY202608110001", "2.00000000", "2026-08-11 00:12:56"),
                                (696003, "JY202608240001", "10.00000000", "2026-08-24 18:34:55")):
        _randoms().add(dict(base, id=rid, code=code, num=num, status=2,
                            valid_num="0.00000000", waste_num="0.00000000",
                            inspect_user_id=None, inspect_time=None, produce_user_id=None,
                            remark=None, quality_remark=None, create_user_id=users[0],
                            created_at=cat, updated_at=cat))

def _seed_rwq():
    """全检视图种子：从 produce_bill_crafts（B11 导出 2125 行）派生 待检/已检/不需检 行。
    任务行是扁平导出行（无 quality_type），经 pub_craft 主档关联补全；数量按确定性规则合成。"""
    if _rwq().rows:
        return
    users = [u.get("id") for u in core.Store("users").all()]
    if not users:
        return
    pc_by_id = {str(c.get("id")): c for c in _pc().all()}
    qt1 = [c for c in _crafts().all()
           if str((pc_by_id.get(str(c.get("pub_craft_id")) or {}).get("quality_type"))) == "1"]
    qt3 = [c for c in _crafts().all()
           if str((pc_by_id.get(str(c.get("pub_craft_id")) or "").get("quality_type"))) == "3"]
    done = [c for c in qt1 if str(c.get("status")) == "3"][:4]    # 已完成任务 → 已质检行
    doing = [c for c in qt1 if str(c.get("status")) == "2"][:4]   # 进行中任务 → 待质检行
    wastes = [w.get("id") for w in core.Store("wastes").all()]

    def mk(idx, craft, qstatus, day, rework=0.0, scrap=0.0):
        pc = pc_by_id.get(str(craft.get("pub_craft_id"))) or {}
        num = max(_f(craft.get("num")), 10.0)
        waste = round(num * (0.05 if idx % 2 else 0.10), 2)
        valid = round(num - waste, 2)
        wids = [wastes[i % len(wastes)] for i in range(min(2, int(waste) or 1))] if waste else []
        rwl = [{"waste_item": _waste(wid), "num": _num(waste / max(len(wids), 1))} for wid in wids]
        um = pc.get("unit_money") or "0.00"
        pm = pc.get("price_mode") or 1
        minutes = 60 if str(pm) == "2" else 0
        uid = users[idx % len(users)]
        qtime = "2026-10-%02d 15:30:00" % (20 + idx % 8) if qstatus == 2 else None
        return {
            "_seed": 1, "type": 1, "type_name": "生产报工",
            "produce_bill_craft_id": craft.get("id"), "produce_bill_id": craft.get("produce_bill_id"),
            "report_user_id": uid, "report_user": _user(uid),
            "valid_num": _num(valid), "waste_num": _num(waste), "report_num": _num(valid + waste),
            "report_waste_list": rwl, "waste_item_sum": len(rwl),
            "working_minutes": minutes,
            "start_time": "2026-10-%02d 08:00:00" % (20 + idx % 8),
            "end_time": "2026-10-%02d 17:00:00" % (20 + idx % 8),
            "report_time": "2026-10-%02d 17:30:00" % (20 + idx % 8),
            "price_mode": pm, "craft_unit_money": _num(um, 2),
            "valid_money": _wage(pm, valid, um, minutes),
            "quality_status": qstatus,
            "rework_num": _num(rework), "scrap_num": _num(scrap),
            "quality_remark": "样例质检备注%d" % idx if qstatus == 2 else None,
            "last_quality_user_id": users[(idx + 1) % len(users)] if qstatus == 2 else None,
            "last_quality_user": _user(users[(idx + 1) % len(users)]) if qstatus == 2 else None,
            "last_quality_time": qtime,
            "need_rework": 1 if rework > 0 else 2,
            "settle_status": 1, "settle_status_name": "未结算",
            "remark": None,
            "created_at": "2026-10-%02d 17:30:00" % (20 + idx % 8),
            "updated_at": qtime or "2026-10-%02d 17:30:00" % (20 + idx % 8),
        }

    rows = []
    idx = 0
    for i, c in enumerate(done):   # 已质检；末条带返工/报废（演示 已返工 筛选）
        rework = round(_f(c.get("num")) * 0.02, 2) if i == len(done) - 1 else 0.0
        scrap = round(_f(c.get("num")) * 0.01, 2) if i == len(done) - 1 else 0.0
        rows.append(mk(idx, c, 2, 20 + idx, rework=rework, scrap=scrap))
        idx += 1
    for c in doing:                # 待质检
        rows.append(mk(idx, c, 1, 20 + idx))
        idx += 1
    for i, c in enumerate(qt3[:2]):  # 不需质检（quality_type=3 工序的报工行）
        r = mk(idx, c, 3, 20 + idx)
        rows.append(r)
        idx += 1
    for i, r in enumerate(rows):
        r["id"] = 777000001 + i
        _rwq().add(r)

_seed_schemes()
_seed_randoms()
_seed_rwq()

ROUTES = {
    # ---- 全检族 report_work_quality（deep\19 §1.1 / §8.1；deep\01 §3.2）----
    ("report_work_quality", "list"): _rwq_list,                    # 全检列表（页面前端冗余入口）
    ("report_work_quality", "list_by_filter_view"): _rwq_list,     # 全检页数据源（质检视图）
    ("report_work_quality", "status_count"): _rwq_status_count,    # 质检状态/已返工计数
    ("report_work_quality", "quality"): _rwq_quality,              # 单条质检判定（含返工自动化）
    ("report_work_quality", "batch_quality"): _rwq_batch_quality,  # 批量质检（四段计数）
    ("report_work_quality", "detail"): _rwq_detail,
    # ---- 全检族 report_work_record（§8.1：编辑弹窗复用族；detail 归 report_record.py 报工记录域）----
    ("report_work_record", "quality_list"): _rwq_list,
    ("report_work_record", "quality"): _rwq_quality,
    ("report_work_record", "edit_quality_status"): _rwr_edit_quality_status,  # 接口在册、无样例（推断实现）
    # ---- 抽检族 produce_inspection_random（deep\19 §1.2/§2；status 与全检相反）----
    ("produce_inspection_random", "list"): _random_list,                 # 抽检明细页（同表只读视图）
    ("produce_inspection_random", "list_by_filter_view"): _random_list,  # 抽检页（视图）
    ("produce_inspection_random", "status_count"): _random_status_count,  # inspect_status_count
    ("produce_inspection_random", "add"): _random_add,                    # 创建（type=1，status=2 待质检）
    ("produce_inspection_random", "edit"): _random_edit,                  # 已质检/方案单锁定数量与工序
    ("produce_inspection_random", "del"): _random_del,                    # 仅 type=1 可删
    ("produce_inspection_random", "detail"): _random_detail,
    ("produce_inspection_random", "quality"): _random_quality,            # 判定（status→1 已质检）
    ("produce_inspection_random", "get_attach_module_inspect_field_list"): _random_inspect_fields,  # 实测空
    # ---- 抽检链路配套（§2.1/§2.3/§4.3）----
    ("produce_bill_craft", "inspect_random_craft_list"): _inspect_random_craft_list,  # 可抽检任务选择器
    ("produce_bill_craft", "get_produce_user_list"): _get_produce_user_list,          # 生产人员选择器
    ("user", "quality_able_user_list"): _quality_able_user_list,                      # 可质检人
    # ---- 抽检方案 inspection_scheme（deep\19 §3）----
    ("inspection_scheme", "list"): _scheme_list,
    ("inspection_scheme", "add"): _scheme_add,
    ("inspection_scheme", "edit"): _scheme_edit,
    ("inspection_scheme", "del"): _scheme_del,
    ("inspection_scheme", "detail"): _scheme_detail,
}
