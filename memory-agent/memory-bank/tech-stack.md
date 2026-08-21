# 技术栈

| 依赖 | 版本要求 | 用途 |
| --- | --- | --- |
| Python | >= 3.11 | 运行时 |
| fastapi | >= 0.115,< 1.0 | Web 框架与 API 路由 |
| uvicorn[standard] | >= 0.30,< 1.0 | ASGI 应用服务器 |
| chromadb | >= 0.5,< 2.0 | 本地向量数据库与语义检索 |
| openai | >= 1.0,< 3.0 | OpenAI 兼容 LLM API 客户端 |
| sqlite3 | Python 标准库 | 原始记忆与结构化数据存储 |
| python-dotenv | >= 1.0,< 2.0 | 加载环境变量 |

OpenAI 兼容服务地址、模型名称和 API 密钥通过环境变量配置，便于切换不同服务提供商。
