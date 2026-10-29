# -*- coding: utf-8 -*-
"""快工单后端复刻 · 核心（demo 级：业务侧功能复刻，不做安全加固）。
契约与原站一致：POST /api/<module>/<action>，body 含 token/enterprise_id/pageNo/pageSize/timestamp/sign/channel。
"""
import os, json, time, hashlib, threading, uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
SECRET = "81ad0be7fd53914f8cf8193c1886f635"   # 与原站前端同款（demo 沿用，保持前端可无缝切换）

_lock = threading.Lock()

def _load(name, default=None):
    p = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def _save(name, obj):
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        # 原子写：临时文件 + os.replace，防并行进程交错写坏 JSON
        tmp = os.path.join(DATA_DIR, name + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(DATA_DIR, name + ".json"))

# ---- 存储层：每实体一个 JSON 集合（demo 级，内存单例 + 原子落盘） ----
_store_cache = {}

class Store:
    """同名实体全局单例：首次加载后驻留内存，避免循环内反复整文件重读（慢端点根因），
    也消除多实例 flush 互相覆盖（B5/B4 曾各自绕过，此处根治）。"""
    def __new__(cls, name):
        if name not in _store_cache:
            inst = super().__new__(cls)
            inst.name = name
            inst.rows = _load(name, [])
            inst.meta = _load(name + ".meta", {})
            _store_cache[name] = inst
        return _store_cache[name]

    @classmethod
    def reload(cls, name=None):
        """重置缓存（seed 重跑后调用）。"""
        if name:
            _store_cache.pop(name, None)
        else:
            _store_cache.clear()

    def all(self):
        return self.rows

    def get(self, rid):
        for r in self.rows:
            if str(r.get("id")) == str(rid):
                return r
        return None

    def add(self, row, code_field="code"):
        row["id"] = row.get("id") or int(time.time() * 1000) % 100000000 + len(self.rows)
        row.setdefault("created_at", now())
        row.setdefault("updated_at", now())
        row.setdefault("del", 2)
        self.rows.insert(0, row)
        self.flush()
        return row

    def update(self, rid, patch):
        r = self.get(rid)
        if not r:
            return None
        r.update(patch)
        r["updated_at"] = now()
        self.flush()
        return r

    def remove(self, rid):
        self.rows = [r for r in self.rows if str(r.get("id")) != str(rid)]
        self.flush()

    def flush(self):
        _save(self.name, self.rows)

    def page(self, params, default_size=30):
        """分页：契约字段 pageNo/pageSize，返回 {list,total}。"""
        try:
            pageNo = int(params.get("pageNo") or 1)
        except Exception:
            pageNo = 1
        try:
            pageSize = int(params.get("pageSize") or default_size)
        except Exception:
            pageSize = default_size
        return {"list": self.rows[(pageNo - 1) * pageSize: pageNo * pageSize], "total": len(self.rows), "pageNo": pageNo, "pageSize": pageSize}

def now(fmt="%Y-%m-%d %H:%M:%S"):
    return time.strftime(fmt)

def next_code(prefix, rows, width=4, datefmt="%Y%m%d"):
    """编号规则：商品/BOM=前缀+YYYYMMDD+4位；工艺路线=前缀+YYYYMM+3位（datefmt 传入）。"""
    stamp = time.strftime(datefmt)
    mx = 0
    for r in rows:
        c = str(r.get("code") or "")
        if c.startswith(prefix + stamp):
            try:
                n = int(c[len(prefix + stamp):])
                mx = max(mx, n)
            except ValueError:
                pass
    return prefix + stamp + str(mx + 1).zfill(width)

# ---- 会话（内存，重启失效；demo 级） ----
SESSIONS = {}

def make_session(user):
    tok = uuid.uuid4().hex
    SESSIONS[tok] = user
    return tok

def check_sign(body):
    ts = body.get("timestamp")
    sign = body.get("sign")
    if not ts or not sign:
        return False
    return hashlib.md5((str(ts) + SECRET).encode()).hexdigest() == sign

def session_of(body):
    return SESSIONS.get(str(body.get("token") or ""))

# ---- 响应 ----
def ok(data=None, count=None):
    r = {"success": True}
    if count is not None:
        r["count"] = count
    if data is not None:
        r["data"] = data
    return r

def fail(msg, error_code=None, post_track=2):
    r = {"success": False, "post_track": post_track, "msg": msg}
    if error_code:
        r["error_code"] = error_code
    return r

def paged_ok(store_page_result):
    """把 Store.page 的结果包装成原站 {success, count, data:{list,total,...}}。"""
    return {"success": True, "count": store_page_result["total"], "data": store_page_result}
