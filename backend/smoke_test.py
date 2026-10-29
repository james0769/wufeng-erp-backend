# -*- coding: utf-8 -*-
"""B11 · 冒烟测试：起临时 server（或连已运行实例）→ 登录拿 token → 逐模块调 2-3 个端点 → 打印 成功/失败 表格。

用法：
  python backend/smoke_test.py                     # 自动起临时 server（默认端口 9377，测完即关）
  python backend/smoke_test.py --port 9400
  python backend/smoke_test.py --external http://127.0.0.1:9001   # 连已在跑的 server
  python backend/smoke_test.py --only user,goods                   # 只测指定模块
  python backend/smoke_test.py --list-modules                      # 只看已实现模块，不发请求

- 用例来源：CASES 显式登记（带业务参数，如 filter_view_id）；未登记的模块按动作名自动挑
  只读类端点（list/count/info/detail/tree/dd/selector/config…），跳过写操作（add/edit/del/pause…）。
- CASES 里登记但 server 未加载的模块记「未实现」（供收尾时盘点缺哪些模块）。
- 退出码：全部成功=0（存在未实现模块不算失败）；任一用例失败=1。
"""
import os
import sys
import json
import time
import hashlib
import argparse
import subprocess
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core                                    # 复用 SECRET，保持与 server 签名一致
import server as server_mod                    # 仅取 ROUTES 清单（import 不起服务）

MOBILE, PWD = "15871772084", "demo123456"      # demo 账号（auth 模块只校验非空）

# ---- 显式用例：动作名 → 附加业务参数（以 api_dump 抓包 req / 各模块 ROUTES 实测为准）----
CASES = {
    "user": [
        ("login_by_pwd", {"mobile": MOBILE, "pwd": PWD}),
        ("info", {}),
        ("home", {}),
    ],
    "goods": [
        ("list_by_filter_view", {"filter_view_id": 528445}),
        ("list", {}),
    ],
    "goods_bom": [("list", {})],
    # 以下模块（B 导入/导出专项）目前仅实现 import/export 族，取只读的表头配置动作探活
    "pub_craft": [("import_template_header", {})],
    "craft_line": [("import_template_header", {})],
    "waste_item": [("import_template_header", {})],
    "goods_craft": [("import_template_header", {})],
    "outsource_produce_bill": [("import_template_header", {})],
    "produce_schedule": [("import_template_header", {})],
    "supplier": [("list", {}), ("list_by_filter_view", {}), ("status_count", {})],
    "produce_bill": [
        ("web_list", {}),
        ("list", {}),
        ("status_count", {}),
    ],
    "produce_bill_craft": [("list", {}), ("list_by_filter_view", {})],
    "customer": [("list", {})],
    "customer_contract": [("list", {})],
    "linkman": [("list", {})],
    "goods_stock": [("list", {}), ("list", {"has_stock": 2})],  # 默认 has_stock=1 只看有库存
    "stock_item": [("list", {})],
    "purchase_bill": [("list", {})],
    "purchase_plan": [("list", {}), ("list_by_filter_view", {})],
    "msg": [("list", {})],
    "notice": [("list", {}), ("not_read_count", {})],
    "filter_view": [("list_by_permission", {"type": "goods"})],
    "machine": [("list", {}), ("list_by_filter_view", {})],
    "workbench": [("today_finish", {}), ("overdue_produce", {})],
    "versions": [("now_push_versions", {})],
    "autocomplete": [("supplier", {"keyword": "山"})],
    # ---- B13 全模块补全：各新域显式用例（参数型端点必须带参，否则业务校验报错）----
    "report_work_record": [("list", {}), ("status_count", {}), ("report_statistics", {})],
    "report_work_quality": [("list", {}), ("status_count", {})],
    "produce_inspection_random": [("list", {}), ("status_count", {})],
    "inspection_scheme": [("list", {})],
    "produce_project": [("list", {}), ("status_count", {}), ("list_by_filter_view", {"filter_view_id": 294961})],
    "import_record": [("list", {})],
    "produce_schedule": [("list", {}), ("status_count", {}), ("list_by_filter_view", {})],
    "production_scheduling": [("list_by_filter_view", {"filter_view_id": 794598}), ("statistics", {"filter_view_id": 794598})],
    "outsource_produce_bill": [("list", {}), ("status_count", {}), ("import_template_header", {})],
    "outsource_acceptance": [("list", {})],
    "outsource_settle": [("list", {}), ("statistics", {})],
    "outsource_acceptance_content": [("list", {})],
    "cost_share": [("list", {})],
    "cost_price_item": [("list", {})],
    "station": [("list", {})],
    "process_additional_component": [("list", {})],
    "craft_attach_module": [("list", {})],
    "traceability_code": [("list", {})],
    "single": [("list", {})],
    "single_qr_code": [("list", {})],
    "enterprise_config": [("get_by_module", {"module": "produce_config"}), ("batch_get_by_module", {"module_name_array": ["enterprise_config", "produce_config"]})],
    "priority_configure": [("list", {"type": 1})],
    "user": [
        ("login_by_pwd", {"mobile": MOBILE, "pwd": PWD}),
        ("info", {}),
        ("home", {}),
        ("enterprise_user_list", {}),
    ],
    "role": [("enterprise_role_list", {})],
    "team": [("list", {}), ("team_tree", {})],
    "category": [("list", {"busi_type": 2}), ("list", {"busi_type": 1}), ("list", {"busi_type": 5})],
    "dic": [("list", {"type_id": 1})],
    "fun_display": [("tree", {})],
    "knowledge": [("list", {})],
    "selector": [("user", {})],
    "msg_template": [("list", {})],
    "sms_package": [("list", {})],
    "data_screen": [("list", {}), ("detail", {"id": 334674})],
    "stock_in_bill": [("list", {})],
    "stock_out_bill": [("list", {})],
    # ---- B14 报表端点轮 ----
    "statistics": [("machine_analysis_list", {}), ("waste_trend_analysis", {"start_date": "2025-01-01", "end_date": "2026-10-29", "time_granularity": 3}),
                   ("waste_main_analysis_list", {}), ("new_work_order_total_use_time_list", {})],
    "machine_tool": [("statistics", {})],
    "work_in_progress_report": [("work_in_process_statistics", {}), ("list_by_filter_view", {})],
    "produce_craft_time_record": [("list", {})],
    "waste_detail": [("list", {})],
    "inspection_random_analysis": [("inspection_random_list", {})],
    "scrap_rework_analysis": [("list", {})],
    "salary_report": [("list_by_filter_view", {})],
    "customer_contract_item": [("list_by_filter_view", {})],
    "produce_bill_cost": [("statistics_by_filter_view", {}), ("list_by_filter_view", {})],
    "outsource_produce_bill_cost": [("statistics_by_filter_view", {}), ("list_by_filter_view", {})],
    "purchase_inspection_report": [("purchase_inspection_list", {})],
    "after_sale_salary_report": [("list_by_filter_view", {})],
    "clock_in": [("report_forms", {})],
    "linkman": [("list", {})],
}

