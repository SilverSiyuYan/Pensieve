# 架构与数据流

```text
用户注册 / 登录
  │
  ▼
不透明 Bearer Session（数据库仅保存 Token 哈希）
  │
  ▼
认证获得可信 user_id
  │
  ▼
用户输入
  │
  ▼
FastAPI 路由
  │
  ▼
判断意图：存储 / 查询
  ├────────────────────────── 存储 ──────────────────────────┐
  │                                                          │
  │                                          写入 SQLite：原始数据
  │                                                          │
  │                                          写入 ChromaDB：向量嵌入
  │                                                          │
  └────────────────────────── 查询 ──────────────────────────┐
                                                             │
                              ChromaDB 按 user_id 过滤语义检索  │
                                                             ▼
                                             LLM 整合回答（OpenAI 兼容 API）
                                                             │
                                                             ▼
                                            返回整合结果 + 原始记忆
```

- SQLite 是用户、Session、对话、消息和原始记忆的权威存储；所有用户数据包含 `user_id`。
- ChromaDB 使用共享 collection，每条向量 metadata 包含 `user_id`，检索时强制使用相同字段过滤。
- 查询时根据 ChromaDB 返回的标识再次以 `user_id + memory_id` 从 SQLite 回查，形成双层隔离。
- LLM 客户端可以共享，但每次请求只使用当前认证用户的对话和召回记忆。
