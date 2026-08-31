# 部署前冻结报告

冻结日期：2026-08-21

## 当前版本状态

- 应用形态：带账户认证的静态前端、FastAPI 单体后端、SQLite、ChromaDB、OpenAI 兼容 LLM
- 部署形态：Docker Compose 单实例，Nginx 为唯一 Web 入口
- 数据形态：SQLite 为权威数据，ChromaDB 为可重建索引
- 自动化验证：16 项测试通过，包含双用户隔离测试
- Docker 验证：Compose 结构已解析；审计机器未安装 Docker，尚未执行真实镜像构建

## 已完成事项

- 添加项目 README、部署说明和部署检查清单
- 添加生产 Dockerfile、Nginx 代理和持久数据卷
- 锁定生产依赖，分离开发测试依赖
- 添加 `.gitignore` 和 Docker 构建忽略规则
- 提供不含真实密钥的 `.env.example`
- 从当前 Git 索引取消跟踪 `.env`、SQLite、Chroma 和 Python 缓存，同时保留本地文件
- 验证前端不包含 API Key，后端密钥仅通过环境变量读取
- 增加用户注册、登录、注销及只存 Token 哈希的不透明 Session
- 为记忆、对话、消息、embedding 和任务记录增加 `user_id`
- Chroma 使用共享 collection，并通过 `where={"user_id": ...}` 强制过滤
- 旧单用户数据安全迁移到禁用的 legacy 用户

## 未完成事项

- 历史 Git 提交仍含旧 `.env`、数据库、向量索引和缓存，需要仓库负责人协调历史清理
- 必须在模型服务商后台吊销并轮换旧 API Key
- 当前没有管理员/RBAC 和登录频率限制
- CORS 必须在生产环境设置为正式前端域名
- 重建索引接口已限定当前用户，但仍缺少调用频率限制
- 没有显式 LLM 超时、Token 上限、业务重试和限流
- 没有数据库 migration 框架和自动备份任务
- 尚未在安装 Docker 的 Linux 主机执行镜像构建与恢复演练

## 部署人员须知

- 本版本只允许单后端实例、单 Uvicorn worker
- `backend/init_db.py` 可用于结构初始化，不再写入演示记忆
- 不直接公开后端端口；公网入口必须有 HTTPS 和认证网关
- 不执行 `docker compose down -v`，除非明确要求销毁全部数据
- 更新前备份 `memory_data`，并把备份复制到服务器之外
- 真实密钥只放入受限 Secret 或 `backend/.env`，不得提交仓库

## 未来扩展建议

- 在现有用户隔离上增加 RBAC、tenant 隔离及审计日志
- 将 SQLite 迁移至 PostgreSQL，将向量存储迁移到共享服务
- 引入 schema migration、异步索引任务和一致性补偿机制
- 增加 LLM Token 预算、超时、重试、熔断、限流和成本监控
- 增加 CI 镜像构建、密钥扫描、依赖漏洞扫描及备份恢复测试
