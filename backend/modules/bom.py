# -*- coding: utf-8 -*-
"""B3 商品BOM域（/goods_bom/*）。
契约与数据形状依据逆向报告：
- analysis/06-BOM管理.md（列表/详情字段、编号规则 BOM+YYYYMMDD+4位、保存校验文案、API 清单）
- analysis/deep/02-BOM多级成本核算.md（成本总额=全行平铺和 Σround8(goods_cost_money×num)，非递归卷积；
  structure_tree 参数 {goods_bom_id, lazy_load:2}，返回根节点(level=0,is_root)+children 树，行字段为拍平前缀；
  引用BOM=活引用，仅 structure_tree 端点展开（被引用行原 id/bom_id 挂入，level 按挂点重算）；detail 不展开引用；
  is_default 同商品唯一，无母件不可设默认；保存时整棵结构重建（payload 不带 id/parent_id））
- 数据样例 api_dump/bom_all.json / bom_trees.json / bom_details.json
Store 约定（B11 种子）：boms=列表行、bom_trees=结构树（兼容 {bom_id,tree} / 根节点本体 / boms 行内嵌
structure_tree 三种形态）、goods/crafts/categories 为关联主数据。
"""
from decimal import Decimal, ROUND_HALF_UP
import core

ENTERPRISE_ID = 16807

# ---------------- 数字工具（成本 8 位小数口径，全模块统一 Decimal） ----------------

def _d(v):
    try:
        if v in (None, ""):
            return Decimal(0)
        return Decimal(str(v))
    except Exception:
        return Decimal(0)

def _q8(x):
    return x.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

def _s8(x):
    """8 位定点串：num / 行成本金额。"""
    return format(_q8(x), "f")

def _trim(x):
    """去尾零数字串：bom_cost_money_total / goods_cost_money（"1841"、"44.8367"、"0"）。"""
    return format(_q8(x).normalize(), "f")

# ---------------- Store 访问 ----------------

def _boms():
    return core.Store("boms")

def _trees():
    return core.Store("bom_trees")

def _goods():
    return core.Store("goods")

def _crafts():
    return core.Store("crafts")

def _goods_row(goods_id):
    if goods_id in (None, ""):
        return None
    return _goods().get(goods_id)

def _craft_name(cid):
    if cid in (None, ""):
        return ""
    c = _crafts().get(cid)
    return (c or {}).get("name") or ""

# ---------------- 结构树读取（兼容三种存储形态） ----------------

def _entry_matches(r, bid):
    if not isinstance(r, dict):
        return False
    if str(r.get("bom_id") or "") == str(bid):
        return True
    return bool(r.get("is_root")) and str(r.get("id")) == str(bid)

def _get_tree(bid):
    """取拍平形态结构树根节点（level=0/is_root）。无则 None。"""
    if bid in (None, ""):
        return None
    ts = _trees().all()
    for r in ts:
        if isinstance(r, dict) and str(r.get("bom_id") or "") == str(bid) and isinstance(r.get("tree"), dict):
            return r["tree"]
    for r in ts:
        if isinstance(r, dict) and r.get("is_root") and str(r.get("id")) == str(bid):
            return r
    return None

def _tree_from_own_detail(bom):
    """兜底：bom 行内嵌 detail 样例结构（structure_tree 为 detail 形态）→ 拍平树。"""
    own = bom.get("structure_tree")
    if not isinstance(own, list):
        return None

    def conv(n, level):
        g = n.get("goods") or {}
        unit = g.get("unit") if isinstance(g.get("unit"), dict) else None
        kids = [conv(c, level + 1) for c in (n.get("children") or [])]
        out = {
            "id": n.get("id"), "bom_id": n.get("goods_bom_id") or bom.get("id"),
            "goods_id": n.get("goods_id"), "goods_name": g.get("name") or "",
            "goods_code": g.get("code") or "", "goods_standard": g.get("standard") or "",
            "goods_unit": (unit or {}).get("name") or "", "goods_source": g.get("source_name") or "",
            "goods_cost_money": n.get("goods_cost_money") or "0", "goods_remark": g.get("remark") or "",
            "num": n.get("num") or "1.00000000", "level": level, "is_root": False,
            "is_reference": bool(n.get("reference_bom_id")), "reference_bom_id": n.get("reference_bom_id"),
            "pub_craft_id": n.get("pub_craft_id"),
            "pub_craft_name": (n.get("pub_craft") or {}).get("name") or "",
            "stock_in_craft_id": n.get("stock_in_craft_id"),
            "stock_in_craft_name": (n.get("stock_in_craft") or {}).get("name") or "",
            "remark": n.get("remark") or "", "fieldValueList": n.get("fieldValueList") or [],
            "has_children": bool(kids),
        }
        if kids:
            out["children"] = kids
        return out

    kids = [conv(n, 1) for n in own if isinstance(n, dict)]
    return _root_node(bom, kids)

