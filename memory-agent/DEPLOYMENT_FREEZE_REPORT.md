# 部署前冻结报告

冻结日期：2026-08-21

## 当前版本状态

- 应用形态：纯静态前端、FastAPI 单体后端、SQLite、ChromaDB、OpenAI 兼容 LLM
- 部署形态：Docker Compose 单实例，Nginx 为唯一 Web 入口
- 数据形态：SQLite 为权威数据，ChromaDB 为可重建索引
- 自动化验证：11 项测试通过
- Docker 验证：Compose 结构已解析；审计机器未安装 Docker，尚未执行真实镜像构建

## 已完成事项

- 添加项目 README、部署说明和部署检查清单
- 添加生产 Dockerfile、Nginx 代理和持久数据卷
- 锁定生产依赖，分离开发测试依赖
- 添加 `.gitignore` 和 Docker 构建忽略规则
- 提供不含真实密钥的 `.env.example`
- 从当前 Git 索引取消跟踪 `.env`、SQLite、Chroma 和 Python 缓存，同时保留本地文件
- 验证前端不包含 API Key，后端密钥仅通过环境变量读取

## 未完成事项

- 历史 Git 提交仍含旧 `.env`、数据库、向量索引和缓存，需要仓库负责人协调历史清理
- 必须在模型服务商后台吊销并轮换旧 API Key
- 当前没有应用层认证、授权和多用户数据隔离
- CORS 仍允许任意来源
- 重建索引接口没有管理员权限保护
- 没有显式 LLM 超时、Token 上限、业务重试和限流
- 没有数据库 migration 框架和自动备份任务
- 尚未在安装 Docker 的 Linux 主机执行镜像构建与恢复演练

## 部署人员须知

- 本版本只允许单后端实例、单 Uvicorn worker
- 不运行 `backend/init_db.py`，避免加入演示记忆
- 不直接公开后端端口；公网入口必须有 HTTPS 和认证网关
- 不执行 `docker compose down -v`，除非明确要求销毁全部数据
- 更新前备份 `memory_data`，并把备份复制到服务器之外
- 真实密钥只放入受限 Secret 或 `backend/.env`，不得提交仓库

## 未来扩展建议

- 增加认证、RBAC、`user_id`/tenant 隔离及审计日志
- 将 SQLite 迁移至 PostgreSQL，将向量存储迁移到共享服务
- 引入 schema migration、异步索引任务和一致性补偿机制
- 增加 LLM Token 预算、超时、重试、熔断、限流和成本监控
- 增加 CI 镜像构建、密钥扫描、依赖漏洞扫描及备份恢复测试

