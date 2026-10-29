# -*- coding: utf-8 -*-
"""B12 项目管理域：produce_project（+ import_record 创建记录轮询）。
依据：deep\\22-项目与生产计划.md
  §1  列表（快捷筛选四组/筛选字段/行字段全集/编号 YYYYMMDD+3位序 无前缀）
  §2  保存：item_list>80 条走 add_async/edit_async + 全局 CreateLog 5 秒轮询（/import_record/list type=1）
  §9  接口存活清单 + 枚举速查（PROJECT_TYPE/PROJECT_STATUS/CREATE_RECORD_TYPE）
样例：api_dump/deep_m14（project_list/detail/status_count/goods_selector/
      contract_item_goods/import_record_list，字段名与取值形态逐字对齐）。
种子：production_projects.json 首载时由 produce_bills.json 的 project_code/project_name 聚合生成（"_seed":1）。
注：produce_plan 族原站 404（deep\\22 §9），不实现；打印/导出族不在本模块范围。
"""
import threading
import time

import core

# ---------------- 枚举（deep\22 §9 枚举速查，逐字） ----------------
PROJECT_TYPE = {1: "普通项目", 2: "合同项目", 3: "外协项目"}
PROJECT_STATUS = {1: "未开始", 2: "进行中", 3: "已完成", 4: "暂停"}
CREATE_RECORD_TYPE = {1: "生产项目", 2: "物料分析", 3: "服务工单"}   # CREATE_RECORD_TYPE 原文
# 创建状态 s-tag 枚举：原站本租户无记录无样例（deep_m14/import_record_list.json count=0），
# 取快工单通用 1=进行中/2=成功/3=失败 形态（推断，demo 占位）。
RECORD_STATUS = {1: "创建中", 2: "创建成功", 3: "创建失败"}
RECORD_TAG_CLASS = {1: "1", 2: "2", 3: "5"}
ASYNC_THRESHOLD = 80   # 前端 item_list.length > 80 切异步（deep\22 §2.1，切换逻辑在原站前端，后端两个端点各自恒同/异步）
MAX_NUM = 99999999

# ---------------- 存取（单例） ----------------
_PROJECTS = _LOGS = None


def _projects():
    global _PROJECTS
    if _PROJECTS is None:
        _PROJECTS = core.Store("production_projects")
    return _PROJECTS


def _logs():
    global _LOGS
    if _LOGS is None:
        _LOGS = core.Store("production_project_create_logs")
    return _LOGS


def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _num8(x):
    """8 位小数字符串（工单/工序数量口径，对齐 produce.py）。"""
    try:
        return "%.8f" % float(x or 0)
    except Exception:
        return "0.00000000"


def _n(x):
    """整数字符串形态（项目行统计字段：dump 实测 "10"/"0"，非 8 位小数）。"""
    v = _f(x)
    return str(int(v)) if v == int(v) else ("%.2f" % v).rstrip("0").rstrip(".")


def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "real_name": u.get("real_name") or u.get("name") or str(uid)}
    try:  # users 种子未生成时回退 demo 账号
        from modules.auth import DEMO_USER
        if str(DEMO_USER.get("id")) == str(uid):
            return {"id": DEMO_USER["id"], "real_name": DEMO_USER["real_name"]}
    except Exception:
        pass
    return {"id": uid, "real_name": "用户%s" % uid}


def _user_by_name(name):
    """种子聚合：按创建人姓名反查 users（导出转换行只有 create_user_name）。"""
    name = str(name or "").strip()
    if not name:
        return None
    for u in core.Store("users").all():
        if (u.get("real_name") or u.get("name")) == name:
            return {"id": u.get("id"), "real_name": name}
    return {"id": None, "real_name": name}   # 无同名用户：id 置空（王总/管理员等非系统账号）


def _contract_of(cid=None, code=None):
    for c in core.Store("contracts").all():
        if cid is not None and str(c.get("id")) == str(cid):
            return c
        if code is not None and str(c.get("code") or "") == str(code):
            return c
    return None


