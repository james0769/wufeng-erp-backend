# -*- coding: utf-8 -*-
"""快工单后端复刻 · HTTP 服务（demo 级）。
- API：POST /api/<module>/<action>，契约同原站（body 携带 token/enterprise_id/pageNo/pageSize/timestamp/sign/channel）
- 静态：同进程托管 ../ 前端（含 SPA 回退），一个进程跑起整个 demo
启动：python backend/server.py [端口，默认 9001]
"""
import sys, os, json, re, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

FRONTEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".woff": "font/woff",
        ".woff2": "font/woff2", ".ttf": "font/ttf", ".json": "application/json", ".ico": "image/x-icon"}

def collect_routes():
    """聚合 modules/*.py 的 ROUTES：{(module, action): handler(body, session) -> dict}。"""
    routes = {}
    mdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")
    if not os.path.isdir(mdir):
        return routes
    for fn in sorted(os.listdir(mdir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        mod_name = "modules." + fn[:-3]
        try:
            m = __import__(mod_name, fromlist=["ROUTES"])
            for key, handler in getattr(m, "ROUTES", {}).items():
                routes[key] = handler
        except Exception as e:
            print("[modules] 加载失败 %s: %s" % (fn, e))
    return routes

ROUTES = collect_routes()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, dict) and isinstance(body.get("__binary__"), (bytes, bytearray)):
            return self._send_bin(status, bytes(body["__binary__"]), str(body.get("__filename__") or "export.xlsx"))
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_bin(self, status, data, filename):
        """handler 返回 {"__binary__": bytes, "__filename__": "xx.xlsx"} 时的二进制直通（B10）。"""
        from urllib.parse import quote
        self.send_response(status)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Length", str(len(data)))
        # 中文名走 RFC 5987 filename*（http.server 头仅允许 latin-1）
        self.send_header("Content-Disposition", "attachment; filename=\"export.xlsx\"; filename*=UTF-8''%s" % quote(filename))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(200, b"", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0]
        # 深链下的相对资源归一（与 serve.py 同款）
        m = re.search(r"/(vendor|js|css|assets)/", path)
        if m and path.rfind("/" + m.group(1) + "/") > 0:
            path = "/" + path[m.start() + 1:]
        fp = os.path.normpath(os.path.join(FRONTEND_ROOT, path.lstrip("/")))
        if fp.startswith(FRONTEND_ROOT) and os.path.isfile(fp):
            ext = os.path.splitext(fp)[1].lower()
            with open(fp, "rb") as f:
                self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))
            return
        if not os.path.splitext(path)[1] or not os.path.isfile(fp):
            with open(os.path.join(FRONTEND_ROOT, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        self._send(404, {"success": False, "msg": "404"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/"):
            return self._send(404, {"success": False, "msg": "404"})
        parts = path[len("/api/"):].strip("/").split("/")
        if len(parts) != 2:
            return self._send(404, {"success": False, "msg": "404"})
        module, action = parts
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send(200, core.fail("参数解析失败"))
        if not core.check_sign(body):
            return self._send(200, core.fail("验证失败"))
        session = core.session_of(body)
        if action not in ("login_by_pwd", "login_by_sms") and not session and module not in ("user",):
            # 与原站语义一致：无 token 返回 001 请登录
            return self._send(200, core.fail("请登录", error_code="001"))
        handler = ROUTES.get((module, action))
        if not handler:
            return self._send(200, core.fail("404"))
        try:
            return self._send(200, handler(body, session))
        except Exception:
            traceback.print_exc()
            return self._send(200, core.fail("服务器开小差了", post_track=3))

def main():
    # 端口：命令行参数 > PORT 环境变量（Render 等云平台注入）> 默认 9001
    # 绑定地址：HOST 环境变量（云平台需 0.0.0.0 对外）> 默认 127.0.0.1（本地）
    port = int(sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("PORT") or 9001))
    host = os.environ.get("HOST") or "127.0.0.1"
    print("KGD demo backend + frontend -> http://%s:%d  (routes=%d)" % (host, port, len(ROUTES)))
    ThreadingHTTPServer((host, port), Handler).serve_forever()

if __name__ == "__main__":
    main()
