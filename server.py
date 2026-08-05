"""
KGDS API Server v3 — 管理后台版
端点:
  【用户端】
  GET  /api/nodes, /api/edges      知识图谱
  POST /api/register               注册（姓名+邮箱）
  POST /api/login                  登录（邮箱）
  POST /api/generate-test          生成试题
  POST /api/submit                 提交答案
  GET  /api/sessions               我的历史
  【管理端】
  POST /admin/login                管理员登录（邮箱+密码）
  GET  /admin/roles                岗位列表
  POST /admin/roles                新建岗位
  GET  /admin/roles/<id>/data      岗位数据（nodes+edges+tests）
  PUT  /admin/roles/<id>/meta      更新岗位元信息
  PUT  /admin/roles/<id>/nodes     更新节点
  PUT  /admin/roles/<id>/edges     更新连线
  PUT  /admin/roles/<id>/tests     更新题库
  DELETE /admin/roles/<id>         删除岗位
  GET  /admin/users                用户列表
  DELETE /admin/users/<id>         删除用户
  GET  /admin/settings             系统设置
  PUT  /admin/settings             更新设置
  GET  /admin/export               导出全部数据
  GET  /                             静态文件服务
启动: python server.py [--port 8081]
"""

import json, os, sys, sqlite3, hashlib, secrets, re, shutil, random
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "src"
WEB_DIR = ROOT / "web"

# 云端持久化：KGDS_DATA_DIR 指向 Zeabur 挂载的持久卷（如 /data）
# 本地开发时回退到项目根目录
_KGDS_DATA_DIR = os.environ.get("KGDS_DATA_DIR", str(ROOT))
DATA_DIR = Path(_KGDS_DATA_DIR) / "data"
# roles 数据随代码部署（知识图谱），不在持久卷
ROLE_DIR = ROOT / "data" / "roles" if _KGDS_DATA_DIR != str(ROOT) else DATA_DIR / "roles"
DB_PATH = Path(_KGDS_DATA_DIR) / "kgds.db"
# 设置环境变量让子模块（llm_variant 等）也能用到持久卷
os.environ.setdefault("KGDS_DATA_DIR", str(_KGDS_DATA_DIR))

sys.path.insert(0, str(SRC_DIR))
from variant_generator import generate_test_with_variants, generate_variants_for
from test_engine import TestEngine, TestSession, Question
from flywheel import FlywheelEngine, FlywheelScheduler
from selector import select_questions, extract_tested_qids, extract_node_confidence, QUOTAS

_flywheel = FlywheelEngine()  # 三飞轮引擎实例
_scheduler = FlywheelScheduler(_flywheel, min_new=10)  # 自动触发调度器

