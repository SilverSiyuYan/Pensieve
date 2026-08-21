# memory-agent 项目指令

本项目是一个**带长期记忆的智能体 Web 应用**。

## 技术约束

- 后端：Python、FastAPI、SQLite、ChromaDB，以及 OpenAI 兼容 API。
- 前端：纯 HTML/JavaScript 单页应用，不引入构建工具或前端框架。
- SQLite 用于保存原始、结构化记忆；ChromaDB 用于向量化语义检索。

## 开发要求

- 保持 API、前端和文档的一致性。
- 所有代码修改后，必须确保项目功能可运行；至少验证应用能够启动，并在适用时运行相关检查。
- 不要提交真实密钥；仅在 `.env` 中配置本地密钥，并维护 `.env.example` 模板。
