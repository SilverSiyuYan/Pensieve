#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
冥想盆 · 网页版 v2.0（多用户）
================================
在 pensieve.py 外面包一层 Flask：注册/登录 + 按用户隔离的私有记忆库。

使用方法：
    pip install flask          # 只需装这一个库
    python web.py              # 启动后浏览器打开 http://127.0.0.1:5000

说明：
    · 未登录访问会被引导到登录/注册页
    · 每个用户只能看到和检索自己的记忆（user_id 隔离）
    · 会话密钥保存在 .secret_key（自动生成，勿泄露、勿提交 git）
"""
import re
import urllib.request
import urllib.error
import functools
import json
import os
import secrets
from pathlib import Path
from flask import Flask, g, jsonify, request, send_file, session, render_template


try:
    import pensieve            # 核心文件叫 pensieve.py 时
except ImportError:
    import test1 as pensieve   # 核心文件还叫 test1.py 时也能跑

app = Flask(__name__)


def _load_secret_key() -> str:
    """会话签名密钥：优先环境变量，否则从 .secret_key 文件读取/生成。"""
    key = os.environ.get('PENSIEVE_SECRET')
    if key:
        return key
    f = Path(__file__).with_name('.secret_key')
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding='utf-8')
    return f.read_text(encoding='utf-8').strip()


app.secret_key = _load_secret_key()

# 问句特征词（命中即视为查询）
QUESTION_MARKS = ('什么', '怎么', '怎样', '为什么', '为啥', '哪里', '哪个', '哪些',
                  '谁', '多少', '几点', '几时', '何时', '吗', '呢', '？', '?',
                  '是不是', '有没有')
# 显式存入指令（开头命中即强制存入，并去掉前缀）
ADD_PREFIXES = ('记住', '记一下', '记下', '存一下', '存：', '存:', '/add', '添加')


def get_db():
    """每个请求使用独立的 SQLite 连接（Flask 多线程环境下必须如此）。"""
    if 'db' not in g:
        g.db = pensieve.get_db()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def login_required(view):
    """API 登录门禁：未登录返回 401。"""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get('uid'):
            return jsonify({'error': '未登录', 'need_login': True}), 401
        return view(*args, **kwargs)
    return wrapper


def detect_intent(msg: str):
    """返回 (意图, 内容)：'add' 存入 / 'ask' 查询。"""
    for p in ADD_PREFIXES:
        if msg.startswith(p):
            return 'add', msg[len(p):].strip(' ：:')
    if any(m in msg for m in QUESTION_MARKS):
        return 'ask', msg
    return 'add', msg


# ============================================================
# 页面
# ============================================================
@app.route('/')
def index():
    if not session.get('uid'):
        return send_file('login.html')
    return send_file('index.html')


# ============================================================
# 认证 API
# ============================================================
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if len(username) < 2:
        return jsonify({'ok': False, 'error': '用户名至少 2 个字符'}), 400
    if len(password) < 4:
        return jsonify({'ok': False, 'error': '密码至少 4 位'}), 400
    try:
        uid = pensieve.create_user(get_db(), username, password)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    session['uid'] = uid
    session['username'] = username
    return jsonify({'ok': True, 'username': username})


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    uid = pensieve.verify_user(get_db(),
                               data.get('username') or '', data.get('password') or '')
    if uid is None:
        return jsonify({'ok': False, 'error': '用户名或密码错误'}), 401
    session['uid'] = uid
    session['username'] = data['username'].strip()
    return jsonify({'ok': True, 'username': session['username']})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/me')
def me():
    if not session.get('uid'):
        return jsonify({'login': False}), 401
    return jsonify({'login': True, 'username': session.get('username')})


# ============================================================
# 记忆 API（全部登录可用，且只限自己的数据）
# ============================================================
@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    msg = (request.json or {}).get('message', '').strip()
    if not msg:
        return jsonify({'error': '消息为空'}), 400
    uid = session['uid']
    db = get_db()
    intent, content = detect_intent(msg)

    # ---------- 存入记忆 ----------
    if intent == 'add':
        if not content:
            return jsonify({'type': 'added', 'ok': False,
                            'answer': '没有识别到要存的内容，请在指令后写上内容。'})
        rid = pensieve.add_record(db, content, user_id=uid, verbose=False)
        r = db.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
        return jsonify({
            'type': 'added', 'ok': True, 'id': rid,
            'summary': r['summary'],
            'tags': json.loads(r['tags']),
            'people': json.loads(r['people']),
            'event_date': r['event_date'],
        })

    # ---------- 查询记忆（只查自己的） ----------
    results, qdate, mode = pensieve.search(db, content, user_id=uid)
    if not results:
        return jsonify({
            'type': 'answer', 'found': False,
            'answer': '没有找到相关记录。（你的记忆库中不存在与该问题相关的内容）',
            'sources': [],
        })
    answer = pensieve.llm_answer(content, results) if pensieve.llm_available() else None
    sources = [{
        'id': r['id'], 'content': r['content'],
        'created_at': r['created_at'], 'event_date': r['event_date'],
        'tags': json.loads(r['tags']),
    } for r in results]
    return jsonify({'type': 'answer', 'found': True, 'answer': answer, 'sources': sources})


@app.route('/api/memories')
@login_required
def memories():
    """当前用户最近 50 条记忆。"""
    rows = get_db().execute(
        'SELECT * FROM records WHERE user_id = ? ORDER BY id DESC LIMIT 50',
        (session['uid'],)).fetchall()
    
    # 调试：打印出字段名，确认 category 是否存在
    if rows:
        print("DEBUG: 字段列表:", rows[0].keys())
    
    result = []
    for r in rows:
        # 确保 category 字段存在，如果不存在则设为 "未分类"
        cat = r['category'] if 'category' in r.keys() and r['category'] else '未分类'
        result.append({
            'id': r['id'],
            'content': r['content'],
            'created_at': r['created_at'],
            'event_date': r['event_date'],
            'tags': json.loads(r['tags']),
            'category': cat,   # 关键：加上这一行
        })
    return jsonify(result)


@app.route('/api/memories/<int:rid>', methods=['DELETE'])
@login_required
def delete_memory(rid):
    db = get_db()
    cur = db.execute('DELETE FROM records WHERE id = ? AND user_id = ?', (rid, session['uid']))
    db.commit()
    if cur.rowcount > 0:
        return jsonify({'success': True, 'message': '已删除'})
    else:
        return jsonify({'success': False, 'error': '记忆不存在或无权限'}), 404


# ============================================================
# 新增：日历导出接口
# ============================================================
@app.route('/api/export_calendar', methods=['GET'])
@login_required
def export_calendar():
    """导出当前用户所有带日期的记忆为 .ics 日历文件"""
    uid = session['uid']
    db = get_db()
    
    rows = db.execute(
        'SELECT id, content, event_date FROM records WHERE user_id = ? AND event_date IS NOT NULL ORDER BY event_date',
        (uid,)
    ).fetchall()
    
    if not rows:
        return jsonify({'error': '没有可导出的任务'}), 404
    
    ics_lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Pensieve//CN',
        'CALSCALE:GREGORIAN'
    ]
    for r in rows:
        date_str = r['event_date'].replace('-', '')
        ics_lines.append('BEGIN:VEVENT')
        ics_lines.append(f'SUMMARY:{r["content"][:50]}')
        ics_lines.append(f'DTSTART;VALUE=DATE:{date_str}')
        ics_lines.append(f'DTEND;VALUE=DATE:{date_str}')
        ics_lines.append('END:VEVENT')
    ics_lines.append('END:VCALENDAR')
    
    response = app.response_class(
        '\n'.join(ics_lines),
        mimetype='text/calendar',
        headers={'Content-Disposition': 'attachment; filename=pensieve_tasks.ics'}
    )
    return response

#管理页面路由（显示 HTML 页面）==============================
@app.route('/manage')
def manage_page():
    if 'uid' not in session:
        return redirect('/login')  # 跳转到你们现有的登录页
    return render_template('manage.html')
#获取记忆列表的 API
@app.route('/api/memories', methods=['GET'])
def api_get_memories():
    uid = session.get('uid')
    if not uid:
        return jsonify({'error': '未登录'}), 401
    
    db = get_db()
    memories = get_memories_by_user(db, uid)
    return jsonify(memories)
#删除记忆的 API
@app.route('/api/memories/<int:memory_id>', methods=['DELETE'])
def api_delete_memory(memory_id):
    uid = session.get('uid')
    if not uid:
        return jsonify({'error': '未登录'}), 401
    
    db = get_db()
    success = delete_memory_by_id(db, memory_id, uid)
    if success:
        return jsonify({'success': True, 'message': '已删除'})
    else:
        return jsonify({'error': '记忆不存在或无权限'}), 404

# ============================================================
# 新增：智谱 Web Search API（非 MCP）
# ============================================================
def search_zhipu_web(query: str, api_key: str) -> list:
    """
    通过智谱 Web Search API 进行联网搜索
    官方文档: https://open.bigmodel.cn/api/paas/v4/web_search
    """
    url = "https://open.bigmodel.cn/api/paas/v4/web_search"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = json.dumps({
        "search_query": query[:70],
        "search_engine": "search_std",
        "search_intent": False,
        "count": 5
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        
        search_results = result.get('search_result', [])
        return [{
            "title": item.get('title', ''),
            "snippet": item.get('content', ''),
            "url": item.get('link', ''),
            "media": item.get('media', ''),
            "publish_date": item.get('publish_date', '')
        } for item in search_results[:5]]
    
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"[搜索错误] HTTP {e.code}: {error_body}")
        return []
    except Exception as e:
        print(f"[搜索错误] {e}")
        return []


def generate_report_with_glm(idea: str, search_results: list, api_key: str) -> dict:
    if not search_results:
        return {
            'summary': '未找到相关公开信息，这可能是一个新颖的想法！',
            'competitors': '未发现直接竞品',
            'feasibility': '建议进一步调研市场需求',
            'risk': '暂无公开风险信息',
            'next_steps': '尝试用不同关键词搜索验证'
        }
    
    search_text = "\n".join([
        f"- {item.get('title', '')}: {item.get('snippet', '')[:100]}"
        for item in search_results[:5]
    ])
    
    prompt = f"""你是一个创意评估专家。用户有一个灵感，并搜索到了相关资料。请基于这些资料，生成一份简洁的评估报告。

