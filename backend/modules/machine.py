# -*- coding: utf-8 -*-
"""B13 机台管理 / 岗位安排 / 工序附加组件 三小域。
依据：
- 基础 13-生产管理其他页面.md §1.10 机台管理（/machine_platform/list → /machine/list_by_filter_view）、
  §1.11 岗位安排（/station/list，行=用户：姓名/部门/角色/状态/生产工序/质检工序）、
  §1.13 工序附加组件（/process_additional_component/list → 原站接口 /craft_attach_module/list，列：标题/字段/备注/创建人/创建时间）
- deep/11-设备维保域.md §设备表单（机台=设备镜像：同步创建机台 is_sync_machine；表单校验 名称必填 max50、
  设备状态必选、类别必选、车间末级取值；设备状态为租户字典 /device_status/*，本企业实测 33350正常/63110运行中/33351维修中）
- deep/18-报工与工时工资.md §6（craft_attach_module 主数据 + 挂接 pub_craft.report/quality_attach_module_id）
- deep/06-导入解析与端点攻坚.md §4.4（真实存量 2 条：id=2056 重量、id=3257 尺寸，各含 1 个字段）
- deep/19-质检体系.md（抽检详情检验项取自质检附加组件 get_attach_module_inspect_field_list → 字段表同源）
数据：
- data/machines.json：api_dump/dd_machine.json 原样 10 条（种子已有，直接 Store("machines")）
- data/stations.json / data/process_additional_components.json：本模块 import 时按需合成（合成行带 "_seed":1）
注意：原站机台下拉为 /dropdown/get_machine_list（前端字典），机台列表页走 /machine/list_by_filter_view，
  两者字段同形（dd_machine.json 即该形状）；status_count 未见样例，不实现。
"""
import os
import time
import core

# ---------------- 通用小工具（模块内自持，不跨模块 import） ----------------
BOOL_YES, BOOL_NO = 1, 2


def _alive(row):
    return str(row.get("del", 2)) != "1"


def _as_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ids(v):
    """任意标量/逗号串/数组 → str id 列表。"""
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


def _new_id(base, i=0):
    return int(time.time() * 1000) % 100000000 + base + i


def _enable_val(v, default=1):
    """is_enable 归一：种子行布尔 true/false（dd_machine/dd_user 原样），参数 1/2（BOOL 枚举）→ 统一 1/2。"""
    if v is None or v == "":
        return default
    if v is True:
        return BOOL_YES
    if v is False:
        return BOOL_NO
    n = _as_int(v)
    return BOOL_NO if n == 2 else BOOL_YES


def _user_brief(u):
    """创建人/用户摘要（同 craft.py _set_create_user 口径）。"""
    return {"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name") or u.get("name"),
            "avatar": u.get("avatar"), "type_name": u.get("type_name") or "普通用户",
            "qr_code_url": u.get("qr_code_url")}


def _set_create_user(row, session):
    if not row.get("create_user_id"):
        row["create_user_id"] = (session or {}).get("id")
    if not row.get("create_user"):
        row["create_user"] = _user_brief(session or {})
    return row


def _by_id(store_name):
    idx = {}
    for r in core.Store(store_name).all():
        idx[str(r.get("id"))] = r
    return idx


def _craft_names(idx, ids):
    out = []
    for i in _ids(ids):
        c = idx.get(i)
        if c and _alive(c) and c.get("name"):
            out.append(c["name"])
    return ",".join(out)


def _craft_ids(v):
    return [_as_int(x) for x in _ids(v) if _as_int(x) is not None]


# ---------------- 种子合成（stations / process_additional_components） ----------------
SEED_TS = "2026-01-04 15:19:14"   # 与 waste/machine 等预置数据同一时刻（waste_all.json 口径）

# 真实存量（deep/06 §4.4 实测）：id=2056 重量、id=3257 尺寸，各含 1 个字段；字段形状参照
# deep/11 §维保内容 检查项表（检查项/字段类型/是否必填/检查项说明/字段名）+ deep/19 抽检检验项回读。
_PAC_SEED_FIELDS = {
    2056: [{"id": 4601434, "label": "重量", "name": "weight", "type": 1, "type_name": "文本",
            "must_fill": BOOL_NO, "describe": "报工时填写实际重量(kg)"}],
    3257: [{"id": 4601435, "label": "尺寸", "name": "size", "type": 1, "type_name": "文本",
            "must_fill": BOOL_YES, "describe": "质检时填写关键尺寸(mm)"}],
}


