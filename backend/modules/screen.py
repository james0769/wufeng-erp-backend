# -*- coding: utf-8 -*-
"""数据大屏域（data_screen，demo 级）。
依据：api_dump/data_screen_list.json（8 块真实大屏逐字）、data_screen_detail_334674.json、
data_screen_not_login_detail_334674.json、verify_visit_pwd_test.json + deep\\05-数据大屏体系.md。

覆盖（deep\\05 §1.4 六接口 + _screen 自有三接口）：
- /data_screen/list：大屏管理列表页数据源（含侧栏动态菜单）。外层 {success, count, data[], code:0,
  enterprise_config_update_time}，data 为全量数组（实测空体也返回全量），字段逐字照实测样例
  （含 config 布局串 / ds_token / layout_type_name / sale_module_name / is_enable_name 派生列）
- /data_screen/detail：编辑态详情（含 fieldValueList，外层带 enterprise_config_update_time）
- /data_screen/not_login_detail：浏览态详情（剔除 visit_pwd 与 fieldValueList，外层无 enterprise_config_update_time，照实测样例）
- /data_screen/verify_visit_pwd：访问密码校验——对（含免密大屏空密码）code:0 + 记录（同浏览态剔除）；
  错 code:1 / success:false（msg 文案实测样例未含，推断）
- /data_screen/add：新增（config 固定 "{}"、name=menu_name——31.chunk save() 逐字；菜单名必填且 ≤7 字；
  布局/功能模块必填）
- /data_screen/edit：整记录回传式编辑（_screen 编辑器 iB(O) 同款），仅白名单字段落库，派生 *_name 同步重算
- /data_screen/enable / disable：启停（is_enable 1/2 + is_enable_name）
- /data_screen/del：删除

种子：backend/data/data_screens.json = data_screen_list.json 的 data 数组原样 8 行（无 _seed 标记）。

大屏组件数值端点（_screen 独立 SPA 直连的 statistics/big_board_*、spectaculars/* 族）走独立契约，
本轮不复刻（deep\\05 §2.3/§5：与主应用 /intelli_report/* 完全不共用）；前端大屏渲染页按 config
布局串 + deep\\05 §3 注册表对齐即可。
"""
import uuid

import core

# BORD_LAYOUT 五布局（deep\\05 §1.2，主应用 BORD_LAYOUT.*.name 逐字）
LAYOUT_NAMES = {1: "默认", 2: "单模块", 3: "上下双模块", 4: "左右双模块", 5: "四模块"}
# FUNCTIONAL_MODULE（§1.1/§3.8）：仓储 v4 服务端返回"客户/仓储模块"，列表页前端强制显示"仓库模块"
MODULE_NAMES = {1: "生产模块", 2: "售后模块", 3: "设备维保模块", 4: "客户/仓储模块"}
ENABLE_NAMES = {1: "已启用", 2: "已停用"}

# 编辑可落库字段（其余回传字段如 id/created_at/ds_token 一律不动）
_EDITABLE = ("menu_name", "name", "description", "visit_pwd", "config", "layout_type", "sale_module", "is_enable")


def _store():
    return core.Store("data_screens")


def _alive(row):
    return str(row.get("del", 2)) == "2"


def _public(row):
    """浏览态记录（not_login_detail / verify_visit_pwd 实测样例）：剔除 visit_pwd 与 fieldValueList，保持原字段序。"""
    return {k: v for k, v in row.items() if k not in ("visit_pwd", "fieldValueList")}


# ---------------- 查询 ----------------
def _list(body, session):
    """大屏列表：{success, count, data[], code:0, enterprise_config_update_time}（data_screen_list.json 外层逐字）。"""
    rows = [r for r in _store().all() if _alive(r)]
    return {"success": True, "count": len(rows), "data": rows, "code": 0,
            "enterprise_config_update_time": core.now()}


def _detail(body, session):
    """编辑态详情：记录原样（含 visit_pwd + fieldValueList），外层带 enterprise_config_update_time。"""
    row = _store().get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("404")
    return {"success": True, "data": row, "enterprise_config_update_time": core.now()}


def _not_login_detail(body, session):
    """浏览态详情（_screen SPA 进入即调）：剔除 visit_pwd/fieldValueList，外层仅 success+data（照实测样例）。"""
    row = _store().get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("404")
    return {"success": True, "data": _public(row)}


