# 接入国产 LLM API 指南

`pensieve.py` 已内置 OpenAI 兼容接口调用，接入 = **设置 3 个环境变量，零改代码**。

## 一、接入原理

```
检测到 OPENAI_API_KEY？
   ├─ 是 → LLM 打标（抽取 JSON 元数据）+ LLM 组织回答（强制引用原文 + 附出处）
   └─ 否 / 接口故障 → 自动回退本地规则打标 + 直接呈现原文（功能不中断）
```

已验证的三种运行状态：

| 状态 | 表现 |
|---|---|
| 正常 LLM 模式 | `打标引擎：LLM`，回答为"LLM 引用原文 + 出处核对区"双段式 |
| 拒答 | 检索无结果时**直接回答"没有"**，不调用 LLM（不产生幻觉机会） |
| LLM 接口故障 | 自动降级为规则打标，记忆正常存入 |

## 二、平台选择（都兼容 OpenAI 接口格式）

| 平台 | 申请地址 | `OPENAI_BASE_URL` | `PENSIEVE_MODEL` | 备注 |
|---|---|---|---|---|
| 智谱 GLM | open.bigmodel.cn | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | **免费**，比赛首选 |
| DeepSeek | platform.deepseek.com | `https://api.deepseek.com` | `deepseek-chat` | 约 1 元/百万 token，JSON 稳 |
| 通义千问 | bailian.console.aliyun.com | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | 新用户有免费额度 |
| Kimi | platform.moonshot.cn | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | 长文本处理好 |

申请流程（各平台一致）：注册 → 实名认证 → 控制台「API Key 管理」→ 创建并复制 Key（**只显示一次**）。

## 三、配置步骤

```bash
# 1. 设置环境变量（以智谱为例）
export OPENAI_API_KEY="你的API Key"
export OPENAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
export PENSIEVE_MODEL="glm-4-flash"

# 2. 验证
python3 pensieve.py stats        # 最后一行应显示：打标/回答引擎：LLM（glm-4-flash）
python3 pensieve.py add "下周三下午3点开项目周会，记得带报表"
python3 pensieve.py ask "项目周会什么时候开"
```

持久化配置（二选一）：

```bash
# 方式 A：写入 shell 配置
echo 'export OPENAI_API_KEY="xxx"' >> ~/.bashrc

# 方式 B：项目 .env 文件（注意加入 .gitignore，防止提交泄露）
cat > .env <<'EOF'
OPENAI_API_KEY=xxx
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
PENSIEVE_MODEL=glm-4-flash
EOF
```

## 四、接入后系统行为变化

| 环节 | 无 LLM（规则模式） | 有 LLM |
|---|---|---|
| 打标 | 正则抽日期 + 关键词匹配标签 | LLM 理解语义，输出 JSON（摘要/标签/**人物**/日期） |
| 相对日期 | 支持常见词（明天/下周三） | 任意复杂表达都能换算成绝对日期 |
| 回答 | 直接列出原文记录 | LLM 组织自然语言 + **逐字引用** + **永远附出处区** |
| 拒答 | 无结果直接说"没有" | 相同（拒答在检索层完成，不依赖模型自觉） |

LLM 回答的 prompt 中已内置三条铁律约束（见 `pensieve.py` 的 `LLM_ANSWER_PROMPT`）：

1. 只能依据提供的记录回答，必须逐字引用并用 #编号 标注出处；
2. 记录中没有的信息绝对不允许推测、编造；
3. 所有记录都不相关时只回复"没有找到相关记录"。

## 五、注意事项

- **temperature 已固定为 0**：打标和回答都追求确定性，不要调高
- **JSON 解析有兜底**：模型返回非 JSON 时 `llm_extract` 自动降级规则打标，不会报错中断
- **超时 60 秒**：网络差时会等待较久再降级，属正常现象
- **成本控制**：打标每次约消耗 200~400 token，glm-4-flash 免费、DeepSeek 约几厘钱/条，比赛规模随便用
- **Key 安全**：绝不写进代码；若不慎泄露，立刻去平台控制台吊销重建
- **比赛演示建议**：提前 `export` 好再演示；同时演示一次"拔掉 Key 也能用"的降级能力，体现系统健壮性