def _ref_goods(goods_id):
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(goods_id or ""):
            return {"id": g.get("id"), "code": g.get("code"), "name": g.get("name"),
                    "standard": g.get("standard"), "unit": g.get("unit")}
    return None

# ---------------- 项目 ↔ 加工单/工序 聚合 ----------------


def _bills_of(project):
    """项目加工单：新链路 produce_project_id 直连；种子链路按 project_code 兜底（B11 导出转换行）。"""
    pid, pcode = str(project.get("id")), str(project.get("code") or "")
    out = []
    for b in core.Store("produce_bills").all():
        if str(b.get("produce_project_id") or "") == pid:
            out.append(b)
        elif pcode and str(b.get("project_code") or "") == pcode:
            out.append(b)
    return out


def _crafts_of_bills(bills):
    ids = {str(b.get("id")) for b in bills}
    return [c for c in core.Store("produce_bill_crafts").all()
            if str(c.get("produce_bill_id")) in ids]


def _stock_in_of(bill):
    """入库数量：新单取 stock_in_num；种子导出行解析 "0%(0/1)" 形态的入库进度串。"""
    if bill.get("stock_in_num") is not None:
        return _f(bill.get("stock_in_num"))
    s = str(bill.get("stock_in_percent") or "")
    if "(" in s and "/" in s:
        try:
            return _f(s[s.index("(") + 1:s.index("/")])
        except Exception:
            pass
    return 0.0


def _project_status(bills):
    """项目状态（demo 口径，推断）：全部完成→已完成；任一生产中→进行中；任一暂停→暂停；
    全部未开始→未开始；混合（含取消）默认进行中。无工单→未开始。"""
    if not bills:
        return 1
    sts = []
    for b in bills:
        try:
            sts.append(int(b.get("status") or 1))
        except Exception:
            sts.append(1)
    if all(s == 3 for s in sts):
        return 3
    if 2 in sts:
        return 2
    if 5 in sts:
        return 4
    if all(s == 1 for s in sts):
        return 1
    return 2


def _stats(p):
    """行级统计字段全集（deep\22 §1.5/§8.3 实测字段名与取值形态）。"""
    bills = _bills_of(p)
    crafts = _crafts_of_bills(bills)
    total = len(bills)
    fin_bill = len([b for b in bills if str(b.get("status")) == "3"])
    plan_sum = sum(_f(b.get("num")) for b in bills)
    stock_sum = sum(_stock_in_of(b) for b in bills)
    craft_total = len(crafts)
    craft_fin = len([c for c in crafts if str(c.get("status")) == "3"])
    # 生产进度 = 100×完成工序/总工序（2 位；dump 实测 type=2 亦按工序口径，§1.5 数据印证）
    prog = (100.0 * craft_fin / craft_total) if craft_total else 0.0
    # 项目进度 = 100×已完成工单/工单数（表头 tooltip 语义；无工单 0.00）
    pprog = (100.0 * fin_bill / total) if total else 0.0
    st = _project_status(bills)
    return {
        "project_status": st,
        "project_status_name": PROJECT_STATUS.get(st, ""),
        "produce_bill_total_count": total,
        "finish_produce_bill_count": fin_bill,
        "produce_bill_total_plan_num": _n(plan_sum),
        "produce_bill_total_stock_in_num": _n(stock_sum),
        "project_progress": "%.2f" % pprog,
        "produce_bill_craft_total_count": craft_total,
        "finish_produce_bill_craft_count": craft_fin,
        "produce_bill_progress": "%.2f" % prog,
        "produce_bill_progress_format_by_excel": "%.2f%%（%d/%d）" % (prog, craft_fin, craft_total),
        "produce_bill_ids": [b.get("id") for b in bills],
        "produce_bill_material_count": sum(len(b.get("material_list") or []) for b in bills),
        "produce_bill_stock_in_percent": "%d" % (100 * stock_sum / plan_sum) if plan_sum else "0",
        "editAccessible": True,
    }


