# -*- coding: utf-8 -*-
"""B12 系统管理域（sysadmin）：用户/角色/部门(category busi_type=2)/团队/字典双表/席位/功能树。
依据：deep/14-系统管理域.md（§2 用户与组织、§3.2 席位、§4 字典双表、§8.3 密码校验）
      + deep/03-权限与版本矩阵.md（fun_ids/data_fun 序列化、席位漏斗）
实测样例：api_dump/deep_m6/user_list_p1.json（enterprise_user_list 40 行全字段）、deep_m6/role_list.json（角色5）、
        dd_team.json + deep_m6/team_tree.json（团队6）、deep_m6/department_tree.json（部门树）、
        dic_type1_unit.json + deep_m6/dic_t2/t3.json（dic 49 行）、deep_m6/category_list_bt12.json（商城分类）、
        fun_display_tree.json（功能树）、dd_enterprise_seat.json（席位 30/17|4|6）
范围与键位约定（勿与他人冲突）：
- auth.py 已持有 (user, login_by_pwd/logout/info/home/nav)，machine.py 已持有 (station,*)，quality.py 持有
  (user, quality_able_user_list)——本文件只新增 user 域管理动作，不重定义上述键。
- goods.py 持有 (dropdown, category/unit/team/all_user/...)，本文件只新增 (dropdown, dic/enterprise_seat)。
- 商品类别（category busi_type=5）数据源与 goods.py 共用 Store("categories")（读时投影成字典表全形状，
  写入追加全形状行——goods.py 仅消费 id/name/parent_id/del，形状兼容）；部门(busi_type=2)在
  Store("departments")、商城分类(12)在 Store("mall_categories")，与商品类别物理隔离，避免污染商品下拉。
种子（backend/data/，均 dump 原样行，无合成行故无 _seed 标记）：roles/departments/mall_categories/
dics/fun_display_tree/enterprise_seats 由本模块生成；users/teams 沿用既有种子。
席位口径说明（deep/14 §12.1 待裁决）：use_seat_count 取 dd_enterprise_seat.json 实测值（17/4/6），
与用户表 seat 开关数（38/8/12）不一致为原站口径差异，复刻按 seat_info 口径静态建模，不随用户增删联动。
"""
import re
import core

ECUT = lambda: core.now()  # enterprise_config_update_time（原站各响应随带）

BOOL_YES, BOOL_NO = 1, 2          # 原站 BOOL：1 是 / 2 否
SEAT_NAMES = {1: "生产席位", 2: "售后席位", 3: "维保席位"}
SEAT_FIELD = {1: "produce_seat", 2: "after_sale_seat", 3: "maintenance_seat"}
TYPE_NAMES = {1: "普通用户", 2: "虚拟用户", 3: "智能设备用户"}       # USER_TYPE
CLAW_NAMES = {1: "不允许使用", 2: "仅支持访问数据", 3: "支持访问和写入数据"}  # KCLAW
DEFAULT_AVATAR = "https://api.kgd.ltd/weapp/images/default_avatar.png"

# 弱密码黑名单 50 词（deep/14 §8.3，user.chunk HF 函数逐字，toLowerCase 比对）
WEAK_PWDS = set("""abc123 abc123456 abcdef abcdefg abcdefgh password password1 password123 password123456
passwd pass123 pass1234 passw0rd p@ssword p@ssw0rd qwerty qwerty123 qwertyuiop asdfgh asdfghjk zxcvbn
zxcvbnm 123qwe qwe123 1q2w3e 1qaz2wsx qazwsx admin123 admin123456 administrator root123 test123 welcome
welcome123 letmein iloveyou woaini woaini123 aaaaaa aaaaaaaa aaaaaaa123 111111a a123456 123456a 123456abc
abcabc 123abc changeme default secret""".split())


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _kw(body, *names):
    """去空白取首个非空字符串参。"""
    for n in names:
        v = body.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _ids(v):
    """fun_ids / user_ids 兼容 数组 与 逗号串 两种入参 → 逗号串。"""
    if v in (None, ""):
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v if x not in (None, ""))
    return str(v)


def _page(rows, body, default_size=10):
    try:
        pageNo = max(1, _int(body.get("pageNo"), 1))
    except Exception:
        pageNo = 1
    try:
        pageSize = max(1, _int(body.get("pageSize"), default_size))
    except Exception:
        pageSize = default_size
    return rows[(pageNo - 1) * pageSize: pageNo * pageSize], len(rows)


def _arr_ok(rows, total=None, paged=True):
    """原站列表族信封：{success, count, data:[数组], code:0, enterprise_config_update_time}（user/role/dic/category 实测同构）。"""
    return {"success": True, "count": total if total is not None else len(rows),
            "data": rows, "code": 0, "enterprise_config_update_time": ECUT()}


def _data_ok(data):
    """无 count 信封：{success, data, enterprise_config_update_time}（team_tree/dd_dic 同构）。"""
    return {"success": True, "data": data, "enterprise_config_update_time": ECUT()}


def _check_pwd(pwd, name="", mobile=""):
    """密码三段校验（deep/14 §8.3，文案逐字）。"""
    pwd = str(pwd or "")
    if len(pwd) < 6 or re.fullmatch(r"\d+", pwd):
        return "密码长度不少于 6 位，且不能为纯数字"
    if pwd.lower() in WEAK_PWDS:
        return "密码过于常见，容易被猜到，请更换"
    if pwd == mobile or pwd in (name, name + "123", name + "123456"):
        return "密码不能包含账号等个人信息，请更换"
    return None


# ================= 用户管理（/user，deep/14 §2.1） =================