SAFE_PAT = ("list", "count", "info", "home", "tree", "dropdown", "dd",
            "selector", "config", "statistics", "stats", "suggestion", "nav", "template_header")
UNSAFE_PAT = ("add", "edit", "del", "update", "remove", "pause", "cancel", "dispatch",
              "report", "import", "export", "print", "upload", "login_by_sms", "logout")


def auto_cases(module, actions):
    """未登记模块：自动挑最多 3 个只读动作。"""
    cands = []
    for a in sorted(actions):
        if a in ("login_by_pwd", "logout"):
            continue
        low = a.lower()
        if any(u in low for u in UNSAFE_PAT):
            continue
        if any(s in low for s in SAFE_PAT):
            cands.append(a)
        if len(cands) >= 3:
            break
    return [(a, {}) for a in cands[:3]]


def sign_body(extra, token=None):
    ts = str(int(time.time() * 1000))
    body = {"enterprise_id": 16807, "pageNo": 1, "pageSize": 3,
            "timestamp": ts, "channel": 1,
            "sign": hashlib.md5((ts + core.SECRET).encode()).hexdigest()}
    if token:
        body["token"] = token
    body.update(extra)
    return body


def post(base, module, action, body, timeout=60):
    url = "%s/api/%s/%s" % (base, module, action)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"success": False, "msg": "HTTP %d" % e.code}
    except Exception as e:
        return {"success": False, "msg": "%s: %s" % (type(e).__name__, e)}


def describe(resp):
    if not isinstance(resp, dict):
        return "非 dict 响应"
    if not resp.get("success"):
        if str(resp.get("msg")) == "404":
            return "按原站语义 404（未部署接口，预期）"
        return "失败 msg=%s code=%s" % (resp.get("msg"), resp.get("error_code"))
    data = resp.get("data")
    if isinstance(data, dict) and "list" in data:
        return "count=%s list=%s" % (resp.get("count", data.get("total")), len(data["list"] or []))
    if isinstance(data, list):
        return "list=%s" % len(data)
    if isinstance(data, dict):
        keys = ",".join(list(data)[:4])
        return "data={%s…}" % keys
    return "ok"


