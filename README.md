# 无锋 ERP 云端后端（Render）

- 仓库内容：`backend/`（Python stdlib 单进程 HTTP 服务，541 条路由，JSON 文件存储 + 真实种子数据）
- Render 配置见 `render.yaml`（free 计划，rootDir=backend，绑定 0.0.0.0:$PORT）
- 启动：`python server.py $PORT`（HOST=0.0.0.0）
- 健康检查：`POST /api/user/home`（签名校验，返回 error_code 001 即服务正常）
- 数据写入为 ephemeral（Render 重启后回到种子状态；免费层特性）