def _synth_station_rows():
    """岗位安排种子：行=用户（13 报告 §1.11 行结构），工序按 车间→工序 分组轮转组合（合成，_seed:1）。"""
    users = [u for u in core.Store("users").all() if _alive(u)]
    workshops = [w for w in core.Store("workshops").all() if _alive(w)]
    crafts = [c for c in core.Store("crafts").all() if _alive(c)]
    crafts_idx = {str(c.get("id")): c for c in crafts}
    by_ws, free = {}, []
    for c in crafts:                      # 按工序所属车间（末级）分组
        wids = c.get("workshop_path_ids") or []
        if wids:
            by_ws.setdefault(str(wids[-1]), []).append(c)
        else:
            free.append(c)
    rows = []
    for i, u in enumerate(users):
        rep, qua = [], []
        group, g, base = [], 0, 0
        if workshops:
            w = workshops[i % len(workshops)]
            group = by_ws.get(str(w.get("id"))) or free or crafts
            g, base = len(group), i // len(workshops)
        elif crafts:
            group, g, base = crafts, len(crafts), 0
        if g:
            for k in range(2 if i % 2 == 0 else 1):        # 生产工序 1~2 条
                cid = group[(base + k) % g].get("id")
                if cid not in rep:
                    rep.append(cid)
            if i % 3 != 2:                                  # 每 3 人留 1 人无质检工序
                qua.append(group[(base + 2) % g].get("id"))
        role = u.get("role") if isinstance(u.get("role"), dict) else None
        rows.append({
            "id": u.get("id"), "user_id": u.get("id"),
            "name": u.get("name"), "real_name": u.get("real_name") or u.get("name"),
            "avatar": u.get("avatar"), "mobile": u.get("mobile"),
            "department": u.get("department"), "department_id": u.get("department_id"),
            "department_path_names": u.get("department_path_names") or "",
            "role": role, "role_id": u.get("role_id"),
            "role_name": (role or {}).get("name") or "",
            "is_enable": bool(u.get("is_enable", True)),
            "reportable_craft_ids": rep, "reportable_craft_names": _craft_names(crafts_idx, rep),
            "quality_able_craft_ids": qua, "quality_able_craft_names": _craft_names(crafts_idx, qua),
            "enterprise_id": u.get("enterprise_id", 16807), "del": 2,
            "created_at": SEED_TS, "updated_at": SEED_TS, "_seed": 1,
        })
    return rows


def _synth_pac_rows():
    """工序附加组件种子：deep/06 实测存量 2 条（重量/重量字段各 1），行带 _seed:1。"""
    cu = None
    for u in core.Store("users").all():
        if u.get("id") == 250574:         # 佳H（实测账号）
            cu = _user_brief(u)
            break
    cu = cu or (_user_brief(core.Store("users").all()[0]) if core.Store("users").all() else {})
    rows = []
    for pid, title, remark in ((2056, "重量", ""), (3257, "尺寸", "记录关键工序尺寸数据")):
        rows.append({
            "id": pid, "title": title, "remark": remark,
            "inspection_items": [dict(x) for x in _PAC_SEED_FIELDS[pid]],
            "create_user_id": cu.get("id"), "create_user": cu,
            "enterprise_id": 16807, "del": 2, "fieldValueList": [],
            "created_at": SEED_TS, "updated_at": SEED_TS, "_seed": 1,
        })
    return rows


def _ensure_seeds():
    """种子文件缺失时按上表合成落盘（幂等；只认文件不存在，不覆盖已有数据）。"""
    for name, synth in (("stations", _synth_station_rows),
                        ("process_additional_components", _synth_pac_rows)):
        if not os.path.exists(os.path.join(core.DATA_DIR, name + ".json")):
            st = core.Store(name)
            st.rows = synth()
            st.flush()


try:
    _ensure_seeds()
except Exception:                     # 种子合成失败不阻断服务加载
    pass


# ========================= machine（机台管理） =========================
# 设备状态字典（租户自定义，deep/11 §设备状态字典；本企业实测三态，从种子行自举 + 静态兜底）
_FALLBACK_STATUS = {"id": 33350, "name": "正常", "color": "#00af57",
                    "enterprise_id": 16807, "status_type": 1, "status_type_name": "完好"}


