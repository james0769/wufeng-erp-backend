# -*- coding: utf-8 -*-
"""B? 溯源码管理（product_traceability 页）+ single（产品）+ 双码池（demo 级复刻）。

依据：deep\\25-溯源码与单件管理.md（三套码体系 / 双码池各 1000001 起号 / 绑码三方式）
实测样例：_gen_tmp\\kgd_reverse\\_beaut_m17\\{tc_list,tc_detail,tc_hist,single_detail,
single_ptrace,single_log}.json（字段名/嵌套结构逐字对齐）。

关键口径（deep\\25）：
- 三套码：商品二维码（goods 只读 #/g?qr_id={goods_id}）/ 产品码池（#/s?qr_id={qr_id}）/
  溯源码池（#/trace?code={code}&enterprise_id={eid}）；消费端域名 single.kgd.ltd。
- 产品码池与溯源码池两张独立表、各自从 1000001 起号（本模块两 Store 独立计数）。
- 绑码三方式：①按码绑定 bind_code / bind_traceability_code_by_code；
  ②自动取号 batch_set_code（服务端从池取号）；③批量关联导入 traceability_code/import
  （模板说明第 4 条：自动解除旧绑再绑新 = 改绑语义）。random_bind_code 顺序取空闲码（推断，
  Web 无调用方）。
- 列表族响应为扁平数组 {success,count,data:[...]}（tc_list/tc_hist 实测，客户端分页）；
  single/log 为 {success,data:{count,data:[...]}}（single_log 实测，注意 pageNo/pageSize）。
- 溯源码行的商品/加工单信息来自 report_work_relation → report_work_record →
  produce_bill_craft → produce_bill 链（deep\\25 §2.3），种子关联真实 produce_bills
  （含 bill_qr_code 加工单二维码列）。
- 生成历史文件名规则 {生成时刻unix}({起始码}-{结束码}).{xlsx|zip}（deep\\25 §2.7）。
- 未实现（保持 404 / 无证据不臆造）：single_qr_code 打印族（batch_template_print 等）、
  single/dispatch2contract、single/dispatch2stock、single/get_single_material_import_data、
  single 报表族（produce_statistical 等）——原站有定义但零 Web 调用方且参数未捕获（deep\\25 §9）。
"""
import hashlib
import time
import zipfile
from io import BytesIO

import core

QR_HOST = "https://single.kgd.ltd"                       # 消费端域名（deep\25 §0）
ENTERPRISE_NAME = "山东德萨快工单王曼曼"                   # 与 auth.user_home 同源，QR 公司简称默认值
MAX_GEN = 500                                            # 每次最多生成 500 个（deep\25 §2.5）
MAX_IMPORT = 5000                                        # 单次导入 ≤5000（模板说明第 5 条）
BOOL = {1: True, 2: False}

# ---------------- 存取（单例，避免多实例 flush 互相覆盖） ----------------
_TC = _QR = _SG = _REL = _TCH = _QRH = _LOG = _BILLS = None

def _tc():
    global _TC
    if _TC is None:
        _TC = core.Store("traceability_codes")
    return _TC

def _qr():
    global _QR
    if _QR is None:
        _QR = core.Store("single_qr_codes")
    return _QR

def _sg():
    global _SG
    if _SG is None:
        _SG = core.Store("singles")
    return _SG

def _rel():
    global _REL
    if _REL is None:
        _REL = core.Store("report_work_relations")
    return _REL

def _tch():
    global _TCH
    if _TCH is None:
        _TCH = core.Store("traceability_code_generate_histories")
    return _TCH

def _qrh():
    global _QRH
    if _QRH is None:
        _QRH = core.Store("qr_generate_histories")
    return _QRH

def _log():
    global _LOG
    if _LOG is None:
        _LOG = core.Store("single_logs")
    return _LOG

def _bills():
    global _BILLS
    if _BILLS is None:
        _BILLS = core.Store("produce_bills")
    return _BILLS

def _user(uid):
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name") or str(uid),
                    "type_name": "普通用户", "has_password": False, "qr_code_url": "%s/#/u?qr_id=%s" % (QR_HOST, uid)}
    try:
        from modules.auth import DEMO_USER
        if str(DEMO_USER.get("id")) == str(uid):
            return {"id": DEMO_USER["id"], "real_name": DEMO_USER["real_name"]}
    except Exception:
        pass
    return {"id": uid, "real_name": "用户%s" % uid}

# ---------------- 码号规则：两池各自 1000001 起号、纯数字递增（deep\25 §0） ----------------
def _next_pool_code(store):
    mx = 1000000
    for r in store.all():
        c = str(r.get("code") or "")
        if c.isdigit():
            mx = max(mx, int(c))
    return mx + 1

def _tc_url(code, ent_id):
    return "%s/#/trace?code=%s&enterprise_id=%s" % (QR_HOST, code, ent_id)

def _qr_url(qr_id):
    return "%s/#/s?qr_id=%s" % (QR_HOST, qr_id)

def _by_code(store, code):
    code = str(code or "").strip()
    for r in store.all():
        if str(r.get("code") or "") == code:
            return r
    return None

def _rels_of(tc_id):
    """该码的报工关联（按工序完成时间倒序，对齐 tc_detail.json 实测行序）。"""
    rows = [r for r in _rel().all() if str(r.get("traceability_code_id")) == str(tc_id)]
    return sorted(rows, key=lambda r: str(r.get("pub_craft_end_time") or ""), reverse=True)

def _flat(rows):
    """原站列表族响应：扁平数组 + count（tc_list/tc_hist 实测，非 {list,total} 分页壳）。"""
    return {"success": True, "count": len(rows), "data": rows}

def _bool_filter(body, key, hit):
    """has_bind 族过滤：1=是/2=否/all 全部（BOOL 枚举）。"""
    v = body.get(key)
    if v in (None, "", "all"):
        return True
    return (str(v) == "1") == bool(hit)

# ---------------- 产品日志（/single/log，busi_type=1310，实测 single_log.json） ----------------
def _add_log(sg, operation_name, content, session):
    row = _log().add({
        "enterprise_id": sg.get("enterprise_id") or session.get("enterprise_id"),
        "customer_id": sg.get("customer_id"),
        "operator_id": session.get("id"),
        "content": content,
        "is_private": 2,
        "busi_type": 1310,
        "busi_id": sg["id"],
        "operation_type": None, "operation": None, "agent_id": None,
        "content_raw": None, "channel": None,
        "operation_name": operation_name, "color": None,
        "attachments": [],
    })
    return row

def _decor_log(r, session):
    sg = _sg().get(r.get("busi_id"))
    cust = (sg or {}).get("customer") or {}
    d = dict(r)
    d.setdefault("fieldValueList", [])
    d["operator"] = _user(r.get("operator_id"))
    d["customer"] = {"id": cust.get("id"), "name": cust.get("name")} if cust.get("id") else None
    return d

# ---------------- 绑定状态机：一码一产品、先解旧再绑新（deep\25 §6） ----------------
def _bind_tc(sg, tc, session):
    """绑溯源码（改绑语义：码与产品各自的旧绑定互解，模板说明第 4 条）。"""
    if sg.get("traceability_code_id"):
        old = _tc().get(sg["traceability_code_id"])
        if old is not None and str(old.get("id")) != str(tc.get("id")):
            old["single_id"] = None
    if tc.get("single_id") and str(tc["single_id"]) != str(sg.get("id")):
        oldsg = _sg().get(tc["single_id"])
        if oldsg is not None:
            oldsg["traceability_code_id"] = None
    sg["traceability_code_id"] = tc["id"]
    tc["single_id"] = sg["id"]
    _add_log(sg, "绑定溯源码", "绑定溯源码,编号：%s" % tc.get("code"), session)