def _get_tree_any(bom):
    """structure_tree/detail 共用：优先 bom_trees Store，缺省从 detail 样例结构兜底。"""
    t = _get_tree(bom.get("id"))
    if t is not None:
        return t
    return _tree_from_own_detail(bom)

# ---------------- 根节点 / 响应树（引用展开） ----------------

def _unit_name(g):
    u = g.get("unit") if isinstance(g, dict) else None
    return (u or {}).get("name") or "" if isinstance(u, dict) else ""

def _root_node(bom, children):
    g = _goods_row(bom.get("goods_id")) or bom.get("goods") or {}
    root = {
        "id": bom.get("id"), "code": bom.get("code"), "name": bom.get("name"),
        "goods_id": bom.get("goods_id"),
        "goods_name": (g or {}).get("name") or "", "goods_code": (g or {}).get("code") or "",
        "goods_standard": (g or {}).get("standard") or "", "goods_unit": _unit_name(g or {}),
        "num": "1", "level": 0, "is_root": True, "is_reference": False, "reference_bom_id": None,
        "pub_craft_id": None, "pub_craft_name": "", "stock_in_craft_id": None, "stock_in_craft_name": "",
        "remark": "", "has_children": bool(children),
    }
    if children:
        root["children"] = children
    return root

def _resp_node(fnode, seen, shift=0):
    """/goods_bom/structure_tree 响应节点：构造新 dict（不改 Store），引用行实时挂入被引用 BOM 的行。
    引用行已带 children（种子数据为端点抓取、已展开）则原样返回，避免重复挂入。
    level 语义：本树行=存储绝对层级；引用展开行=挂点层级+1 起重算（对齐 dump：纱线 level=2）。
    叶子节点不带 children 键（对齐 dump 抓取形态）。"""
    out = dict(fnode)
    lvl = (fnode.get("level") or 0) + shift
    out["level"] = lvl
    out["fieldValueList"] = out.get("fieldValueList") or []
    kids = fnode.get("children") or []
    rid = fnode.get("reference_bom_id")
    if fnode.get("is_reference") and rid and not kids and str(rid) not in seen:
        ref_bom = _boms().get(rid)
        ref_root = _get_tree(rid)
        if ref_root is None and ref_bom:
            ref_root = _tree_from_own_detail(ref_bom)
        if ref_root is not None:
            seen2 = seen | {str(rid)}
            kids = [_resp_node(c, seen2, shift=lvl) for c in (ref_root.get("children") or [])]
    else:
        kids = [_resp_node(c, seen, shift=shift) for c in kids]
    out["has_children"] = bool(kids)
    if kids:
        out["children"] = kids
    else:
        out.pop("children", None)
    return out

# ---------------- 成本（平铺和，非递归卷积） ----------------

def _flat_sum(nodes):
    t = Decimal(0)
    for n in nodes or []:
        t += _q8(_d(n.get("goods_cost_money")) * _d(n.get("num")))
        t += _flat_sum(n.get("children") or [])
    return t

def _flat_total(nodes):
    """bom_cost_money_total = Σ 全部行 round8(goods_cost_money × num)（树平铺，不乘祖先 num）。"""
    return _trim(_flat_sum(nodes))

