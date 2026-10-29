# -*- coding: utf-8 -*-
r"""B-info 信息管理域（info_admin）：知识库 CRUD + 知识分类 + 公告管理端补充 + 短信/公众号模板查询端。
依据：deep/15-信息管理域.md（§1 公告管理端 / §2 知识库 / §3 短信 / §4.2 msg_template）
实测样例：api_dump/info_knowledge_list.json（count=0，字段按报告推导）、info_category_list.json、
        info_notice_list.json、info_msg_template_list.json、info_selector_user.json、
        sms_remain_num.json、sms_package_list.json、sms_recharge_list.json、sms_send_log_list.json
种子（本模块名下 data\*.json）：knowledge（补造5，_seed）、info_categories（真实1+补造4）、
        msg_templates（原样23）、sms_packages（原样3）、sms_recharges（补造2）、sms_send_logs（原样10）。
边界：
- notice 模块 list/add/read/not_read_count 四键已由 misc.py（B9 接收端）持有，本文件**不重定义**，
  只补管理端缺的 ("notice","edit") / ("notice","del")（deep/15 §1.3 API 族，同模块不同 action），
  共用 Store("notices")，与 misc 两种行形状（dump 原样行 / read_user_ids 简化行）兼容。
- 原站未部署/无触发路径的接口不实现：/category/move、/category/workchat_department_list（deep/15 §9 待裁决）、
  /msg_template/save、/sms_recharge/buy_sms（支付宝流，本轮只要查询端）→ 均保持 404 语义。
- 反馈（feedback 族）属售后域，不在本文件；/user/home 已在 auth.py。
- ⚠ 路由撞名（跨代理）：sysadmin.py（B12）同持 ("category", list/add/edit/del)，其 _cat_store 只认 busi_type
  2(部门)/12(商城)/其余落商品 categories 表——busi_type=1（知识分类）会错路由到商品类别。
  server.py 后加载者胜（sysadmin > info_admin 字母序），四个键现归 sysadmin；本文件保留同键注册
  （busi_type=1 时行为以 deep/15 + info_category_list.json 抓包为准），**待协调**：sysadmin._cat_store
  增加 bt==1 → Store("info_categories") 一行即可两侧全对。("category","publish") 与
  ("dropdown","category_cascade") 无撞名，知识分类停启用/级联不受影响。
- 分类存储用独立 Store("info_categories")（与商品 categories 表物理隔离），id 段 823490-823493 为补造
  （823478/823479 已被商城分类 bt12 真实 id 占用，故挪段）。
"""
import json
import re
import core

BOOL_YES, BOOL_NO = 1, 2          # has_read/is_publish/del 同 KGD 布尔：1 是 / 2 否
CATEGORY_BUSI_TYPE_KNOWLEDGE = 1  # CATEGORY_BUSI_TYPE.KNOWLEDGE（deep/15 §2.3）
DEFAULT_ENT_ID = 16807
QR_HOST = "https://single.kgd.ltd"  # 与 traceability.py 同源（user.qr_code_url 拼接）

ECUT = lambda: core.now()  # enterprise_config_update_time


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _out(row):
    """透出行时剔除私有键（_seed 等）。"""
    return {k: v for k, v in row.items() if not str(k).startswith("_")}


def _plain(html):
    """Quill HTML → 纯文本摘要（profile）。实测：content "<p>！！！</p>" → profile "！！！"（info_notice_list.json）。"""
    t = re.sub(r"<[^>]+>", "", str(html or ""))
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        t = t.replace(a, b)
    return t.strip()


def _user_brief(uid):
    """users store 查人 → dump create_user/user 简报形状；查无则占位（不阻塞渲染）。"""
    uid = uid if uid is not None else 0
    for u in core.Store("users").all():
        if str(u.get("id")) == str(uid):
            return {"id": u.get("id"), "name": u.get("name"), "real_name": u.get("real_name") or u.get("name"),
                    "avatar": u.get("avatar"), "has_password": False, "type_name": "普通用户",
                    "claw_fun_type_name": "", "qr_code_url": "%s/#/u?qr_id=%s" % (QR_HOST, uid)}
    return {"id": uid, "name": str(uid), "real_name": "用户%s" % uid, "avatar": None,
            "has_password": False, "type_name": "普通用户", "claw_fun_type_name": "", "qr_code_url": None}