def _status_registry():
    reg = {}
    for r in core.Store("machines").all():
        ds = r.get("device_status")
        if isinstance(ds, dict) and ds.get("id") is not None:
            reg.setdefault(str(ds.get("id")), ds)
    return reg


def _category_registry():
    reg = {}
    for r in core.Store("machines").all():
        c = r.get("category")
        if isinstance(c, dict) and c.get("id") is not None:
            reg.setdefault(str(c.get("id")), c)
    return reg


def _workshop_path(idx, wid):
    ids, names, cur, guard = [], [], idx.get(str(wid)) if wid is not None else None, 0
    while cur and guard < 20:
        ids.insert(0, cur.get("id"))
        names.insert(0, cur.get("name"))
        cur = idx.get(str(cur.get("parent_id")))
        guard += 1
    return ids, names


def _produce_line_names(idx, ids):
    out = []
    for i in _ids(ids):
        p = idx.get(i)
        if p and p.get("name"):
            out.append(p["name"])
    return ",".join(out)


def _machine_decorate(row, status_reg, cat_reg, crafts):
    """列表行装饰：补 device_status/category 对象引用 + 生产工序（引用该机台的公共工序，13 §1.10 tooltip）。"""
    out = dict(row)
    out.setdefault("avatars", [])
    out.setdefault("type_name", "")
    ds = row.get("device_status")
    if not isinstance(ds, dict):
        ds = status_reg.get(str(row.get("device_status_id"))) or _FALLBACK_STATUS
    out["device_status"] = ds
    out["device_status_id"] = ds.get("id")
    cat = row.get("category")
    if not isinstance(cat, dict):
        cat = cat_reg.get(str(row.get("category_id")))
    out["category"] = cat
    if not out.get("category_path_names") and isinstance(cat, dict):
        out["category_path_names"] = cat.get("name_path") or cat.get("name") or ""
    if not out.get("category_path_ids") and isinstance(cat, dict):
        out["category_path_ids"] = [cat.get("id")]
    mid = str(row.get("id"))
    names = [c.get("name") for c in crafts
             if mid in [str(x) for x in (c.get("device_ids") or [])]]
    out["reportable_craft_names"] = ",".join(x for x in names if x)
    if not out.get("produce_line_names") and out.get("produce_line_ids"):
        out["produce_line_names"] = _produce_line_names(_by_id("produce_lines"), out.get("produce_line_ids"))
    return out


def _machine_rows():
    return [r for r in core.Store("machines").all() if _alive(r)]


def _filter_machines(rows, body):
    """筛选：机台编号(code)/机台名称(name)/生产工序(pub_craft_name)/机台状态(device_status_id)/创建时间起止/启用禁用（13 §1.10）。"""
    if str(body.get("code") or "").strip():
        rows = [r for r in rows if _like(r.get("code"), body.get("code"))]
    if str(body.get("name") or "").strip():
        rows = [r for r in rows if _like(r.get("name"), body.get("name"))]
    pcn = str(body.get("pub_craft_name") or "").strip()
    if pcn:
        hit = set()
        for c in core.Store("crafts").all():
            if _alive(c) and (_like(c.get("name"), pcn) or _like(c.get("jianpin"), pcn)):
                hit |= {str(x) for x in (c.get("device_ids") or [])}
        rows = [r for r in rows if str(r.get("id")) in hit]
    if body.get("device_status_id") not in (None, ""):
        rows = [r for r in rows if str(r.get("device_status_id")) == str(body.get("device_status_id"))]
    if body.get("is_enable") not in (None, ""):
        rows = [r for r in rows if _enable_val(r.get("is_enable")) == _enable_val(body.get("is_enable"))]
    for f in ("created_at_start", "created_at_end"):
        v = str(body.get(f) or "").strip()
        if not v:
            continue
        rows = [r for r in rows if (
            str(r.get("created_at") or "") >= v if f.endswith("_start")
            else str(r.get("created_at") or "") <= v + " 23:59:59")]
    return rows


def _machine_list(body, session):
    rows = _filter_machines(_machine_rows(), body)
    crafts = [c for c in core.Store("crafts").all() if _alive(c)]
    status_reg, cat_reg = _status_registry(), _category_registry()
    data = [_machine_decorate(r, status_reg, cat_reg, crafts) for r in rows]
    return _paged(data, body)