def _has_ref(nodes):
    for n in nodes or []:
        if n.get("is_reference") or n.get("reference_bom_id"):
            return True
        if _has_ref(n.get("children") or []):
            return True
    return False

# ---------------- 提交归一（原站 structure_list / 复刻前端 materials 双兼容） ----------------

def _norm_children(ch):
    out = []
    for m in ch or []:
        if not isinstance(m, dict):
            continue
        num = m.get("num") if m.get("num") not in (None, "") else m.get("dosage")
        out.append({
            "goods_id": m.get("goods_id") if m.get("goods_id") is not None else m.get("id"),
            "num": num if num not in (None, "") else None,
            "pub_craft_id": m.get("pub_craft_id"), "stock_in_craft_id": m.get("stock_in_craft_id"),
            "remark": m.get("remark"), "reference_bom_id": m.get("reference_bom_id"),
            "fieldValueList": m.get("fieldValueList") or [],
            "children": _norm_children(m.get("children")),
        })
    return out

def _norm_rows(body):
    """structure_list（原站：{goods_id,num,工序,remark,reference_bom_id,children}）；
    materials（复刻前端：id=商品id、dosage=单位用量）。num 缺省回落 dosage，仍空则 None（触发校验）。"""
    src = body.get("structure_list")
    if src is None:
        src = body.get("materials") or []
        rows = []
        for m in src:
            if not isinstance(m, dict):
                continue
            num = m.get("num") if m.get("num") not in (None, "") else m.get("dosage")
            rows.append({
                "goods_id": m.get("goods_id") if m.get("goods_id") is not None else m.get("id"),
                "num": num if num not in (None, "") else None,
                "pub_craft_id": m.get("pub_craft_id"), "stock_in_craft_id": m.get("stock_in_craft_id"),
                "remark": m.get("remark"), "reference_bom_id": m.get("reference_bom_id"),
                "fieldValueList": m.get("fieldValueList") or [],
                "children": _norm_children(m.get("children")),
                "_name": m.get("name"), "_code": m.get("code"), "_standard": m.get("standard"),
            })
        return rows
    rows = []
    for m in src:
        if not isinstance(m, dict):
            continue
        num = m.get("num") if m.get("num") not in (None, "") else m.get("dosage")
        rows.append({
            "goods_id": m.get("goods_id"), "num": num if num not in (None, "") else None,
            "pub_craft_id": m.get("pub_craft_id"), "stock_in_craft_id": m.get("stock_in_craft_id"),
            "remark": m.get("remark"), "reference_bom_id": m.get("reference_bom_id"),
            "fieldValueList": m.get("fieldValueList") or [],
            "children": _norm_children(m.get("children")),
        })
    return rows

def _check_num(rows):
    for r in rows:
        if r.get("num") in (None, ""):
            return False
        if not _check_num(r.get("children") or []):
            return False
    return True

def _check_rows(rows):
    """先判顶层空（bom结构至少有一条），再逐行判用量空（含子行递归，空子表不算错）。"""
    if not rows:
        return "bom结构至少有一条"
    return None if _check_num(rows) else "每条bom结构的单位用量不能为空"

# ---------------- 新结构落库（保存即整树重建，行 id 由后端重发） ----------------

def _max_id(nodes, mx):
    for x in nodes or []:
        i = x.get("id") if isinstance(x, dict) else None
        if isinstance(i, int) and not x.get("is_root") and i > mx:
            mx = i
        mx = _max_id(x.get("children") or [], mx)
    return mx

def _alloc_ids(n):
    mx = 8000000
    ts = _trees()
    for r in ts.all():
        if not isinstance(r, dict):
            continue
        t = r.get("tree") if isinstance(r.get("tree"), dict) else (r if r.get("is_root") else None)
        if t is not None:
            mx = _max_id([t], mx)
    for b in _boms().all():
        if isinstance(b, dict) and isinstance(b.get("structure_tree"), list):
            mx = _max_id(b["structure_tree"], mx)
    return iter(range(mx + 1, mx + 1 + n))