def _roles_map():
    return {str(r.get("id")): r for r in core.Store("roles").all()}


def _dept_map():
    return {str(r.get("id")): r for r in core.Store("departments").all()}


def _team_brief(tid, teams=None):
    t = (teams or {}).get(str(tid))
    return {"id": tid, "name": t.get("name") if t else str(tid)}


def _norm_user(r):
    """users 行（dd_all_user 形状 / 本模块 add 生成形状）→ enterprise_user_list 全字段形状
    （字段集对齐 deep_m6/user_list_p1.json 逐字清单；种子缺的字段按实测众数补默认）。"""
    uid = r.get("id")
    roles = _roles_map()
    deps = _dept_map()
    role = r.get("role")
    role_row = roles.get(str(r.get("role_id")), {})
    if role is None and role_row:
        role = {"id": role_row.get("id"), "name": role_row.get("name")}
    dep = r.get("department")
    if dep is None and r.get("department_id") is not None:
        d = deps.get(str(r.get("department_id")))
        if d:
            dep = {"id": d.get("id"), "parent_id": d.get("parent_id"), "name": d.get("name"),
                   "is_leaf": _int(d.get("is_leaf"), 1), "order_number": d.get("order_number")}
    team = r.get("team") or []
    enable = _int(1 if r.get("is_enable") in (True, 1, "1") else 2, 2)
    seats = {m: _int(r.get(f), 2 if m != 1 else 1) for m, f in SEAT_FIELD.items()}
    seat_modules = [m for m in (1, 2, 3) if seats[m] == BOOL_YES]
    return {
        "id": uid, "name": r.get("name") or "", "real_name": r.get("real_name") or "",
        "mobile": r.get("mobile") or "", "web_token": None, "wechat_token": None,
        "enterprise_id": r.get("enterprise_id", 16807),
        "updated_at": r.get("updated_at"), "created_at": r.get("created_at"),
        "create_user_id": r.get("create_user_id"), "fun_ids": r.get("fun_ids") or role_row.get("fun_ids", ""),
        "is_admin": r.get("is_admin"), "sms": None, "role_id": r.get("role_id"),
        "department_id": r.get("department_id"), "location": r.get("location"),
        "avatar": r.get("avatar"), "team_id": None, "openid": None, "work_status_id": None,
        "unionid": None, "data_fun": r.get("data_fun") or role_row.get("data_fun", ""),
        "web_open_id": None, "last_location_time": None, "address": None, "sms_mobile": None,
        "has_changed_pwd": _int(r.get("has_changed_pwd"), BOOL_NO), "mp_token": None,
        "has_novice_guide": _int(r.get("has_novice_guide"), BOOL_NO),
        "is_enable": enable, "add_enterprise_day_limit": None,
        "is_super_admin": _int(r.get("is_super_admin"), BOOL_NO),
        "is_founder": _int(r.get("is_founder"), BOOL_NO),
        "is_read_versions": _int(r.get("is_read_versions"), BOOL_NO),
        "jianpin": r.get("jianpin") or "", "del": _int(r.get("del"), BOOL_NO),
        "service_mobile": None, "service_avatar": None, "service_qr": None, "service_name": None,
        "mp_department_id": None, "induction_date": None, "station_token": None, "face": None,
        "agent_id": 5, "platform": 2, "agent_token": None, "agent_department_id": None,
        "suite_user_id": r.get("suite_user_id"), "open_userid": None,
        "external_contact_user_id": None, "agent_app_token": None, "pda_token": None,
        "ding_talk_userid": None, "ding_talk_unionid": None, "ding_talk_token": None,
        "open_api_token": None, "erp_user_id": None, "kcd_user_id": None, "jpush_id": None,
        "app_token": None, "is_template_user": BOOL_NO, "machine_token": None,
        "type": _int(r.get("type"), 1), "machine_tool_code": None, "external_agv_id": None,
        "produce_seat": seats[1], "after_sale_seat": seats[2], "maintenance_seat": seats[3],
        "ai_agent_api_token": None, "claw_fun_type": _int(r.get("claw_fun_type"), 1),
        "tv_token": None, "tv_scan_code": None,
        "has_password": bool(r.get("has_password", False)),
        "type_name": TYPE_NAMES.get(_int(r.get("type"), 1), ""),
        "claw_fun_type_name": CLAW_NAMES.get(_int(r.get("claw_fun_type"), 1), ""),
        "qr_code_url": r.get("qr_code_url") or ("https://single.kgd.ltd/#/u?qr_id=%s" % uid),
        "reportable_craft_ids": r.get("reportable_craft_ids"), "reportable_craft_names": r.get("reportable_craft_names"),
        "reportable_display_craft_names": r.get("reportable_display_craft_names"),
        "quality_able_craft_ids": r.get("quality_able_craft_ids"), "quality_able_craft_names": r.get("quality_able_craft_names"),
        "quality_able_display_craft_names": r.get("quality_able_display_craft_names"),
        "department_path_names": r.get("department_path_names") or "",
        "department_path_ids": r.get("department_path_ids") or [],
        "mp_department_path_names": "", "mp_department_path_ids": [],
        "agent_department_path_names": "", "agent_department_path_ids": [],
        "seat_sale_modules": seat_modules,
        "seat_sale_module_names": ",".join(SEAT_NAMES[m] for m in seat_modules),
        "department": dep, "work_status": None, "region_list": [],
        "team": team, "team_names": r.get("team_names") or ",".join(t.get("name") or "" for t in team),
        "role": role, "fieldValueList": r.get("fieldValueList") or [], "_raw": r,
    }