def _id_list(v):
    """user_ids 解析：兼容 "1,2,3" 逗号串（保存载荷原样）/ [1,2] / [{id,...}]（PersonSelect 对象数组）。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.split(",")
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("id")
        try:
            out.append(int(str(x).strip()))
        except Exception:
            pass
    return out


def _page_slice(rows, body, default_size=30):
    try:
        pageNo = max(1, _int(body.get("pageNo"), 1))
    except Exception:
        pageNo = 1
    try:
        pageSize = max(1, _int(body.get("pageSize"), default_size))
    except Exception:
        pageSize = default_size
    return rows[(pageNo - 1) * pageSize: pageNo * pageSize], len(rows)


# ================= knowledge（deep/15 §2：/knowledge/list|detail|add|edit|del|read|log） =================

def _cat_map():
    return {c.get("id"): c for c in core.Store("info_categories").all()}


def _tags(r):
    """tag_list 存储形态为 JSON 串（保存载荷逐字 tag_list: JSON.stringify(tagList)）。"""
    v = r.get("tag_list")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = [t for t in v.split(",") if t]
    return v or []


def _k_out(r):
    cats = _cat_map()
    big = cats.get(r.get("big_category_id"))
    small = cats.get(r.get("small_category_id"))
    d = _out(r)
    d.setdefault("profile", _plain(r.get("content")))
    d["tags"] = _tags(r)
    d["create_user"] = _user_brief(r.get("create_user_id"))
    d["big_category"] = {"id": big.get("id"), "name": big.get("name")} if big else None
    d["small_category"] = {"id": small.get("id"), "name": small.get("name")} if small else None
    d.setdefault("view_count", 0)
    d.setdefault("attachments", [])
    d.setdefault("fieldValueList", [])
    # 详情面板「编辑/删除」按钮双条件：权限 && editAccessible/delAccessible（demo 全放行，权限由前端控制）
    d["editAccessible"] = BOOL_YES
    d["delAccessible"] = BOOL_YES
    return d


def _k_rows():
    return [r for r in core.Store("knowledge").all() if _int(r.get("del"), BOOL_NO) != 1]


def _k_filter(rows, body, session):
    """列表页筛选（deep/15 §2.1 工具条）：我发布的(create_user_id)/分类级联/关键词(title)/标签(tag_list)。"""
    cui = body.get("create_user_id")
    if cui not in (None, ""):
        rows = [r for r in rows if _int(r.get("create_user_id")) == _int(cui)]
    big, small = body.get("big_category_id"), body.get("small_category_id")
    if big not in (None, ""):
        rows = [r for r in rows if _int(r.get("big_category_id")) == _int(big)]
    if small not in (None, ""):
        rows = [r for r in rows if _int(r.get("small_category_id")) == _int(small)]
    kw = str(body.get("title") or "").strip()
    if kw:
        rows = [r for r in rows if kw in str(r.get("title") or "")]
    tags = body.get("tag_list")
    if tags not in (None, ""):
        toks = [t.strip() for t in str(tags).split(",") if t.strip()]
        if toks:
            rows = [r for r in rows if any(t in _tags(r) or t in str(r.get("tag_list") or "") for t in toks)]
    return rows


def _k_list(body, session):
    rows = _k_filter(_k_rows(), body, session)
    sf = str(body.get("sortField") or "").strip()
    if sf in ("created_at", "view_count"):  # 排序选择：默认""/created_at/view_count + sortOrder descend（§2.1）
        rev = str(body.get("sortOrder") or "descend") != "ascend"
        rows = sorted(rows, key=lambda r: (r.get(sf) if r.get(sf) is not None else 0), reverse=rev)
    else:
        rows = sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)  # 默认最新在前（推断）
    page_rows, total = _page_slice(rows, body, 10)
    return {"success": True, "count": total, "data": [_k_out(r) for r in page_rows],
            "code": 0, "enterprise_config_update_time": ECUT()}


def _k_detail(body, session):
    r = core.Store("knowledge").get(body.get("id"))
    if not r or _int(r.get("del"), BOOL_NO) == 1:
        return core.fail("知识不存在")
    return core.ok(_k_out(r))


def _k_check(body, session, row):
    """表单校验逐字（deep/15 §2.2）：标题255/分类必选/内容20000。"""
    title = str(body.get("title") or "").strip()
    if not title:
        return None, core.fail("请输入标题")
    if len(title) > 255:
        return None, core.fail("长度超出限制")
    big, small = body.get("big_category_id"), body.get("small_category_id")
    if not big and not small and body.get("category"):  # 前端 category 数组拆 big/small（§2.2 保存载荷）
        arr = body["category"]
        big, small = (arr + [None, None])[:2] if isinstance(arr, list) else (arr, None)
    if not big:
        return None, core.fail("请选择分类")
    content = str(body.get("content") or "").strip()
    if not content:
        return None, core.fail("请输入内容")
    if len(content) > 20000:
        return None, core.fail("长度超出限制")
    tag_list = body.get("tag_list")
    if not isinstance(tag_list, str):  # 兼容直接传数组
        tag_list = json.dumps(tag_list or [], ensure_ascii=False)
    row.update({"title": title, "content": content, "profile": _plain(content),
                "big_category_id": _int(big), "small_category_id": _int(small) if small else None,
                "is_publish": _int(body.get("is_publish"), BOOL_YES),  # switch 公开1/不公开2，缺省公开（推断）
                "tag_list": tag_list, "attachments": body.get("attachment_list") or []})
    return row, None


def _k_add(body, session):
    row = {"create_user_id": session.get("id"), "enterprise_id": session.get("enterprise_id", DEFAULT_ENT_ID),
           "del": BOOL_NO, "reason": None, "view_count": 0}
    row, err = _k_check(body, session, row)
    if err:
        return err
    core.Store("knowledge").add(row)
    return core.ok()


def _k_edit(body, session):
    store = core.Store("knowledge")
    r = store.get(body.get("id"))
    if not r or _int(r.get("del"), BOOL_NO) == 1:
        return core.fail("知识不存在")
    row, err = _k_check(body, session, dict(r))
    if err:
        return err
    store.update(body.get("id"), {k: v for k, v in row.items() if k in
                                  ("title", "content", "profile", "big_category_id", "small_category_id",
                                   "is_publish", "tag_list", "attachments")})
    return core.ok()


def _k_del(body, session):
    store = core.Store("knowledge")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("知识不存在")
    store.update(body.get("id"), {"del": BOOL_YES, "reason": body.get("reason")})  # 软删（del=2 正常 → 1 删除）
    return core.ok()


def _k_read(body, session):
    """点击条目：/knowledge/read {id}，前端本地 view_count++（§2.1）；服务端同步自增。"""
    store = core.Store("knowledge")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("知识不存在")
    store.update(body.get("id"), {"view_count": _int(r.get("view_count")) + 1})
    return core.ok()


def _k_log(body, session):
    """修改日志弹窗：req=knowledge.getLog param {busi_id:id}（§2.1 详情面板「查看修改日志」）。
    demo 从行 created_at/updated_at 推导两条（创建必有；改过则补修改）。"""
    r = core.Store("knowledge").get(body.get("busi_id", body.get("id")))
    if not r:
        return core.ok([])
    u = _user_brief(r.get("create_user_id"))
    logs = [{"id": 1, "busi_id": r.get("id"), "user_id": r.get("create_user_id"), "user": u,
             "content": "创建了知识", "operation_name": "创建", "attachments": [],
             "created_at": r.get("created_at"), "updated_at": r.get("created_at")}]
    if r.get("updated_at") and r.get("updated_at") != r.get("created_at"):
        logs.append({"id": 2, "busi_id": r.get("id"), "user_id": r.get("create_user_id"), "user": u,
                     "content": "修改了知识", "operation_name": "修改", "attachments": [],
                     "created_at": r.get("updated_at"), "updated_at": r.get("updated_at")})
    return core.ok(logs)


# ================= category（deep/15 §2.3：/category/list|add|edit|del|publish；move/workchat_* 不实现） =================

def _cat_rows(body):
    bt = body.get("busi_type")
    bt = _int(bt, CATEGORY_BUSI_TYPE_KNOWLEDGE) if bt not in (None, "") else CATEGORY_BUSI_TYPE_KNOWLEDGE
    rows = [c for c in core.Store("info_categories").all()
            if _int(c.get("busi_type"), CATEGORY_BUSI_TYPE_KNOWLEDGE) == bt
            and _int(c.get("del"), BOOL_NO) != 1]
    rows.sort(key=lambda c: _int(c.get("order_number")))  # order_number 升序（推断；dump 单行 1176）
    return rows


def _cat_out(c):
    cats = _cat_map()
    parent = cats.get(c.get("parent_id")) if c.get("parent_id") else None
    d = _out(c)
    d["parent_category"] = {"id": parent.get("id"), "name": parent.get("name")} if parent else None
    d.setdefault("fieldValueList", [])
    return d


def _cat_list(body, session):
    rows = _cat_rows(body)
    page_rows, total = _page_slice(rows, body, 30)
    return {"success": True, "count": total, "data": [_cat_out(c) for c in page_rows],
            "code": 0, "enterprise_config_update_time": ECUT()}


def _cat_check(body):
    name = str(body.get("name") or "").strip()
    if not name:
        return None, core.fail("请输入分类名称！")
    if len(name) > 20:
        return None, core.fail("长度超出限制")
    return name, None


def _next_order(store, bt):
    mx = 0
    for c in store.all():
        if _int(c.get("busi_type"), CATEGORY_BUSI_TYPE_KNOWLEDGE) == bt:
            mx = max(mx, _int(c.get("order_number")))
    return mx + 1


def _name_path(store, parent_id, name):
    """name_path 分隔符 ","（category 表同构证据：deep_m6/category_list_bt12.json 子行 "电视,海尔"）。"""
    if parent_id in (None, ""):
        return name
    p = store.get(parent_id)
    return (p.get("name_path") + "," + name) if p and p.get("name_path") else name


def _cat_add(body, session):
    name, err = _cat_check(body)
    if err:
        return err
    store = core.Store("info_categories")
    bt = _int(body.get("busi_type"), CATEGORY_BUSI_TYPE_KNOWLEDGE)
    row = {"name": name, "parent_id": body.get("parent_id") or None, "busi_type": bt,
           "enterprise_id": session.get("enterprise_id", DEFAULT_ENT_ID),
           "is_leaf": BOOL_YES, "order_number": _next_order(store, bt), "del": BOOL_NO, "reason": None,
           "dic_workshop_id": None, "workchat_department_id": None, "ding_talk_department_id": None,
           "pic": None, "fieldValueList": [], "parent_category": None}
    row["name_path"] = _name_path(store, row["parent_id"], name)  # 未选择父分类时为顶级（表单 page-tip 逐字）
    store.add(row)
    return core.ok()


def _fix_path_chain(store, cid):
    """自上而下重算 name_path（改名/改父后子分类路径连带）。"""
    c = store.get(cid)
    if not c:
        return
    np = _name_path(store, c.get("parent_id"), c.get("name") or "")
    if np != c.get("name_path"):
        store.update(cid, {"name_path": np})
    for x in store.all():
        if x.get("parent_id") and str(x.get("parent_id")) == str(cid):
            _fix_path_chain(store, x.get("id"))


def _cat_edit(body, session):
    store = core.Store("info_categories")
    c = store.get(body.get("id"))
    if not c:
        return core.fail("分类不存在")
    name, err = _cat_check(body)
    if err:
        return err
    patch = {"name": name}
    if "parent_id" in body:  # 编辑时有父分类才可选（表单禁改根，§2.1 CategoryForm）
        patch["parent_id"] = body.get("parent_id") or None
    if "busi_type" in body:
        patch["busi_type"] = _int(body.get("busi_type"), CATEGORY_BUSI_TYPE_KNOWLEDGE)
    store.update(body.get("id"), patch)
    _fix_path_chain(store, body.get("id"))
    return core.ok()


def _cat_del(body, session):
    store = core.Store("info_categories")
    c = store.get(body.get("id"))
    if not c:
        return core.fail("分类不存在")
    kids = [x for x in store.all() if x.get("parent_id") and str(x.get("parent_id")) == str(c.get("id"))
            and _int(x.get("del"), BOOL_NO) != 1]
    if kids:
        return core.fail("请先删除子分类")  # 树表展开结构，带子不可删（推断）
    store.update(body.get("id"), {"del": BOOL_YES, "reason": body.get("reason")})
    return core.ok()


def _cat_publish(body, session):
    """分类停启用：is_publish 1↔2（§2.3 toggleChecked）。"""
    store = core.Store("info_categories")
    c = store.get(body.get("id"))
    if not c:
        return core.fail("分类不存在")
    if body.get("is_publish") not in (None, ""):
        ip = _int(body.get("is_publish"), BOOL_YES)
    else:  # 未传则取反（停用↔启用）
        ip = BOOL_NO if _int(c.get("is_publish"), BOOL_YES) == BOOL_YES else BOOL_YES
    store.update(body.get("id"), {"is_publish": ip})
    return core.ok()


def _dropdown_category_cascade(body, session):
    """知识表单/列表页分类级联（§2.1/§2.2：/dropdown/category_cascade {busi_type:1}，change-on-select）。"""
    bt = body.get("busi_type")
    bt = _int(bt, CATEGORY_BUSI_TYPE_KNOWLEDGE) if bt not in (None, "") else CATEGORY_BUSI_TYPE_KNOWLEDGE
    rows = [c for c in core.Store("info_categories").all()
            if _int(c.get("busi_type"), CATEGORY_BUSI_TYPE_KNOWLEDGE) == bt]
    rows.sort(key=lambda c: _int(c.get("order_number")))
    by_parent = {}
    for c in rows:
        by_parent.setdefault(c.get("parent_id"), []).append(c)
    def build(parent_id):
        out = []
        for c in by_parent.get(parent_id, []):
            label = c.get("name") or ""
            if _int(c.get("del"), BOOL_NO) == BOOL_YES:
                label += "(禁用)"  # 报告 §2.1：del=1 选项 label 追 (禁用)
            kids = build(c.get("id"))
            node = {"value": c.get("id"), "label": label}
            if kids:
                node["children"] = kids
            out.append(node)
        return out
    return core.ok(build(None))


# ================= notice 管理端补充（deep/15 §1.3：edit/del；list/add/read/not_read_count 在 misc.py） =================

def _notice_join(uid, nid, has_read):
    u = _user_brief(uid)
    return {"id": None, "user_id": uid, "notice_id": nid, "has_read": has_read, "in_range": BOOL_YES, "user": u}


def _notice_edit(body, session):
    """§1.2 保存载荷：{id, title, user_ids:"1,2,3", attachment_list, content}（Quill HTML 原样存）。"""
    store = core.Store("notices")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("公告不存在")
    # 表单校验逐字（§1.2）：标题必填255/内容必填20000；键缺省时沿用原值（局部补丁场景）
    title = str(body["title"] if "title" in body else (r.get("title") or "")).strip()
    content = str(body["content"] if "content" in body else (r.get("content") or "")).strip()
    if not title:
        return core.fail("请输入标题")
    if len(title) > 255:
        return core.fail("长度超出限制")
    if not content:
        return core.fail("请输入内容")
    if len(content) > 20000:
        return core.fail("长度超出限制")
    patch = {"title": title, "content": content, "profile": _plain(content)}
    if "attachment_list" in body:
        patch["attachments"] = body.get("attachment_list") or []
    ids = _id_list(body.get("user_ids") or body.get("range_user_ids"))
    if ids is not None:  # 范围变更：保留已读者状态，重建已读/未读名单
        if "has_read" in r:  # misc dump 原样行形状
            prev_read = {x.get("user_id") for x in (r.get("notice_user") or []) if _int(x.get("has_read")) == BOOL_YES}
            nu = [_notice_join(i, r.get("id"), BOOL_YES if i in prev_read else BOOL_NO) for i in ids]
            patch["notice_user"] = nu
            patch["has_read_user_list"] = [x for x in nu if x["has_read"] == BOOL_YES]
            patch["not_read_user_list"] = [x for x in nu if x["has_read"] == BOOL_NO]
            patch["has_read_count"] = len(patch["has_read_user_list"])
            patch["not_read_count"] = len(patch["not_read_user_list"])
            uid = session.get("id")
            patch["has_read"] = BOOL_YES if uid in prev_read else BOOL_NO
        else:  # misc _notice_add 生成的简化行形状
            patch["range_user_ids"] = ids
            patch["read_user_ids"] = [i for i in (r.get("read_user_ids") or []) if i in ids]
    store.update(body.get("id"), patch)
    return core.ok()


def _notice_del(body, session):
    """详情面板右上删除（§1.1，权限 info-manageNotice）；软删，misc 列表即滤除。"""
    store = core.Store("notices")
    r = store.get(body.get("id"))
    if not r:
        return core.fail("公告不存在")
    store.update(body.get("id"), {"del": BOOL_YES, "reason": body.get("reason")})
    return core.ok()


# ================= selector（公告范围 PersonSelect 数据源，§1.2） =================

def _selector_user(body, session):
    """/selector/user → [{id,name,userList:[{id,name}]}] 按部门分组（info_selector_user.json 形状；
    未设部门归 id=-1「未设置部门」且排首位）。demo 从 users store 现算（新增用户自动入组）。"""
    groups, order = {}, []
    for u in core.Store("users").all():
        did = u.get("department_id") or -1
        if did not in groups:
            if did == -1:
                name = "未设置部门"
            else:
                name = (str(u.get("department_path_names") or "").split("/")[-1]).strip() or ("部门%s" % did)
            groups[did] = {"id": did, "name": name, "userList": []}
            order.append(did)
        groups[did]["userList"].append({"id": u.get("id"), "name": u.get("real_name") or u.get("name")})
    data = [groups[d] for d in (-1, *(d for d in order if d != -1)) if d in groups]
    return {"success": True, "data": data, "enterprise_config_update_time": ECUT()}


# ================= msg_template（§4.2 公众号模版消息设置，本轮查询端） =================

def _msg_template_list(body, session):
    """/msg_template/list {belong_type:1, belong_id:企业id} → 23 条 {name,template_key,template_id,is_category_template_msg}。"""
    rows = [_out(r) for r in core.Store("msg_templates").all()]
    return {"success": True, "data": rows}


# ================= sms 查询端（§3.1 /setting/sms；购买/签名写端本轮不做） =================

def _sms_remain(body, session):
    """/enterprise/get_sms_remain_num → data 为纯数字（sms_remain_num.json 实测 93）。"""
    return {"success": True, "data": 93}


def _sms_package_list(body, session):
    rows = [_out(r) for r in core.Store("sms_packages").all() if _int(r.get("del"), BOOL_NO) != 1]
    return {"success": True, "count": len(rows), "data": rows, "code": 0}


def _sms_recharge_list(body, session):
    rows = [_out(r) for r in core.Store("sms_recharges").all()]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    page_rows, total = _page_slice(rows, body, 10)
    return {"success": True, "count": total, "data": page_rows, "code": 0}


def _sms_send_log_list(body, session):
    rows = [_out(r) for r in core.Store("sms_send_logs").all()]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    page_rows, total = _page_slice(rows, body, 10)
    return {"success": True, "count": total, "data": page_rows, "code": 0}


ROUTES = {
    # knowledge（§2.3 接口族：find/detail/add/edit/del/log/read）
    ("knowledge", "list"): _k_list,
    ("knowledge", "detail"): _k_detail,
    ("knowledge", "add"): _k_add,
    ("knowledge", "edit"): _k_edit,
    ("knowledge", "del"): _k_del,
    ("knowledge", "read"): _k_read,
    ("knowledge", "log"): _k_log,
    # category（§2.3；move/workchat_department_list 无触发路径，不注册=404 语义）
    ("category", "list"): _cat_list,
    ("category", "add"): _cat_add,
    ("category", "edit"): _cat_edit,
    ("category", "del"): _cat_del,
    ("category", "publish"): _cat_publish,
    # 分类级联（知识列表/表单共用）
    ("dropdown", "category_cascade"): _dropdown_category_cascade,
    # notice 管理端补充（list/add/read/not_read_count 已在 misc.py，勿重复注册）
    ("notice", "edit"): _notice_edit,
    ("notice", "del"): _notice_del,
    # 公告范围选择
    ("selector", "user"): _selector_user,
    # 公众号模板（查询端；save 本轮不做）
    ("msg_template", "list"): _msg_template_list,
    # 短信查询端（余额/套餐/充值记录/发送记录；buy_sms 不做）
    ("enterprise", "get_sms_remain_num"): _sms_remain,
    ("sms_package", "list"): _sms_package_list,
    ("sms_recharge", "list"): _sms_recharge_list,
    ("sms_send_log", "list"): _sms_send_log_list,
}