def _count_rows(rows):
    n = 0
    for r in rows:
        n += 1 + _count_rows(r.get("children") or [])
    return n

def _mk_nodes(rows, level, bom, ids):
    nodes = []
    for r in rows:
        g = _goods_row(r.get("goods_id")) or {}
        gname = g.get("name") or r.get("_name") or ""
        gcode = g.get("code") or r.get("_code") or ""
        gstd = g.get("standard") if g.get("standard") is not None else (r.get("_standard") or "")
        cost = _trim(_d(g.get("cost_money"))) if g else "0"
        num = _s8(_d(r.get("num")))
        ref = r.get("reference_bom_id")
        node = {
            "id": next(ids), "bom_id": bom.get("id"), "goods_id": r.get("goods_id"),
            "goods_name": gname, "goods_code": gcode, "goods_standard": gstd or "",
            "goods_unit": _unit_name(g), "goods_source": g.get("source_name") or "",
            "goods_cost_money": cost, "goods_remark": g.get("remark") or "",
            "num": num, "level": level, "is_root": False,
            "is_reference": bool(ref), "reference_bom_id": ref,
            "pub_craft_id": r.get("pub_craft_id"), "pub_craft_name": _craft_name(r.get("pub_craft_id")),
            "stock_in_craft_id": r.get("stock_in_craft_id"), "stock_in_craft_name": _craft_name(r.get("stock_in_craft_id")),
            "remark": r.get("remark") or "", "fieldValueList": r.get("fieldValueList") or [],
        }
        # 引用与子件互斥：引用行不带子树（structure_tree 端点读时实时展开）
        if not ref:
            kids = _mk_nodes(r.get("children") or [], level + 1, bom, ids)
            node["has_children"] = bool(kids)
            if kids:
                node["children"] = kids
        else:
            node["has_children"] = True
        nodes.append(node)
    return nodes

def _build_tree(bom, rows):
    ids = _alloc_ids(_count_rows(rows))
    kids = _mk_nodes(rows, 1, bom, ids)
    root = _root_node(bom, kids)
    total = _flat_total(kids)
    return root, total, _has_ref(kids)

def _save_tree(bom, root):
    ts = _trees()
    ts.rows = [r for r in ts.rows if not _entry_matches(r, bom.get("id"))]
    ts.rows.insert(0, {
        "bom_id": bom.get("id"), "code": bom.get("code"), "name": bom.get("name"),
        "goods_id": bom.get("goods_id"), "is_default": bom.get("is_default"),
        "bom_cost_money_total": bom.get("bom_cost_money_total"), "tree": root,
    })
    ts.flush()

def _remove_tree(bid):
    ts = _trees()
    before = len(ts.rows)
    ts.rows = [r for r in ts.rows if not _entry_matches(r, bid)]
    if len(ts.rows) != before:
        ts.flush()

# ---------------- 默认 BOM 唯一性（同商品至多 1 个 is_default=1） ----------------

def _clear_default(row, st):
    if row.get("is_default") != 1 or row.get("goods_id") in (None, ""):
        return
    for r in st.all():
        if r.get("id") != row.get("id") and str(r.get("goods_id")) == str(row.get("goods_id")) and r.get("is_default") == 1:
            st.update(r.get("id"), {"is_default": 2})

def _norm_is_default(v, goods_id):
    """无母件不可设默认（goods_id=None 组恒 2）。"""
    if goods_id in (None, ""):
        return 2
    return 1 if v in (1, "1", True) else 2

# ---------------- detail 形态（编辑回显：嵌套 goods 对象、parent_id、行成本金额；不展开引用） ----------------

def _craft_obj(cid, cname):
    if cid in (None, ""):
        return {"id": None, "name": cname} if cname else None
    c = _crafts().get(cid)
    return {"id": cid, "name": (c or {}).get("name") or cname or ""}

def _ref_bom_obj(rid):
    if rid in (None, ""):
        return None
    b = _boms().get(rid)
    if not b:
        return None
    return {"id": b.get("id"), "code": b.get("code"), "name": b.get("name")}