def _users_all():
    rows = [x for x in core.Store("users").all() if _int(x.get("del"), BOOL_NO) != 1]
    return [_norm_user(x) for x in rows]


def _filter_users(rows, body):
    kw = _kw(body, "name", "keyword")
    if kw:
        rows = [x for x in rows if kw in (x["name"] or "") or kw in (x["real_name"] or "")]
    for f in ("mobile",):
        v = _kw(body, f)
        if v:
            rows = [x for x in rows if v in (x[f] or "")]
    for f in ("role_id", "department_id", "type", "is_enable"):
        v = body.get(f)
        if v not in (None, ""):
            rows = [x for x in rows if str(x[f]) == str(_int(v, v))]
    v = body.get("team_id")
    if v not in (None, ""):
        rows = [x for x in rows if any(str(t.get("id")) == str(_int(v, v)) for t in x["team"])]
    v = body.get("sale_module")  # 席位筛选：用户持有该席位
    if v not in (None, ""):
        rows = [x for x in rows if _int(v) in x["seat_sale_modules"]]
    ts, te = str(body.get("created_at_start") or "")[:10], str(body.get("created_at_end") or "")[:10]
    if ts:
        rows = [x for x in rows if (x["created_at"] or "")[:10] >= ts]
    if te:
        rows = [x for x in rows if (x["created_at"] or "")[:10] <= te]
    return rows


def _user_enterprise_list(body, session):
    """用户列表（原站动作名 enterprise_user_list，deep/14 §2.1 接口表 + app.js user API 模块逐字）。
    信封：{success,count,data:[数组],code}——实测 user_list_p1/p2（40 行，data 为数组非 {list,total}）。"""
    rows = _filter_users(_users_all(), body)
    page_rows, total = _page(rows, body, 10)
    for x in page_rows:
        x.pop("_raw", None)
    return _arr_ok(page_rows, total)


def _user_detail(body, session):
    uid = body.get("id")
    row = core.Store("users").get(uid)
    if not row:
        return core.fail("用户不存在")
    d = _norm_user(row)
    d.pop("_raw", None)
    return core.ok(d)


def _resolve_team_rows(body, store_rows):
    """入参 team（id 数组）→ 存量形状 [{id,name}] + team_names（与 dd_all_user 种子行同构）。"""
    raw = body.get("team") if body.get("team") is not None else body.get("team_ids")
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        raw = [x for x in raw.split(",") if x]
    teams = {str(t.get("id")): t for t in store_rows}
    out = [_team_brief(_int(t), teams) for t in raw]
    return out, ",".join(x["name"] for x in out)


def _user_add(body, session):
    name = _kw(body, "name")
    real_name = _kw(body, "real_name")
    mobile = _kw(body, "mobile")
    pwd = str(body.get("pwd") or body.get("password") or "")
    role_id = body.get("role_id")
    if not name:
        return core.fail("请输入用户名")
    if len(name) > 32:
        return core.fail("用户名不能超过32个字符")
    if not real_name:
        return core.fail("请输入姓名")
    if not re.fullmatch(r"\d{1,20}", mobile):
        return core.fail("请输入正确的手机号")
    store = core.Store("users")
    if any(str(u.get("mobile")) == mobile for u in store.all()):
        return core.fail("该手机号已被使用")
    if not pwd:
        return core.fail("请输入密码")
    err = _check_pwd(pwd, name, mobile)
    if err:
        return core.fail(err)
    if role_id in (None, ""):
        return core.fail("请选择角色")
    roles = _roles_map()
    if str(role_id) not in roles:
        return core.fail("角色不存在")
    teams_store = core.Store("teams").all()
    resolved = _resolve_team_rows(body, teams_store)
    seat_modules = body.get("seat_sale_modules")
    if isinstance(seat_modules, str) and seat_modules:
        seat_modules = [int(x) for x in seat_modules.split(",") if x.strip()]
    if not seat_modules:
        seat_modules = [1]  # 所属席位必选（虚拟用户不受剩余数限制，demo 缺省给生产席位）
    seats = {m: (BOOL_YES if m in seat_modules else BOOL_NO) for m in (1, 2, 3)}
    row = {"name": name, "real_name": real_name, "mobile": mobile, "role_id": _int(role_id),
           "type": _int(body.get("type"), 1), "claw_fun_type": _int(body.get("claw_fun_type"), 1),
           "department_id": body.get("department_id"),
           "produce_seat": seats[1], "after_sale_seat": seats[2], "maintenance_seat": seats[3],
           "fun_ids": _ids(body.get("fun_ids")), "avatar": body.get("avatar"),
           "has_password": True, "is_enable": BOOL_YES,
           "create_user_id": (session or {}).get("id"), "enterprise_id": 16807}
    if resolved:
        row["team"], row["team_names"] = resolved
    dep = _dept_map().get(str(body.get("department_id"))) if body.get("department_id") is not None else None
    if dep:
        row["department"] = {"id": dep.get("id"), "parent_id": dep.get("parent_id"), "name": dep.get("name"),
                             "is_leaf": _int(dep.get("is_leaf"), 1), "order_number": dep.get("order_number")}
        row["department_path_names"] = (dep.get("name_path") or dep.get("name") or "").replace(",", "/")
    added = store.add(row)
    return core.ok({"id": added["id"]})