def _decorate(p):
    """列表/详情行装饰（字段名对齐 api_dump/deep_m14/project_list.json 实测行）。"""
    d = dict(p)
    d.setdefault("fieldValueList", [])
    d.update(_stats(d))
    d["type_name"] = PROJECT_TYPE.get(d.get("type") or 1, "")
    d["qr_code_url"] = "https://single.kgd.ltd/pep?qr_id=%s&type=%s" % (d.get("id"), d.get("type") or 1)
    d["outsource_produce_bill_list"] = []
    cu = d.get("create_user") or _user(d.get("create_user_id"))
    uu = d.get("update_user") or _user(d.get("update_user_id") or d.get("create_user_id"))
    d["create_user"], d["update_user"] = cu, uu
    d["customer_contract"] = _contract_of(cid=d.get("customer_contract_id")) if d.get("customer_contract_id") else None
    return d

# ---------------- 列表：筛选/分页/计数 ----------------


def _filter_projects(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        d = _decorate(r)   # 快捷筛选用到派生字段（project_status 等），先装饰后过滤
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        if s("name") and s("name") not in str(r.get("name") or ""):
            continue
        cu = (d.get("create_user") or {}).get("real_name") or ""
        uu = (d.get("update_user") or {}).get("real_name") or ""
        if s("create_user_name") and s("create_user_name") not in cu:
            continue
        if s("update_user_name") and s("update_user_name") not in uu:
            continue
        cc = (d.get("customer_contract") or {}).get("code") or ""
        if s("customer_contract_code") and s("customer_contract_code") not in cc:
            continue
        if s("type") and str(r.get("type") or 1) != s("type"):
            continue
        # 快捷筛选：项目进度/生产进度（deep\22 §1.2：NO 未完成 v"2" / YES 已完成 v"1"）
        if s("project_is_finish") not in ("", "all"):
            if (d["project_status"] == 3) != (s("project_is_finish") == "1"):
                continue
        if s("produce_is_finish") not in ("", "all"):
            fin = bool(d["produce_bill_total_count"]) and \
                d["finish_produce_bill_count"] == d["produce_bill_total_count"]
            if fin != (s("produce_is_finish") == "1"):
                continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        if s("updated_at_start") and str(r.get("updated_at") or "") < s("updated_at_start"):
            continue
        if s("updated_at_end") and str(r.get("updated_at") or "") > s("updated_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out


def _paged(store, rows, body):
    """契约分页：筛选在 page() 前对 all() 过滤（简报规则3，同 produce.py 手法）。"""
    backup = store.rows
    store.rows = rows
    try:
        return core.paged_ok(store.page(body))
    finally:
        store.rows = backup


def _page_rows(rows, body):
    """无自有 store 的列表（选择器）用纯分页，不动他人 store 的 rows。"""
    try:
        pageNo = int(body.get("pageNo") or 1)
    except Exception:
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or 30)
    except Exception:
        pageSize = 30
    return core.paged_ok({"list": rows[(pageNo - 1) * pageSize: pageNo * pageSize],
                          "total": len(rows), "pageNo": pageNo, "pageSize": pageSize})


def _list(body, session):
    store = _projects()
    rows = _filter_projects(store.all(), body)
    rows = sorted(rows, key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))), reverse=True)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_decorate(r) for r in res["data"]["list"]]
    return res


def _list_by_filter_view(body, session):
    # deep\22 §1.7：需 filter_view_id，无则参数校验错误（视图本体归 filter_view/goods 模块）
    if body.get("filter_view_id") in (None, ""):
        return core.fail("参数校验错误")
    return _list(body, session)