def _detail_goods(fnode):
    g = _goods_row(fnode.get("goods_id"))
    if g:
        return dict(g)
    unit = {"name": fnode.get("goods_unit")} if fnode.get("goods_unit") else None
    return {
        "id": fnode.get("goods_id"), "name": fnode.get("goods_name") or "",
        "code": fnode.get("goods_code") or "", "standard": fnode.get("goods_standard") or None,
        "source": None, "avatars": [], "cost_money": fnode.get("goods_cost_money") or "0",
        "source_name": fnode.get("goods_source") or None, "unit": unit,
        "remark": fnode.get("goods_remark") or None,
    }

def _detail_node(fnode, bom, parent_id):
    kids = [_detail_node(k, bom, fnode.get("id")) for k in (fnode.get("children") or [])]
    return {
        "id": fnode.get("id"), "enterprise_id": bom.get("enterprise_id", ENTERPRISE_ID),
        "goods_bom_id": fnode.get("bom_id") or bom.get("id"),
        "goods_id": fnode.get("goods_id"), "parent_id": parent_id,
        "pub_craft_id": fnode.get("pub_craft_id"),
        "num": fnode.get("num") or "1.00000000",
        "level": fnode.get("level") or 1, "del": 2,
        "created_at": bom.get("created_at"), "updated_at": bom.get("updated_at"),
        "remark": fnode.get("remark") or "",
        "stock_in_craft_id": fnode.get("stock_in_craft_id"),
        "reference_bom_id": fnode.get("reference_bom_id"),
        "fieldValueList": fnode.get("fieldValueList") or [],
        "goods_cost_money": fnode.get("goods_cost_money") or "0",
        "goods_cost_money_total": _s8(_d(fnode.get("goods_cost_money")) * _d(fnode.get("num"))),
        "goods": _detail_goods(fnode),
        "pub_craft": _craft_obj(fnode.get("pub_craft_id"), fnode.get("pub_craft_name") or ""),
        "stock_in_craft": _craft_obj(fnode.get("stock_in_craft_id"), fnode.get("stock_in_craft_name") or ""),
        "reference_bom": _ref_bom_obj(fnode.get("reference_bom_id")),
        "children": kids,
    }

def _detail_payload(bom):
    data = dict(bom)
    own = bom.get("structure_tree") if isinstance(bom.get("structure_tree"), list) else None
    tree = _get_tree(bom.get("id"))
    if tree is not None:
        stree = [_detail_node(k, bom, None) for k in (tree.get("children") or [])]
    elif own:
        stree = own
    else:
        stree = []
    data["structure_tree"] = stree
    data["structure"] = [{"id": n.get("id")} for n in stree]
    data.setdefault("structure_field_list", [])
    data["bom_cost_money_total"] = _flat_total(stree)
    data["has_reference_structure"] = bool(_has_ref(stree) or bom.get("has_reference_structure"))
    return data

# ---------------- 分页（core.Store.page 同款切片，用于筛选后的临时列表） ----------------

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

def _like(v, kw):
    return not kw or str(kw).lower() in str(v if v is not None else "").lower()

def _kw(body, key):
    v = body.get(key)
    return str(v).strip() if v not in (None, "") else ""

# ---------------- handlers ----------------