def _user_edit(body, session):
    uid = body.get("id")
    store = core.Store("users")
    r = store.get(uid)
    if not r:
        return core.fail("用户不存在")
    patch = {}
    for f in ("name", "real_name", "avatar", "location", "address", "jianpin"):
        if f in body and body[f] not in (None, ""):
            patch[f] = str(body[f]).strip()
    if _kw(body, "mobile") and body["mobile"] != r.get("mobile"):
        if not re.fullmatch(r"\d{1,20}", str(body["mobile"])):
            return core.fail("请输入正确的手机号")
        if any(str(u.get("mobile")) == str(body["mobile"]) and str(u.get("id")) != str(uid) for u in store.all()):
            return core.fail("该手机号已被使用")
        patch["mobile"] = str(body["mobile"]).strip()
    for f in ("type", "claw_fun_type"):
        if f in body and body[f] not in (None, ""):
            patch[f] = _int(body[f])
    if "department_id" in body:
        patch["department_id"] = body.get("department_id")
        dep = _dept_map().get(str(body.get("department_id"))) if body.get("department_id") is not None else None
        if dep:
            patch["department"] = {"id": dep.get("id"), "parent_id": dep.get("parent_id"), "name": dep.get("name"),
                                   "is_leaf": _int(dep.get("is_leaf"), 1), "order_number": dep.get("order_number")}
            patch["department_path_names"] = (dep.get("name_path") or dep.get("name") or "").replace(",", "/")
    resolved = _resolve_team_rows(body, core.Store("teams").all())
    if resolved:
        patch["team"], patch["team_names"] = resolved
    seat_modules = body.get("seat_sale_modules")
    if isinstance(seat_modules, str) and seat_modules:
        seat_modules = [int(x) for x in seat_modules.split(",") if x.strip()]
    if isinstance(seat_modules, list) and seat_modules:
        for m in (1, 2, 3):
            patch[SEAT_FIELD[m]] = BOOL_YES if m in seat_modules else BOOL_NO
    if "fun_ids" in body:
        patch["fun_ids"] = _ids(body.get("fun_ids"))
    if "data_fun" in body:
        patch["data_fun"] = _ids(body.get("data_fun"))
    if body.get("role_id") not in (None, ""):
        role = _roles_map().get(str(body["role_id"]))
        if not role:
            return core.fail("角色不存在")
        patch["role_id"] = _int(body["role_id"])
        patch["role"] = {"id": role.get("id"), "name": role.get("name")}
        if "fun_ids" not in patch:  # 换角色未显式带权限时随角色
            patch["fun_ids"] = role.get("fun_ids", "")
    pwd = str(body.get("pwd") or body.get("password") or "")
    if pwd:
        err = _check_pwd(pwd, patch.get("name") or r.get("name") or "", patch.get("mobile") or r.get("mobile") or "")
        if err:
            return core.fail(err)
        patch["has_password"] = True
    if not patch:
        return core.ok()
    store.update(uid, patch)
    return core.ok()


def _user_del(body, session):
    store = core.Store("users")
    uid = body.get("id")
    if not store.get(uid):
        return core.fail("用户不存在")
    store.remove(uid)
    return core.ok()


def _user_batch_del(body, session):
    store = core.Store("users")
    ids = body.get("ids") or body.get("id_list") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    n = 0
    for i in ids:
        if store.get(i):
            store.remove(i)
            n += 1
    return core.ok({"count": n})


def _user_enable(body, session):
    return _toggle_user(body, BOOL_YES)


def _user_disable(body, session):
    return _toggle_user(body, BOOL_NO)


def _toggle_user(body, enable):
    store = core.Store("users")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("用户不存在")
    store.update(body.get("id"), {"is_enable": enable})
    return core.ok()


def _user_change_role(body, session):
    """更换角色（角色页"管理用户"弹窗与用户编辑共用）：fun_ids 随新角色重置。"""
    role_id = body.get("role_id")
    role = _roles_map().get(str(role_id))
    if not role:
        return core.fail("请选择新角色")
    ids = body.get("user_ids") or ([] if body.get("id") in (None, "") else [body.get("id")])
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    store = core.Store("users")
    n = 0
    for i in ids:
        if store.get(i):
            store.update(i, {"role_id": role.get("id"), "role": {"id": role.get("id"), "name": role.get("name")},
                             "fun_ids": role.get("fun_ids", ""), "data_fun": role.get("data_fun", "")})
            n += 1
    if not n:
        return core.fail("请选择需要更换角色的用户")
    return core.ok()


def _user_reset_password(body, session):
    uid = body.get("id")
    store = core.Store("users")
    r = store.get(uid)
    if not r:
        return core.fail("用户不存在")
    pwd = str(body.get("pwd") or "")
    if pwd:
        err = _check_pwd(pwd, r.get("name") or "", r.get("mobile") or "")
        if err:
            return core.fail(err)
        store.update(uid, {"has_password": True})
    return core.ok()  # 不带 pwd：原站重置为随机密码短信下发，demo 直接成功


# ================= 角色管理（/role，deep/14 §2.2） =================

def _role_users(role_id):
    """角色成员：users 表按 role_id 关联（dump role_list.users 为全量用户行，demo 给简要行）。"""
    out = []
    for u in core.Store("users").all():
        if str(u.get("role_id")) == str(role_id):
            out.append({"id": u.get("id"), "name": u.get("name") or "", "real_name": u.get("real_name") or "",
                        "mobile": u.get("mobile") or "", "role_id": u.get("role_id"),
                        "is_enable": 1 if u.get("is_enable") in (True, 1, "1") else 2,
                        "avatar": u.get("avatar"), "has_password": bool(u.get("has_password", False)),
                        "type": _int(u.get("type"), 1), "type_name": TYPE_NAMES.get(_int(u.get("type"), 1), "")})
    return out


