# 快工单后端复刻（demo 级）· 子代理任务简报

## 目标
为复刻应用做一个**业务侧后端**（demo，不做安全/兼容性加固），契约与原站完全一致：
`POST /api/<module>/<action>`，body 携带 `{token, enterprise_id, pageNo, pageSize, timestamp, sign=md5(ts+"81ad0be7fd53914f8cf8193c1886f635"), channel:1}`；
响应 `{success, count?, data?, msg?, error_code?}`（无 token → error_code "001" 请登录）。

## 核心骨架（已完成，勿改）
- `backend\server.py` — 路由聚合（自动加载 modules/*.py 的 ROUTES）+ 静态托管前端 + SPA 回退
- `backend\core.py` — Store(名).all/get/add/update/remove/page、ok/fail/paged_ok、next_code、会话
- `backend\modules\auth.py` — **参考模式**（ROUTES 字典 + handler(body, session)）

## 编写模块的规则
1. 你只写**自己名下的那一个文件** `backend\modules\<域>.py`，禁止改 server.py/core.py/别人的模块
2. handler 签名 `def _xx(body, session)`；session 是登录用户 dict（auth 模块外必非空）
3. 分页一律 `core.paged_ok(store.page(body))`；筛选在 page() 前对 store.all() 做过滤（字段名以 api_dump 真实字段为准）
4. **种子数据约定**（B11 会生成到 backend\data\*.json，你直接 `Store("goods")` 等使用）：
   goods / boms / bom_trees / crafts / routings / wastes / suppliers / users / teams / categories / units / workshops / produce_lines / machines / customers / contacts / contracts / invoices / purchase_bills / produce_bills / produce_bill_crafts / stock_items / goods_stocks / messages / notices / filter_views
5. 业务规则**以逆向报告为准**（字段/枚举/状态机/公式逐字），报告在 `_gen_tmp\kgd_reverse\analysis\`（deep\ 深挖 + 基础），数据样例在同目录 `..\..\api_dump\`（注意 deep_m1~m21/intelli/probes 子目录）
6. 原站 404/未部署的接口（如 produce_plan 族）**不要实现**，保持 404 语义（fail("404")）
7. 写完自测：`python -c "import sys; sys.path.insert(0,'<backend目录>'); import server"` 能加载（ROUTES 数量>0），并用 core 手工调用你的 2-3 个 handler 打印结果验证
8. 界面功能提示也参考用户整理的工程包：`D:\zcoding\报表资料持续检索\跨境报表\快工单ERP工程包\`（上传模板/报表清单/业务链路说明）