def wait_up(base, proc, seconds=15):
    for _ in range(seconds * 4):
        if proc is not None and proc.poll() is not None:
            sys.exit("[smoke] server 进程提前退出（exit=%s）" % proc.returncode)
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9377)
    ap.add_argument("--external", default=None, help="已运行的 server 地址，如 http://127.0.0.1:9001")
    ap.add_argument("--only", default="", help="逗号分隔，只测这些模块")
    ap.add_argument("--list-modules", action="store_true")
    args = ap.parse_args()

    routes = server_mod.ROUTES
    mods = {}
    for (m, a) in routes:
        mods.setdefault(m, []).append(a)
    print("已加载模块（%d 个，routes=%d）：%s" % (len(mods), len(routes), ", ".join(sorted(mods))))
    if args.list_modules:
        return

    # ---- 启动/连接 server ----
    proc = None
    if args.external:
        base = args.external.rstrip("/")
    else:
        base = "http://127.0.0.1:%d" % args.port
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), str(args.port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        if not wait_up(base, proc):
            if proc:
                proc.terminate()
            sys.exit("[smoke] server 启动超时：%s" % base)

    results, missing = [], []
    try:
        # ---- 登录拿 token ----
        resp = post(base, "user", "login_by_pwd", sign_body({"mobile": MOBILE, "pwd": PWD}))
        token = (resp.get("data") or {}).get("web_token") if isinstance(resp.get("data"), dict) else None
        results.append(("user", "login_by_pwd", resp.get("success") is True and bool(token),
                         "token=%s" % (token[:8] + "…" if token else describe(resp))))
        if not token:
            print("\n[smoke] 登录失败，后续 signed 请求会全部 001。表格如下：")

        only = [x.strip() for x in args.only.split(",") if x.strip()]
        plan = []           # (module, action, extra)
        for m in sorted(set(list(CASES) + list(mods))):
            if only and m not in only:
                continue
            if m not in mods:
                missing.append(m)
                continue
            cases = [(a, ex) for a, ex in CASES.get(m, []) if a in mods[m]]
            if len(cases) < len(CASES.get(m, [])) and m in CASES:
                # 登记了但部分动作缺失——记录未实现动作
                for a, _ in CASES[m]:
                    if a not in mods[m]:
                        results.append((m, a, False, "未实现（ROUTES 无此动作）"))
            if not cases:
                cases = auto_cases(m, mods[m])
            if not cases:
                results.append((m, "-", None, "跳过：无 list/count 类只读动作，均为参数型或写操作"))
                continue
            for a, ex in cases:
                if m == "user" and a == "login_by_pwd":
                    continue  # 登录预步骤已测过，避免重复行
                plan.append((m, a, ex))

        for m, a, ex in plan:
            resp = post(base, m, a, sign_body(ex, token=token))
            okk = (isinstance(resp, dict) and resp.get("success") is True) or (
                isinstance(resp, dict) and str(resp.get("msg")) == "404")   # 简报规则6：原站 404 的接口保持 404
            results.append((m, a, okk, describe(resp)))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    # ---- 表格输出 ----
    W = (14, 26, 6, 44)
    print("\n%s %s %s %s" % ("模块".ljust(W[0]), "动作".ljust(W[1]), "结果".ljust(W[2]), "说明"))
    print("-" * (W[0] + W[1] + W[2] + W[3] + 3))
    for m, a, okk, note in results:
        mark = "跳过" if okk is None else ("成功" if okk else "失败")
        print("%s %s %s %s" % (m.ljust(W[0]), a.ljust(W[1]), mark.ljust(W[2]), note))
    n_ok = sum(1 for r in results if r[2] is True)
    n_all = sum(1 for r in results if r[2] is not None)
    print("-" * (W[0] + W[1] + W[2] + W[3] + 3))
    print("合计：%d/%d 成功（另有 %d 条跳过）" % (n_ok, n_all, len(results) - n_all))
    if missing:
        print("未实现模块（CASES 已登记但 ROUTES 缺失）：%s" % ", ".join(missing))
    sys.exit(0 if n_ok == n_all else 1)


if __name__ == "__main__":
    main()
