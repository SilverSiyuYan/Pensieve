# 服务器部署交接说明

本文是服务器部署人员的主交接文档。项目提供账户和用户级记忆隔离，但当前 SQLite/本地 ChromaDB 架构仍只支持单后端实例。公网入口必须配置 HTTPS 和限流。

## 1. 环境要求

推荐环境：

- 64 位 Linux
- Docker Engine 24+
- Docker Compose v2
- 2 CPU、4 GB 内存、10 GB 以上可用磁盘
- 可以访问配置的 OpenAI 兼容 API
- 首次使用向量检索时，可以下载 ChromaDB 默认嵌入模型

原生运行要求：

- Python 3.11 或 3.12
- pip、venv
- Node.js 不需要；前端是纯静态 HTML/JavaScript

## 2. Docker 部署步骤

```bash
cd memory-agent
cp .env.example backend/.env
chmod 600 backend/.env
# 使用安全编辑器填写真实 OPENAI_API_KEY
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8080/health
```

浏览器入口：`http://服务器地址:8080`。生产环境应由已有网关提供 HTTPS，只把流量转发到该入口。后端 `8000` 端口没有映射到宿主机。

停止服务但保留数据：

```bash
docker compose down
```

禁止在没有已验证备份时执行 `docker compose down -v`。

## 3. 环境变量配置

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 是 | OpenAI 兼容服务密钥，禁止提交到 Git、镜像或工单 |
| `OPENAI_BASE_URL` | 是 | Chat Completions 兼容 API 根地址 |
| `MODEL_NAME` | 是 | 服务商提供的模型名称 |
| `SESSION_TTL_HOURS` | 是 | Bearer Session 有效小时数，默认 24 |
| `CORS_ALLOWED_ORIGINS` | 是 | 允许的前端源，多个值用英文逗号分隔 |

Compose 从 `backend/.env` 注入变量。模板中的值不是生产密钥。建议由服务器 Secret 管理系统生成该文件，并限制为部署用户可读。

## 4. 数据目录说明

容器内：

- `/data/memory.db`：SQLite 权威记忆数据
- `/data/chroma_data/`：ChromaDB 派生向量索引

Compose 使用 `memory_data` 命名卷挂载 `/data`。SQLite 应作为首要备份对象；ChromaDB 可以从 SQLite 重新生成。当前架构只允许一个后端实例和一个 Uvicorn worker，不要直接横向扩容。

应用启动时会自动创建或迁移表。`backend/init_db.py` 现在只初始化结构，不插入演示数据，也可以在启动前显式执行。

旧单用户数据库首次迁移时，原有记忆会归属到禁用的 legacy 用户，不会自动暴露给任何新注册账户。迁移前必须备份；如需把旧数据转交给指定账户，应另行执行经审核的数据归属迁移。

需要重建索引时，在禁止普通用户访问且暂停写入后执行：

```bash
curl --fail -X POST -H "Authorization: Bearer $ACCESS_TOKEN" http://127.0.0.1:8080/api/memory/rebuild
```

## 5. 数据备份方法

先停止应用写入，再备份命名卷：

```bash
docker compose down
mkdir -p backups
docker run --rm \
  -v memory-agent_memory_data:/data:ro \
  -v "$PWD/backups:/backup" \
  alpine tar czf /backup/memory-data-backup.tgz -C /data .
docker compose up -d
```

实际卷名以 `docker volume ls` 为准。备份文件需要加密并复制到服务器之外。上线前至少完成一次恢复演练，确认 SQLite 记录可读并能重建向量索引。

## 6. 更新版本方法

1. 记录当前 Git commit 和镜像版本。
2. 执行数据备份。
3. 拉取经过测试的发布版本。
4. 执行 `docker compose build --pull`。
5. 执行 `docker compose up -d`。
6. 检查健康状态、日志、保存、查询和删除流程。
7. 失败时恢复旧代码/镜像和对应数据备份。

当前没有数据库 migration 框架。未来涉及表结构的版本必须附带迁移和回滚脚本，不能只替换镜像。

## 7. 常见错误排查

### 后端持续 unhealthy

```bash
docker compose ps
docker compose logs --tail=200 backend
```

检查 `.env` 是否存在、变量名是否正确、模型地址是否可访问，以及数据卷是否可写。

### 页面可以打开，但请求失败

```bash
docker compose logs --tail=100 frontend
curl -i http://127.0.0.1:8080/health
```

确认 backend 已健康，且 `frontend/nginx.conf` 未被错误覆盖。

### 首次查询较慢或嵌入失败

ChromaDB 默认嵌入模型可能尚未下载。检查容器网络、磁盘空间和后端日志。生产环境建议预热模型缓存。

### LLM 返回 401、403 或模型不存在

检查 API Key 是否有效、Base URL 是否正确、账号是否拥有 `MODEL_NAME` 对应模型权限。不要把密钥输出到日志。

### SQLite 锁定或数据不一致

确认只有一个后端容器和一个 worker。停止写入后备份 SQLite；如原始记录存在但语义检索缺失，可由管理员重建 Chroma 索引。

### Docker 卷名称不匹配

执行 `docker volume ls` 查找带项目名前缀的 `memory_data` 卷。不要猜测卷名后执行删除命令。

## 上线限制

- 当前已提供账户认证和用户级记忆隔离，但没有管理员角色和 API 限流。
- `/api/memory/rebuild` 需要认证并只处理当前用户，仍建议限制调用频率。
- 必须把 `CORS_ALLOWED_ORIGINS` 设置为实际 HTTPS 前端域名。
- 应由外部网关配置 TLS、认证、请求体限制、速率限制和访问日志。
- 部署前必须轮换曾进入 Git 历史的 API Key，并由仓库负责人清理历史。

上线前逐项执行 [部署检查清单](docs/DEPLOYMENT_CHECKLIST.md)。