def _norm_role(r):
    return {"id": r.get("id"), "name": r.get("name") or "", "del": _int(r.get("del"), BOOL_NO),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "enterprise_id": r.get("enterprise_id", 16807), "fun_ids": r.get("fun_ids") or "",
            "is_admin": r.get("is_admin"), "data_fun": r.get("data_fun") or "",
            "is_enterprise_admin": r.get("is_enterprise_admin"), "agent_id": r.get("agent_id"),
            "platform": _int(r.get("platform"), 2), "is_agent_admin": r.get("is_agent_admin"),
            "fieldValueList": r.get("fieldValueList") or [],
            "users": _role_users(r.get("id"))}


def _role_list(body, session):
    """角色列表：信封 {success,count,data:[数组],code,ecfg}（deep_m6/role_list.json 实测同构）。"""
    rows = [_norm_role(r) for r in core.Store("roles").all() if _int(r.get("del"), BOOL_NO) != 1]
    kw = _kw(body, "name")
    if kw:
        rows = [x for x in rows if kw in x["name"]]
    page_rows, total = _page(rows, body, 10)
    return _arr_ok(page_rows, total)


def _role_detail(body, session):
    r = core.Store("roles").get(body.get("id"))
    if not r:
        return core.fail("角色不存在")
    return core.ok(_norm_role(r))


def _role_add(body, session):
    name = _kw(body, "name")
    if not name:
        return core.fail("请输入角色名称")
    store = core.Store("roles")
    if any(u.get("name") == name and _int(u.get("del"), BOOL_NO) != 1 for u in store.all()):
        return core.fail("角色名称已存在")
    row = {"name": name, "del": BOOL_NO, "enterprise_id": 16807, "platform": 2,
           "fun_ids": _ids(body.get("fun_ids")), "data_fun": _ids(body.get("data_fun")),
           "is_admin": None, "fieldValueList": []}
    added = store.add(row)
    return core.ok({"id": added["id"]})


def _role_edit(body, session):
    store = core.Store("roles")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("角色不存在")
    patch = {}
    if _kw(body, "name"):
        patch["name"] = _kw(body, "name")
    if "fun_ids" in body:
        patch["fun_ids"] = _ids(body.get("fun_ids"))
    if "data_fun" in body:
        patch["data_fun"] = _ids(body.get("data_fun"))
    if not patch:
        return core.ok()
    store.update(body.get("id"), patch)
    # 同步权限（编辑表单 sync_fun2user：是 → 把权限同步给此角色下的所有用户，文案见 deep/14 §2.2）
    if _int(body.get("sync_fun2user"), BOOL_NO) == BOOL_YES:
        users = core.Store("users")
        for u in users.all():
            if str(u.get("role_id")) == str(body.get("id")):
                users.update(u.get("id"), {"fun_ids": patch.get("fun_ids", u.get("fun_ids") or ""),
                                           "data_fun": patch.get("data_fun", u.get("data_fun") or "")})
    return core.ok()


def _role_del(body, session):
    store = core.Store("roles")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("角色不存在")
    if _role_users(body.get("id")):
        return core.fail("该角色下存在用户，请先转移用户")
    store.remove(body.get("id"))
    return core.ok()


# ================= 团队管理（/team，deep/14 §2.4） =================

def _team_members(team_id):
    out = []
    for u in core.Store("users").all():
        if any(str(t.get("id")) == str(team_id) for t in (u.get("team") or [])):
            out.append({"id": u.get("id"), "name": u.get("name") or "", "real_name": u.get("real_name") or "",
                        "mobile": u.get("mobile") or "", "has_password": bool(u.get("has_password", False)),
                        "type_name": TYPE_NAMES.get(_int(u.get("type"), 1), ""),
                        "claw_fun_type_name": "", "qr_code_url": None})
    return out


def _norm_team(r, with_members=True):
    row = {"id": r.get("id"), "name": r.get("name") or "", "del": _int(r.get("del"), BOOL_NO),
           "parent_id": r.get("parent_id"), "name_path": r.get("name_path") or r.get("name") or "",
           "id_path": r.get("id_path") or str(r.get("id")), "level": _int(r.get("level"), 1),
           "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
           "enterprise_id": r.get("enterprise_id", 16807), "description": r.get("description"),
           "is_template": _int(r.get("is_template"), BOOL_NO), "resp_user_id": r.get("resp_user_id"),
           "fieldValueList": r.get("fieldValueList") or []}
    if with_members:
        row["resp_user"] = None
        row["regions"] = r.get("regions") or []
        row["members"] = _team_members(r.get("id"))
    return row


def _team_list(body, session):
    """团队列表：无 team/list 实测 dump，按通用分页信封 {success,count,data:{list,total}}。"""
    store = core.Store("teams")
    rows = [r for r in store.all() if _int(r.get("del"), BOOL_NO) != 1]
    kw = _kw(body, "name")
    if kw:
        rows = [r for r in rows if kw in (r.get("name") or "")]
    # 过滤在 page() 前完成（briefing 规则 3），这里手动分页以输出 _norm_team 形状
    page_rows, total = _page(rows, body, 10)
    return {"success": True, "count": total,
            "data": {"list": [_norm_team(r) for r in page_rows], "total": total,
                     "pageNo": _int(body.get("pageNo"), 1), "pageSize": _int(body.get("pageSize"), 10)}}


def _team_tree(body, session):
    """团队树（新增/转移团队级联 + 成员维护）：信封 {success,data,ecfg}（deep_m6/team_tree.json 同构）。"""
    rows = [_norm_team(r, with_members=True) for r in core.Store("teams").all() if _int(r.get("del"), BOOL_NO) != 1]
    return _data_ok(rows)