def _unbind_tc(sg, session):
    tc = _tc().get(sg.get("traceability_code_id")) if sg.get("traceability_code_id") else None
    sg["traceability_code_id"] = None
    if tc is not None:
        tc["single_id"] = None
        _add_log(sg, "解绑溯源码", "解绑溯源码,编号：%s" % tc.get("code"), session)

def _bind_qr(sg, qr, session):
    if sg.get("qr_code_id"):
        old = _qr().get(sg["qr_code_id"])
        if old is not None and str(old.get("id")) != str(qr.get("id")):
            old["single_id"] = None
    if qr.get("single_id") and str(qr["single_id"]) != str(sg.get("id")):
        oldsg = _sg().get(qr["single_id"])
        if oldsg is not None:
            oldsg["qr_code_id"] = None
    sg["qr_code_id"] = qr["id"]
    qr["single_id"] = sg["id"]
    _add_log(sg, "绑定产品码", "绑定产品码,编号：%s" % qr.get("code"), session)

def _unbind_qr(sg, session):
    qr = _qr().get(sg.get("qr_code_id")) if sg.get("qr_code_id") else None
    sg["qr_code_id"] = None
    if qr is not None:
        qr["single_id"] = None
        _add_log(sg, "解绑产品码", "解绑产品码,编号：%s" % qr.get("code"), session)

def _alloc_pool_code(store, ent_id):
    """取一个空闲码：优先池内未绑定且号最小；池空则新起号（batch_set_code 自动生成语义）。"""
    free = sorted([r for r in store.all() if not r.get("single_id")], key=lambda r: str(r.get("code")))
    if free:
        return free[0]
    return store.add({"enterprise_id": ent_id, "code": str(_next_pool_code(store))})

# ---------------- 报工关联链装饰（traceability_code → relation → record → craft → bill） ----------------
def _bill_mini(bill_id):
    """produce_bill 精简视图（字段名对齐 tc_list.json 实测；B11 种子行 goods 为扁平列）。"""
    b = _bills().get(bill_id)
    if not b:
        return None
    g = b.get("goods") or {}
    goods = {
        "id": b.get("goods_id") or g.get("id"),
        "name": g.get("name") or b.get("goods_name"),
        "code": g.get("code") or b.get("goods_code"),
        "standard": g.get("standard") or b.get("goods_standard"),
        "avatars": g.get("avatars"),
        "source_name": None, "produce_type_name": "", "source_tag_class": "", "qr_code_url": None,
    }
    return {
        "id": b.get("id"), "goods_id": goods["id"], "code": b.get("code"),
        "status_name": "", "produce_type_name": "", "produce_type_color": "", "status_tag_class": "",
        "create_mode_name": "", "create_mode_color": "", "requisition_status_name": "",
        "requisition_status_color": "", "requisition_status_tag_class": "",
        "work_type_name": "", "work_type_color": "", "work_type_tag_class": "",
        "stock_in_percent": 0, "delivery_countdown": "", "goods": goods,
        # 种子关联佐证：加工单自带二维码信息列（bill_qr_code，导出表 41 列之一）
        "bill_qr_code": b.get("bill_qr_code"),
    }

def _decor_rel(rel):
    """relation → tc_list.json 实测嵌套结构（多余空字段照抄原站占位）。"""
    pb = _bill_mini(rel.get("produce_bill_id"))
    craft = {
        "id": rel.get("produce_bill_craft_id"),
        "produce_bill_id": (pb or {}).get("id"),
        "status_name": "", "status_tag_class": "", "status_color": "",
        "total_use_time": None, "price_mode_name": "", "price_mode_color": "", "price_mode_tag_class": "",
        "create_type_name": "", "create_type_tag_class": "", "wait_produce_num": "0",
        "quality_type_name": "", "quality_type_tag_class": "",
        "produce_bill": pb, "report_waste_list": rel.get("report_waste_list") or [],
    }
    rec = {
        "id": rel.get("report_work_record_id"),
        "produce_bill_craft_id": rel.get("produce_bill_craft_id"),
        "report_num": rel.get("report_num"), "valid_num": rel.get("valid_num"),
        "waste_num": rel.get("waste_num"),
        "quality_status_name": rel.get("quality_status_name"),
        "start_time_format": None, "end_time_format": None, "report_time_format": None,
        "quality_status_tag_class": "", "settle_status_tag_class": "", "settle_status_name": "",
        "type_tag_class": "", "type_name": "", "price_mode_name": "", "price_mode_color": "",
        "price_mode_tag_class": "", "produce_bill_craft": craft,
    }
    return {"id": rel.get("id"), "traceability_code_id": rel.get("traceability_code_id"),
            "report_work_record_id": rel.get("report_work_record_id"), "report_work_record": rec}

def _rel_flat(rel):
    """生产信息弹窗/produce_trace 的 12 列扁平记录（tc_detail.json 实测字段逐字）。"""
    return {
        "pub_craft_name": rel.get("pub_craft_name"),
        "report_user_names": rel.get("report_user_names"),
        "create_user_names": rel.get("create_user_names"),
        "quality_user_names": rel.get("quality_user_names"),
        "pub_craft_end_time": rel.get("pub_craft_end_time"),
        "report_num": rel.get("report_num"),
        "valid_num": rel.get("valid_num"),
        "waste_num": rel.get("waste_num"),
        "report_waste_list": rel.get("report_waste_list") or [],
        "remark": rel.get("remark"),
        "attachment_list": rel.get("attachment_list") or [],
    }

# ---------------- single（产品）装饰 ----------------
def _guarantee(gd):
    gd = str(gd or "")[:10]
    if not gd:
        return None, None
    return (1, "保内") if gd >= time.strftime("%Y-%m-%d") else (2, "保外")

def _sg_mini_tc(sg):
    """tc_list.json 里 single 节点的字段集（实测原文）。"""
    g = sg.get("goods") or {}
    return {
        "id": sg.get("id"), "code": sg.get("code"), "goods_id": sg.get("goods_id"),
        "traceability_code_id": sg.get("traceability_code_id"),
        "status_name": "", "status_color": "", "code_url": None,
        "in_guarantee": None, "in_guarantee_name": None,
        "goods": {"id": g.get("id"), "name": g.get("name"), "source_name": g.get("source_name"),
                  "produce_type_name": "", "source_tag_class": "", "qr_code_url": None},
    }

def _decor_sg(sg, detail=False):
    g = sg.get("goods") or {}
    tc = _tc().get(sg["traceability_code_id"]) if sg.get("traceability_code_id") else None
    qr = _qr().get(sg["qr_code_id"]) if sg.get("qr_code_id") else None
    ig, ign = _guarantee(sg.get("guarantee_date"))
    d = dict(sg)
    d.setdefault("fieldValueList", [])
    d["goods"] = g or None
    d["customer"] = sg.get("customer")
    d["qr_code"] = ({"id": qr["id"], "enterprise_id": qr.get("enterprise_id"), "code": qr["code"],
                     "del": qr.get("del", 2), "created_at": qr.get("created_at"),
                     "updated_at": qr.get("updated_at"), "code_url": _qr_url(qr["id"])}
                    if qr else None)
    d["traceability_code"] = ({"id": tc["id"], "enterprise_id": tc.get("enterprise_id"),
                               "code": tc["code"], "code_url": _tc_url(tc["code"], sg.get("enterprise_id"))}
                              if tc else None)
    d["resp_user"] = _user(sg.get("resp_user_id"))
    d["create_user"] = _user(sg.get("create_user_id"))
    d["in_guarantee"], d["in_guarantee_name"] = ig, ign
    d["status_name"], d["status_color"] = "", ""
    d["code_url"] = _qr_url(qr["id"]) if qr else None
    d["editAccessible"] = True
    d["delAccessible"] = True
    for k in ("work_order_count", "not_finish_work_order_count", "feedback_count",
              "send_repair_feedback_count", "inspection_plan_count", "point_inspection_plan_count",
              "all_node_count", "finish_node_count"):
        d.setdefault(k, 0)
    d.setdefault("finish_node_percent", 0)
    if detail:
        d["enterprise"] = {"id": sg.get("enterprise_id"), "name": ENTERPRISE_NAME}
    return d