def _status_count(body, session):
    """快捷筛选四组计数（deep\22 §1.2/§8.3 读取路径；原站本租户空数组，此处按数据实算）。"""
    rows = _filter_projects(_projects().all(), body)
    type_count = {"normal": 0, "contract": 0, "outsource": 0}
    pg = {"project_progress_count": 0, "project_finish_count": 0}
    pd = {"produce_progress_count": 0, "produce_finish_count": 0}
    ps = {"wait_start": 0, "processing": 0, "produce_finish": 0, "pause": 0}
    _ps_key = {1: "wait_start", 2: "processing", 3: "produce_finish", 4: "pause"}
    for r in rows:
        d = _decorate(r)
        t = d.get("type") or 1
        if t in type_count:
            type_count[{1: "normal", 2: "contract", 3: "outsource"}[t]] += 1
        if d["project_status"] == 3:
            pg["project_finish_count"] += 1
        else:
            pg["project_progress_count"] += 1
        if d["produce_bill_total_count"] and d["finish_produce_bill_count"] == d["produce_bill_total_count"]:
            pd["produce_finish_count"] += 1
        else:
            pd["produce_progress_count"] += 1
        ps[_ps_key.get(d["project_status"], "processing")] += 1
    return core.ok({
        "type_count": type_count,
        "project_progress_count": pg,
        "produce_progress_count": pd,
        "project_status_count": ps,
    })


def _detail(body, session):
    p = _projects().get(body.get("id"))
    if not p:
        return core.fail("数据不存在")
    return core.ok(_decorate(p))   # 项目头详情：不含 item_list（deep\22 §1.7）

# ---------------- 编号（deep\22 §1.5：YYYYMMDD+3 位序，无前缀，实测 100% 吻合） ----------------


def _next_project_code(rows):
    stamp = time.strftime("%Y%m%d")
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(stamp) and len(c) == len(stamp) + 3:
            try:
                mx = max(mx, int(c[len(stamp):]))
            except ValueError:
                pass
    return stamp + str(mx + 1).zfill(3)

# ---------------- 增：item_list → 加工单（+工序） ----------------
# 前置校验文案逐字（deep\22 §2.1；原为前端预检，后端 demo 同步把关）


def _validate(body):
    items = body.get("item_list") or []
    if not items:
        return None, core.fail("至少保证有一条加工单才允许提交。")
    for it in items:
        if it.get("num") in (None, ""):
            return None, core.fail("所有加工单的计划数必须填写。")
        if not (it.get("craft_list") or []):
            return None, core.fail("所有加工单的都至少选择有一条工序流程。")
    return items, None


def _new_craft(bill, c, seq):
    num = c.get("num")
    if num in (None, ""):
        num = bill.get("num")
    row = {
        "enterprise_id": bill.get("enterprise_id"),
        "produce_bill_id": bill["id"], "produce_bill_code": bill.get("code"),
        "pub_craft_id": c.get("pub_craft_id"),
        "craft_name": c.get("craft_name") or c.get("name") or "工序",
        "num": _num8(num), "status": 1, "status_name": "未开始",
        "order_number": c.get("order_number") or seq,
        "price_mode": c.get("price_mode") or 1,
        "unit_money": _num8(c.get("unit_money")),
        "quality_type": c.get("quality_type") or 3,
        "need_quality": c.get("need_quality") or 2,
        "waste_item_ids": c.get("waste_item_ids") or [],
        "is_open_clock": c.get("is_open_clock") or 2,
        "workshop_id": c.get("workshop_id"),
        "plan_start": c.get("plan_start"), "plan_end": c.get("plan_end"),
        "valid_num": "0.00000000", "total_valid_num": "0.00000000",
        "waste_num": "0.00000000", "rework_num": "0.00000000", "scrap_num": "0.00000000",
        "report_num": "0.00000000", "working_minutes": "0.00",
        "dispatch_user_ids": None, "dispatch_list": [],
        "remark": c.get("remark"),
    }
    return row


# ---- 批量落库：整批一次 flush（逐条 add 会整文件重写 N 次，>80 条异步路径实测过慢；
#      且 Windows 下 os.replace 偶发被索引/杀软短暂占用 → 小步重试） ----


def _flush(store, tries=4):
    for i in range(tries):
        try:
            store.flush()
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    store.flush()


def _bulk_add(store, rows):
    out = []
    for row in rows:
        row["id"] = row.get("id") or int(time.time() * 1000) % 100000000 + len(store.rows)
        row.setdefault("created_at", core.now())
        row.setdefault("updated_at", core.now())
        row.setdefault("del", 2)
        store.rows.insert(0, row)
        out.append(row)
    if rows:
        _flush(store)
    return out