def _team_detail(body, session):
    r = core.Store("teams").get(body.get("id"))
    if not r:
        return core.fail("团队不存在")
    return core.ok(_norm_team(r, with_members=True))


def _team_add(body, session):
    name = _kw(body, "name")
    if not name:
        return core.fail("请输入团队名称")
    store = core.Store("teams")
    parent_id = body.get("parent_id")
    parent = store.get(parent_id) if parent_id not in (None, "") else None
    level = _int(parent.get("level"), 0) + 1 if parent else 1
    row = {"name": name, "del": False, "parent_id": parent.get("id") if parent else None,
           "level": level, "name_path": (parent.get("name_path") + "," + name) if parent else name,
           "id_path": ((parent.get("id_path") or str(parent.get("id"))) + "," if parent else "") + "%s",
           "enterprise_id": 16807, "description": body.get("description"),
           "resp_user_id": body.get("resp_user_id"), "regions": body.get("regions") or [],
           "is_template": BOOL_NO}
    added = store.add(row)
    added["id_path"] = (str(parent.get("id")) + "," if parent else "") + str(added["id"])
    store.flush()
    return core.ok({"id": added["id"]})


def _team_edit(body, session):
    store = core.Store("teams")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("团队不存在")
    patch = {}
    if _kw(body, "name"):
        patch["name"] = _kw(body, "name")
    for f in ("description", "resp_user_id", "regions"):
        if f in body:
            patch[f] = body[f]
    parent_id = body.get("parent_id", None)
    if parent_id is not None and str(parent_id) != str(r.get("parent_id") or ""):
        parent = store.get(parent_id) if parent_id not in (None, "") else None
        if parent and str(parent.get("id")) == str(r.get("id")):
            return core.fail("上级团队不能是自己")
        patch["parent_id"] = parent.get("id") if parent else None
        patch["level"] = _int(parent.get("level"), 0) + 1 if parent else 1
    if patch:
        store.update(body.get("id"), patch)
        cur = store.get(body.get("id"))
        # 重算 name_path/id_path（本级；demo 不级联子级）
        parent = store.get(cur.get("parent_id")) if cur.get("parent_id") else None
        cur["name_path"] = ((parent.get("name_path") + ",") if parent else "") + (cur.get("name") or "")
        cur["id_path"] = ((parent.get("id_path") + ",") if parent else "") + str(cur.get("id"))
        store.flush()
    return core.ok()


def _team_del(body, session):
    store = core.Store("teams")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("团队不存在")
    if any(str(t.get("parent_id")) == str(r.get("id")) for t in store.all()):
        return core.fail("该团队存在下级团队，无法删除")
    if _team_members(r.get("id")):
        return core.fail("该团队存在成员，请先移除")
    store.remove(body.get("id"))
    return core.ok()


def _team_add_user(body, session):
    team = core.Store("teams").get(body.get("team_id"))
    if not team:
        return core.fail("团队不存在")
    ids = body.get("user_ids") or ([] if body.get("user_id") in (None, "") else [body.get("user_id")])
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    users = core.Store("users")
    n = 0
    for i in ids:
        u = users.get(i)
        if not u:
            continue
        team_list = [t for t in (u.get("team") or [])]
        if any(str(t.get("id")) == str(team.get("id")) for t in team_list):
            continue
        team_list.append({"id": team.get("id"), "name": team.get("name")})
        users.update(i, {"team": team_list,
                         "team_names": ",".join(t.get("name") or "" for t in team_list)})
        n += 1
    return core.ok({"count": n})


def _team_remove_user(body, session):
    team_id = str(body.get("team_id"))
    users = core.Store("users")
    u = users.get(body.get("user_id"))
    if not u:
        return core.fail("用户不存在")
    team_list = [t for t in (u.get("team") or []) if str(t.get("id")) != team_id]
    users.update(body.get("user_id"), {"team": team_list,
                                       "team_names": ",".join(t.get("name") or "" for t in team_list)})
    return core.ok()


# ================= 部门 / 品类（/category，deep/14 §2.3 + §4.1） =================
# 部门=category 表 busi_type=2（存 Store("departments")）；商城分类=12（Store("mall_categories")）；
# 商品类别=5（与 goods.py 共用 Store("categories")，读时投影字典表全形状）。

def _goods_categories_full():
    """categories 简形状行 → category/list 字典表全形状（字段集对齐 deep_m6/category_list.json 逐字）。"""
    rows = [r for r in core.Store("categories").all() if _int(r.get("del"), BOOL_NO) != 1]
    by_id = {str(r.get("id")): r for r in rows}
    out = []
    for idx, r in enumerate(rows):
        parent = by_id.get(str(r.get("parent_id")))
        children = [x for x in rows if str(x.get("parent_id")) == str(r.get("id"))]
        out.append({"id": r.get("id"), "name": r.get("name") or "", "parent_id": r.get("parent_id"),
                    "updated_at": r.get("updated_at"), "created_at": r.get("created_at"),
                    "del": _int(r.get("del"), BOOL_NO), "reason": None, "busi_type": 5,
                    "enterprise_id": r.get("enterprise_id", 16807),
                    "is_leaf": _int(r.get("is_leaf"), BOOL_NO if children else BOOL_YES),
                    "order_number": r.get("order_number") if r.get("order_number") is not None else idx + 1,
                    "name_path": r.get("name_path") or ((parent.get("name_path") + "," if parent else "") + (r.get("name") or "")),
                    "dic_workshop_id": r.get("dic_workshop_id"), "workchat_department_id": None,
                    "ding_talk_department_id": None, "pic": r.get("pic"),
                    "fieldValueList": [], "parent_category": ({"id": parent.get("id"), "name": parent.get("name")} if parent else None)})
    out.sort(key=lambda x: (x["order_number"] or 0))
    return out