def _list(body, session):
    st = _boms()
    code, name = _kw(body, "code"), _kw(body, "name")
    g_kw = _kw(body, "goods_keyword")
    g_name, g_code, g_std = _kw(body, "goods_name"), _kw(body, "goods_code"), _kw(body, "goods_standard")
    sub_kw = _kw(body, "structure_goods_keyword")
    cu_kw = _kw(body, "create_user_name")
    goods_id = _kw(body, "goods_id")
    is_enable = body.get("is_enable")
    big_id, small_id = _kw(body, "big_category_id"), _kw(body, "small_category_id")
    at_start, at_end = _kw(body, "created_at_start"), _kw(body, "created_at_end")

    lst = []
    for r in st.all():
        g = r.get("goods") or {}
        if not _like(r.get("code"), code):
            continue
        if not _like(r.get("name"), name):
            continue
        blob = "%s %s %s" % (g.get("name") or "", g.get("code") or "", g.get("standard") or "")
        if g_kw and not _like(blob, g_kw):
            continue
        if not _like(g.get("name"), g_name):
            continue
        if not _like(g.get("code"), g_code):
            continue
        if not _like(g.get("standard"), g_std):
            continue
        if goods_id and str(r.get("goods_id") or "") != goods_id:
            continue
        if is_enable not in (None, "", "0") and str(r.get("is_enable")) != str(is_enable):
            continue
        if big_id and str(r.get("big_category_id") or "") != big_id:
            continue
        if small_id and str(r.get("small_category_id") or "") != small_id:
            continue
        if not _like((r.get("create_user") or {}).get("real_name") or (r.get("create_user") or {}).get("name"), cu_kw):
            continue
        created = str(r.get("created_at") or "")
        if at_start and created[:10] < at_start[:10]:
            continue
        if at_end and created[:10] > at_end[:10]:
            continue
        if sub_kw:
            tree = _get_tree(r.get("id"))
            names = []
            def _collect(nodes):
                for n in nodes or []:
                    names.append("%s %s %s" % (n.get("goods_name") or "", n.get("goods_code") or "", n.get("goods_standard") or ""))
                    _collect(n.get("children") or [])
            _collect((tree or {}).get("children") or [])
            if not _like(" ".join(names), sub_kw):
                continue
        lst.append(r)
    return core.paged_ok(_page(lst, body))

def _detail(body, session):
    bom = _boms().get(body.get("id"))
    if not bom:
        return core.fail("数据不存在或已经被删除")
    return core.ok(_detail_payload(bom))

def _add(body, session):
    st = _boms()
    name = str(body.get("name") or "").strip()
    if not name:
        return core.fail("请填写BOM名称")
    rows = _norm_rows(body)
    err = _check_rows(rows)
    if err:
        return core.fail(err)
    code = str(body.get("code") or "").strip() or core.next_code("BOM", st.all())
    goods_id = body.get("goods_id")
    goods = _goods_row(goods_id)
    is_default = _norm_is_default(body.get("is_default"), goods_id)
    cats = core.Store("categories")
    big_id, small_id = body.get("big_category_id"), body.get("small_category_id")
    cu = {"id": session.get("id"), "name": session.get("name"),
          "real_name": session.get("real_name") or session.get("name")} if session else None
    bom = st.add({
        "enterprise_id": (session or {}).get("enterprise_id") or ENTERPRISE_ID,
        "code": code, "name": name, "goods_id": goods_id,
        "create_user_id": (session or {}).get("id"),
        "is_enable": 1, "is_default": is_default,
        "remark": body.get("remark"),
        "big_category_id": big_id, "small_category_id": small_id,
        "fieldValueList": body.get("fieldValueList") or [],
        "goods": dict(goods) if goods else None,
        "create_user": cu,
        "big_category": {"id": big_id, "name": (cats.get(big_id) or {}).get("name")} if big_id else None,
        "small_category": {"id": small_id, "name": (cats.get(small_id) or {}).get("name")} if small_id else None,
    })
    root, total, has_ref = _build_tree(bom, rows)
    bom["bom_cost_money_total"] = total
    bom["has_reference_structure"] = has_ref
    st.flush()
    _save_tree(bom, root)
    _clear_default(bom, st)
    return core.ok(_detail_payload(bom))