def _bulk_remove(store, ids):
    ids = {str(x) for x in ids}
    keep = [r for r in store.rows if str(r.get("id")) not in ids]
    if len(keep) != len(store.rows):
        store.rows = keep
        _flush(store)


def _bill_row(project, item, seq, session):
    """项目投产：一条 item_list 生成一张加工单行（编码=项目编号-N，对齐种子实测形态）。"""
    code = str(item.get("code") or "").strip() or "%s-%s" % (project.get("code"), seq)
    goods = item.get("goods") or _ref_goods(item.get("goods_id")) or {}
    contract = _contract_of(cid=project.get("customer_contract_id"))
    return {
        "enterprise_id": session.get("enterprise_id"),
        "code": code,
        "produce_project_id": project["id"],
        "project_code": project.get("code"), "project_name": project.get("name"),
        "goods_id": goods.get("id") or item.get("goods_id"),
        "goods": goods, "goods_name": goods.get("name"),
        "goods_code": goods.get("code"), "goods_standard": goods.get("standard"),
        "num": _num8(item.get("num")),
        "status": 1, "work_type": 1,
        "produce_type": item.get("produce_type") or 1,
        "create_mode": 2 if (project.get("type") or 1) == 2 else 1,   # 合同项目→合同创建
        "requisition_status": 2,
        "customer_contract_id": project.get("customer_contract_id"),
        "contract_code": (contract or {}).get("code"),
        "customer_contract_item_id": item.get("customer_contract_item_id"),
        "project_order_number": item.get("project_order_number") or seq,
        "level_code": item.get("level_code"), "parent_level_code": item.get("parent_level_code"),
        "goods_bom": item.get("goods_bom") or item.get("goods_bom_id"),
        "delivery_date": item.get("delivery_date"),
        "start_produce_date": item.get("start_produce_date"),
        "end_produce_date": item.get("end_produce_date"),
        "priority_configure_id": item.get("priority_configure_id"),
        "remark": item.get("remark"),
        "pause_reason": None, "cancel_reason": None,
        "start_time": None, "end_time": None, "sort_num": None,
        "actual_num": "0.00000000", "stock_in_num": "0.00000000",
        "material_list": item.get("material_list") or [],
        "create_user_id": session.get("id"),
    }


def _bulk_create_bills(project, items, session, start_seq=1):
    """整批建加工单 + 工序（各一次落盘）。"""
    bill_rows = [_bill_row(project, it, start_seq + i, session) for i, it in enumerate(items)]
    bills = _bulk_add(core.Store("produce_bills"), bill_rows)
    craft_rows = []
    for bill, it in zip(bills, items):
        for i, cl in enumerate(it.get("craft_list") or [], 1):
            craft_rows.append(_new_craft(bill, cl, i))
    if craft_rows:
        _bulk_add(core.Store("produce_bill_crafts"), craft_rows)
    return bills


def _do_add(body, session):
    """同步创建核心（add 与 add_async 后台线程共用）。"""
    items, err = _validate(body)
    if err:
        return None, err
    store = _projects()
    code = str(body.get("code") or "").strip() or _next_project_code(store.all())
    contract = _contract_of(cid=body.get("customer_contract_id"))
    p = {
        "enterprise_id": session.get("enterprise_id"),
        "code": code,
        "name": body.get("name"),
        "type": int(body.get("type") or 1) if str(body.get("type") or "").isdigit() else 1,
        "customer_contract_id": body.get("customer_contract_id"),
        "remark": body.get("remark"),
        "create_user_id": session.get("id"),
        "update_user_id": session.get("id"),
    }
    p = store.add(p)
    _bulk_create_bills(p, items, session)
    return _projects().get(p["id"]), None


def _add(body, session):
    p, err = _do_add(body, session)
    if err:
        return err
    return core.ok(_decorate(p))


# ---------------- 异步：CreateLog（/import_record/list 5 秒轮询，deep\22 §2.4） ----------------