def _cat_store(busi_type):
    bt = _int(busi_type, 5)
    if bt == 1:
        return core.Store("info_categories")   # 知识分类（info_admin.py 域，B13 撞名协调）
    if bt == 2:
        return core.Store("departments")
    if bt == 12:
        return core.Store("mall_categories")
    return core.Store("categories")


def _cat_rows(busi_type):
    bt = _int(busi_type, 5)
    if bt in (1, 2, 12):
        src = [r for r in _cat_store(bt).all() if _int(r.get("del"), BOOL_NO) != 1]
        return sorted([dict(r, fieldValueList=r.get("fieldValueList") or []) for r in src],
                      key=lambda x: (x.get("order_number") or 0))
    return _goods_categories_full()


def _category_list(body, session):
    """品类字典列表：信封 {success,count,data:[数组],code,ecfg}（category_list.json / category_list_bt12.json 同构）。"""
    rows = _cat_rows(body.get("busi_type"))
    kw = _kw(body, "name")
    if kw:
        rows = [x for x in rows if kw in x["name"]]
    page_rows, total = _page(rows, body, 10)
    return _arr_ok(page_rows, total)


def _category_department_tree(body, session):
    """部门树（a-cascader changeOnSelect）：信封 {success,data}（department_tree.json 同构，叶节点无 children 键）。"""
    rows = _cat_rows(2)
    nodes = {str(r["id"]): dict(r) for r in rows}
    roots = []
    for r in rows:
        pid = str(r.get("parent_id")) if r.get("parent_id") is not None else None
        if pid and pid in nodes:
            nodes[pid].setdefault("children", []).append(nodes[str(r["id"])])
        else:
            roots.append(nodes[str(r["id"])])
    return {"success": True, "data": roots}


def _category_add(body, session):
    name = _kw(body, "name")
    if not name:
        return core.fail("请输入名称")
    store = _cat_store(body.get("busi_type"))
    rows = [r for r in store.all()]
    parent = store.get(body.get("parent_id")) if body.get("parent_id") not in (None, "") else None
    order = max([_int(r.get("order_number"), 0) for r in rows] or [0]) + 1
    if store.name == "categories":
        # 商品类别：与 goods.py 共表，写入全形状行（goods.py 只读 id/name/parent_id/del，兼容）
        row = {"name": name, "parent_id": parent.get("id") if parent else None,
               "busi_type": 5, "enterprise_id": 16807, "del": BOOL_NO, "reason": None,
               "is_leaf": BOOL_YES, "order_number": order,
               "name_path": ((parent.get("name_path") + ",") if parent else "") + name,
               "dic_workshop_id": None, "workchat_department_id": None,
               "ding_talk_department_id": None, "pic": None, "fieldValueList": []}
    else:
        row = {"name": name, "parent_id": parent.get("id") if parent else None,
               "busi_type": {"departments": 2, "mall_categories": 12, "info_categories": 1}.get(store.name, 12),
               "enterprise_id": 16807,
               "del": BOOL_NO, "reason": None,
               "is_leaf": BOOL_YES, "order_number": order,
               "name_path": ((parent.get("name_path") + ",") if parent else "") + name,
               "dic_workshop_id": None, "workchat_department_id": None,
               "ding_talk_department_id": None, "pic": None, "fieldValueList": []}
    if parent:
        store.update(parent.get("id"), {"is_leaf": BOOL_NO})
    added = store.add(row)
    return core.ok({"id": added["id"]})


def _find_category(cid):
    for bt in (1, 2, 12, 5):
        store = _cat_store(bt)
        r = store.get(cid)
        if r:
            return bt, store, r
    return None, None, None


def _category_edit(body, session):
    cid = body.get("id")
    bt, store, r = _find_category(cid)
    if not r:
        return core.fail("数据不存在")
    patch = {}
    if _kw(body, "name"):
        patch["name"] = _kw(body, "name")
    parent_id = body.get("parent_id", None)
    if parent_id is not None and str(parent_id) != str(r.get("parent_id") or ""):
        if str(parent_id) == str(cid):
            return core.fail("上级不能是自己")
        parent = store.get(parent_id) if parent_id not in (None, "") else None
        patch["parent_id"] = parent.get("id") if parent else None
    if patch:
        store.update(cid, patch)
        cur = store.get(cid)
        if cur is not None and "name" in patch:  # 重算 name_path（本级）
            parent = store.get(cur.get("parent_id")) if cur.get("parent_id") else None
            cur["name_path"] = ((parent.get("name_path") + ",") if parent else "") + (cur.get("name") or "")
            store.flush()
    return core.ok()


def _category_del(body, session):
    cid = body.get("id")
    bt, store, r = _find_category(cid)
    if not r:
        return core.fail("数据不存在")
    if any(str(x.get("parent_id")) == str(cid) for x in store.all()):
        return core.fail("存在下级，无法删除")
    if bt == 2:  # 部门被用户引用则拒绝
        if any(str(u.get("department_id")) == str(cid) for u in core.Store("users").all()):
            return core.fail("该部门下存在用户，无法删除")
    store.remove(cid)
    return core.ok()


# ================= 数据字典 dic（/dic，deep/14 §4.1/§4.3） =================

