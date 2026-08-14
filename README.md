# 冥想盆（Pensieve）—— 长期记忆智能体

> 随手往里丢任何信息（消息、笔记、文件），它自动打标签并**长期保存**；
> 随时提问，它**只根据存过的原文回答**并标注出处——**没有就说没有，绝不编造**。

## 一、整体架构

```
【写入路径】
用户消息 / 文件(txt·md·pdf·docx)
   │
   ├─ 1. 文本提取：纯文本直接读；PDF 用 pdfplumber，docx 用 python-docx
   ├─ 2. 元数据打标：LLM 抽取（摘要/标签/人物/事件日期），无 API 时规则兜底
   └─ 3. 落库：SQLite 单文件长期存储
          ├─ records      原文（写入后不可变）+ 结构化元数据（可 SQL 精确过滤）
          └─ records_fts  FTS5 全文索引（中文二元分词）

【读取路径】
用户提问
   ├─ 1. 问题解析：抽取日期（含"明天/下周三"等相对日期）与关键词
   ├─ 2. 混合召回：SQL 日期硬过滤 + FTS 关键词（先 AND 精确、再 OR 召回，bm25 排序）
   └─ 3. 答案生成（三条铁律保证"绝对准确"）：
          ① 原文不可变存储 —— 答案引用的永远是一字未改的原话
          ② 必须标注出处 #编号 —— 用户可以逐字核对
          ③ 检索无结果 → 直接回答"没有" —— 从源头杜绝幻觉
```

## 二、为什么是 SQLite 而不是向量数据库

"绝对准确"和"语义理解"是两个目标，本设计**以准确为先**：

| 方案 | 优点 | 风险 |
|---|---|---|
| 纯向量 RAG | 语义召回强 | 相似度≠相关，易幻觉，无法保证"没有就说没有" |
| **SQL + FTS（本方案）** | 命中即原文、可解释、可核对、零依赖 | 同义改写召回弱（可用 LLM/向量增强） |

工程上后续可以叠加向量召回���`sqlite-vec` + bge-m3）作为**第三路召回**，
但**回答仍然只允许引用命中的原文**——向量只负责"找到"，不负责"回答"。

## 三、快速开始

零依赖（纯标准库），Python ≥ 3.8 即可运行：

```bash
cd pensieve

# 存入记忆
python3 pensieve.py add "下周三下午3点要开项目周会，地点在3号会议室"
python3 pensieve.py add -f 体检报告.pdf        # 支持 txt/md/pdf/docx

# 查询
python3 pensieve.py ask "项目周会什么时候开？"
python3 pensieve.py ask "我的身份证号是多少"     # 没存过 → 明确回答"没有"

# 管理
python3 pensieve.py list          # 最近记忆
python3 pensieve.py show 1        # 查看详情
python3 pensieve.py delete 1      # 删除
python3 pensieve.py stats         # 统计
python3 pensieve.py export 备份.jsonl   # 全量备份
```

数据默认存在当前目录的 `pensieve.db`（单文件，拷贝即备份）；
可用环境变量 `PENSIEVE_DB` 指定其他路径。

## 四、接入 LLM（可选增强）

设置环境变量���，打标和回答自动切换为 LLM 驱动（任何 OpenAI 兼容接口均可）：

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可指向国产模型接口
export PENSIEVE_MODEL=gpt-4o-mini
```

LLM 模式下有两道保险：

1. **打标**：用 prompt 约束模型只输出 JSON（摘要/标签/人物/统一换算后的事件日期）；
2. **回答**：prompt 强制"只能逐字引用给定记录、必须标注 #出处、无相关记录必须回答没有"，
   且界面上**永远同时附上原始记录**供用户核对——LLM 只负责组织语言，不作事实来源。

## 五、扩展成完整智能体的路线

当前是 CLI 原型，按比赛需要可以逐层加码：

| 层级 | 做法 | 说明 |
|---|---|---|
| 服务化 | FastAPI 包一层 `/add` `/ask` 接口 | `pensieve.py` 可直接 import 复用全部函数 |
| 对话入口 | 接入微信/Telegram/飞书 bot | 消息即 `add`，问句即 `ask` |
| 召回增强 | `sqlite-vec` + bge-m3 向量索引 | 解决同义改写（"花了多少钱"≈"费用"） |
| 多模态 | 图片→OCR（paddleocr）、语音→whisper | 统一转成文本后走同一写入路径 |
| 可靠性 | 定时 `export` + 云端同步 db 文件 | 单文件天然易备份 |

FastAPI 最小示例：

```python
from fastapi import FastAPI
import pensieve
app = FastAPI()
db = pensieve.get_db()

@app.post('/add')
def add(text: str):
    return {'id': pensieve.add_record(db, text, verbose=False)}

@app.get('/ask')
def ask(q: str):
    results, _, _ = pensieve.search(db, q)
    return {'found': bool(results),
            'records': [dict(r) for r in results]}
```

## 六、已验证的功能测试

- ✅ 文本存入 + 自动打标（标签/日期/相对日期换算）
- ✅ PDF / txt 文件解析入库
- ✅ 关键词查询命中原文（含 OR 兜底召回）
- ✅ "9月8日有什么安排" 日期硬过滤
- ✅ "下周三我要做什么" 相对日期匹配
- ✅ 未存入信息 → 明确回答"没有找到相关记录"
- ✅ 删除 / 统计 / JSONL 导出备份
