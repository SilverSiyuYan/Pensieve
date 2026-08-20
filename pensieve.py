#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
冥想盆 (Pensieve) v2.0 —— 多用户长期记忆智能体
=================================================
用户可以随意发消息/文字文件进来，系统自动打标签并长期保存；
随时可以提问，系统只根据存过的原文回答并标注出处，没有就说"没有"。

v2.0 新增：多用户支持
  · users 表：用户名 + PBKDF2 加密口令（不存明文密码）
  · 每条记忆归属一个 user_id，检索/回答严格按用户隔离
  · 旧版单用户数据库自动迁移：补 user_id 列，首个注册用户自动认领旧记忆

运行方式（零依赖，Python ≥ 3.8 即可）：
    python3 pensieve.py add "下周三下午3点开项目周会"        # 默认存入 1 号用户
    python3 pensieve.py -u 2 ask "周会什么时候开？"           # 以 2 号用户身份查询
    python3 pensieve.py list / show 1 / delete 1 / stats / export 备份.jsonl

可选 LLM 增强（任何 OpenAI 兼容接口；不设置则纯本地规则运行）：
    export OPENAI_API_KEY=你的Key
    export OPENAI_BASE_URL=https://api.deepseek.com
    export PENSIEVE_MODEL=deepseek-chat
（也可在脚本同目录放 .env 文件，三行 KEY=VALUE，会被自动读取）

数据存储：当前目录下 pensieve.db（SQLite 单文件，拷贝即备份；
可用环境变量 PENSIEVE_DB 指定其他路径）。
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

VERSION = '2.0.0'


def _load_env_file():
    """如果脚本同目录下存在 .env 文件，自动读取其中的 KEY=VALUE 配置。
    （已存在的系统环境变量优先，.env 只作补充）"""
    env_file = Path(__file__).with_name('.env')
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()
DB_PATH = os.environ.get('PENSIEVE_DB', str(Path.cwd() / 'pensieve.db'))

# ============================================================
# 一、分词：中文按二元组，英文/数字按单词（供 FTS5 全文索引使用）
# ============================================================
SEG_RE = re.compile(r'[㐀-䶿一-鿿]+|[^㐀-䶿一-鿿]+')
CJK_RE = re.compile(r'[㐀-䶿一-鿿]')


def tokenize(text: str) -> list:
    """'项目周会3点开始' -> ['项目', '目周', '周会', '3', '点开', ...]"""
    tokens = []
    for seg in SEG_RE.findall(text.lower()):
        if CJK_RE.match(seg[0]):
            if len(seg) == 1:
                tokens.append(seg)
            else:
                tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.extend(re.findall(r'[a-z0-9]+', seg))
    return tokens


# 查询时的停用词（按长度从长到短替换，避免误切）
STOPWORDS = [
    '什么时候', '怎么样', '是不是', '有没有', '多少钱', '哪些', '哪里', '哪儿',
    '怎么', '如何', '为什么', '什么', '多少', '几点', '请问', '帮我', '告诉',
    '知道', '记得', '一下', '我们', '这个', '那个', '曾经', '的', '了', '吗',
    '呢', '吧', '啊', '是', '在', '有', '和', '跟', '与', '及', '或', '谁',
    '请', '想', '我', '你', '他', '她', '它', '这', '那', '过', '曾', '到',
]
STOP_RE = re.compile('|'.join(sorted((re.escape(w) for w in STOPWORDS),
                                     key=len, reverse=True)))


def query_tokens(query: str) -> list:
    """问题剔除停用词后分词，得到检索词元。"""
    return tokenize(STOP_RE.sub(' ', query))


# ============================================================
# 二、日期抽取（绝对日期 + 相对日期，统一换算成 YYYY-MM-DD）
# ============================================================
REL_DAYS = {'今天': 0, '明天': 1, '后天': 2, '大后天': 3,
            '昨天': -1, '前天': -2, '大前天': -3}
WEEKDAYS = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4,
            '六': 5, '日': 6, '天': 6, '末': 5}