def _machine_list_by_filter_view(body, session):
    """列表页主接口（13 §1.10）：视图 tab 体系参数 filter_view_id 接受但机台无视图枚举（FILTER_MODULE_ENUMS 未登记），同 list。"""
    return _machine_list(body, session)


def _machine_from_body(body):
    """机台表单可写字段（镜像设备表单，deep/11 §设备表单：车间/类别取末级，状态必选）。"""
    patch = {}
    for f in ("name", "code", "standard", "single_day_running_time",
              "standard_efficiency", "remark"):
        if f in body:
            patch[f] = body.get(f)
    if "avatars" in body:
        patch["avatars"] = body.get("avatars") or []
    if isinstance(body.get("device_status"), dict):
        patch["device_status"] = body.get("device_status")
        patch["device_status_id"] = body.get("device_status").get("id")
    elif body.get("device_status_id") not in (None, ""):
        patch["device_status_id"] = body.get("device_status_id")
    if isinstance(body.get("category"), dict):
        patch["category"] = body.get("category")
        patch["category_id"] = body.get("category").get("id")
    elif body.get("category_id") not in (None, ""):
        patch["category_id"] = body.get("category_id")
    if body.get("category_path_ids") not in (None, ""):
        pids = [_as_int(x) for x in _ids(body.get("category_path_ids")) if _as_int(x) is not None]
        patch["category_path_ids"] = pids
        if pids:                                   # 末级为 category_id（deep/11 设备表单保存逻辑）
            patch["category_id"] = pids[-1]
    if body.get("workshop_id") not in (None, ""):
        wids, wnames = _workshop_path(_by_id("workshops"), body.get("workshop_id"))
        patch["workshop_id"] = wids[-1] if wids else None
        patch["workshop_path_ids"] = wids
        patch["workshop_path_names"] = "/".join(wnames)
    if body.get("produce_line_ids") not in (None, "") or body.get("produce_line_id") not in (None, ""):
        pl = body.get("produce_line_ids")
        if pl in (None, ""):
            pl = [body.get("produce_line_id")]
        patch["produce_line_ids"] = [_as_int(x) for x in _ids(pl) if _as_int(x) is not None]
    if "is_enable" in body:
        patch["is_enable"] = _enable_val(body.get("is_enable")) == BOOL_YES
    return patch


def _machine_add(body, session):
    name = str(body.get("name") or "").strip()
    if not name:
        return core.fail("请输入机台名称")
    if len(name) > 50:
        return core.fail("长度超出限制")
    store = core.Store("machines")
    patch = _machine_from_body(body)
    row = {"name": name, "standard": patch.pop("standard", None),
           "avatars": patch.pop("avatars", []), "enterprise_id": (session or {}).get("enterprise_id") or 16807,
           "is_enable": True, "type_name": ""}
    code = str(patch.pop("code", "") or "").strip()
    if not code:                                   # 编号忽略将自动生成：数值序号顺延（dd 样例 "01"，推断）
        nums = [_as_int(r.get("code")) for r in store.all() if _as_int(r.get("code")) is not None]
        code = "%02d" % ((max(nums) + 1) if nums else 1)
    row["code"] = code
    row.update(patch)
    if "is_enable" not in patch:
        row["is_enable"] = True
    row.setdefault("device_status_id", _FALLBACK_STATUS["id"])   # 状态必选：缺省给 正常
    row.setdefault("workshop_path_ids", [])
    row.setdefault("workshop_path_names", "")
    row.setdefault("category_path_ids", [])
    row.setdefault("category_path_names", "")
    row = _set_create_user(row, session)
    store.add(row)
    return core.ok(_machine_decorate(row, _status_registry(), _category_registry(),
                                     [c for c in core.Store("crafts").all() if _alive(c)]))


def _machine_edit(body, session):
    store = core.Store("machines")
    row = store.get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    name = str(body.get("name") or "").strip()
    if name:
        if len(name) > 50:
            return core.fail("长度超出限制")
        row["name"] = name
    patch = _machine_from_body(body)
    patch.pop("name", None)
    for k, v in list(patch.items()):
        if v in (None, "", []):
            patch.pop(k)
    row.update(patch)
    row["updated_at"] = core.now()
    store.flush()
    return core.ok(_machine_decorate(row, _status_registry(), _category_registry(),
                                     [c for c in core.Store("crafts").all() if _alive(c)]))


