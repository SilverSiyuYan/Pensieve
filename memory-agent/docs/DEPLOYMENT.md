# 部署说明

本文面向不需要理解业务代码的服务器部署人员。推荐使用 Docker Compose 部署单实例服务。

## 1. 系统依赖

### 推荐方案

- 64 位 Linux 服务器
- Docker Engine 24 或更高版本
- Docker Compose v2
- 至少 2 个 CPU、4 GB 内存、10 GB 可用磁盘（嵌入模型和数据增长需额外空间）
- 可访问配置的 OpenAI 兼容模型服务
- 首次运行时可下载 ChromaDB 默认嵌入模型

### 原生运行方案

- Python 3.11 或 3.12
- pip 和 Python venv
- SQLite 由 Python 标准库提供，无需独立数据库服务
- Node.js：不需要。前端没有 npm 依赖和构建步骤

## 2. 环境变量

复制 `backend/.env.example` 为 `backend/.env`。文件权限建议设为仅部署用户可读，例如 Linux 上执行 `chmod 600 backend/.env`。

| 变量 | 必填 | 示例 | 说明 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | 是 | `由密钥系统注入` | OpenAI 兼容模型服务密钥，禁止提交 Git |
| `OPENAI_BASE_URL` | 是 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容 API 根地址 |
| `MODEL_NAME` | 是 | `qwen-plus` | Chat Completions 模型名称 |
| `SESSION_TTL_HOURS` | 是 | `24` | 登录 Session 有效小时数 |
| `CORS_ALLOWED_ORIGINS` | 是 | `https://memory.example.com` | 允许的前端源；多个值用英文逗号分隔 |

当前版本的数据库和 Chroma 路径由代码固定。容器镜像通过符号链接把它们统一存放到 `/data`，Compose 再把 `/data` 挂载为持久卷。

## 3. Docker Compose 部署

```bash
cd memory-agent
cp backend/.env.example backend/.env
vi backend/.env
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8080/health
```

成功标准：

- `backend` 和 `frontend` 均为 running/healthy。
- 健康检查返回 `{"status":"ok", ...}`。
- 打开 `http://服务器地址:8080` 可以看到页面。
- 保存一条非敏感测试记忆后，可以在记忆列表看到它。

查看日志：

```bash
docker compose logs --tail=200 backend
docker compose logs --tail=100 frontend
```

停止但保留数据：

```bash
docker compose down
```

不要在生产环境执行 `docker compose down -v`，它会删除记忆数据卷。

## 4. 数据初始化

FastAPI 启动时会自动创建或迁移用户、Session、对话、消息、记忆、embedding 和任务表。`backend/init_db.py` 只初始化结构，不写入演示数据。

旧单用户记忆会迁移到禁用的 legacy 用户，不会被新账户看到。迁移前必须完成备份。

如果当前登录用户的 SQLite 数据存在但向量索引丢失，可携带该用户 Bearer Token 执行：

```bash
curl --fail -X POST -H "Authorization: Bearer $ACCESS_TOKEN" http://127.0.0.1:8080/api/memory/rebuild
```

重建期间不要并发写入，并在完成后抽样验证检索结果。

## 5. 原生 Python 启动

```bash
cd memory-agent/backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
uvicorn main:application --host 127.0.0.1 --port 8001 --workers 1
```

前端可交给现有 Nginx 静态托管，将 `/api/` 和 `/health` 反向代理到 `127.0.0.1:8001`。示例配置见 `frontend/nginx.conf`。

不建议直接使用 `uvicorn --reload`、把 Uvicorn 端口暴露到公网，或为当前 SQLite/Chroma 架构启动多个 worker。

## 6. 升级与回滚

升级前必须先备份数据卷，再构建新镜像：

```bash
docker compose down
docker run --rm -v memory-agent_memory_data:/data -v "$PWD/backups:/backup" alpine \
  tar czf /backup/memory-data-$(date +%Y%m%d-%H%M%S).tgz -C /data .
docker compose build --pull
docker compose up -d
```

实际 volume 名称以 `docker volume ls` 为准。备份应复制到服务器之外并加密保存。

回滚时使用上一个已验证的镜像版本，同时恢复与该版本兼容的数据备份。当前项目没有 schema migration 框架，任何未来表结构变更都必须附带独立迁移和回滚说明。

## 7. 生产网络与安全边界

- 推荐由现有网关或负载均衡器终止 TLS，再转发到本机 `8080`。
- Compose 不对宿主机公开后端端口；Nginx 是唯一入口。
- 当前应用有账户认证和用户级数据隔离，但登录/注册仍需由网关限流。
- `/api/memory/rebuild` 只重建当前用户索引，仍应限制调用频率。
- 不要在日志、工单或聊天中粘贴 `.env` 内容。
- API Key 轮换后重新创建后端容器：`docker compose up -d --force-recreate backend`。