def _dic_rows(body):
    rows = [r for r in core.Store("dics").all() if _int(r.get("del"), BOOL_NO) != 1]
    tid = body.get("type_id")
    if tid not in (None, ""):
        rows = [r for r in rows if _int(r.get("type_id")) == _int(tid)]
    kw = _kw(body, "name")
    if kw:
        rows = [r for r in rows if kw in (r.get("name") or "")]
    rows.sort(key=lambda x: (x.get("order_number") or 0))
    return rows


def _dic_list(body, session):
    """字典列表：信封 {success,count,data:[数组],code,ecfg}（dic_type1_unit.json / dic_t*.json 同构）。"""
    page_rows, total = _page(_dic_rows(body), body, 20)
    return _arr_ok(page_rows, total)


def _dic_get_dic_list(body, session):
    """轻量字典（表单下拉）：行不带 fieldValueList 字段（deep/14 §4.1 实测差异）。"""
    rows = [{k: v for k, v in r.items() if k != "fieldValueList"} for r in _dic_rows(body)]
    page_rows, total = _page(rows, body, 20)
    return _arr_ok(page_rows, total)


def _dic_add(body, session):
    name = _kw(body, "name")
    if not name:
        return core.fail("请输入名称")
    tid = body.get("type_id")
    if tid in (None, ""):
        return core.fail("参数缺失")
    store = core.Store("dics")
    if any(_int(r.get("type_id")) == _int(tid) and r.get("name") == name for r in store.all()):
        return core.fail("该字典值已存在")
    order = max([_int(r.get("order_number"), 0) for r in store.all()] or [0]) + 1
    added = store.add({"enterprise_id": 16807, "type_id": _int(tid), "name": name,
                       "del": BOOL_NO, "order_number": order, "fieldValueList": []})
    return core.ok({"id": added["id"]})


def _dic_edit(body, session):
    store = core.Store("dics")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("数据不存在")
    name = _kw(body, "name")
    if not name:
        return core.fail("请输入名称")
    store.update(body.get("id"), {"name": name})
    return core.ok()


def _dic_del(body, session):
    store = core.Store("dics")
    if not store.get(body.get("id")):
        return core.fail("数据不存在")
    store.remove(body.get("id"))
    return core.ok()


def _dropdown_dic(body, session):
    """/dropdown/dic {type_id}：轻量 {id,name,del:false} 列表（deep_m6/dd_dic.json 形状，del 为布尔）。"""
    rows = [{"id": r.get("id"), "name": r.get("name") or "",
             "del": _int(r.get("del"), BOOL_NO) == BOOL_YES}
            for r in _dic_rows(body)]
    return _data_ok(rows)


# ================= 席位 / 功能树（deep/14 §3.2 + §2.2） =================

def _dropdown_enterprise_seat(body, session):
    """席位余额：{sale_module,seat_name,seat_limit,use_seat_count,remain_seat_count}（dd_enterprise_seat.json 口径）。"""
    rows = []
    for r in core.Store("enterprise_seats").all():
        limit = _int(r.get("seat_limit"), 30)
        use = _int(r.get("use_seat_count"), 0)
        rows.append({"sale_module": _int(r.get("sale_module")), "seat_name": r.get("seat_name") or "",
                     "seat_limit": limit, "use_seat_count": use,
                     "remain_seat_count": _int(r.get("remain_seat_count"), limit - use)})
    return {"success": True, "data": rows}


def _fun_display_tree(body, session):
    """权限树（用户/角色表单 fun_display/tree）：backend/data/fun_display_tree.json（真实 dump 原样内置）。"""
    tree = core._load("fun_display_tree", [])
    return {"success": True, "data": tree}


ROUTES = {
    # ---- 用户管理（/user；auth.py 的 login/info/home/nav/logout 与 quality/machine 的键不在本表） ----
    ("user", "enterprise_user_list"): _user_enterprise_list,
    ("user", "detail"): _user_detail,
    ("user", "add"): _user_add,
    ("user", "edit"): _user_edit,
    ("user", "del"): _user_del,
    ("user", "batch_del"): _user_batch_del,
    ("user", "enable"): _user_enable,
    ("user", "disable"): _user_disable,
    ("user", "change_role"): _user_change_role,
    ("user", "reset_password"): _user_reset_password,
    # ---- 角色管理（/role） ----
    ("role", "enterprise_role_list"): _role_list,
    ("role", "detail"): _role_detail,
    ("role", "add"): _role_add,
    ("role", "edit"): _role_edit,
    ("role", "del"): _role_del,
    # ---- 团队管理（/team） ----
    ("team", "list"): _team_list,
    ("team", "team_tree"): _team_tree,
    ("team", "detail"): _team_detail,
    ("team", "add"): _team_add,
    ("team", "edit"): _team_edit,
    ("team", "del"): _team_del,
    ("team", "add_user"): _team_add_user,
    ("team", "remove_user"): _team_remove_user,
    # ---- 部门/品类字典（/category；dropdown/category 归 goods.py 名下，勿动） ----
    ("category", "list"): _category_list,
    ("category", "department_tree"): _category_department_tree,
    ("category", "add"): _category_add,
    ("category", "edit"): _category_edit,
    ("category", "del"): _category_del,
    # ---- 数据字典（/dic） ----
    ("dic", "list"): _dic_list,
    ("dic", "get_dic_list"): _dic_get_dic_list,
    ("dic", "add"): _dic_add,
    ("dic", "edit"): _dic_edit,
    ("dic", "del"): _dic_del,
    ("dropdown", "dic"): _dropdown_dic,
    # ---- 席位 / 功能树 ----
    ("dropdown", "enterprise_seat"): _dropdown_enterprise_seat,
    ("fun_display", "tree"): _fun_display_tree,
}