def _open_log(session, name, count):
    """落一条"创建中"记录（列名四件套：项目名称/加工单条数/创建状态/失败原因）。"""
    return _logs().add({
        "type": 1, "type_name": CREATE_RECORD_TYPE[1],
        "name": name or "(未命名项目)",
        "count": count,
        "status": 1,
        "reason": None,
        "create_user": _user(session.get("id")),
        "create_user_id": session.get("id"),
    })


def _run_async(fn, log, delay=1.0):
    """后台执行 → 更新创建记录状态（demo：1 秒模拟后台创建耗时，前端 5 秒轮询可见 创建中→创建成功）。"""
    def worker():
        time.sleep(delay)
        try:
            err = fn()
            if err:
                _logs().update(log["id"], {"status": 3, "status_name": RECORD_STATUS[3],
                                           "status_tag_class": RECORD_TAG_CLASS[3],
                                           "reason": err.get("msg") or "创建失败"})
            else:
                _logs().update(log["id"], {"status": 2, "status_name": RECORD_STATUS[2],
                                           "status_tag_class": RECORD_TAG_CLASS[2]})
        except Exception as e:
            _logs().update(log["id"], {"status": 3, "status_name": RECORD_STATUS[3],
                                       "status_tag_class": RECORD_TAG_CLASS[3],
                                       "reason": str(e)})
    threading.Thread(target=worker, daemon=True).start()


def _add_async(body, session):
    """项目异步创建（前端 item_list>80 走此端点，deep\22 §2.1；结果在 创建记录 里查看）。"""
    items, err = _validate(body)
    if err:
        return err
    log = _open_log(session, body.get("name"), len(items))
    payload = dict(body)

    def job():
        _, e = _do_add(payload, session)
        return e

    _run_async(job, log)
    return core.ok()

# ---------------- 改 / 删 ----------------

_EDITABLE = ("code", "name", "type", "customer_contract_id", "remark")
_BILL_EDITABLE = ("num", "delivery_date", "start_produce_date", "end_produce_date",
                  "priority_configure_id", "remark", "project_order_number")


def _do_edit(body, session):
    p = _projects().get(body.get("id"))
    if not p:
        return core.fail("数据不存在")
    patch = {k: body[k] for k in _EDITABLE if k in body and body[k] is not None}
    if "type" in patch and not str(patch.get("type")).isdigit():
        patch.pop("type")
    items = body.get("item_list") or []
    bills = core.Store("produce_bills")
    if "code" in patch:   # 项目改号：同步关联单的 project_code（种子链路按编码聚合）
        for b in _bills_of(p):
            bills.update(b["id"], {"project_code": patch["code"]})
    # 明细增改：带 id/匹配编码的更新，其余新建；仅回收"经项目创建"的单（produce_project_id 直连）
    keep_ids, keep_codes, new_items = set(), set(), []
    seq = len(_bills_of(p))
    for it in items:
        b = None
        if it.get("id"):
            b = bills.get(it.get("id"))
            if b and str(b.get("produce_project_id") or "") != str(p["id"]):
                b = None
        if b is None and it.get("code"):
            for x in _bills_of(p):
                if str(x.get("code")) == str(it.get("code")):
                    b = x
                    break
        if b is not None:
            bp = {k: it[k] for k in _BILL_EDITABLE if k in it}
            if "num" in bp:
                bp["num"] = _num8(bp["num"])
            bp["project_name"] = patch.get("name", p.get("name"))
            bills.update(b["id"], bp)
            keep_ids.add(str(b["id"]))
            keep_codes.add(str(b.get("code")))
        else:
            new_items.append(it)
    if new_items:
        seq += 1
        for nb in _bulk_create_bills(p, new_items, session, start_seq=seq):
            keep_ids.add(str(nb["id"]))
            keep_codes.add(str(nb.get("code")))
    if items:   # 载荷含明细才做回收（避免局部更新误删）
        drop_ids = [b["id"] for b in _bills_of(p)
                    if str(b.get("produce_project_id") or "") == str(p["id"])
                    and str(b["id"]) not in keep_ids and str(b.get("code")) not in keep_codes]
        if drop_ids:
            _bulk_remove(core.Store("produce_bill_crafts"),
                         [c["id"] for c in _crafts_of_bills(
                             [b for b in _bills_of(p) if str(b["id"]) in {str(x) for x in drop_ids}])])
            _bulk_remove(bills, drop_ids)
    if patch:
        patch["update_user_id"] = session.get("id")
        _projects().update(p["id"], patch)
    return None