def extract_date(text: str, now: datetime = None):
    """从文本中抽取一个 YYYY-MM-DD 日期；没有则返回 None。"""
    now = now or datetime.now()
    # 相对日：今天/明天/大前天……
    for word in ('大后天', '大前天', '今天', '明天', '后天', '昨天', '前天'):
        if word in text:
            return (now + timedelta(days=REL_DAYS[word])).strftime('%Y-%m-%d')
    # 绝对日：2024年5月1日 / 2024-05-01 / 2024/5/1
    m = re.search(r'(\d{4})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*[日号]?', text)
    if m:
        y, mo, d = map(int, m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f'{y:04d}-{mo:02d}-{d:02d}'
    # 缺省年：5月1日（默认今年）
    m = re.search(r'(?<![\d\-/\.])(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', text)
    if m:
        mo, d = map(int, m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f'{now.year:04d}-{mo:02d}-{d:02d}'
    # 周X / 下周X / 下下周X
    m = re.search(r'(下{1,2})?(?:周|星期)([一二三四五六日天末])', text)
    if m:
        nxt, wd = m.groups()
        delta = (WEEKDAYS[wd] - now.weekday()) % 7
        if nxt:
            delta += 7 * len(nxt)
        elif delta == 0:
            delta = 7
        return (now + timedelta(days=delta)).strftime('%Y-%m-%d')
    return None


# ============================================================
# 三、规则打标（无 LLM 时的兜底；有 LLM 时由 LLM 打标）
# ============================================================
TAG_KEYWORDS = {
    '工作': ['工作', '会议', '开会', '周会', '项目', '汇报', '老板', '同事', '客户',
             '面试', '加班', '出差', 'deadline', '周报'],
    '学习': ['学习', '考试', '课程', '论文', '读书', '复习', '作业', '老师'],
    '生活': ['快递', '房租', '水电', '搬家', '购物', '打扫', '洗衣'],
    '健康': ['医院', '医生', '牙医', '体检', '健身', '跑步', '感冒', '发烧', '疫苗', '药'],
    '财务': ['工资', '转账', '报销', '发票', '银行', '投资', '理财', '基金', '股票', '花了'],
    '社交': ['朋友', '聚会', '生日', '婚礼', '吃饭', '约'],
    '账号密码': ['密码', '账号', '账户', '口令', '验证码', '密钥'],
    '行程': ['机票', '火车', '高铁', '航班', '酒店', '旅行', '签证', '护照'],
    '想法': ['想法', '灵感', '计划', '打算', '目标', '感悟'],
}


def extract_tags(text: str) -> list:
    low = text.lower()
    return [tag for tag, kws in TAG_KEYWORDS.items()
            if any(k.lower() in low for k in kws)]


def rule_extract(content: str) -> dict:
    """本地规则打标���摘要取首行，标签靠关键词，日期靠正则。"""
    return {
        'summary': content.strip().split('\n')[0][:30],
        'tags': extract_tags(content),
        'people': [],
        'event_date': extract_date(content),
    }


# ============================================================
# 四、可选 LLM 增强（OpenAI 兼容接口，标准库 urllib 调用，零依赖）
# ============================================================
def llm_available() -> bool:
    return bool(os.environ.get('OPENAI_API_KEY'))


def llm_chat(prompt: str) -> str:
    """向 OpenAI 兼容接口发一次对话请求，返回文本。temperature 固定为 0。"""
    import urllib.request
    base = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    model = os.environ.get('PENSIEVE_MODEL', 'gpt-4o-mini')
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model,
            'temperature': 0,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8'),
        headers={
            'Authorization': f"Bearer {os.environ['OPENAI_API_KEY']}",
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))['choices'][0]['message']['content']


LLM_EXTRACT_PROMPT = """你是信息打标助手。阅读用户存入的一段记忆，抽取结构化元数据。
今天是 {today}。
只输出 JSON，不要输出任何其他内容：
{{
  "summary": "一句话概括（不超过30字）",
  "tags": ["从以下类别选0~3个：工作/学习/生活/健康/财务/社交/账号密码/行程/想法/其他"],
  "people": ["内容中提到的人名或称呼"],
  "event_date": "内容涉及的具体日期，统一换算为 YYYY-MM-DD；相对日期（明天、下周三等）按今天换算；没有则为 null"
}}

记忆内容：
{content}"""


def llm_extract(content: str):
    """LLM 打标；失败（超时/非JSON）返回 None，由调用方降级为规则打标。"""
    try:
        out = llm_chat(LLM_EXTRACT_PROMPT.format(
            today=datetime.now().strftime('%Y-%m-%d'), content=content))
        data = json.loads(re.search(r'\{.*\}', out, re.S).group(0))
        date = data.get('event_date')
        return {
            'summary': str(data.get('summary') or '')[:100],
            'tags': [str(t) for t in (data.get('tags') or [])][:5],
            'people': [str(p) for p in (data.get('people') or [])][:5],
            'event_date': date if re.match(r'^\d{4}-\d{2}-\d{2}$', str(date or '')) else None,
        }
    except Exception:
        return None


LLM_ANSWER_PROMPT = """你是"冥想盆"记忆查询助手。用户向你提问，下面是从记忆库中检索到的原始记录。
铁律：
1. 只能依据这些记录回答，必须逐字引用相关原文，并用 #编号 标注出处；
2. 记录中没有的信息，绝对不允许推测、编造或补充；
3. 如果所有记录都与问题无关或不足以回答，只回复："没有找到相关记录。"

用户问题：{question}

检索到的记录：
{records}"""


def llm_answer(question: str, records: list):
    """LLM 组织回答（只能引用原文）；失败返回 None，由调用方直接呈现原文。"""
    try:
        rec_text = '\n\n'.join(
            f"#{r['id']}（存入于 {r['created_at']}，日期 {r['event_date'] or '—'}）\n{r['content']}"
            for r in records)
        return llm_chat(LLM_ANSWER_PROMPT.format(
            question=question, records=rec_text)).strip()
    except Exception:
        return None


# ============================================================
# 五、存储层（SQLite 单文件 = 长期记忆）
#   users        用户表（口令 PBKDF2 加密，不存明文）
#   records      原文（写入后不可变）+ 结构化元数据 + user_id 归属
#   records_fts  FTS5 全文索引（中文二元分词）
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 0,  -- 归属用户；0 = 待认领的旧版数据
    content     TEXT NOT NULL,               -- 原文，写入后不可变
    summary     TEXT DEFAULT '',
    tags        TEXT DEFAULT '[]',           -- JSON 数组
    people      TEXT DEFAULT '[]',           -- JSON 数组
    event_date  TEXT,                        -- YYYY-MM-DD 或 NULL
    source      TEXT DEFAULT 'text',
    created_at  TEXT NOT NULL
    category    TEXT DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(tokens);
"""

INDEX_SQL = 'CREATE INDEX IF NOT EXISTS idx_records_user_date ON records(user_id, event_date)'


def get_db() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    
    # —— 自动迁移：检查并补全缺失的列 ——
    cols = [r[1] for r in conn.execute('PRAGMA table_info(records)')]
    
    # 旧版迁移：user_id
    if 'user_id' not in cols:
        conn.execute('ALTER TABLE records ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0')
        conn.commit()
        cols.append('user_id')  # 更新列名列表
    
    # 新增迁移：category（任务/灵感/感悟）
    if 'category' not in cols:
        conn.execute('ALTER TABLE records ADD COLUMN category TEXT DEFAULT ""')
        conn.commit()
        print('✅ 已自动添加 category 列')
    
    conn.execute(INDEX_SQL)
    conn.commit()
    return conn


# ---------- 用户账号（口令 PBKDF2-HMAC-SHA256 加盐哈希） ----------
def _hash_password(password: str, salt: str = None) -> str:
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                            bytes.fromhex(salt), 100_000).hex()
    return f'{salt}:{h}'


def create_user(conn: sqlite3.Connection, username: str, password: str) -> int:
    """注册新用户，返回用户 id；用户名已存在则抛 ValueError。
    首个注册用户自动认领旧版遗留的记忆（user_id = 0 的记录）。"""
    username = username.strip()
    if not username:
        raise ValueError('用户名不能为空')
    if conn.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
        raise ValueError('用户名已被注册')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute('INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)',
                       (username, _hash_password(password), now))
    uid = cur.lastrowid
    conn.execute('UPDATE records SET user_id = ? WHERE user_id = 0', (uid,))
    conn.commit()
    return uid


def verify_user(conn: sqlite3.Connection, username: str, password: str):
    """登录校验：成功返回用户 id，失败返回 None。"""
    row = conn.execute('SELECT id, password_hash FROM users WHERE username = ?',
                       (username.strip(),)).fetchone()
    if not row:
        return None
    salt, expect = row['password_hash'].split(':')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                 bytes.fromhex(salt), 100_000).hex()
    return row['id'] if actual == expect else None


# ---------- 记忆写入 ----------
def add_record(conn: sqlite3.Connection, content: str, source: str = 'text',
               user_id: int = 1, verbose=True):
    """写入路径：打标 -> 原文落库（带用户归属）-> 建立全文索引。"""
    content = content.strip()
    if not content:
        print('⚠️  内容为空，未存入。')
        return None
    meta, engine = (llm_extract(content), 'LLM') if llm_available() else (None, '')
    if meta is None:                      # 无 Key 或 LLM 故障 → 规则兜底
        meta, engine = rule_extract(content), '规则'
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute(
        'INSERT INTO records(user_id, content, summary, tags, people, event_date, source, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        (user_id, content, meta['summary'], json.dumps(meta['tags'], ensure_ascii=False),
         json.dumps(meta['people'], ensure_ascii=False), meta['event_date'], source, now))
    rid = cur.lastrowid
    conn.execute('INSERT INTO records_fts(rowid, tokens) VALUES (?,?)',
                 (rid, ' '.join(tokenize(content))))
    conn.commit()
    if verbose:
        print(f'✅ 已存入记忆 #{rid}（用户 #{user_id}，打标引擎：{engine}）')
        print(f'   摘要：{meta["summary"]}')
        print(f'   标签：{"、".join(meta["tags"]) or "无"}　'
              f'日期：{meta["event_date"] or "无"}　'
              f'人物：{"、".join(meta["people"]) or "无"}')
    return rid


# ---------- 记忆检索（严格按用户隔离） ----------
def fts_search(conn: sqlite3.Connection, tokens: list, user_id: int = None, limit: int = 8):
    """全文检索：先 AND（精确），无结果再 OR（召回），按 bm25 相关度排序。
    指定 user_id 时只检索该���户的记忆。"""
    if not tokens:
        return [], 'none'
    quoted = ['"' + t.replace('"', '') + '"' for t in tokens]
    where_user = ' AND r.user_id = ?' if user_id is not None else ''
    for mode, expr in (('AND', ' AND '.join(quoted)), ('OR', ' OR '.join(quoted))):
        params = [expr] + ([user_id] if user_id is not None else []) + [limit]
        try:
            rows = conn.execute(
                'SELECT r.*, bm25(records_fts) AS score'
                ' FROM records_fts JOIN records r ON r.id = records_fts.rowid'
                f' WHERE records_fts MATCH ?{where_user} ORDER BY score LIMIT ?',
                params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return rows, mode
    return [], 'none'


def search(conn: sqlite3.Connection, question: str, user_id: int = None, limit: int = 5):
    """混合召回：日期硬过滤 + 关键词全文检索，去重合并（日期命中的排前面）。
    指定 user_id 时只在该用户的记忆范围内检索。"""
    qdate = extract_date(question)
    date_rows = []
    if qdate:
        sql = 'SELECT * FROM records WHERE event_date = ?'
        params = [qdate]
        if user_id is not None:
            sql += ' AND user_id = ?'
            params.append(user_id)
        date_rows = list(conn.execute(
            sql + ' ORDER BY id DESC LIMIT ?', (*params, limit)).fetchall())
    rows, mode = fts_search(conn, query_tokens(question), user_id=user_id, limit=limit)
    seen, results = set(), []
    for r in date_rows + list(rows):
        if r['id'] not in seen:
            seen.add(r['id'])
            results.append(r)
    return results[:limit], qdate, mode


# ============================================================
# 六、查询���输出（回答铁律：只引用原文 + 标注出处 + 无则拒答）
# ============================================================
def render_record(r, prefix='——'):
    tags = '、'.join(json.loads(r['tags'])) or '无'
    print(f'{prefix} 记忆 #{r["id"]} | 存入于 {r["created_at"]} | '
          f'日期 {r["event_date"] or "—"} | 标签 [{tags}] | 来源 {r["source"]}')
    print(f'   原文：{r["content"]}')


def ask(conn: sqlite3.Connection, question: str, user_id: int = 1, limit: int = 5):
    results, qdate, mode = search(conn, question, user_id=user_id, limit=limit)
    if not results:
        print('🚫 没有找到相关记录。（你的记忆库中不存在与该问题相关的内容）')
        return
    header = f'🔍 找到 {len(results)} 条相关记录'
    hints = []
    if qdate:
        hints.append(f'日期过滤：{qdate}')
    if mode == 'OR':
        hints.append('部分关键词匹配')
    if hints:
        header += f'（{"，".join(hints)}）'
    print(header + '：\n')
    answered = False
    if llm_available():
        answer = llm_answer(question, results)
        if answer:
            print(f'💡 回答：{answer}\n')
            answered = True
    if not answered:
        print('💡 以下为记忆库中的原始记录（未接入 LLM，直接呈现原文）：\n')
    print('—— 出处（可逐字核对）——')   # 无论是否有 LLM，原文出处永远附上
    for r in results:
        render_record(r)


def list_records(conn: sqlite3.Connection, user_id: int = None, limit: int = 20):
    sql, params = 'SELECT * FROM records', []
    if user_id is not None:
        sql += ' WHERE user_id = ?'
        params.append(user_id)
    rows = conn.execute(sql + ' ORDER BY id DESC LIMIT ?', (*params, limit)).fetchall()
    if not rows:
        print('（记忆库为空）')
        return
    for r in rows:
        tags = '、'.join(json.loads(r['tags'])) or '—'
        first = r['content'].split('\n')[0][:40]
        print(f'#{r["id"]:<4} [u{r["user_id"]}] [{r["created_at"]}] '
              f'日期:{r["event_date"] or "—"} 标签:{tags}  {first}')


def show_record(conn: sqlite3.Connection, rid: int):
    r = conn.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
    if not r:
        print(f'⚠️  记忆 #{rid} 不存在。')
        return
    render_record(r, prefix='')
    print(f'   摘要：{r["summary"]}')
    print(f'   人物：{"、".join(json.loads(r["people"])) or "无"}')
    print(f'   归属用户：#{r["user_id"]}')


def delete_record(conn: sqlite3.Connection, rid: int, user_id: int = None):
    sql, params = 'DELETE FROM records WHERE id = ?', [rid]
    if user_id is not None:               # 指定用户时只能删自己的
        sql += ' AND user_id = ?'
        params.append(user_id)
    cur = conn.execute(sql, params)
    conn.execute('DELETE FROM records_fts WHERE rowid = ?', (rid,))
    conn.commit()
    print(f'🗑️  已删除记忆 #{rid}。' if cur.rowcount else f'⚠️  记忆 #{rid} 不存在或不属于该用户。')


def stats(conn: sqlite3.Connection):
    n = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    u = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    rng = conn.execute('SELECT MIN(created_at), MAX(created_at) FROM records').fetchone()
    size = Path(DB_PATH).stat().st_size / 1024 if Path(DB_PATH).exists() else 0
    tag_count = {}
    for (t,) in conn.execute('SELECT tags FROM records'):
        for tag in json.loads(t):
            tag_count[tag] = tag_count.get(tag, 0) + 1
    print(f'📦 记忆总数：{n}　|　注册用户：{u}　|　库文件：{DB_PATH}（{size:.1f} KB）')
    if n:
        print(f'   时间跨度：{rng[0]} ~ {rng[1]}')
        print(f'   标签分布：' + '，'.join(
            f'{k}×{v}' for k, v in sorted(tag_count.items(), key=lambda x: -x[1])))
    engine = ('LLM（' + os.environ.get('PENSIEVE_MODEL', 'gpt-4o-mini') + '）'
              if llm_available() else '本地规则（设置 OPENAI_API_KEY 可切换 LLM）')
    print(f'   打标/回答引擎：{engine}')


def export_jsonl(conn: sqlite3.Connection, out_path: str, user_id: int = None):
    sql, params = 'SELECT * FROM records', []
    if user_id is not None:
        sql += ' WHERE user_id = ?'
        params.append(user_id)
    rows = conn.execute(sql + ' ORDER BY id', params).fetchall()
    with open(out_path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(dict(r), ensure_ascii=False) + '\n')
    print(f'💾 已导出 {len(rows)} 条记忆到 {out_path}')


# ============================================================
# 七、文件读取（txt/md/csv/json 直接读；pdf/docx 需对应解析库）
# ============================================================
def read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        sys.exit(f'文件不存在：{path}')
    suf = p.suffix.lower()
    if suf in ('.txt', '.md', '.markdown', '.log', '.csv', '.json'):
        return p.read_text(encoding='utf-8', errors='ignore')
    if suf == '.pdf':
        try:
            import pdfplumber
        except ImportError:
            sys.exit('解析 PDF 需要先安装：pip3 install pdfplumber')
        with pdfplumber.open(str(p)) as pdf:
            return '\n'.join(page.extract_text() or '' for page in pdf.pages)
    if suf == '.docx':
        try:
            import docx
        except ImportError:
            sys.exit('解析 docx 需要先安装：pip3 install python-docx')
        d = docx.Document(str(p))
        return '\n'.join(par.text for par in d.paragraphs)
    sys.exit(f'暂不支持的文件类型：{suf}（支持 txt/md/pdf/docx/csv/json）')


# ============================================================
# 八、命令行入口（-u 指定用户 id；add/ask 默认 1 号用户）
# ============================================================
def main():
    ap = argparse.ArgumentParser(
        prog='pensieve',
        description='冥想盆 —— 多用户长期记忆智能体（存进去，随时问，只答有的）',
        epilog='示例：\n'
               '  pensieve.py add "下周三下午3点开项目周会"     （默认存入1号用户）\n'
               '  pensieve.py -u 2 ask "周会什么时候开？"\n'
               '  pensieve.py list / show 1 / delete 1 / stats / export 备份.jsonl',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-V', '--version', action='version', version=f'%(prog)s {VERSION}')
    ap.add_argument('-u', '--user', type=int, default=None,
                    help='以哪个用户身份操作（add/ask 默认为 1；list/stats/export 默认为全部用户）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('add', help='存入一条记忆（文本或文件）')
    p.add_argument('content', nargs='?', help='记忆文本')
    p.add_argument('-f', '--file', help='从文件存入（txt/md/pdf/docx/csv/json）')

    p = sub.add_parser('ask', help='查询记忆（只答存过的，没有就说没有）')
    p.add_argument('question', help='你的问题')
    p.add_argument('-n', '--limit', type=int, default=5, help='最多返回几条（默认5）')

    p = sub.add_parser('list', help='列出最近的记忆')
    p.add_argument('-n', '--limit', type=int, default=20, help='条数（默认20）')

    p = sub.add_parser('show', help='查看某条记忆详情')
    p.add_argument('id', type=int, help='记忆编号')

    p = sub.add_parser('delete', help='删除某条记忆')
    p.add_argument('id', type=int, help='记忆编号')

    sub.add_parser('stats', help='记忆库统计')

    p = sub.add_parser('export', help='导出记忆为 JSONL（备份用）')
    p.add_argument('output', help='输出文件路径')

    args = ap.parse_args()
    conn = get_db()

    if args.cmd == 'add':
        uid = args.user if args.user is not None else 1
        if args.file:
            add_record(conn, read_file(args.file), source=args.file, user_id=uid)
        elif args.content:
            add_record(conn, args.content, user_id=uid)
        else:
            sys.exit('用法: pensieve.py add "文本"  或  pensieve.py add -f 文件路径')
    elif args.cmd == 'ask':
        uid = args.user if args.user is not None else 1
        ask(conn, args.question, user_id=uid, limit=args.limit)
    elif args.cmd == 'list':
        list_records(conn, user_id=args.user, limit=args.limit)
    elif args.cmd == 'show':
        show_record(conn, args.id)
    elif args.cmd == 'delete':
        delete_record(conn, args.id, user_id=args.user)
    elif args.cmd == 'stats':
        stats(conn)
    elif args.cmd == 'export':
        export_jsonl(conn, args.output, user_id=args.user)

# ---------- 记忆管理相关函数（新增） ----------

def get_memories_by_user(db, uid):
    """获取某个用户的所有记忆，按时间倒序"""
    cur = db.execute('''
        SELECT id, content, category, summary, tags, created_at 
        FROM memories 
        WHERE uid = ? 
        ORDER BY created_at DESC
    ''', (uid,))
    rows = cur.fetchall()
    # 把 sqlite3.Row 对象转成字典列表
    return [dict(row) for row in rows]

def delete_memory_by_id(db, memory_id, uid):
    """
    删除指定记忆（必须同时传入 uid，防止删别人的）
    返回：True 表示删除了，False 表示没找到或无权限
    """
    cur = db.execute('DELETE FROM memories WHERE id = ? AND uid = ?', (memory_id, uid))
    db.commit()
    return cur.rowcount > 0

if __name__ == '__main__':
    main()
