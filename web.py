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

import functools
import json
import os
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, request, send_file, session

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
    return jsonify([{
        'id': r['id'], 'content': r['content'],
        'created_at': r['created_at'], 'event_date': r['event_date'],
        'tags': json.loads(r['tags']),
    } for r in rows])


@app.route('/api/memories/<int:rid>', methods=['DELETE'])
@login_required
def delete_memory(rid):
    """删除自己的一条记忆（删别人���会返回 ok=false）。"""
    db = get_db()
    cur = db.execute('DELETE FROM records WHERE id = ? AND user_id = ?',
                     (rid, session['uid']))
    if cur.rowcount:
        db.execute('DELETE FROM records_fts WHERE rowid = ?', (rid,))
        db.commit()
    return jsonify({'ok': bool(cur.rowcount)})


if __name__ == '__main__':
    print('=' * 48)
    print('  冥想盆网页版（多用户）已启动')
    print('  浏览器打开： http://127.0.0.1:5000')
    print('  按 Ctrl+C 停止')
    print('=' * 48)
    app.run(host='127.0.0.1', port=5000, debug=False)