用户灵感：{idea}

搜索到的相关资料：
{search_text}

请生成 JSON 格式的报告（只输出 JSON，不要其他内容）：
{{
    "summary": "一句话总结这个灵感的价值或现状（30字内）",
    "competitors": "列出现有的相关产品或竞品（50字内）",
    "feasibility": "实现可行性和建议（50字内）",
    "risk": "潜在风险或注意事项（50字内）",
    "next_steps": "给用户的下一步行动建议（30字内）"
}}
"""
    try:
        response = pensieve.llm_chat(prompt)
        print(f"[调试] GLM返回原始内容: {response[:200]}...")
        
        # 提取 Markdown 代码块内的 JSON
        code_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.S)
        if code_match:
            json_str = code_match.group(1)
        else:
            # 直接匹配第一个 JSON 对象
            json_match = re.search(r'\{.*\}', response, re.S)
            if json_match:
                json_str = json_match.group(0)
            else:
                print(f"[报告生成错误] 未找到JSON: {response[:200]}")
                raise ValueError("LLM返回未包含JSON")
        
        print(f"[调试] 提取的JSON字符串: {json_str[:200]}...")
        report = json.loads(json_str)
        
        # 确保必填字段存在
        required = ['summary', 'competitors', 'feasibility', 'risk', 'next_steps']
        for key in required:
            if key not in report:
                report[key] = '（未提供）'
        return report
        
    except Exception as e:
        print(f"[报告生成错误] {e}")
        import traceback
        traceback.print_exc()
        return {
            'summary': f'发现 {len(search_results)} 条相关信息，建议进一步分析。',
            'competitors': '、'.join([r.get('title', '')[:20] for r in search_results[:3]]) or '未发现直接竞品',
            'feasibility': '建议参考以上信息评估技术可行性',
            'risk': '需关注已有产品的市场占有情况',
            'next_steps': '深入调研竞品用户评价'
        }
@app.route('/api/idea/evaluate', methods=['POST'])
@login_required
def evaluate_idea():
    """灵感联网评估"""
    data = request.json or {}
    idea = data.get('idea', '').strip()
    if not idea:
        return jsonify({'error': '灵感内容不能为空'}), 400
    
    keywords = idea[:30]
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({
            'idea': idea,
            'error': '未配置 API Key，请设置 OPENAI_API_KEY 环境变量'
        }), 503
    
    search_results = search_zhipu_web(keywords, api_key)
    report = generate_report_with_glm(idea, search_results, api_key)
    
    return jsonify({
        'idea': idea,
        'keywords': keywords,
        'search_results': search_results,
        'report': report,
        'status': 'success'
    })


if __name__ == '__main__':
    print('=' * 48)
    print('  冥想盆网页版（多用户）已启动')
    print('  浏览器打开： http://127.0.0.1:5000')
    print('  按 Ctrl+C 停止')
    print('=' * 48)
    app.run(host='127.0.0.1', port=5000, debug=False)
00, debug=False)