def _edit(body, session):
    err = _do_edit(body, session)
    if err:
        return err
    return core.ok()


def _edit_async(body, session):
    p = _projects().get(body.get("id"))
    if not p:
        return core.fail("数据不存在")
    items, err = _validate(body)
    if err:
        return err
    log = _open_log(session, body.get("name") or p.get("name"), len(items))
    payload = dict(body)

    def job():
        return _do_edit(payload, session)

    _run_async(job, log)
    return core.ok()


def _del(body, session):
    """删除项目（确认文案/结果见 deep\22 §1.5：确认删除该项目吗？/删除成功）。
    demo 口径（推断，原站未测）：经项目创建的加工单及其工序一并删除；
    种子/外部单（仅 project_code 关联）解除关联保留。"""
    p = _projects().get(body.get("id"))
    if not p:
        return core.fail("数据不存在")
    bills = core.Store("produce_bills")
    mine, linked = [], []
    for b in _bills_of(p):
        (mine if str(b.get("produce_project_id") or "") == str(p["id"]) else linked).append(b)
    if mine:
        _bulk_remove(core.Store("produce_bill_crafts"), [c["id"] for c in _crafts_of_bills(mine)])
        _bulk_remove(bills, [b["id"] for b in mine])
    for b in linked:
        bills.update(b["id"], {"project_code": None, "project_name": None})
    _projects().remove(p["id"])
    return core.ok()

# ---------------- 创建记录轮询（全局 CreateLog，deep\22 §2.4） ----------------


def _record_list(body, session):
    """POST /import_record/list {pageNo, pageSize:30, type}（5 秒轮询；type 默认 1 生产项目）。"""
    store = _logs()
    rows = store.all()
    t = body.get("type")
    if t not in (None, ""):
        rows = [r for r in rows if str(r.get("type")) == str(t)]
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    res = _paged(store, rows, body)
    res["data"]["list"] = [_record_decorate(dict(r)) for r in res["data"]["list"]]
    return res


def _record_decorate(r):
    st = r.get("status") or 1
    r.setdefault("type_name", CREATE_RECORD_TYPE.get(r.get("type"), ""))
    r["status_name"] = RECORD_STATUS.get(st, "")
    r["status_tag_class"] = RECORD_TAG_CLASS.get(st, "")
    r.setdefault("reason", None)
    r.setdefault("create_user", _user(r.get("create_user_id")))
    return r

# ---------------- 商品/合同商品选择器（deep\22 §2.3，原站存活） ----------------


def _goods_selector(body, session):
    """商品选择器：自产源固定 source=1（fixed-source GOODS_SOURCE.SELF_PRODUCE）。"""
    kw = str(body.get("keyword") or body.get("name") or body.get("goods_keyword") or "").strip()
    code = str(body.get("code") or "").strip()
    boms_by_goods = {}
    for b in core.Store("boms").all():
        boms_by_goods.setdefault(str(b.get("goods_id")), []).append(b)
    rows = []
    for g in core.Store("goods").all():
        if str(g.get("source")) != "1":
            continue
        if kw and kw not in "%s %s %s" % (g.get("name") or "", g.get("code") or "", g.get("standard") or ""):
            continue
        if code and code not in str(g.get("code") or ""):
            continue
        rows.append(g)
    res = _page_rows(rows, body)
    out = []
    for g in res["data"]["list"]:
        d = dict(g)
        d.setdefault("sum_stock_num", "0")
        d["goods_bom_list"] = [
            {"id": b.get("id"), "name": b.get("name"), "code": b.get("code"),
             "goods_id": b.get("goods_id"), "is_default": b.get("is_default"),
             "is_enable": b.get("is_enable"), "created_at": b.get("created_at"),
             "has_reference_structure": b.get("has_reference_structure")}
            for b in boms_by_goods.get(str(g.get("id")), [])
        ]
        out.append(d)
    res["data"]["list"] = out
    return res