# =================================================================
# traceability_code（溯源码管理页，deep\25 §2 / §7.2）
# =================================================================
def _tc_filter(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()

    out = []
    for r in rows:
        sg = _sg().get(r.get("single_id")) if r.get("single_id") else None
        rels = _rels_of(r.get("id"))
        rel = rels[0] if rels else None
        bill = _bill_mini(rel.get("produce_bill_id")) if rel else None
        bg = (bill or {}).get("goods") or {}
        if not _bool_filter(body, "has_bind", sg is not None):
            continue
        gk = s("goods_keyword")   # 占位：商品名/简拼/规格/编号（§2.2）
        if gk and gk not in "%s %s %s" % (bg.get("name") or "", bg.get("code") or "", bg.get("standard") or ""):
            continue
        sk = s("single_keyword")  # 产品名/产品编号
        sname = ((sg or {}).get("goods") or {}).get("name") or ""
        if sk and sk not in "%s %s" % (sname, (sg or {}).get("code") or ""):
            continue
        if s("goods_name") and s("goods_name") not in str(bg.get("name") or ""):
            continue
        if s("goods_code") and s("goods_code") not in str(bg.get("code") or ""):
            continue
        if s("goods_standard") and s("goods_standard") not in str(bg.get("standard") or ""):
            continue
        out.append(r)
    return out

def _tc_decor(r):
    sg = _sg().get(r.get("single_id")) if r.get("single_id") else None
    rels = _rels_of(r.get("id"))
    return {
        "id": r.get("id"), "enterprise_id": r.get("enterprise_id"), "code": r.get("code"),
        "created_at": r.get("created_at"), "fieldValueList": [],
        "code_url": _tc_url(r.get("code"), r.get("enterprise_id")),
        "single": _sg_mini_tc(sg) if sg else None,
        "report_work_relation": _decor_rel(rels[0]) if rels else None,
    }

def _tc_list(body, session):
    rows = _tc_filter(_tc().all(), body)
    return _flat([_tc_decor(r) for r in rows])

def _tc_detail(body, session):
    """生产信息弹窗：返回该码的报工记录数组（tc_detail.json 实测）。"""
    r = _tc().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    return core.ok([_rel_flat(x) for x in _rels_of(r["id"])])

# ---------------- 生成 / 下载（blob 二进制直通，deep\25 §2.5/§2.6/§7.4） ----------------
def _codes_xlsx(items):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "二维码导出"
    ws.append(["编号"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for it in items:
        ws.append([it["code"]])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()

def _codes_zip(items):
    """img 模式：每码一张 PNG 打包 zip（demo：无 qrcode 库，占位图渲染编号/附加信息文字）。"""
    from PIL import Image, ImageDraw, ImageFont
    bio = BytesIO()
    zf = zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED)
    font = ImageFont.load_default()
    for it in items:
        img = Image.new("RGB", (360, 480), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([8, 8, 352, 472], outline="black")
        d.text((20, 24), "生产溯源二维码" if it.get("kind") == "tc" else "产品二维码", fill="black", font=font)
        y = 90
        for t in it["texts"]:
            d.text((20, y), str(t), fill="black", font=font)
            y += 26
        d.text((20, 440), it.get("url") or "", fill="gray", font=font)
        b = BytesIO()
        img.save(b, "PNG")
        zf.writestr("%s.png" % it["code"], b.getvalue())
    zf.close()
    return bio.getvalue()

def _qr_items(rows, body, session, kind):
    """按 show_code/show_goods_name/show_enterprise_name 组装图片附加信息（§2.5/§2.6）。"""
    def flag(k):
        return str(body.get(k) or "1") == "1"   # 前端默认勾选 show_code / show_enterprise_name
    ent_name = str(body.get("enterprise_name") or "").strip() or ENTERPRISE_NAME
    items = []
    for r in rows:
        sg = _sg().get(r.get("single_id")) if r.get("single_id") else None
        texts = []
        if flag("show_code"):
            texts.append(str(r.get("code")))
        if flag("show_goods_name") and sg:
            texts.append(((sg.get("goods") or {}).get("name") or ""))
        if flag("show_enterprise_name"):
            texts.append(ent_name)
        url = _tc_url(r.get("code"), session.get("enterprise_id")) if kind == "tc" else _qr_url(r.get("id"))
        items.append({"code": r.get("code"), "texts": [t for t in texts if t], "url": url, "kind": kind})
    return items

def _gen_binary(items, body):
    if str(body.get("download_type") or "excel") == "img":
        return {"__binary__": _codes_zip(items), "__filename__": "二维码导出.zip"}
    return {"__binary__": _codes_xlsx(items), "__filename__": "二维码导出.xlsx"}

def _hist_add(store, session, start, end, amount, ext):
    fname = "%d(%s-%s).%s" % (int(time.time()), start, end, ext)
    return store.add({
        "enterprise_id": session.get("enterprise_id"), "user_id": session.get("id"),
        "file_name": fname,
        "file_path": "https://up.sh-t.com.cn/kgd-demo/%s.%s" % (hashlib.md5(fname.encode()).hexdigest(), ext),
        "amount": amount, "code_start": start, "code_end": end, "download_type": ext,
        "operator": _user(session.get("id")),
    })

def _generate(body, session, store, hist, kind):
    """生成并下载：1~500 个/次；起号=池内最大号+1（首号 1000001）；落码池 + 生成历史。"""
    raw = body.get("count")
    if raw in (None, ""):
        return core.fail("请输入生成数量")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return core.fail("请输入生成数量")
    if count < 1 or count > MAX_GEN:
        return core.fail("每次最多生成%d个二维码" % MAX_GEN)
    start = _next_pool_code(store)
    for i in range(count):
        store.add({"enterprise_id": session.get("enterprise_id"), "code": str(start + i)})
    end = start + count - 1
    ext = "zip" if str(body.get("download_type") or "excel") == "img" else "xlsx"
    _hist_add(hist, session, str(start), str(end), count, ext)
    rows = sorted([r for r in store.all()
                   if str(r.get("code")).isdigit() and start <= int(r["code"]) <= end],
                  key=lambda r: int(r["code"]))
    return _gen_binary(_qr_items(rows, body, session, kind), body)

def _batch_download(body, session, store, kind):
    ids = body.get("qr_ids") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x.strip()]
    rows = [store.get(i) for i in ids]
    rows = [r for r in rows if r]
    if not rows:
        return core.fail("请选择二维码")
    return _gen_binary(_qr_items(rows, body, session, kind), body)

def _tc_generate(body, session):
    return _generate(body, session, _tc(), _tch(), "tc")

def _tc_batch_download(body, session):
    return _batch_download(body, session, _tc(), "tc")

# ---------------- 导入（批量关联，自动改绑，deep\25 §2.9） ----------------
def _parse_rows(body, titles):
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        return None, core.fail("参数校验错误")
    if len(rows) - 1 > MAX_IMPORT:
        return None, core.fail("单次最多导入数目为%d条，如需要，请分批导入。" % MAX_IMPORT)
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    idx = {}
    for i, t in enumerate(header):
        if t and t not in idx:
            idx[t] = i
    if not any(t in idx for t in titles):
        return None, core.fail("参数校验错误")
    return (rows, idx), None

def _tc_import(body, session):
    parsed, err = _parse_rows(body, ("产品编号", "溯源码编号"))
    if err:
        return err
    rows, idx = parsed
    i_sgc, i_tcc = idx.get("产品编号"), idx.get("溯源码编号")
    errors = []
    for n, r in enumerate(rows[1:], start=2):
        cells = ["" if c is None else str(c).strip() for c in r]
        if not any(cells):
            continue
        sgc = cells[i_sgc] if i_sgc is not None and i_sgc < len(cells) else ""
        tcc = cells[i_tcc] if i_tcc is not None and i_tcc < len(cells) else ""
        if not sgc:
            errors.append(["第%d行：%s" % (n, "产品编号不能为空")] + cells)
            continue
        if not tcc:
            errors.append(["第%d行：%s" % (n, "溯源码编号不能为空")] + cells)
            continue
        sg = _by_code(_sg(), sgc)
        tc = _by_code(_tc(), tcc)
        if sg is None:
            errors.append(["第%d行：%s" % (n, "产品不存在")] + cells)
            continue
        if tc is None:
            errors.append(["第%d行：%s" % (n, "溯源码不存在")] + cells)
            continue
        _bind_tc(sg, tc, session)   # 自动解旧绑新（导入说明第 4 条）
    _sg().flush()
    _tc().flush()
    return core.ok(errors)          # 原站语义：data=失败行集合（首元素=错误原因）

TC_TPL_NOTES = [
    "欢迎使用溯源码批量关联导入模板，请注意以下事项：",
    "1、导入之后会自动建立产品与溯源码之间的关联关系，之后扫码可见溯源信息；",
    "2、注意0开头的编号请使用字符串方式输入，比如 00008；",
    "3、背景色红色标记的字段为必填项；",
    "4、如果当前文件中的产品或者溯源码已经绑定过，则系统将会自动解除之前的绑定关系，并且绑定您导入的溯源码和产品关系。",
    "5、单次最多导入数目为5000条，如需要，请分批导入。",
]

def _tpl_binary(sheet_title, headers, notes):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws2 = wb.create_sheet("导入说明")
    for i, t in enumerate(notes, start=1):
        ws2.cell(row=i, column=1, value=t)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()

def _tc_tpl(body, session):
    return {"__binary__": _tpl_binary("Worksheet", ["产品编号", "溯源码编号"], TC_TPL_NOTES),
            "__filename__": "溯源码导入模板.xlsx"}

# ---------------- 删除 ----------------
def _tc_del(body, session):
    r = _tc().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if r.get("single_id"):   # 已绑码一并解除关联（服务端行为原站未实测，demo 取安全口径）
        sg = _sg().get(r["single_id"])
        if sg is not None:
            _unbind_tc(sg, session)
            _sg().flush()
    _tc().remove(r["id"])
    return core.ok()

def _tc_batch_del(body, session):
    ids = body.get("ids") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x.strip()]
    if not ids:
        return core.fail("请选择溯源码")
    for i in ids:
        _tc_del(dict(body, id=i), session)
    return core.ok()

# =================================================================
# traceability_code_generate_history / qr_generate_history（§2.7 / §4）
# =================================================================
def _hist_list(store, body, session):
    def s(k):
        return str(body.get(k) or "").strip()
    rows = [r for r in store.all() if not s("file_name") or s("file_name") in str(r.get("file_name") or "")]
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    out = []
    for r in rows:
        d = dict(r)
        d.setdefault("fieldValueList", [])
        if not d.get("operator"):
            d["operator"] = _user(d.get("user_id"))
        out.append(d)
    return _flat(out)

def _hist_download(store, kind, body, session):
    """原站为 GET 直链（§7.4 签名四件套）；demo 架构仅 POST，按历史行的码段重产文件。"""
    r = store.get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    try:
        start, end = int(r.get("code_start")), int(r.get("code_end"))
    except (TypeError, ValueError):
        return core.fail("数据不存在")
    pool = {str(x.get("code")): x for x in (kind == "tc" and _tc() or _qr()).all()}
    items = []
    for n in range(start, end + 1):   # 号段内已删的码也占位输出（生成后删除不回滚号段，deep\25 §9-7）
        code = str(n)
        x = pool.get(code)
        items.append({"code": code, "texts": [code], "kind": kind,
                      "url": _tc_url(code, session.get("enterprise_id")) if kind == "tc"
                      else _qr_url((x or {}).get("id", n))})
    if r.get("download_type") == "zip":
        return {"__binary__": _codes_zip(items), "__filename__": str(r.get("file_name") or "二维码导出.zip")}
    return {"__binary__": _codes_xlsx(items), "__filename__": str(r.get("file_name") or "二维码导出.xlsx")}

def _tch_list(body, session):
    return _hist_list(_tch(), body, session)

def _tch_download(body, session):
    return _hist_download(_tch(), "tc", body, session)

def _qrh_list(body, session):
    return _hist_list(_qrh(), body, session)

def _qrh_download(body, session):
    return _hist_download(_qrh(), "qr", body, session)

# =================================================================
# single_qr_code（产品二维码页，deep\25 §4 / §7.3）
# =================================================================
def _qr_decor(r):
    sg = _sg().get(r.get("single_id")) if r.get("single_id") else None
    single = None
    if sg:
        g = sg.get("goods") or {}
        single = {"id": sg.get("id"), "code": sg.get("code"),
                  "customer": {"id": (sg.get("customer") or {}).get("id"),
                               "name": (sg.get("customer") or {}).get("name")},
                  "product": {"id": g.get("id"), "name": g.get("name")}}
    return {"id": r.get("id"), "enterprise_id": r.get("enterprise_id"), "code": r.get("code"),
            "created_at": r.get("created_at"), "fieldValueList": [],
            "code_url": _qr_url(r.get("id")), "single": single}

def _qr_filter(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()
    out = []
    for r in rows:
        sg = _sg().get(r.get("single_id")) if r.get("single_id") else None
        if not _bool_filter(body, "has_bind", sg is not None):
            continue
        kw = s("keyword")   # 支持二维码编号查询（§4）
        if kw and kw not in str(r.get("code") or ""):
            continue
        if s("qrcode_number") and s("qrcode_number") not in str(r.get("code") or ""):
            continue
        if s("single_code") and (not sg or s("single_code") not in str(sg.get("code") or "")):
            continue
        if s("customer_name"):
            cn = ((sg or {}).get("customer") or {}).get("name") or ""
            if s("customer_name") not in cn:
                continue
        out.append(r)
    return out

def _qr_list(body, session):
    rows = _qr_filter(_qr().all(), body)
    return _flat([_qr_decor(r) for r in rows])

def _qr_generate(body, session):
    return _generate(body, session, _qr(), _qrh(), "qr")

def _qr_batch_download(body, session):
    return _batch_download(body, session, _qr(), "qr")

QR_TPL_NOTES = [
    "欢迎使用产品二维码批量关联导入模板，请注意以下事项：",
    "1、导入之后会自动建立产品与产品二维码之间的关联关系；",
    "2、注意0开头的编号请使用字符串方式输入，比如 00008；",
    "3、背景色红色标记的字段为必填项；",
    "4、如果当前文件中的产品或者产品二维码已经绑定过，则系统将会自动解除之前的绑定关系，并且绑定您导入的产品二维码和产品关系。",
    "5、单次最多导入数目为5000条，如需要，请分批导入。",
]

def _qr_tpl(body, session):
    # v=6 模板未实测（原站该页权限不足），列名按 v=5 溯源码模板同构推断
    return {"__binary__": _tpl_binary("Worksheet", ["产品编号", "二维码编号"], QR_TPL_NOTES),
            "__filename__": "产品二维码导入模板.xlsx"}

def _qr_import(body, session):
    parsed, err = _parse_rows(body, ("产品编号", "二维码编号"))
    if err:
        return err
    rows, idx = parsed
    i_sgc, i_qrc = idx.get("产品编号"), idx.get("二维码编号")
    errors = []
    for n, r in enumerate(rows[1:], start=2):
        cells = ["" if c is None else str(c).strip() for c in r]
        if not any(cells):
            continue
        sgc = cells[i_sgc] if i_sgc is not None and i_sgc < len(cells) else ""
        qrc = cells[i_qrc] if i_qrc is not None and i_qrc < len(cells) else ""
        sg = _by_code(_sg(), sgc) if sgc else None
        qr = _by_code(_qr(), qrc) if qrc else None
        if not sgc or sg is None:
            errors.append(["第%d行：%s" % (n, "产品不存在" if sgc else "产品编号不能为空")] + cells)
            continue
        if not qrc or qr is None:
            errors.append(["第%d行：%s" % (n, "二维码不存在" if qrc else "二维码编号不能为空")] + cells)
            continue
        _bind_qr(sg, qr, session)
    _sg().flush()
    _qr().flush()
    return core.ok(errors)

def _qr_del(body, session):
    r = _qr().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if r.get("single_id"):   # 删除条件=未绑定（§4 行操作：删除之后此二维码将不可用）
        return core.fail("该二维码已绑定产品，不能删除")
    _qr().remove(r["id"])
    return core.ok()

def _qr_batch_del(body, session):
    ids = body.get("ids") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x.strip()]
    if not ids:
        return core.fail("请选择二维码")
    for i in ids:            # 前端已滤已绑定行；服务端对已绑行跳过（failTipText=已绑定）
        r = _qr().get(i)
        if r and not r.get("single_id"):
            _qr().remove(r["id"])
    return core.ok()

def _qr_add(body, session):
    code = str(body.get("code") or "").strip() or str(_next_pool_code(_qr()))
    if _by_code(_qr(), code):
        return core.fail("二维码编号已存在")
    row = _qr().add({"enterprise_id": session.get("enterprise_id"), "code": code})
    return core.ok(_qr_decor(row))

def _qr_edit(body, session):
    r = _qr().get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    if r.get("single_id"):
        return core.fail("已绑定的二维码不能修改")
    code = str(body.get("code") or "").strip()
    if code:
        if code == "0":
            return core.fail("请输入正确的二维码编号")
        if _by_code(_qr(), code):
            return core.fail("二维码编号已存在")
        r["code"] = code
        _qr().flush()
    return core.ok()

# =================================================================
# single（产品管理页/详情，deep\25 §3/§5/§7.1）
# =================================================================
def _next_single_code():
    """产品编号：CP+YYYYMM+3位（实测样例 CP202504001；is_automatic_generation_single_code=true 自动生成）。"""
    import re
    stamp = time.strftime("%Y%m")
    mx = 0
    for r in _sg().all():
        m = re.match(r"^CP(\d{6})(\d{3})$", str(r.get("code") or ""))
        if m and m.group(1) == stamp:
            mx = max(mx, int(m.group(2)))
    return "CP%s%03d" % (stamp, mx + 1)

def _sg_filter(rows, body):
    def s(k):
        return str(body.get(k) or "").strip()
    out = []
    for r in rows:
        qr = _qr().get(r.get("qr_code_id")) if r.get("qr_code_id") else None
        tc = _tc().get(r.get("traceability_code_id")) if r.get("traceability_code_id") else None
        if not _bool_filter(body, "has_bind", qr is not None):
            continue
        if not _bool_filter(body, "has_bind_traceability_code", tc is not None):
            continue
        kw = s("keyword")   # 支持产品名/产品编号/二维码编号查询（§3.1 tooltip）
        if kw:
            hay = "%s %s %s" % ((r.get("goods") or {}).get("name") or "", r.get("code") or "",
                                (qr or {}).get("code") or "")
            if kw not in hay:
                continue
        if s("code") and s("code") not in str(r.get("code") or ""):
            continue
        if s("name") and s("name") not in str((r.get("goods") or {}).get("name") or ""):
            continue
        if s("qrcode_number") and (not qr or s("qrcode_number") not in str(qr.get("code") or "")):
            continue
        if s("traceability_code") and (not tc or s("traceability_code") not in str(tc.get("code") or "")):
            continue
        if s("customer_name") and s("customer_name") not in str((r.get("customer") or {}).get("name") or ""):
            continue
        if s("resp_user_name"):
            rn = (_user(r.get("resp_user_id")).get("real_name") or "")
            if s("resp_user_name") not in rn:
                continue
        if s("created_at_start") and str(r.get("created_at") or "") < s("created_at_start"):
            continue
        if s("created_at_end") and str(r.get("created_at") or "") > s("created_at_end") + " 23:59:59":
            continue
        out.append(r)
    return out

def _single_list(body, session):
    rows = _sg_filter(_sg().all(), body)
    return _flat([_decor_sg(r) for r in rows])

def _single_detail(body, session):
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    return core.ok(_decor_sg(sg, detail=True))

def _find_customer(cid):
    for c in core.Store("customers").all():
        if str(c.get("id")) == str(cid or ""):
            return {"id": c.get("id"), "name": c.get("name"), "code": c.get("code")}
    return None

def _find_goods(gid):
    for g in core.Store("goods").all():
        if str(g.get("id")) == str(gid or ""):
            return {"id": g.get("id"), "name": g.get("name"), "code": g.get("code"),
                    "standard": g.get("standard"), "avatars": g.get("avatars"),
                    "profile": g.get("profile"), "qr_code_url": "%s/#/g?qr_id=%s" % (QR_HOST, g.get("id"))}
    return None

def _single_add(body, session):
    cust = _find_customer(body.get("customer_id"))
    if not cust:
        return core.fail("请选择客户")
    goods = _find_goods(body.get("goods_id"))
    if not goods:
        return core.fail("请选择商品")
    code = str(body.get("code") or "").strip() or _next_single_code()
    if _by_code(_sg(), code):
        return core.fail("产品编号已存在")
    sg = _sg().add({
        "enterprise_id": session.get("enterprise_id"),
        "customer_id": cust["id"], "customer": cust,
        "goods_id": goods["id"], "goods": goods,
        "code": code,
        "resp_user_id": body.get("resp_user_id") or session.get("id"),
        "create_user_id": session.get("id"),
        "guarantee_date": body.get("guarantee_date") or None,
        "avatars": body.get("avatars") or goods.get("avatars"),   # 自动复制商品图片（§3.4 说明第 2 条）
        "profile": body.get("profile") if body.get("profile") is not None else goods.get("profile"),
        "category_id": body.get("category_id"), "category_path_names": body.get("category_path_names"),
        "remark": body.get("remark"),
        "qr_code_id": None, "traceability_code_id": None,
        "produce_plan_bill_id": None, "produce_stock_in_bill_id": None,
        "stock_in_price": "0.00", "source": 1,
        "need_final_quality": 2, "has_final_quality": 2, "has_make_over": 2, "has_reject_node": 2,
    })
    return core.ok(_decor_sg(sg, detail=True))

_SG_EDITABLE = ("customer_id", "goods_id", "resp_user_id", "guarantee_date", "avatars",
                "profile", "category_id", "category_path_names", "remark", "code")

def _single_edit(body, session):
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    patch = {k: body[k] for k in _SG_EDITABLE if k in body and body[k] not in (None, "")}
    if "customer_id" in patch:
        cust = _find_customer(patch["customer_id"])
        if not cust:
            return core.fail("请选择客户")
        patch["customer"] = cust
    if "goods_id" in patch:
        goods = _find_goods(patch["goods_id"])
        if not goods:
            return core.fail("请选择商品")
        patch["goods"] = goods
    _sg().update(sg["id"], patch)
    return core.ok()

def _single_del_one(sg, session):
    _unbind_qr(sg, session)      # 删除产品时码解绑回池
    _unbind_tc(sg, session)
    _sg().remove(sg["id"])

def _single_del(body, session):
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    _single_del_one(sg, session)
    _qr().flush()
    _tc().flush()
    return core.ok()

def _single_batch_del(body, session):
    """批量删除 → 统计回执（§3.5：反馈/服务工单/维保计划计数，demo 无关联域恒 0）。"""
    ids = body.get("ids")
    if isinstance(ids, list):
        ids = ",".join(str(x) for x in ids)
    ids = [x for x in str(ids or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择产品")
    total = del_count = 0
    for i in ids:
        sg = _sg().get(i)
        if sg is None:
            continue
        total += 1
        _single_del_one(sg, session)
        del_count += 1
    _qr().flush()
    _tc().flush()
    return core.ok({"total": total, "delCount": del_count,
                    "feedbackRelation": 0, "newFeedbackRelation": 0,
                    "workOrder": 0, "newWorkOrder": 0, "inspectionPlan": 0})

def _single_batch_edit(body, session):
    """批量修改：{ids, 改动字段}——空值字段不提交（§3.5，服务端同样忽略空值）。"""
    ids = body.get("ids")
    if isinstance(ids, list):
        ids = ",".join(str(x) for x in ids)
    ids = [x for x in str(ids or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择产品")
    fields = {k: body[k] for k in _SG_EDITABLE if k in body and body[k] not in (None, "")}
    if "customer_id" in fields:
        cust = _find_customer(fields["customer_id"])
        if not cust:
            return core.fail("请选择客户")
        fields["customer"] = cust
    if "goods_id" in fields:
        goods = _find_goods(fields["goods_id"])
        if not goods:
            return core.fail("请选择商品")
        fields["goods"] = goods
    for i in ids:
        sg = _sg().get(i)
        if sg is not None and fields:
            _sg().update(sg["id"], dict(fields))
    return core.ok()

def _single_trans(body, session):
    """转移给他人：{single_ids:"1,2,3", resp_user_id}（§3.5）。"""
    ids = body.get("single_ids")
    if isinstance(ids, list):
        ids = ",".join(str(x) for x in ids)
    ids = [x for x in str(ids or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择产品")
    if not body.get("resp_user_id"):
        return core.fail("请选择转移给的用户")
    for i in ids:
        sg = _sg().get(i)
        if sg is not None:
            _sg().update(sg["id"], {"resp_user_id": body["resp_user_id"]})
    return core.ok()

# ---------------- 绑码三方式（deep\25 §6.2） ----------------
def _single_bind_code(body, session):
    """①按码绑定-产品码：{single_id, qr_code}；空/"0" 前端拦截，服务端再校验。"""
    sg = _sg().get(body.get("single_id"))
    if not sg:
        return core.fail("数据不存在")
    code = str(body.get("qr_code") or "").strip()
    if not code:
        return core.fail("请输入二维码编号")
    if code == "0":
        return core.fail("请输入正确的二维码编号")
    qr = _by_code(_qr(), code)
    if qr is None:
        return core.fail("二维码不存在")
    _bind_qr(sg, qr, session)
    _sg().flush()
    _qr().flush()
    return core.ok()

def _single_bind_tc(body, session):
    """①按码绑定-溯源码：{single_id, traceability_code_code}（字段名后缀 _code_code）。"""
    sg = _sg().get(body.get("single_id"))
    if not sg:
        return core.fail("数据不存在")
    code = str(body.get("traceability_code_code") or "").strip()
    if not code:
        return core.fail("请输入二维码编号")
    if code == "0":
        return core.fail("请输入正确的二维码编号")
    tc = _by_code(_tc(), code)
    if tc is None:
        return core.fail("溯源码不存在")
    _bind_tc(sg, tc, session)
    _sg().flush()
    _tc().flush()
    return core.ok()

def _single_untie_code(body, session):
    """解绑产品码：{id: single_id}（传产品 id 不是码 id，§2.4）。"""
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    if not sg.get("qr_code_id"):
        return core.fail("未绑定产品")
    _unbind_qr(sg, session)
    _sg().flush()
    _qr().flush()
    return core.ok()

def _single_unbind_tc(body, session):
    """解绑溯源码：{id: single_id}。"""
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    if not sg.get("traceability_code_id"):
        return core.fail("未绑定产品")
    _unbind_tc(sg, session)
    _sg().flush()
    _tc().flush()
    return core.ok()

def _single_batch_set_code(body, session):
    """②自动生成并绑定：{ids}——服务端从产品码池取号（前端仅校验未绑，§3.5）。"""
    ids = body.get("ids")
    if isinstance(ids, list):
        ids = ",".join(str(x) for x in ids)
    ids = [x for x in str(ids or "").split(",") if x.strip()]
    if not ids:
        return core.fail("请选择产品")
    for i in ids:
        sg = _sg().get(i)
        if sg is None or sg.get("qr_code_id"):
            continue
        qr = _alloc_pool_code(_qr(), session.get("enterprise_id"))
        _bind_qr(sg, qr, session)
    _sg().flush()
    _qr().flush()
    return core.ok()

def _single_random_bind(body, session):
    """随机绑定：Web 零调用方（deep\25 §6.2/§9-1）；demo 取顺序最小编号的空闲码（推断口径）。"""
    sg = _sg().get(body.get("id") or body.get("single_id"))
    if not sg:
        return core.fail("数据不存在")
    if sg.get("qr_code_id"):
        return core.fail("该产品已绑定二维码")
    qr = _alloc_pool_code(_qr(), session.get("enterprise_id"))
    _bind_qr(sg, qr, session)
    _sg().flush()
    _qr().flush()
    return core.ok()

# ---------------- 日志 / 校验 / 生产溯源 ----------------
def _single_log(body, session):
    """/single/log：{busi_id, pageNo, pageSize}——注意不是 page/limit（§5.3 实测报参数校验错误）。"""
    if not body.get("busi_id"):
        return core.fail("参数校验错误")
    try:
        pageNo = int(body.get("pageNo") or 1)
    except (TypeError, ValueError):
        pageNo = 1
    try:
        pageSize = int(body.get("pageSize") or 15)
    except (TypeError, ValueError):
        pageSize = 15
    rows = [r for r in _log().all() if str(r.get("busi_id")) == str(body.get("busi_id"))
            and r.get("busi_type") == 1310]
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    data = [_decor_log(r, session) for r in rows[(pageNo - 1) * pageSize: pageNo * pageSize]]
    # 响应壳对齐 single_log.json：{success, data:{count, data:[...]}}（无顶层 count）
    return {"success": True, "data": {"count": len(rows), "data": data}}

def _single_check_code_repeat(body, session):
    """生产计划手工编号查重：{single_code_list:[...]}（数组入参，单值报参数校验错误，§6.3）。"""
    lst = body.get("single_code_list")
    if not isinstance(lst, list):
        return core.fail("参数校验错误")
    exist = {str(r.get("code") or "") for r in _sg().all()}
    return core.ok([c for c in lst if str(c) in exist])

def _single_produce_trace(body, session):
    """生产溯源 tab：traceability_craft_list 非空优先，否则退回 produce_craft_list（§5.3）。"""
    sg = _sg().get(body.get("id"))
    if not sg:
        return core.fail("数据不存在")
    tc = _tc().get(sg["traceability_code_id"]) if sg.get("traceability_code_id") else None
    tcl = [_rel_flat(x) for x in _rels_of(tc["id"])] if tc else []
    # produce_craft_list=按产品收集（生产计划/加工单链）；demo 种子未建 single→计划回链，保持空表
    return core.ok({"produce_craft_list": [], "traceability_craft_list": tcl})

# ---------------- 产品导入/导出（§3.6 / §7.1） ----------------
SG_TPL_NOTES = [
    "欢迎使用产品信息导入模板，请注意以下事项：",
    "1、产品编号和产品名称长度不超过50字；",
    "2、产品图片和简介字段将自动复制所属产品模板的图片和简介内容；",
    "3、日期格式为YYYY-MM-DD；",
    "4、背景色红色标记的字段为必填项；",
    "5、单次最多导入数目为5000条。",
]

def _single_tpl(body, session):
    return {"__binary__": _tpl_binary("Worksheet", ["客户名", "商品名", "质保日期", "产品编号"], SG_TPL_NOTES),
            "__filename__": "产品导入模板.xlsx"}

def _find_by_name(store, name):
    for r in store.all():
        if str(r.get("name") or "") == str(name or "").strip():
            return r
    return None

def _single_import(body, session):
    parsed, err = _parse_rows(body, ("客户名", "商品名"))
    if err:
        return err
    rows, idx = parsed
    i_c, i_g, i_d, i_code = (idx.get("客户名"), idx.get("商品名"),
                             idx.get("质保日期"), idx.get("产品编号"))
    errors = []

    def cell(r, i):
        return (str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else "")

    for n, r in enumerate(rows[1:], start=2):
        cells = ["" if c is None else str(c).strip() for c in r]
        if not any(cells):
            continue
        cname, gname = cell(r, i_c), cell(r, i_g)
        if not cname:
            errors.append(["第%d行：%s" % (n, "客户名不能为空")] + cells)
            continue
        if not gname:
            errors.append(["第%d行：%s" % (n, "商品名不能为空")] + cells)
            continue
        cust = _find_by_name(core.Store("customers"), cname)
        goods = _find_by_name(core.Store("goods"), gname)
        if cust is None:
            errors.append(["第%d行：%s" % (n, "客户不存在")] + cells)
            continue
        if goods is None:
            errors.append(["第%d行：%s" % (n, "商品不存在")] + cells)
            continue
        code = cell(r, i_code) or _next_single_code()
        if _by_code(_sg(), code):
            errors.append(["第%d行：%s" % (n, "产品编号已存在")] + cells)
            continue
        _sg().add({
            "enterprise_id": session.get("enterprise_id"),
            "customer_id": cust["id"], "customer": {"id": cust["id"], "name": cust.get("name"), "code": cust.get("code")},
            "goods_id": goods["id"],
            "goods": {"id": goods["id"], "name": goods.get("name"), "code": goods.get("code"),
                      "standard": goods.get("standard"), "avatars": goods.get("avatars"),
                      "profile": goods.get("profile")},
            "code": code, "guarantee_date": cell(r, i_d) or None,
            "resp_user_id": session.get("id"), "create_user_id": session.get("id"),
            "qr_code_id": None, "traceability_code_id": None,
            "stock_in_price": "0.00", "source": 1, "need_final_quality": 2, "has_final_quality": 2,
        })
    return core.ok(errors)

SG_EXPORT_HEADERS = ["客户", "产品编号", "产品名", "产品类别", "产品码编号", "溯源码编号",
                     "负责人", "质保日期", "保修状态", "创建人", "创建时间"]

def _single_export(body, session):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    rows = _sg_filter(_sg().all(), body)
    wb = Workbook()
    ws = wb.active
    ws.title = "产品列表导出"
    ws.append(SG_EXPORT_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        d = _decor_sg(r)
        ws.append([
            (d.get("customer") or {}).get("name"), d.get("code"),
            (d.get("goods") or {}).get("name"), d.get("category_path_names"),
            (d.get("qr_code") or {}).get("code"), (d.get("traceability_code") or {}).get("code"),
            (d.get("resp_user") or {}).get("real_name"), d.get("guarantee_date"),
            d.get("in_guarantee_name"), (d.get("create_user") or {}).get("real_name"), d.get("created_at"),
        ])
    bio = BytesIO()
    wb.save(bio)
    return {"__binary__": bio.getvalue(), "__filename__": "产品列表导出.xlsx"}

# =================================================================
# 种子（合成，行内 "_seed":1；关联真实 produce_bills/customers/goods/users）
# =================================================================
def _seed():
    if _tc().rows:            # 幂等：已有数据（含运行期生成）不重播种
        return
    ent = 16807
    goods_store = core.Store("goods").all()
    g0 = goods_store[0] if goods_store else {"id": 6333412, "code": "SP202504250338", "name": "气动卡盘",
                                             "standard": "80型气动卡盘"}
    g1 = goods_store[1] if len(goods_store) > 1 else g0
    custs = [c for c in core.Store("customers").all() if not str(c.get("name") or "")[:1].isascii()] \
        or core.Store("customers").all()
    c0 = {"id": (custs[0] or {}).get("id") or 1350596, "name": (custs[0] or {}).get("name") or "重庆汽车部件加工厂"}
    users = core.Store("users").all()
    xiaowang = next((u for u in users if u.get("real_name") == "小王"), users[0] if users else None)
    uid = (xiaowang or {}).get("id") or 63097
    kuguan = next((u for u in users if u.get("real_name") == "库管"), None)
    resp_id = (kuguan or {}).get("id") or uid

    def goods_mini(g):
        return {"id": g.get("id"), "name": g.get("name"), "code": g.get("code"),
                "standard": g.get("standard"), "avatars": g.get("avatars"), "profile": g.get("profile"),
                "qr_code_url": "%s/#/g?qr_id=%s" % (QR_HOST, g.get("id"))}

    # ---- 双码池：两表独立、各 1000001 起号（deep\25 §0 实测） ----
    for i in range(10):       # 溯源码池 id=73605..73614 / code=1000001..1000010（镜像实测）
        _tc().add({"id": 73605 + i, "enterprise_id": ent, "code": str(1000001 + i),
                   "created_at": "2026-01-04 15:19:26", "updated_at": "2026-01-04 15:19:26",
                   "del": 2, "_seed": 1})
    for i in range(3):        # 产品码池 id=776936..（镜像实测 776936/code=1000001）
        _qr().add({"id": 776936 + i, "enterprise_id": ent, "code": str(1000001 + i),
                   "created_at": "2026-01-04 15:19:32", "updated_at": "2026-01-04 15:19:32",
                   "del": 2, "_seed": 1})

    # ---- 产品 3 条：已绑双码 / 仅产品码 / 全未绑 ----
    base_sg = {"enterprise_id": ent, "del": 2, "source": 1, "need_final_quality": 2,
               "has_final_quality": 2, "has_make_over": 2, "has_reject_node": 2,
               "produce_plan_bill_id": None, "produce_stock_in_bill_id": None,
               "stock_in_price": "0.00", "profile": None, "remark": None,
               "work_order_count": 0, "feedback_count": 0, "inspection_plan_count": 0, "_seed": 1}
    sg1 = _sg().add(dict(base_sg, id=1331587, code="CP202504001",
                         customer_id=c0["id"], customer=c0, goods_id=g0.get("id"), goods=goods_mini(g0),
                         qr_code_id=776936, traceability_code_id=73614,
                         create_user_id=uid, resp_user_id=resp_id,
                         guarantee_date="2026-01-04", created_at="2026-01-01 15:57:32"))
    sg2 = _sg().add(dict(base_sg, id=1331588, code="CP202601001",
                         customer_id=c0["id"], customer=c0, goods_id=g1.get("id"), goods=goods_mini(g1),
                         qr_code_id=776937, traceability_code_id=None,
                         create_user_id=uid, resp_user_id=uid,
                         guarantee_date="2027-06-30", created_at="2026-01-05 09:10:00"))
    sg3 = _sg().add(dict(base_sg, id=1331589, code="CP202601002",
                         customer_id=c0["id"], customer=c0, goods_id=g0.get("id"), goods=goods_mini(g0),
                         qr_code_id=None, traceability_code_id=None,
                         create_user_id=uid, resp_user_id=uid, guarantee_date=None,
                         created_at="2026-01-06 11:20:00"))
    # 码池 ↔ 产品 双向回填
    _tc().get(73614)["single_id"] = sg1["id"]
    _qr().get(776936)["single_id"] = sg1["id"]
    _qr().get(776937)["single_id"] = sg2["id"]
    _tc().flush()
    _qr().flush()

    # ---- 报工关联链：挂到真实 produce_bills（8835784 / 20261028001-10，bill_qr_code 自带） ----
    bill = _bills().get(8835784)
    bill_id = (bill or {}).get("id") or 8835784
    crafts = [c for c in core.Store("produce_bill_crafts").all()
              if str(c.get("produce_bill_id")) == str(bill_id)]
    cn1 = (crafts[0] or {}).get("craft_name") if crafts else "粗加工"
    cn2 = (crafts[1] or {}).get("craft_name") if len(crafts) > 1 else "粗铣基准面"
    cr1 = (crafts[0] or {}).get("id") if crafts else 28376286
    cr2 = (crafts[1] or {}).get("id") if len(crafts) > 1 else 28376287
    rel_base = {"traceability_code_id": 73614, "produce_bill_id": bill_id,
                "report_num": "1.00000000", "valid_num": "1.00000000", "waste_num": "0.00000000",
                "quality_status_name": "不需质检", "report_waste_list": [], "remark": None,
                "attachment_list": [], "report_user_names": "小王", "create_user_names": "小王",
                "quality_user_names": None, "_seed": 1}
    _rel().add(dict(rel_base, id=147609, report_work_record_id=19055324, produce_bill_craft_id=cr1,
                    pub_craft_name=cn1, pub_craft_end_time="2026-05-30 10:29:57"))
    _rel().add(dict(rel_base, id=147610, report_work_record_id=19055325, produce_bill_craft_id=cr2,
                    pub_craft_name=cn2, pub_craft_end_time="2026-05-30 10:28:51"))

    # ---- 生成历史（文件名规则 {ts}({起}-{止}).{xlsx|zip}，§2.7 实测样例镜像） ----
    for row in (
        {"id": 3102, "file_name": "1786442936(1000011-1000011).zip", "amount": 1,
         "code_start": "1000011", "code_end": "1000011", "download_type": "zip",
         "created_at": "2026-08-11 18:08:56", "user_id": 144814, "real_name": "13810266941"},
        {"id": 2907, "file_name": "1782959175(1000011-1000020).zip", "amount": 10,
         "code_start": "1000011", "code_end": "1000020", "download_type": "zip",
         "created_at": "2026-07-02 10:26:16", "user_id": 214890, "real_name": "小张"},
        {"id": 1937, "file_name": "1762848264(1000014-1000014).xlsx", "amount": 1,
         "code_start": "1000014", "code_end": "1000014", "download_type": "xlsx",
         "created_at": "2025-11-11 16:04:24", "user_id": uid, "real_name": "小王"},
    ):
        _tch().add(dict(row, enterprise_id=ent, updated_at=row["created_at"],
                        file_path="https://up.sh-t.com.cn/kgd-demo/%s.%s" % (
                            hashlib.md5(row["file_name"].encode()).hexdigest(), row["download_type"]),
                        fieldValueList=[], operator={"id": row["user_id"], "real_name": row["real_name"]},
                        **{"del": 2, "_seed": 1}))
    for row in (
        {"id": 2101, "file_name": "1770000000(1000001-1000003).xlsx", "amount": 3,
         "code_start": "1000001", "code_end": "1000003", "download_type": "xlsx",
         "created_at": "2026-01-04 15:19:40", "user_id": uid, "real_name": "小王"},
    ):
        _qrh().add(dict(row, enterprise_id=ent, updated_at=row["created_at"],
                        file_path="https://up.sh-t.com.cn/kgd-demo/%s.xlsx" % row["id"],
                        fieldValueList=[], operator={"id": row["user_id"], "real_name": row["real_name"]},
                        **{"del": 2, "_seed": 1}))

    # ---- 绑定日志（镜像 single_log.json 实测条目） ----
    _log().add({"id": 83487382, "enterprise_id": ent, "customer_id": c0["id"], "operator_id": uid,
                "content": "绑定溯源码,编号：1000010", "is_private": 2,
                "created_at": "2026-06-17 15:41:02", "updated_at": "2026-06-17 15:41:02",
                "busi_type": 1310, "busi_id": sg1["id"], "operation_type": None, "operation": None,
                "agent_id": None, "content_raw": None, "channel": None,
                "operation_name": "绑定溯源码", "color": None, "attachments": [], "_seed": 1})

_seed()

# =================================================================
# 路由（模块/动作名对齐 deep\25 §7 接口清单）
# =================================================================
ROUTES = {
    # traceability_code（§7.2）
    ("traceability_code", "list"): _tc_list,
    ("traceability_code", "generate_and_download"): _tc_generate,
    ("traceability_code", "batch_download_qrcode"): _tc_batch_download,
    ("traceability_code", "download_import_template"): _tc_tpl,
    ("traceability_code", "import"): _tc_import,
    ("traceability_code", "del"): _tc_del,
    ("traceability_code", "batch_del"): _tc_batch_del,
    ("traceability_code", "traceability_detail_by_id"): _tc_detail,
    # traceability_code_generate_history（§2.7；download 原站为 GET 直链，demo 走 POST）
    ("traceability_code_generate_history", "list"): _tch_list,
    ("traceability_code_generate_history", "download"): _tch_download,
    # single_qr_code（§7.3）
    ("single_qr_code", "list"): _qr_list,
    ("single_qr_code", "add"): _qr_add,
    ("single_qr_code", "edit"): _qr_edit,
    ("single_qr_code", "del"): _qr_del,
    ("single_qr_code", "batch_del"): _qr_batch_del,
    ("single_qr_code", "generate_and_download"): _qr_generate,
    ("single_qr_code", "batch_download_qrcode"): _qr_batch_download,
    ("single_qr_code", "download_import_template"): _qr_tpl,
    ("single_qr_code", "import"): _qr_import,
    # qr_generate_history（§4）
    ("qr_generate_history", "list"): _qrh_list,
    ("qr_generate_history", "download"): _qrh_download,
    # single（§7.1；未实现：dispatch2* / get_single_material_import_data / 报表族 / 打印族）
    ("single", "list"): _single_list,
    ("single", "detail"): _single_detail,
    ("single", "add"): _single_add,
    ("single", "edit"): _single_edit,
    ("single", "del"): _single_del,
    ("single", "batch_del"): _single_batch_del,
    ("single", "batch_edit"): _single_batch_edit,
    ("single", "trans"): _single_trans,
    ("single", "bind_code"): _single_bind_code,
    ("single", "bind_traceability_code_by_code"): _single_bind_tc,
    ("single", "untie_code"): _single_untie_code,
    ("single", "unbind_traceability_code"): _single_unbind_tc,
    ("single", "batch_set_code"): _single_batch_set_code,
    ("single", "random_bind_code"): _single_random_bind,
    ("single", "log"): _single_log,
    ("single", "check_code_repeat"): _single_check_code_repeat,
    ("single", "produce_trace"): _single_produce_trace,
    ("single", "download_import_template"): _single_tpl,
    ("single", "import"): _single_import,
    ("single", "export"): _single_export,
}