def _machine_del(body, session):
    row = core.Store("machines").get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    core.Store("machines").update(body.get("id"), {"del": 1})
    return core.ok()


# ========================= station（岗位安排） =========================
def _station_rows():
    return [r for r in core.Store("stations").all() if _alive(r)]


def _station_list(body, session):
    """列表（13 §1.11 筛选）：姓名/部门/角色/生产工序/质检工序 + 视图切换 全部/启用/禁用 → is_enable。"""
    rows = _station_rows()
    if str(body.get("user_name") or "").strip():
        kw = body.get("user_name")
        rows = [r for r in rows if _like(r.get("real_name"), kw) or _like(r.get("name"), kw)]
    if str(body.get("department") or "").strip():
        rows = [r for r in rows if _like(r.get("department_path_names"), body.get("department"))]
    if body.get("role_id") not in (None, ""):
        rows = [r for r in rows if str(r.get("role_id")) == str(body.get("role_id"))]
    if body.get("reportable_craft_id") not in (None, ""):
        rows = [r for r in rows if str(body.get("reportable_craft_id"))
                in [str(x) for x in (r.get("reportable_craft_ids") or [])]]
    if body.get("quality_able_craft_id") not in (None, ""):
        rows = [r for r in rows if str(body.get("quality_able_craft_id"))
                in [str(x) for x in (r.get("quality_able_craft_ids") or [])]]
    if body.get("is_enable") not in (None, ""):
        rows = [r for r in rows if _enable_val(r.get("is_enable")) == _enable_val(body.get("is_enable"))]
    return _paged(rows, body)


def _refresh_craft_names(row):
    idx = {str(c.get("id")): c for c in core.Store("crafts").all() if _alive(c)}
    row["reportable_craft_names"] = _craft_names(idx, row.get("reportable_craft_ids") or [])
    row["quality_able_craft_names"] = _craft_names(idx, row.get("quality_able_craft_ids") or [])
    return row


def _station_add(body, session):
    """新增岗位安排：绑定用户 + 生产/质检工序（合成种子之外的新行）。"""
    uid = body.get("user_id") or body.get("id")
    if uid in (None, ""):
        return core.fail("请选择用户")
    u = core.Store("users").get(uid)
    if not u or not _alive(u):
        return core.fail("用户不存在")
    store = core.Store("stations")
    for r in store.all():                          # 一人一行
        if _alive(r) and str(r.get("user_id")) == str(u.get("id")):
            return core.fail("该用户已有岗位安排，请直接修改")
        if str(r.get("user_id")) == str(u.get("id")):
            store.remove(r.get("id"))              # 清掉历史软删行，避免 id 冲突
    role = u.get("role") if isinstance(u.get("role"), dict) else None
    row = {"user_id": u.get("id"), "name": u.get("name"),
           "real_name": u.get("real_name") or u.get("name"), "avatar": u.get("avatar"),
           "department": u.get("department"), "department_id": u.get("department_id"),
           "department_path_names": u.get("department_path_names") or "",
           "role": role, "role_id": u.get("role_id"),
           "role_name": (role or {}).get("name") or "",
           "is_enable": bool(u.get("is_enable", True)),
           "reportable_craft_ids": _craft_ids(body.get("reportable_craft_ids")),
           "quality_able_craft_ids": _craft_ids(body.get("quality_able_craft_ids")),
           "enterprise_id": u.get("enterprise_id", 16807)}
    _refresh_craft_names(row)
    store.add(row)
    return core.ok(row)


def _station_edit(body, session):
    """修改岗位安排弹窗（13 §1.11）：生产工序/质检工序多选；已禁用用户不可修改工序。"""
    store = core.Store("stations")
    row = store.get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    has_craft_patch = ("reportable_craft_ids" in body) or ("quality_able_craft_ids" in body)
    if has_craft_patch and not row.get("is_enable"):
        return core.fail("已禁用的用户不可修改")
    if "reportable_craft_ids" in body:
        row["reportable_craft_ids"] = _craft_ids(body.get("reportable_craft_ids"))
    if "quality_able_craft_ids" in body:
        row["quality_able_craft_ids"] = _craft_ids(body.get("quality_able_craft_ids"))
    if has_craft_patch:
        _refresh_craft_names(row)
    if "is_enable" in body:
        row["is_enable"] = _enable_val(body.get("is_enable")) == BOOL_YES
    row["updated_at"] = core.now()
    store.flush()
    return core.ok(row)