def _verify_visit_pwd(body, session):
    """访问密码校验（LoginComponent 先静默空密码验一次）：免密大屏空密码也放行（实测确定）；
    验证通过 code:0 + 记录（同浏览态剔除），失败 code:1 + success:false（msg 推断，原样例未含失败分支）。"""
    row = _store().get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("404")
    stored = row.get("visit_pwd")
    supplied = body.get("visit_pwd")
    if stored in (None, "") or str(supplied or "") == str(stored):
        return {"success": True, "code": 0, "data": _public(row)}
    return {"success": False, "code": 1, "msg": "访问密码错误", "post_track": 2}


# ---------------- 维护（大屏管理列表页） ----------------
def _add(body, session):
    """新增大屏：config 固定 "{}"（_screen 端按 模块×布局 套默认组件 Map，deep\\05 §1.3/§3.6）、name=menu_name。
    校验逐字（31.chunk 表单）：菜单名必填/≤7 字、功能模块必填；布局由五宫格弹窗选择必有值。"""
    menu_name = str(body.get("menu_name") or "").strip()
    if not menu_name:
        return core.fail("请输入菜单名")
    if len(menu_name) > 7:
        return core.fail("长度超出限制,最大长度为7")
    try:
        layout = int(body.get("layout_type") or 0)
    except Exception:
        layout = 0
    if layout not in LAYOUT_NAMES:
        return core.fail("请选择大屏布局")
    try:
        module = int(body.get("sale_module") or 0)
    except Exception:
        module = 0
    if module not in MODULE_NAMES:
        return core.fail("请选择功能模块")
    row = {
        "enterprise_id": (session or {}).get("enterprise_id", 16807),
        "del": 2, "sys_default": 2,
        "sale_module": module,
        "menu_name": menu_name,
        "layout_type": layout,
        "visit_pwd": str(body["visit_pwd"]) if body.get("visit_pwd") not in (None, "") else None,
        "name": menu_name,
        "description": body.get("description") if body.get("description") not in (None, "") else None,
        "config": "{}",
        "is_enable": 1,
        "fieldValueList": [],
        "layout_type_name": LAYOUT_NAMES[layout],
        "sale_module_name": MODULE_NAMES[module],
        "ds_token": uuid.uuid4().hex,          # 每块大屏独立令牌（32 位 hex，电视端取数用，§1.1）
        "is_enable_name": ENABLE_NAMES[1],
    }
    return core.ok(_store().add(row))


def _edit(body, session):
    """编辑：整记录回传 + 变更字段（_screen iB(O) 与列表页弹窗共用）；仅白名单字段落库，派生列重算。"""
    s = _store()
    row = s.get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("404")
    patch = {k: body[k] for k in _EDITABLE if k in body}
    try:
        if "layout_type" in patch and int(patch["layout_type"]) in LAYOUT_NAMES:
            patch["layout_type_name"] = LAYOUT_NAMES[int(patch["layout_type"])]
    except Exception:
        pass
    try:
        if "sale_module" in patch and int(patch["sale_module"]) in MODULE_NAMES:
            patch["sale_module_name"] = MODULE_NAMES[int(patch["sale_module"])]
    except Exception:
        pass
    try:
        if "is_enable" in patch and int(patch["is_enable"]) in ENABLE_NAMES:
            patch["is_enable_name"] = ENABLE_NAMES[int(patch["is_enable"])]
    except Exception:
        pass
    return core.ok(s.update(body.get("id"), patch) or row)


def _toggle(body, session, state):
    s = _store()
    row = s.get(body.get("id"))
    if not row or not _alive(row):
        return core.fail("404")
    s.update(body.get("id"), {"is_enable": state, "is_enable_name": ENABLE_NAMES[state]})
    return core.ok()


def _del(body, session):
    _store().remove(body.get("id"))
    return core.ok()


ROUTES = {
    ("data_screen", "list"): _list,
    ("data_screen", "detail"): _detail,
    ("data_screen", "not_login_detail"): _not_login_detail,
    ("data_screen", "verify_visit_pwd"): _verify_visit_pwd,
    ("data_screen", "add"): _add,
    ("data_screen", "edit"): _edit,
    ("data_screen", "enable"): lambda b, s: _toggle(b, s, 1),
    ("data_screen", "disable"): lambda b, s: _toggle(b, s, 2),
    ("data_screen", "del"): _del,
}