def _contract_item_goods_selector(body, session):
    """合同商品选择器：返回合同明细行（need_produce_num 待投产数→前端取作计划数，§2.3）。"""
    cid = body.get("customer_contract_id")
    contract = _contract_of(cid=cid) if cid not in (None, "") else None
    rows = list((contract or {}).get("item_list") or [])
    res = _page_rows(rows, body)
    out = []
    for it in res["data"]["list"]:
        d = dict(it)
        num = _f(d.get("num"))
        out_num = _f(d.get("stock_out_num"))
        produced = _f(d.get("put_into_produce_num"))
        d["need_produce_num"] = _num8(max(0.0, num - produced))
        d["stock_out_status"] = 1 if out_num < num else 2          # 1 待出库（red）
        d["stock_out_status_name"] = "待出库" if out_num < num else "已出库"
        d["stock_out_status_color"] = "red" if out_num < num else "green"
        d["order_number"] = d.get("order_number") or (rows.index(it) + 1)
        d.setdefault("goods", _ref_goods(d.get("goods_id")) or {})
        out.append(d)
    res["data"]["list"] = out
    return res

# ---------------- 种子：produce_bills 的 project_code/project_name 聚合（首载一次） ----------------


def _seed():
    if _projects().rows:
        return
    bills = core.Store("produce_bills").all()
    agg = {}
    for b in bills:
        code = str(b.get("project_code") or "").strip()
        if not code:
            continue
        a = agg.setdefault(code, {"code": code, "names": [], "created": [],
                                  "contract_code": None, "user": None})
        nm = str(b.get("project_name") or "").strip()
        if nm and nm not in a["names"]:
            a["names"].append(nm)
        if b.get("created_at"):
            a["created"].append(str(b["created_at"]))
        if b.get("contract_code"):
            a["contract_code"] = b["contract_code"]
        if b.get("create_user_name"):
            a["user"] = b["create_user_name"]
    for code in sorted(agg, key=lambda c: (min(agg[c]["created"]) if agg[c]["created"] else "")):
        a = agg[code]
        contract = _contract_of(code=a["contract_code"]) if a["contract_code"] else None
        cu = _user_by_name(a["user"])
        _projects().add({
            "enterprise_id": 16807,
            "code": code,
            "name": a["names"][0] if a["names"] else None,
            "type": 2 if contract else 1,
            "customer_contract_id": (contract or {}).get("id"),
            "created_at": min(a["created"]) if a["created"] else core.now(),
            "updated_at": max(a["created"]) if a["created"] else core.now(),
            "create_user": cu, "update_user": cu,
            "create_user_id": (cu or {}).get("id"), "update_user_id": (cu or {}).get("id"),
            "_seed": 1,
        })


_seed()

ROUTES = {
    # produce_project（deep\22 §1.7 接口清单；打印/导出族不在本模块）
    ("produce_project", "list"): _list,
    ("produce_project", "list_by_filter_view"): _list_by_filter_view,
    ("produce_project", "status_count"): _status_count,
    ("produce_project", "detail"): _detail,
    ("produce_project", "add"): _add,
    ("produce_project", "add_async"): _add_async,
    ("produce_project", "edit"): _edit,
    ("produce_project", "edit_async"): _edit_async,
    ("produce_project", "del"): _del,
    ("produce_project", "goods_selector"): _goods_selector,
    ("produce_project", "contract_item_goods_selector"): _contract_item_goods_selector,
    # 创建记录（全局 CreateLog 5 秒轮询，deep\22 §2.4）
    ("import_record", "list"): _record_list,
}
# production_project 别名：复刻前端页面路由同名（/production_project/list），便于前端直连不误改契约
for (_m, _a), _h in list(ROUTES.items()):
    if _m == "produce_project":
        ROUTES.setdefault(("production_project", _a), _h)