def _edit(body, session):
    st = _boms()
    bom = st.get(body.get("id"))
    if not bom:
        return core.fail("数据不存在或已经被删除")
    name = str(body.get("name") or bom.get("name") or "").strip()
    if not name:
        return core.fail("请填写BOM名称")
    has_struct = body.get("structure_list") is not None or body.get("materials") is not None
    rows = _norm_rows(body) if has_struct else None
    if has_struct:
        err = _check_rows(rows)
        if err:
            return core.fail(err)
    patch = {"name": name}
    if str(body.get("code") or "").strip():
        patch["code"] = str(body.get("code")).strip()
    if "remark" in body:
        patch["remark"] = body.get("remark")
    if "goods_id" in body:
        g = _goods_row(body.get("goods_id"))
        patch["goods_id"] = body.get("goods_id")
        patch["goods"] = dict(g) if g else None
    if "big_category_id" in body or "small_category_id" in body:
        cats = core.Store("categories")
        big_id = body.get("big_category_id", bom.get("big_category_id"))
        small_id = body.get("small_category_id", bom.get("small_category_id"))
        patch["big_category_id"] = big_id
        patch["small_category_id"] = small_id
        patch["big_category"] = {"id": big_id, "name": (cats.get(big_id) or {}).get("name")} if big_id else None
        patch["small_category"] = {"id": small_id, "name": (cats.get(small_id) or {}).get("name")} if small_id else None
    if "fieldValueList" in body:
        patch["fieldValueList"] = body.get("fieldValueList") or []
    if "is_default" in body:
        patch["is_default"] = _norm_is_default(body.get("is_default"), patch.get("goods_id", bom.get("goods_id")))
    bom = st.update(bom.get("id"), patch) or bom
    if rows is not None:
        root, total, has_ref = _build_tree(bom, rows)
        bom["bom_cost_money_total"] = total
        bom["has_reference_structure"] = has_ref
        st.flush()
        _save_tree(bom, root)
    _clear_default(bom, st)
    return core.ok(_detail_payload(_boms().get(bom.get("id"))))

def _del(body, session):
    st = _boms()
    bid = body.get("id")
    if not st.get(bid):
        return core.fail("数据不存在或已经被删除")
    st.remove(bid)
    _remove_tree(bid)
    return core.ok()

def _batch_del(body, session):
    st = _boms()
    ids = body.get("ids") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    n = 0
    for bid in ids:
        if st.get(bid):
            st.remove(bid)
            _remove_tree(bid)
            n += 1
    return core.ok(count=n)

def _enable(body, session):
    return _toggle(body, 1)

def _disable(body, session):
    return _toggle(body, 2)

def _toggle(body, is_enable):
    st = _boms()
    bom = st.get(body.get("id"))
    if not bom:
        return core.fail("数据不存在或已经被删除")
    st.update(bom.get("id"), {"is_enable": is_enable})
    return core.ok()

def _structure_tree(body, session):
    bid = body.get("goods_bom_id") or body.get("id")
    bom = _boms().get(bid)
    if not bom:
        return core.fail("数据不存在或已经被删除")
    tree = _get_tree_any(bom)
    if tree is None:
        tree = _root_node(bom, [])
    root = _resp_node(tree, {str(bom.get("id"))})
    if str(body.get("lazy_load") or "2") == "1":
        # 懒加载：仅回第一层（原站前端仅用 lazy_load:2 全树）
        for c in root.get("children") or []:
            c.pop("children", None)
    return core.ok(root)

def _first_level(body, session, only_enable=False):
    bid = body.get("goods_bom_id") or body.get("id")
    bom = _boms().get(bid)
    if not bom:
        return core.fail("数据不存在或已经被删除")
    if only_enable and str(bom.get("is_enable")) != "1":
        return core.ok([], count=0)
    tree = _get_tree_any(bom)
    rows = [_resp_node(c, {str(bom.get("id"))}) for c in ((tree or {}).get("children") or [])]
    return core.ok(rows, count=len(rows))

ROUTES = {
    ("goods_bom", "list"): _list,
    ("goods_bom", "detail"): _detail,
    ("goods_bom", "add"): _add,
    ("goods_bom", "edit"): _edit,
    ("goods_bom", "del"): _del,
    ("goods_bom", "batch_del"): _batch_del,
    ("goods_bom", "enable"): _enable,
    ("goods_bom", "disable"): _disable,
    ("goods_bom", "structure_tree"): _structure_tree,
    ("goods_bom", "get_bom_first_level_list"): _first_level,
    ("goods_bom", "get_bom_first_level_enable_list"): lambda b, s: _first_level(b, s, only_enable=True),
}