def _station_del(body, session):
    row = core.Store("stations").get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    core.Store("stations").update(body.get("id"), {"del": 1})
    return core.ok()


# ========================= process_additional_component（工序附加组件） =========================
# 原站接口族为 /craft_attach_module/{list,add,edit,del}（deep/18 §6），前端页面路由
# /process_additional_component/list（13 §1.13）——本文件两个模块名同注册同族 handler。

def _pac_rows():
    return [r for r in core.Store("process_additional_components").all() if _alive(r)]


def _pac_norm_items(items):
    """字段条目归一：保留原形状，缺 id 补号（形状参照 deep/11 检查项表：检查项/字段类型/是否必填/检查项说明/字段名）。"""
    out = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        it = dict(it)
        it.setdefault("id", _new_id(4602000, i))
        it.setdefault("type", 1)
        it.setdefault("type_name", "文本")
        it.setdefault("must_fill", BOOL_NO)
        it.setdefault("describe", "")
        out.append(it)
    return out


def _pac_list(body, session):
    """列表（13 §1.13）：搜索框『支持标题查询』→ title/keyword。"""
    rows = _pac_rows()
    kw = str(body.get("title") or body.get("keyword") or "").strip()
    if kw:
        rows = [r for r in rows if _like(r.get("title"), kw)]
    return _paged(rows, body)


def _pac_add(body, session):
    title = str(body.get("title") or "").strip()
    if not title:
        return core.fail("请输入标题")
    row = {"title": title,
           "remark": str(body.get("remark") or ""),
           "inspection_items": _pac_norm_items(body.get("inspection_items") or body.get("field_list")),
           "enterprise_id": (session or {}).get("enterprise_id") or 16807,
           "fieldValueList": []}
    row = _set_create_user(row, session)
    core.Store("process_additional_components").add(row)
    return core.ok(row)


def _pac_edit(body, session):
    store = core.Store("process_additional_components")
    row = store.get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    title = str(body.get("title") or "").strip()
    if title:
        row["title"] = title
    if "remark" in body:
        row["remark"] = str(body.get("remark") or "")
    if body.get("inspection_items") is not None or body.get("field_list") is not None:
        row["inspection_items"] = _pac_norm_items(
            body.get("inspection_items") if body.get("inspection_items") is not None else body.get("field_list"))
    row["updated_at"] = core.now()
    store.flush()
    return core.ok(row)


def _pac_del(body, session):
    row = core.Store("process_additional_components").get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("数据不存在")
    core.Store("process_additional_components").update(body.get("id"), {"del": 1})
    return core.ok()


# ========================= 路由注册 =========================
ROUTES = {
    # ---- machine 机台管理（13 §1.10：列表 /machine/list_by_filter_view + 增删改）----
    ("machine", "list"): _machine_list,
    ("machine", "list_by_filter_view"): _machine_list_by_filter_view,
    ("machine", "add"): _machine_add,
    ("machine", "edit"): _machine_edit,
    ("machine", "del"): _machine_del,
    # ---- station 岗位安排（13 §1.11；原站列表走 /user/enterprise_user_list，此处按任务口径挂 station 模块）----
    ("station", "list"): _station_list,
    ("station", "add"): _station_add,
    ("station", "edit"): _station_edit,
    ("station", "del"): _station_del,
    # ---- process_additional_component 工序附加组件（13 §1.13，任务口径模块名）----
    ("process_additional_component", "list"): _pac_list,
    ("process_additional_component", "add"): _pac_add,
    ("process_additional_component", "edit"): _pac_edit,
    ("process_additional_component", "del"): _pac_del,
    # ---- 同族别名：原站真实模块名 craft_attach_module（deep/18 §6 接口族）----
    ("craft_attach_module", "list"): _pac_list,
    ("craft_attach_module", "add"): _pac_add,
    ("craft_attach_module", "edit"): _pac_edit,
    ("craft_attach_module", "del"): _pac_del,
}