# ── Database ──
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(password):
    return hashlib.sha256(("kgds_salt_" + password).encode()).hexdigest()

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # 确保持久卷目录存在
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            token TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS test_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            profile TEXT,
            answers TEXT,
            node_status TEXT,
            questions_summary TEXT,
            overall_score REAL,
            total_correct INTEGER,
            total_questions INTEGER,
            selected_levels TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON test_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON test_sessions(created_at);
          CREATE TABLE IF NOT EXISTS internal_questions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT NOT NULL DEFAULT '基础知识',
              question TEXT NOT NULL,
              options TEXT NOT NULL,
              correct_index INTEGER NOT NULL,
              difficulty INTEGER DEFAULT 2,
              source_doc_id INTEGER,
              source_paragraph TEXT,
              created_at TEXT DEFAULT (datetime('now','localtime'))
          );
          CREATE TABLE IF NOT EXISTS internal_documents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT NOT NULL,
              filename TEXT NOT NULL,
              content TEXT,
              version INTEGER DEFAULT 1,
              created_at TEXT DEFAULT (datetime('now','localtime'))
          );
        CREATE TABLE IF NOT EXISTS arena_episode_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id TEXT NOT NULL,
            episode INTEGER NOT NULL,
            answers TEXT,
            score REAL,
            total_correct INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, book_id, episode),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_arena_episode ON arena_episode_sessions(book_id, episode);
        CREATE TABLE IF NOT EXISTS arena_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id TEXT NOT NULL,
            episode INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_arena_comments ON arena_comments(book_id, episode);
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()

    # Migrate: add columns if missing (for DBs created before v3)
    try:
        conn.execute("SELECT is_admin FROM users LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    try:
        conn.execute("SELECT password_hash FROM users LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    conn.commit()

    # Seed default admin — 密码从环境变量读取，绝不硬编码
    admin = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
    if not admin:
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@kgds.local")
        admin_pw = os.environ.get("ADMIN_PASSWORD", secrets.token_hex(8))
        conn.execute("INSERT INTO users (name, email, token, password_hash, is_admin) VALUES (?,?,?,?,?)",
                     ("管理员", admin_email, new_token(), hash_pw(admin_pw), 1))
        conn.commit()
        if not os.environ.get("ADMIN_PASSWORD"):
            print(f"[KGDS] ⚠️  未设置 ADMIN_PASSWORD 环境变量，已生成随机管理员密码（仅显示一次）:")
            print(f"[KGDS]    邮箱: {admin_email}")
            print(f"[KGDS]    密码: {admin_pw}")
            print(f"[KGDS]    ⚠️  请立即在 Zeabur 环境变量中设置 ADMIN_PASSWORD，否则重新部署后密码会变！")

    conn.close()

# ── 内部知识题库（产品知识）同步与抽取 ──
PRODUCT_TESTS_PATH = ROLE_DIR / "insurance-agent" / "product_tests.json"

def sync_internal_questions():
    """把 product_tests.json（唯一真相源）同步到 internal_questions 表（DB 镜像）。
    幂等：先清空「产品知识」类别旧题再整批插入。启动时调用，云端 Redeploy 后自动恢复。"""
    if not PRODUCT_TESTS_PATH.exists():
        return 0
    try:
        items = json.loads(PRODUCT_TESTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(f"[KGDS] product_tests.json 加载失败: {e}\n")
        return 0
    conn = get_db()
    conn.execute("DELETE FROM internal_questions WHERE category = ?", ("产品知识",))
    n = 0
    for it in items:
        if it.get("category") != "产品知识":
            continue
        meta = json.dumps({
            "product_id": it.get("product_id", ""),
            "product": it.get("product", ""),
            "product_category": it.get("product_category", ""),
            "source": it.get("source", ""),
        }, ensure_ascii=False)
        conn.execute(
            "INSERT INTO internal_questions (category, question, options, correct_index, difficulty, source_paragraph) VALUES (?,?,?,?,?,?)",
            ("产品知识", it["question"],
             json.dumps(it.get("options", []), ensure_ascii=False),
             it.get("correct_index", 0),
             it.get("difficulty", 2),
             meta))
        n += 1
    conn.commit()
    conn.close()
    sys.stderr.write(f"[KGDS] internal_questions 同步：产品知识 {n} 题\n")
    return n

def load_internal_questions(category):
    """从 internal_questions 表读取某内部知识类别全部题目，转成前端题结构。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, category, question, options, correct_index, difficulty, source_paragraph FROM internal_questions WHERE category = ?",
        (category,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        opts = []
        try:
            opts = json.loads(r["options"])
        except Exception:
            pass
        meta = {}
        if r["source_paragraph"]:
            try:
                meta = json.loads(r["source_paragraph"])
            except Exception:
                meta = {}
        out.append({
            "id": f"int_{r['id']}",
            "qid": f"INT-{r['id']}",
            "node_id": meta.get("product_id") or f"INT-{r['id']}",
            "category": r["category"],
            "question": r["question"],
            "options": opts,
            "correct_index": r["correct_index"],
            "correct": r["correct_index"],
            "difficulty": r["difficulty"],
            "type": "internal",
            "is_variant": False,
            "layer": "internal",
            "product": meta.get("product", ""),
            "product_category": meta.get("product_category", ""),
            "source": meta.get("source", ""),
        })
    return out

def pick_internal_questions(items, per_product=2, seed=None):
    """产品知识抽题：按产品均衡，每产品抽 per_product 题（默认 2 → 14 产品 = 28 题）。"""
    rng = random.Random(seed) if seed is not None else random.Random()
    by_product = {}
    for q in items:
        by_product.setdefault(q.get("product", "?"), []).append(q)
    picked = []
    for p, qs in by_product.items():
        picked.extend(rng.sample(qs, min(per_product, len(qs))))
    return picked

def new_token():
    return secrets.token_hex(16)

def register_user(name, email):
    conn = get_db()
    token = new_token()
    try:
        conn.execute("INSERT INTO users (name, email, token) VALUES (?, ?, ?)", (name, email, token))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        return {"id": uid, "name": name, "email": email, "token": token, "is_admin": False}
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id, name, token, is_admin FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return {"id": row["id"], "name": row["name"], "email": email, "token": row["token"], "is_admin": bool(row["is_admin"])}
        raise
    finally:
        conn.close()

def login_user(email):
    conn = get_db()
    row = conn.execute("SELECT id, name, token, is_admin FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if row:
        return {"id": row["id"], "name": row["name"], "email": email, "token": row["token"], "is_admin": bool(row["is_admin"])}
    return None

def admin_login(email, password):
    conn = get_db()
    row = conn.execute("SELECT id, name, password_hash, is_admin FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row or not row["is_admin"]:
        return None
    if not row["password_hash"]:
        # First login — set password
        conn = get_db()
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_pw(password), row["id"]))
        conn.commit()
        conn.close()
        return {"id": row["id"], "name": row["name"], "email": email, "is_admin": True}
    if row["password_hash"] == hash_pw(password):
        # Regenerate token
        token = new_token()
        conn = get_db()
        conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
        conn.commit()
        conn.close()
        return {"id": row["id"], "name": row["name"], "email": email, "token": token, "is_admin": True}
    return None

def check_admin(token):
    if not token:
        return None
    conn = get_db()
    row = conn.execute("SELECT id, name, email, is_admin FROM users WHERE token = ? AND is_admin = 1", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_token(token):
    conn = get_db()
    row = conn.execute("SELECT id, name, email, is_admin FROM users WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None

def save_session(user_id, profile, answers, node_status, questions, overall_score, total_correct, total_questions, levels):
    conn = get_db()
    conn.execute("""
        INSERT INTO test_sessions (user_id, profile, answers, node_status, questions_summary, overall_score, total_correct, total_questions, selected_levels)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, json.dumps(profile, ensure_ascii=False), json.dumps(answers), json.dumps(node_status),
          json.dumps(questions, ensure_ascii=False), overall_score, total_correct, total_questions, json.dumps(levels)))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return sid

def get_user_sessions(user_id, limit=20):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, profile, answers, node_status, questions_summary, overall_score, total_correct, total_questions, selected_levels, created_at
        FROM test_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [_session_row(r) for r in rows]

def _session_row(r):
    return {
        "session_id": r["id"],
        "profile": json.loads(r["profile"]) if r["profile"] else {},
        "overall_score": r["overall_score"],
        "total_correct": r["total_correct"],
        "total_questions": r["total_questions"],
        "layer_stats": {},
        "node_status": json.loads(r["node_status"]) if r["node_status"] else {},
        "answers": json.loads(r["answers"]) if r["answers"] else {},
        "questions": json.loads(r["questions_summary"]) if r["questions_summary"] else [],
        "selected_levels": json.loads(r["selected_levels"]) if r["selected_levels"] else [],
        "timestamp": r["created_at"]
    }

# ── Admin: Role Management ──
def list_roles():
    if not ROLE_DIR.exists():
        return []
    roles = []
    for d in sorted(ROLE_DIR.iterdir()):
        if d.is_dir():
            meta = {}
            mp = d / "meta.json"
            if mp.exists():
                try: meta = json.loads(mp.read_text(encoding="utf-8"))
                except: pass
            nodes = []
            np = d / "nodes.json"
            if np.exists():
                try: nodes = json.loads(np.read_text(encoding="utf-8"))
                except: pass
            tests = []
            tp = d / "tests.json"
            if tp.exists():
                try: tests = json.loads(tp.read_text(encoding="utf-8"))
                except: pass
            roles.append({
                "id": d.name,
                "title": meta.get("title", d.name),
                "description": meta.get("description", ""),
                "industry": meta.get("industry", ""),
                "levels": meta.get("levels", ["foundation", "advanced", "transcendent"]),
                "node_count": len(nodes),
                "question_count": len(tests),
                "created_at": meta.get("created_at", "")
            })
    return roles

def create_role(role_id, meta):
    d = ROLE_DIR / role_id
    if d.exists():
        return None
    d.mkdir(parents=True)
    meta["created_at"] = datetime.now().isoformat()
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # Create template nodes
    template_nodes = [
        {"id": f"{role_id}_node_01", "label": "基础知识", "layer": "foundation", "color": "#5B9BD5", "weight": 3},
        {"id": f"{role_id}_node_02", "label": "核心技能", "layer": "advanced", "color": "#9B59B6", "weight": 3},
        {"id": f"{role_id}_node_03", "label": "进阶能力", "layer": "transcendent", "color": "#F0A500", "weight": 3},
    ]
    (d / "nodes.json").write_text(json.dumps(template_nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "edges.json").write_text("[]", encoding="utf-8")
    (d / "tests.json").write_text("[]", encoding="utf-8")
    return {"id": role_id, "title": meta.get("title", role_id), "node_count": 3, "question_count": 0}

def get_role_data(role_id):
    d = ROLE_DIR / role_id
    if not d.exists():
        return None
    result = {"id": role_id}
    for fname in ["meta.json", "nodes.json", "edges.json", "tests.json"]:
        fp = d / fname
        if fp.exists():
            try:
                result[fname.replace(".json", "")] = json.loads(fp.read_text(encoding="utf-8"))
            except:
                result[fname.replace(".json", "")] = None
        else:
            result[fname.replace(".json", "")] = None
    return result

def update_role_meta(role_id, meta):
    d = ROLE_DIR / role_id
    if not d.exists():
        return False
    mp = d / "meta.json"
    existing = {}
    if mp.exists():
        try: existing = json.loads(mp.read_text(encoding="utf-8"))
        except: pass
    existing.update(meta)
    mp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return True

def update_role_file(role_id, fname, data):
    d = ROLE_DIR / role_id
    if not d.exists():
        return False
    (d / f"{fname}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True

def delete_role(role_id):
    d = ROLE_DIR / role_id
    if not d.exists():
        return False
    backup = ROOT / f"backup_roles_{role_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.move(str(d), str(backup))
    return True

# ── Admin: Users ──
def list_users(search=""):
    conn = get_db()
    if search:
        rows = conn.execute("SELECT id, name, email, is_admin, created_at FROM users WHERE name LIKE ? OR email LIKE ? ORDER BY created_at DESC",
                           (f"%{search}%", f"%{search}%")).fetchall()
    else:
        rows = conn.execute("SELECT id, name, email, is_admin, created_at FROM users ORDER BY created_at DESC LIMIT 100").fetchall()
    conn.close()
    users = []
    for r in rows:
        u = dict(r)
        u["is_admin"] = bool(u["is_admin"])
        nc = get_db().execute("SELECT COUNT(*) FROM test_sessions WHERE user_id = ?", (r["id"],)).fetchone()[0]
        u["session_count"] = nc
        users.append(u)
    return users

def delete_user(uid):
    conn = get_db()
    conn.execute("DELETE FROM test_sessions WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (uid,))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0

# ── Admin: Settings ──
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: json.loads(r["value"]) if r["value"] else None for r in rows}

def save_settings(settings_dict):
    conn = get_db()
    for k, v in settings_dict.items():
        val = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, val))
    conn.commit()
    conn.close()

# ── Arena: Episode Tests ──
def get_episode_tests(episode):
    arena_path = ROOT / "data" / "reading" / "thinking-fast-slow" / "arena_episode_tests.json"
    if not arena_path.exists():
        return None
    data = json.loads(arena_path.read_text(encoding="utf-8"))
    ep_key = str(episode)
    return data.get("episodes", {}).get(ep_key)

# ── Arena: Episode Ranking ──
def get_episode_ranking(book_id, episode):
    conn = get_db()
    rows = conn.execute("""
        SELECT u.name, u.email, s.score, s.total_correct, s.total_questions, s.created_at
        FROM arena_episode_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.book_id = ? AND s.episode = ?
        ORDER BY s.score DESC, s.created_at ASC
        LIMIT 50
    """, (book_id, episode)).fetchall()
    conn.close()
    return [{"name": r["name"], "score": r["score"], "correct": r["total_correct"],
             "total": r["total_questions"], "time": r["created_at"]} for r in rows]

def get_book_ranking(book_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT u.name, u.email,
               AVG(s.score) as avg_score,
               SUM(s.total_correct) as total_correct,
               SUM(s.total_questions) as total_questions,
               COUNT(DISTINCT s.episode) as episodes_done,
               MAX(s.created_at) as last_active
        FROM arena_episode_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.book_id = ?
        GROUP BY u.id
        ORDER BY avg_score DESC, episodes_done DESC
        LIMIT 50
    """, (book_id,)).fetchall()
    conn.close()
    return [{"name": r["name"], "avg_score": round(r["avg_score"], 1),
             "correct": r["total_correct"], "total": r["total_questions"],
             "episodes_done": r["episodes_done"], "last_active": r["last_active"]} for r in rows]

def get_user_episode_status(book_id, user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT episode, score, total_correct, total_questions, created_at
        FROM arena_episode_sessions
        WHERE book_id = ? AND user_id = ?
    """, (book_id, user_id)).fetchall()
    conn.close()
    return {str(r["episode"]): {"score": r["score"], "correct": r["total_correct"],
                                 "total": r["total_questions"], "time": r["created_at"]} for r in rows}

# ── Arena: Comments ──
def add_comment(book_id, episode, user_id, text):
    conn = get_db()
    conn.execute("INSERT INTO arena_comments (user_id, book_id, episode, text) VALUES (?, ?, ?, ?)",
                 (user_id, book_id, episode, text))
    conn.commit()
    conn.close()

def get_comments(book_id, episode, limit=50):
    conn = get_db()
    rows = conn.execute("""
        SELECT u.name, c.text, c.created_at
        FROM arena_comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.book_id = ? AND c.episode = ?
        ORDER BY c.created_at DESC
        LIMIT ?
    """, (book_id, episode, limit)).fetchall()
    conn.close()
    return [{"name": r["name"], "text": r["text"], "time": r["created_at"]} for r in rows]

# ── HTTP Handler ──
class KGDSHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), args[0]))

    def _read_body(self):
        cl = int(self.headers.get("Content-Length", 0))
        if not cl: return {}
        try: return json.loads(self.rfile.read(cl))
        except: return {}

    def _auth_header(self):
        auth = self.headers.get("Authorization", "")
        return auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path):
        if not path.exists():
            self._send_json({"error": "File not found"}, 404)
            return
        self.send_response(200)
        ct = {".json":"application/json",".html":"text/html",".js":"application/javascript",".css":"text/css",
              ".png":"image/png",".jpg":"image/jpeg",".svg":"image/svg+xml",".ico":"image/x-icon",
              ".mp3":"audio/mpeg",".wav":"audio/wav",".webmanifest":"application/manifest+json"}
        ctype = ct.get(path.suffix, "application/octet-stream")
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        # 内测期禁用静态缓存：浏览器缓存旧版 app.html/app.js 曾导致「产品选择项不可见」
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        body = path.read_bytes()
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ── Routing helpers ──
    def _match(self, pattern, method=None):
        """Check if path matches pattern like '/admin/roles/<id>/nodes'"""
        path = urlparse(self.path).path
        if method and self.command != method:
            return None
        pattern_parts = pattern.strip("/").split("/")
        path_parts = path.strip("/").split("/")
        if len(pattern_parts) != len(path_parts):
            return None
        params = {}
        for pp, rp in zip(pattern_parts, path_parts):
            if pp.startswith("<") and pp.endswith(">"):
                params[pp[1:-1]] = rp
            elif pp != rp:
                return None
        return params

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        # ── User API ──
        if path == "/api/sessions":
            user = get_user_by_token(self._auth_header())
            if not user: return self._send_json({"error": "请先登录"}, 401)
            return self._send_json(get_user_sessions(user["id"]))

        # ── 飞轮公开统计（登录用户可见，不含敏感数据）──
        if path == "/api/flywheel/public-stats":
            user = get_user_by_token(self._auth_header())
            if not user: return self._send_json({"error": "请先登录"}, 401)
            stats = _flywheel.get_stats()
            return self._send_json({
                "total_sessions": stats.total_sessions,
                "total_users": stats.total_users,
                "data_sufficiency": stats.data_sufficiency,
                "recent_trend": stats.recent_trend,
                "auto_actions_applied": stats.auto_actions_applied
            })

        if path == "/api/flywheel/stats":
            user = get_user_by_token(self._auth_header())
            if not user: return self._send_json({"error": "请先登录"}, 401)
            if not user.get("is_admin"):
                return self._send_json({"error": "权限不足"}, 403)
            stats = _flywheel.get_stats()
            return self._send_json({
                "total_sessions": stats.total_sessions,
                "total_users": stats.total_users,
                "total_nodes": stats.total_nodes,
                "total_questions": stats.total_questions,
                "last_analysis": stats.last_analysis,
                "auto_actions_applied": stats.auto_actions_applied,
                "nodes_adjusted": stats.nodes_adjusted,
                "questions_flagged": stats.questions_flagged,
                "avg_node_confidence": stats.avg_node_confidence,
                "avg_question_confidence": stats.avg_question_confidence,
                "data_sufficiency": stats.data_sufficiency,
                "recent_trend": stats.recent_trend
            })

        if path in ("/api/nodes", "/api/edges", "/api/tests"):
            return self._serve_file(ROLE_DIR / "insurance-agent" / f"{path.split('/')[-1]}.json")

        # ── Reading Arena (伴读擂台) ──
        if path == "/api/arena/tests":
            arena_path = Path(__file__).parent / "data" / "reading" / "thinking-fast-slow" / "arena_tests.json"
            if arena_path.exists():
                data = json.loads(arena_path.read_text(encoding="utf-8"))
                return self._send_json(data)
            return self._send_json({"error": "擂台题库不存在"}, 404)

        # ── Episode Tests (每期独立题) ──
        if path == "/api/arena/episode-tests":
            episode = int(qs.get("episode", [0])[0])
            if not episode:
                return self._send_json({"error": "请指定 episode 参数"}, 400)
            ep_tests = get_episode_tests(episode)
            if ep_tests:
                # 下发前剥离正确答案（防 F12 查看源码作弊），判分由 episode-submit 在服务端完成
                safe = {k: v for k, v in ep_tests.items() if k != "tests"}
                safe["tests"] = [{k: v for k, v in t.items() if k != "correct"} for t in ep_tests["tests"]]
                return self._send_json(safe)
            return self._send_json({"error": f"第{episode}期题库不存在"}, 404)

        # ── Episode Ranking ──
        if path == "/api/arena/episode-ranking":
            book = qs.get("book", ["thinking-fast-slow"])[0]
            episode = int(qs.get("episode", [0])[0])
            if not episode:
                return self._send_json({"error": "请指定 episode"}, 400)
            ranking = get_episode_ranking(book, episode)
            return self._send_json({"episode": episode, "ranking": ranking})

        # ── Book Ranking ──
        if path == "/api/arena/book-ranking":
            book = qs.get("book", ["thinking-fast-slow"])[0]
            ranking = get_book_ranking(book)
            return self._send_json({"book": book, "title": "思考，快与慢", "ranking": ranking})

        # ── Episode Comments ──
        if path == "/api/arena/comments":
            book = qs.get("book", ["thinking-fast-slow"])[0]
            episode = int(qs.get("episode", [0])[0])
            if not episode:
                return self._send_json({"error": "请指定 episode"}, 400)
            comments = get_comments(book, episode)
            return self._send_json({"episode": episode, "comments": comments})

        # ── User Episode Status ──
        if path == "/api/arena/episode-status":
            user = get_user_by_token(self._auth_header())
            if not user:
                return self._send_json({"error": "请先登录"}, 401)
            book = qs.get("book", ["thinking-fast-slow"])[0]
            status = get_user_episode_status(book, user["id"])
            return self._send_json({"book": book, "episodes_done": status})

        # ── Reading Episodes (兔扑伴读) ──
        if path == "/api/reading/episodes":
            ep_path = Path(__file__).parent / "data" / "reading" / "thinking-fast-slow" / "episodes.json"
            if ep_path.exists():
                data = json.loads(ep_path.read_text(encoding="utf-8"))
                return self._send_json(data)
            return self._send_json({"error": "伴读内容不存在"}, 404)

        # ── Admin API ──
        # Check admin auth for admin routes
        is_admin = check_admin(self._auth_header())

        if path == "/admin/roles":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            return self._send_json(list_roles())

        m = self._match("admin/roles/<id>/data")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            d = get_role_data(m["id"])
            if d is None: return self._send_json({"error": "岗位不存在"}, 404)
            return self._send_json(d)

        if path == "/admin/users":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            search = qs.get("search", [""])[0]
            return self._send_json(list_users(search))

        if path == "/admin/settings":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            return self._send_json(get_settings())

        if path == "/admin/export":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            return self._send_json({
                "users": [dict(r) for r in get_db().execute("SELECT id, name, email, is_admin, created_at FROM users").fetchall()],
                "sessions": [dict(r) for r in get_db().execute("SELECT * FROM test_sessions").fetchall()],
                "roles": list_roles(),
                "settings": get_settings(),
                "exported_at": datetime.now().isoformat()
            })

        # Static files
        if path == "/" or path == "":
            return self._serve_file(WEB_DIR / "app.html")
        if path == "/admin" or path == "/admin/":
            return self._serve_file(WEB_DIR / "admin.html")
        if path == "/arena" or path == "/arena/":
            return self._serve_file(WEB_DIR / "topoo.html")
        if path == "/topoo" or path == "/topoo/":
            return self._serve_file(WEB_DIR / "topoo.html")
        file_path = WEB_DIR / path.lstrip("/")
        if file_path.exists():
            return self._serve_file(file_path)
        # SPA fallback
        return self._serve_file(WEB_DIR / "app.html")

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_body()

        # ── User API ──
        if path == "/api/register":
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip().lower()
            if not name or not email: return self._send_json({"error": "姓名和邮箱不能为空"}, 400)
            if "@" not in email or "." not in email: return self._send_json({"error": "请输入有效的邮箱地址"}, 400)
            user = register_user(name, email)
            return self._send_json({"user": user, "message": "登录成功"})

        if path == "/api/login":
            email = (data.get("email") or "").strip().lower()
            if not email: return self._send_json({"error": "请输入邮箱"}, 400)
            user = login_user(email)
            if user: return self._send_json({"user": user, "message": "欢迎回来"})
            return self._send_json({"error": "该邮箱未注册，请先注册"}, 404)

        if path == "/api/generate-test":
            role = data.get("role", "insurance-agent")
            levels = data.get("levels")
            internal_category = data.get("internal_category")  # 内部知识类别

            # 历史感知：读取该用户的已测 qid + 节点掌握度（四重调度器输入）
            token = data.get("token") or self._auth_header()
            user = get_user_by_token(token) if token else None
            tested_qids, node_conf = None, None
            if user:
                try:
                    sessions = get_user_sessions(user["id"], limit=50)
                    tested_qids = extract_tested_qids(sessions)
                    node_conf = extract_node_confidence(sessions)
                except Exception as e:
                    sys.stderr.write(f"History read error: {e}\n")

            questions = []
            quota = {}

            # ── 题库隔离：内部知识 与 市场竞争 二选一（各自内部选题逻辑独立，互不干扰）──
            if internal_category:
                # ── 内部知识路径：internal_questions 表（完整题集，不经过市场调度器）──
                internal_qs = load_internal_questions(internal_category)
                if not internal_qs:
                    return self._send_json({"error": f"内部知识题库「{internal_category}」暂无题目，请管理员上传文档后生成"}, 400)
                if internal_category == "产品知识":
                    # 按所选产品过滤；每个产品返回完整 12 题（复选 N 个产品 = N×12 题）
                    products = data.get("products") or []
                    if products:
                        internal_qs = [q for q in internal_qs if q.get("product") in products]
                    if not internal_qs:
                        return self._send_json({"error": "请至少选择 1 个产品"}, 400)
                questions.extend(internal_qs)

            elif levels:
                # ── 市场竞争路径：四重调度器（仅在选择了层级时执行）──
                try:
                    # 四重调度器：配额 51/38/30 + 未测70/已测30 + 薄弱优先 + 节点均衡
                    selected = select_questions(role=role, levels=levels,
                                                tested_qids=tested_qids,
                                                node_confidence=node_conf,
                                                seed=data.get("seed"))
                    if not selected:
                        # 降级：旧路径全量
                        selected = generate_test_with_variants(role=role, variant_ratio=data.get("variant_ratio", 1/3),
                                                               seed=data.get("seed"), node_filter=None, use_llm=True)
                    else:
                        # 变体生成（1/3 变体，保留 qid）
                        selected = generate_variants_for(selected, variant_ratio=data.get("variant_ratio", 1/3),
                                                         seed=data.get("seed"), use_llm=True)
                    questions.extend(selected)
                    quota = {k: QUOTAS[k] for k in (levels or list(QUOTAS.keys()))}
                except Exception as e:
                    tests_path = ROLE_DIR / role / "tests.json"
                    if tests_path.exists():
                        raw = json.loads(tests_path.read_text(encoding="utf-8"))
                        for i, q in enumerate(raw):
                            q["id"] = f"raw_{i}"
                            q.setdefault("qid", f"{q.get('node_id','')}#{i}")
                        questions.extend(raw)
                    else:
                        return self._send_json({"error": str(e)}, 500)

            if not questions:
                return self._send_json({"error": "未选择任何诊断范围（请选择市场竞争层级或内部知识方向）"}, 400)

            return self._send_json({"questions": questions, "total": len(questions), "quota": quota})

        # ── 学习推荐 API (POST) ──
        if path == "/api/learning-tips":
            user = get_user_by_token(self._auth_header())
            if not user: return self._send_json({"error": "请先登录"}, 401)
            node_ids = data.get("node_ids", [])
            shown = data.get("shown", {})
            tips_path = BASE_DIR / "data" / "roles" / "insurance-agent" / "learning_tips.json"
            try:
                with open(tips_path, 'r', encoding='utf-8') as f:
                    all_tips = json.load(f)
            except:
                all_tips = {}
            result = {}
            for nid in node_ids:
                pool = all_tips.get(nid, [])
                if not pool:
                    continue
                shown_indices = shown.get(nid, [])
                chosen = None
                for idx, tip in enumerate(pool):
                    if idx not in shown_indices:
                        chosen = tip
                        shown_indices.append(idx)
                        break
                if chosen is None:
                    shown_indices = [0]
                    chosen = pool[0]
                result[nid] = {"tip": chosen, "shown_indices": shown_indices}
            return self._send_json({"tips": result})

        # ── 内部知识类别查询 (POST) ──
        if path == "/api/internal/categories":
            user = get_user_by_token(self._auth_header())
            if not user: return self._send_json({"error": "请先登录"}, 401)
            cats = []
            for cat in ["基础知识", "产品知识", "合规知识"]:
                count = get_db().execute(
                    "SELECT COUNT(*) FROM internal_questions WHERE category = ?", (cat,)
                ).fetchone()[0]
                cats.append({"name": cat, "question_count": count})
            return self._send_json({"categories": cats})

        if path == "/api/submit":
            answers = data.get("answers", {})
            questions = data.get("questions", [])
            profile = data.get("profile", {})
            token = data.get("token") or self._auth_header()
            role = data.get("role", profile.get("role", "insurance-agent"))

            # 加载节点标签映射（node_id → {label, content, layer}）
            node_meta = {}
            nodes_path = ROLE_DIR / role / "nodes.json"
            if nodes_path.exists():
                for n in json.loads(nodes_path.read_text(encoding="utf-8")):
                    node_meta[n["id"]] = {"label": n.get("label", n["id"]), "content": n.get("content", ""), "layer": n.get("layer", "unknown")}

            node_st, tc, ta = {}, 0, 0
            for q in questions:
                if not isinstance(q, dict): continue
                qid = str(q["id"])
                chosen = answers.get(qid)
                correct = (chosen is not None and chosen == q.get("correct_index", q.get("correct")))
                if correct: tc += 1
                ta += 1
                nid = q.get("node_id", "unknown")
                nm = node_meta.get(nid, {"label": nid, "content": "", "layer": q.get("layer", "unknown")})
                s = node_st.get(nid, {"correct":0,"total":0,"label":nm["label"],"content":nm["content"],"layer":nm["layer"]})
                s["total"] += 1
                if correct: s["correct"] += 1
                node_st[nid] = s

            for s in node_st.values():
                s["confidence"] = s["total"] > 0 and s["correct"] / s["total"] or 0
                s["mastered"] = s["confidence"] >= 0.67

            layers = {"foundation":{"correct":0,"total":0},"advanced":{"correct":0,"total":0},"transcendent":{"correct":0,"total":0}}
            for s in node_st.values():
                ly = s.get("layer")
                if ly in layers:
                    layers[ly]["correct"] += s["correct"]
                    layers[ly]["total"] += s["total"]

            report = {"total_score": ta>0 and round(tc/ta*100) or 0, "total_correct": tc, "total_questions": ta,
                      "node_status": node_st, "layer_stats": layers, "profile": profile}

            user = get_user_by_token(token) if token else None
            if user:
                qsummary = [{"id":q.get("id"),"qid":q.get("qid"),"node_id":q.get("node_id"),"type":q.get("type"),"is_variant":q.get("is_variant")} for q in questions if isinstance(q,dict)]
                try:
                    sid = save_session(user["id"], profile, answers, node_st, qsummary, report["total_score"], tc, ta,
                                      profile.get("levels",[]) if isinstance(profile,dict) else [])
                    report["session_id"] = sid
                    # 飞轮：写入用户答题数据（按账号隔离）
                    try:
                        report["session_id"] = sid
                        _flywheel.save_session({
                            "session_id": str(sid),
                            "profile": profile,
                            "answers": answers,
                            "node_status": node_st,
                            "overall_score": report["total_score"]
                        }, user_id=str(user["id"]))
                    except Exception as e:
                        sys.stderr.write(f"Flywheel save error: {e}\n")
                    # 自动触发飞轮分析（有足够新数据时）
                    try:
                        result = _scheduler.try_run()
                        if result:
                            sys.stderr.write(f"Flywheel auto-run: {result.get('applied_result',{}).get('applied',0)} actions applied\n")
                    except Exception as e:
                        sys.stderr.write(f"Flywheel auto-run error: {e}\n")
                except Exception as e:
                    sys.stderr.write(f"Save error: {e}\n")
            return self._send_json(report)

        # ── Reading Arena Submit ──
        if path == "/api/arena/submit":
            answers = data.get("answers", {})
            questions = data.get("questions", [])
            arena_path = Path(__file__).parent / "data" / "reading" / "thinking-fast-slow" / "arena_tests.json"
            if not arena_path.exists():
                return self._send_json({"error": "擂台题库不存在"}, 404)

            book_data = json.loads(arena_path.read_text(encoding="utf-8"))
            test_map = {t["id"]: t for t in book_data["tests"]}
            pairs = book_data["anti_guess_design"]["cross_validation_pairs"]

            tc, ta = 0, 0
            concept_scores = {}
            pair_results = {}

            for qid, chosen in answers.items():
                if qid not in test_map: continue
                t = test_map[qid]
                ta += 1
                correct = (chosen == t["correct"])
                if correct: tc += 1

                c = t["concept"]
                if c not in concept_scores:
                    concept_scores[c] = {"correct": 0, "total": 0, "difficulty": t["difficulty"]}
                concept_scores[c]["total"] += 1
                if correct: concept_scores[c]["correct"] += 1

            for pair in pairs:
                p1, p2 = pair
                r1 = answers.get(p1) is not None and answers.get(p1) == test_map[p1]["correct"]
                r2 = answers.get(p2) is not None and answers.get(p2) == test_map[p2]["correct"]
                pair_results[f"{p1}-{p2}"] = {"both_correct": r1 and r2, "one_correct": (r1 or r2) and not (r1 and r2), "both_wrong": not r1 and not r2}

            cross_validated = 0
            for c, s in concept_scores.items():
                s["mastery"] = s["correct"] / s["total"] if s["total"] > 0 else 0
                pair_key = None
                for pair in pairs:
                    if any(test_map[pid]["concept"] == c for pid in pair):
                        pair_key = f"{pair[0]}-{pair[1]}"
                        break
                if pair_key and pair_results.get(pair_key, {}).get("one_correct"):
                    s["mastery"] *= 0.5
                    s["cross_validated"] = False
                elif pair_key and pair_results.get(pair_key, {}).get("both_correct"):
                    s["cross_validated"] = True
                    cross_validated += 1
                else:
                    s["cross_validated"] = s["mastery"] >= 0.67

            report = {
                "total_score": ta > 0 and round(tc / ta * 100) or 0,
                "total_correct": tc,
                "total_questions": ta,
                "concept_scores": concept_scores,
                "pair_results": pair_results,
                "cross_validated_count": cross_validated,
                "cross_validated_total": len(pairs),
                "anti_guess_active": True,
            }

            token = data.get("token") or self._auth_header()
            user = get_user_by_token(token) if token else None
            if user:
                try:
                    conn = get_db()
                    conn.execute(
                        "INSERT INTO test_sessions (user_id, profile, answers, node_status, questions_summary, overall_score, total_correct, total_questions, selected_levels) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (user["id"], json.dumps({"type": "arena", "book": "thinking-fast-slow"}, ensure_ascii=False),
                         json.dumps(answers, ensure_ascii=False), json.dumps(concept_scores, ensure_ascii=False),
                         json.dumps([{"id": qid, "concept": test_map[qid]["concept"]} for qid in answers if qid in test_map], ensure_ascii=False),
                         report["total_score"], tc, ta, json.dumps(["arena"], ensure_ascii=False))
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    sys.stderr.write(f"Arena save error: {e}\n")
            return self._send_json(report)

        # ── Arena Episode Submit ──
        if path == "/api/arena/episode-submit":
            token = data.get("token") or self._auth_header()
            user = get_user_by_token(token) if token else None
            book = data.get("book", "thinking-fast-slow")
            episode = int(data.get("episode", 0))
            answers = data.get("answers", {})
            if not episode:
                return self._send_json({"error": "请指定 episode"}, 400)

            ep_tests = get_episode_tests(episode)
            if not ep_tests:
                return self._send_json({"error": f"第{episode}期题库不存在"}, 404)

            tests = ep_tests["tests"]
            test_map = {t["id"]: t for t in tests}

            tc, ta = 0, 0
            for qid, chosen in answers.items():
                if qid not in test_map: continue
                t = test_map[qid]
                ta += 1
                if chosen == t["correct"]:
                    tc += 1

            score = round(tc / ta * 100) if ta > 0 else 0
            report = {
                "episode": episode,
                "title": ep_tests["title"],
                "concept": ep_tests["concept"],
                "total_score": score,
                "total_correct": tc,
                "total_questions": ta,
            }

            if user:
                try:
                    conn = get_db()
                    conn.execute("""
                        INSERT OR REPLACE INTO arena_episode_sessions (user_id, book_id, episode, answers, score, total_correct, total_questions)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (user["id"], book, episode, json.dumps(answers, ensure_ascii=False), score, tc, ta))
                    conn.commit()
                    conn.close()
                    # 飞轮：擂台答题数据也写入
                    try:
                        _flywheel.save_session({
                            "session_id": f"arena_{book}_ep{episode}_{user['id']}_{int(datetime.now().timestamp())}",
                            "profile": {"name": user["name"], "role": "insurance-agent", "source": "arena", "book": book},
                            "answers": answers,
                            "node_status": {},
                            "overall_score": score
                        }, user_id=str(user["id"]))
                    except Exception as e:
                        sys.stderr.write(f"Flywheel arena save error: {e}\n")
                    # 自动触发飞轮分析
                    try:
                        result = _scheduler.try_run()
                        if result:
                            sys.stderr.write(f"Flywheel auto-run (arena): {result.get('applied_result',{}).get('applied',0)} actions\n")
                    except Exception as e:
                        sys.stderr.write(f"Flywheel auto-run error: {e}\n")
                except Exception as e:
                    sys.stderr.write(f"Episode submit error: {e}\n")

            return self._send_json(report)

        # ── Arena Comment ──
        if path == "/api/arena/comment":
            token = data.get("token") or self._auth_header()
            user = get_user_by_token(token) if token else None
            if not user:
                return self._send_json({"error": "请先登录"}, 401)
            book = data.get("book", "thinking-fast-slow")
            episode = int(data.get("episode", 0))
            text = (data.get("text") or "").strip()
            if not episode or not text:
                return self._send_json({"error": "请指定 episode 和留言内容"}, 400)
            if len(text) > 500:
                return self._send_json({"error": "留言不能超过500字"}, 400)
            add_comment(book, episode, user["id"], text)
            return self._send_json({"ok": True})

        # ── Admin API ──
        is_admin = check_admin(data.get("token") or self._auth_header())

        if path == "/admin/login":
            email = (data.get("email") or "").strip().lower()
            password = data.get("password", "")
            if not email or not password: return self._send_json({"error": "请输入邮箱和密码"}, 400)
            admin = admin_login(email, password)
            if admin: return self._send_json({"user": admin, "message": "登录成功"})
            return self._send_json({"error": "邮箱或密码错误，或非管理员账号"}, 403)

        if path == "/admin/roles":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            role_id = data.get("id", "").strip().lower().replace(" ", "-")
            if not role_id: return self._send_json({"error": "岗位ID不能为空"}, 400)
            if not re.match(r'^[a-z0-9_-]+$', role_id): return self._send_json({"error": "岗位ID只能包含小写字母、数字、横线和下划线"}, 400)
            meta = {"title": data.get("title", role_id), "description": data.get("description", ""),
                    "industry": data.get("industry", ""), "levels": data.get("levels", ["foundation","advanced","transcendent"])}
            result = create_role(role_id, meta)
            if result: return self._send_json(result)
            return self._send_json({"error": "岗位已存在"}, 409)

        return self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        path = urlparse(self.path).path
        data = self._read_body()
        is_admin = check_admin(data.get("token") or self._auth_header())

        # Admin: update role meta
        m = self._match("admin/roles/<id>/meta")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            meta = {k: v for k, v in data.items() if k in ("title","description","industry","levels")}
            if not meta: return self._send_json({"error": "无有效字段"}, 400)
            if update_role_meta(m["id"], meta): return self._send_json({"ok": True})
            return self._send_json({"error": "岗位不存在"}, 404)

        # Admin: update role nodes
        m = self._match("admin/roles/<id>/nodes")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if not isinstance(data, list): return self._send_json({"error": "nodes 必须是数组"}, 400)
            if update_role_file(m["id"], "nodes", data): return self._send_json({"ok": True, "count": len(data)})
            return self._send_json({"error": "岗位不存在"}, 404)

        # Admin: update role edges
        m = self._match("admin/roles/<id>/edges")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if not isinstance(data, list): return self._send_json({"error": "edges 必须是数组"}, 400)
            if update_role_file(m["id"], "edges", data): return self._send_json({"ok": True, "count": len(data)})
            return self._send_json({"error": "岗位不存在"}, 404)

        # Admin: update role tests
        m = self._match("admin/roles/<id>/tests")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if not isinstance(data, list): return self._send_json({"error": "tests 必须是数组"}, 400)
            if update_role_file(m["id"], "tests", data): return self._send_json({"ok": True, "count": len(data)})
            return self._send_json({"error": "岗位不存在"}, 404)

        # Admin: update settings
        if path == "/admin/settings":
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if not isinstance(data, dict): return self._send_json({"error": "settings 必须是对象"}, 400)
            save_settings(data)
            return self._send_json({"ok": True})

        return self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        token = qs.get("token", [""])[0] or self._auth_header()
        is_admin = check_admin(token)

        # Admin: delete role
        m = self._match("admin/roles/<id>")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if delete_role(m["id"]): return self._send_json({"ok": True})
            return self._send_json({"error": "岗位不存在"}, 404)

        # Admin: delete user
        m = self._match("admin/users/<id>")
        if m:
            if not is_admin: return self._send_json({"error": "需要管理员权限"}, 403)
            if delete_user(m["id"]): return self._send_json({"ok": True})
            return self._send_json({"error": "用户不存在或无法删除（管理员不可删除）"}, 404)

        return self._send_json({"error": "Not found"}, 404)


# ── Main ──
if __name__ == "__main__":
    init_db()
    sync_internal_questions()
    port = int(os.environ.get("PORT", sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--port" else 8081))
    server = ThreadingHTTPServer(("0.0.0.0", port), KGDSHandler)
    print(f"[KGDS] Server v3 running on port {port}")
    print(f"[KGDS] Admin: http://localhost:{port}/admin")
    print(f"[KGDS] SQLite: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[KGDS] Shutting down...")
        server.server_close()
