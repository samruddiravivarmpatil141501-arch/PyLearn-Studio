from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import re
import ast
import json
import math
import random
import secrets
import subprocess
import tempfile
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pylearn.db")
CONTENT_DIR = os.path.join(BASE_DIR, "content")


def load_secret_key():
    """Use PYLEARN_SECRET if set, otherwise create and reuse a random key file."""
    env_key = os.environ.get("PYLEARN_SECRET")
    if env_key:
        return env_key
    key_path = os.path.join(BASE_DIR, "secret.key")
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# ---------------------------------------------------------------------------
# Course content now lives in content/modules.json and content/quizzes.json
# (edit those files to change lessons or quiz questions).
# ---------------------------------------------------------------------------
def _load_json(name):
    with open(os.path.join(CONTENT_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


MODULES = _load_json("modules.json")
QUIZ_BANK = _load_json("quizzes.json")   # {"1": [{"q":..., "options":[correct, wrong, wrong, wrong]}, ...]}

PASS_PERCENT = 70          # a module quiz is passed with 70% or more (5/7)
QUESTIONS_PER_QUIZ = 7

for _mid, _qs in QUIZ_BANK.items():
    assert len(_qs) == QUESTIONS_PER_QUIZ, f"Module {_mid} quiz must have {QUESTIONS_PER_QUIZ} questions"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        completed_at TEXT NOT NULL,
        UNIQUE(user_id, topic_id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS quiz_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        module_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS practice_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS certificates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        certificate_id TEXT UNIQUE NOT NULL,
        issued_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()


def current_user():
    if "user_id" not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return user


def all_topics():
    return [topic for module in MODULES for topic in module["topics"]]


def find_topic(topic_id):
    for module in MODULES:
        for topic in module["topics"]:
            if topic["id"] == topic_id:
                return topic, module
    return None, None


def find_module(module_id):
    return next((m for m in MODULES if m["id"] == module_id), None)


def completed_topic_ids(user_id):
    conn = get_db()
    rows = conn.execute("SELECT topic_id FROM progress WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return {row["topic_id"] for row in rows}


def total_progress(user_id):
    total = len(all_topics())
    done = len(completed_topic_ids(user_id))
    return round(done / total * 100) if total else 0


def quiz_status(user_id):
    """Best result per module: {module_id: {...}} (only modules that were attempted)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT module_id, score, total FROM quiz_results WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    status = {}
    for r in rows:
        pct = r["score"] * 100.0 / r["total"] if r["total"] else 0
        s = status.setdefault(r["module_id"], {"best_pct": -1, "best_score": 0, "best_total": 0, "attempts": 0})
        s["attempts"] += 1
        if pct > s["best_pct"]:
            s.update(best_pct=pct, best_score=r["score"], best_total=r["total"])
    for s in status.values():
        s["passed"] = s["best_pct"] >= PASS_PERCENT
    return status


def pass_mark(total=QUESTIONS_PER_QUIZ):
    return math.ceil(PASS_PERCENT * total / 100)


# ---------------------------------------------------------------------------
# CSRF protection (every POST must carry the session token)
# ---------------------------------------------------------------------------
def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(str(sent), expected):
            abort(400, description="Security token missing or expired. Go back, refresh the page and try again.")


@app.context_processor
def inject_globals():
    user = current_user()
    return {
        "current_user": user,
        "progress_percent": total_progress(user["id"]) if user else 0,
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not name or not email or not password:
            flash("Please fill in all required fields.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
        else:
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO users(name,email,password_hash,created_at) VALUES (?,?,?,?)",
                    (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds"))
                )
                conn.commit()
                flash("Account created. You can now log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That email is already registered.", "error")
            finally:
                conn.close()
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    done = completed_topic_ids(user["id"])
    module_cards = []
    for module in MODULES:
        completed = sum(1 for t in module["topics"] if t["id"] in done)
        module_cards.append({**module, "completed": completed, "total": len(module["topics"])})
    qs = quiz_status(user["id"])
    quiz_avg = round(sum(s["best_pct"] for s in qs.values()) / len(qs)) if qs else 0
    conn = get_db()
    practice_count = conn.execute(
        "SELECT COUNT(*) AS c FROM practice_codes WHERE user_id=?", (user["id"],)
    ).fetchone()["c"]
    conn.close()
    return render_template(
        "dashboard.html", modules=module_cards, quiz_avg=quiz_avg, practice_count=practice_count
    )


@app.route("/modules")
def modules():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    done = completed_topic_ids(user["id"])
    cards = [{**m, "completed": sum(t["id"] in done for t in m["topics"])} for m in MODULES]
    return render_template("modules.html", modules=cards)


def topic_video(module):
    """One real, embeddable YouTube lesson per module (set video_id in content/modules.json)."""
    video_id = module.get("video_id", "rfscVS0vtbw")
    return {
        "id": video_id,
        "title": module.get("video_title", f"Module {module['id']} video lesson"),
        "embed": f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0&modestbranding=1",
        "watch": f"https://www.youtube.com/watch?v={video_id}",
    }


@app.route("/module/<int:module_id>")
def module_view(module_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    module = find_module(module_id)
    if not module:
        return "Module not found", 404

    # Topics are displayed INSIDE the module page (sidebar switches topics).
    selected_id = request.args.get("topic", type=int)
    topic = next((t for t in module["topics"] if t["id"] == selected_id), None) or module["topics"][0]

    topic_index = next(i for i, t in enumerate(module["topics"]) if t["id"] == topic["id"])
    done = completed_topic_ids(user["id"])
    module_done_count = sum(1 for t in module["topics"] if t["id"] in done)
    module_index = next(i for i, m in enumerate(MODULES) if m["id"] == module_id)
    previous_module = MODULES[module_index - 1] if module_index > 0 else None
    next_module = MODULES[module_index + 1] if module_index < len(MODULES) - 1 else None
    next_topic = module["topics"][topic_index + 1] if topic_index < len(module["topics"]) - 1 else None
    previous_topic = module["topics"][topic_index - 1] if topic_index > 0 else None

    return render_template(
        "module.html",
        module=module, topic=topic,
        topic_number=topic_index + 1, total_topics=len(module["topics"]),
        next_topic=next_topic, previous_topic=previous_topic,
        video=topic_video(module), done=done, module_done_count=module_done_count,
        previous_module=previous_module, next_module=next_module,
        module_number=module_index + 1, total_modules=len(MODULES),
    )


@app.route("/topic/<int:topic_id>")
def topic_view(topic_id):
    topic, module = find_topic(topic_id)
    if not topic:
        return "Topic not found", 404
    return redirect(url_for("module_view", module_id=module["id"], topic=topic_id))


@app.post("/topic/<int:topic_id>/complete")
def complete_topic(topic_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    topic, module = find_topic(topic_id)
    if not topic:
        return "Topic not found", 404
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO progress(user_id,topic_id,completed_at) VALUES (?,?,?)",
        (user["id"], topic_id, datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit()
    conn.close()
    flash("Topic marked as completed.", "success")
    return redirect(url_for("module_view", module_id=module["id"], topic=topic_id))


# ---------------------------------------------------------------------------
# Practice Lab: restricted beginner runner (local academic demo, NOT a full sandbox)
# ---------------------------------------------------------------------------
BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "dir", "help", "breakpoint",
    "exit", "quit", "os", "sys", "subprocess", "socket", "pathlib", "shutil"
}
# Modules needed by the lessons.  sqlite3 is restricted to :memory: or a plain file name.
ALLOWED_MODULES = {"math", "random", "datetime", "string", "collections", "itertools",
                   "json", "csv", "sqlite3", "statistics"}
NETWORK_MODULES = {"requests", "urllib", "http", "socket"}
# Dunder attributes beginners legitimately use. Anything else (__class__, __dict__, __globals__ ...) stays blocked.
ALLOWED_DUNDER_ATTRS = {"__init__", "__str__", "__repr__", "__len__", "__name__"}


def _is_simple_filename(value):
    return (isinstance(value, str) and value and len(value) <= 60
            and not any(ch in value for ch in "/\\:") and ".." not in value)


def _safe_open_calls(tree):
    """Return ids of Name nodes used in open('plain_name.txt', 'mode') calls."""
    safe = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open" and node.args
                and isinstance(node.args[0], ast.Constant)
                and _is_simple_filename(node.args[0].value)):
            mode_ok = True
            if len(node.args) > 1:
                mode_ok = isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode_ok = mode_ok and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                elif kw.arg not in ("encoding", "newline"):
                    mode_ok = False
            if len(node.args) <= 2 and mode_ok:
                safe.add(id(node.func))
    return safe


def validate_code(source):
    if len(source) > 6000:
        return "Code is too long for the practice runner."
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    safe_open = _safe_open_calls(tree)
    module_names, sqlite_names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NETWORK_MODULES:
                    return (f"'{root}' needs internet access, so it is not available in the Practice Lab. "
                            "You can run that code on your own computer.")
                if alias.name not in ALLOWED_MODULES:
                    return f"Importing '{alias.name}' is not allowed. Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}."
                module_names.add(alias.asname or alias.name)
                if alias.name == "sqlite3":
                    sqlite_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level == 0 and root in NETWORK_MODULES:
                return (f"'{root}' needs internet access, so it is not available in the Practice Lab. "
                        "You can run that code on your own computer.")
            if node.level != 0 or node.module not in ALLOWED_MODULES:
                return f"Importing from '{node.module}' is not allowed. Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}."
            if node.module == "sqlite3":
                return "Use 'import sqlite3' and call sqlite3.connect(...) instead of 'from sqlite3 import ...'."
            if any(a.name.startswith("_") for a in node.names):
                return "Importing private names is not allowed."
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDER_ATTRS:
                return "That special (dunder) attribute is disabled in the Practice Lab."
            if (attr.startswith("_") and isinstance(node.value, ast.Name) and node.value.id in module_names):
                return "Private module attributes are disabled."
            if attr in ("load_extension", "enable_load_extension"):
                return "load_extension is not allowed."
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "connect" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in sqlite_names:
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and (first.value == ":memory:" or _is_simple_filename(first.value))):
                return "sqlite3.connect() must use \":memory:\" or a plain file name in quotes, e.g. sqlite3.connect(\"school.db\")."
        if isinstance(node, ast.Name):
            if node.id == "open" and id(node) in safe_open:
                continue
            if node.id in BLOCKED_NAMES:
                return f"'{node.id}' is not allowed in the Practice Lab."
            if node.id.startswith("__") and node.id != "__name__":
                return "Dunder names are disabled."
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > 2000:
                return "Very large string literals are not allowed."
            if re.search(r"attach\s+database", node.value, re.I):
                return "ATTACH DATABASE is not allowed in the Practice Lab."
    if "open" in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} and not safe_open:
        return "open() only works with a plain file name in quotes, e.g. open('notes.txt', 'w')."
    return None


def _child_limits():
    """Memory / CPU / file-size limits for the child process (Linux and macOS)."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024))


def run_user_code(code, stdin_text=""):
    """Validate and run code. Returns (ok, output)."""
    stdin_text = str(stdin_text)[:2000]
    if stdin_text and not stdin_text.endswith("\n"):
        stdin_text += "\n"
    error = validate_code(code)
    if error:
        return False, error
    # Only pass the variables Python needs to start; secrets are NOT passed on.
    child_env = {k: os.environ[k] for k in ("SYSTEMROOT", "PATH", "TEMP", "TMP") if k in os.environ}
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "program.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", path],
                input=stdin_text, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=4,
                cwd=td, env=child_env,
                preexec_fn=_child_limits if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            return False, "Execution stopped: time limit exceeded (4 seconds). Check for an infinite loop."
        output = (result.stdout + result.stderr).strip()
        if "EOFError" in result.stderr and "input" in result.stderr:
            output += "\n\nHint: this program uses input(). Type the values into the 'Program input' box (one per line) and run again."
        if not output:
            output = "Program finished without printed output."
        return result.returncode == 0, output[:8000]


@app.route("/practice")
def practice():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    topic_id = request.args.get("topic_id", type=int)
    topic, module = find_topic(topic_id) if topic_id else (None, None)
    saved_code = None
    if topic:
        conn = get_db()
        row = conn.execute(
            "SELECT code FROM practice_codes WHERE user_id=? AND topic_id=? ORDER BY id DESC LIMIT 1",
            (user["id"], topic["id"])
        ).fetchone()
        conn.close()
        saved_code = row["code"] if row else None
    return render_template("practice.html", topic=topic, module=module, saved_code=saved_code)


@app.post("/api/run")
def api_run():
    if not current_user():
        return jsonify({"ok": False, "output": "Please log in first."}), 401
    data = request.get_json(silent=True) or {}
    ok, output = run_user_code(str(data.get("code", "")), data.get("stdin", ""))
    return jsonify({"ok": ok, "output": output})


@app.post("/api/save-code")
def save_code():
    user = current_user()
    if not user:
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", ""))
    try:
        topic_id = int(data.get("topic_id", 0))
    except (TypeError, ValueError):
        topic_id = 0
    topic, _ = find_topic(topic_id)
    if not code.strip() or not topic:
        return jsonify({"ok": False, "message": "Code and a valid topic are required."})
    if len(code) > 6000:
        return jsonify({"ok": False, "message": "Code is too long to save."})
    conn = get_db()
    conn.execute(
        "INSERT INTO practice_codes(user_id,topic_id,code,saved_at) VALUES (?,?,?,?)",
        (user["id"], topic_id, code, datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Code saved successfully."})


@app.route("/progress")
def progress():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    done = completed_topic_ids(user["id"])
    conn = get_db()
    quizzes = conn.execute(
        "SELECT module_id, score, total, created_at FROM quiz_results WHERE user_id=? ORDER BY id DESC",
        (user["id"],)
    ).fetchall()
    practice_count = conn.execute(
        "SELECT COUNT(*) AS c FROM practice_codes WHERE user_id=?", (user["id"],)
    ).fetchone()["c"]
    cert = conn.execute("SELECT * FROM certificates WHERE user_id=?", (user["id"],)).fetchone()
    conn.close()
    return render_template(
        "progress.html", modules=MODULES, done=done, quizzes=quizzes, qstatus=quiz_status(user["id"]),
        practice_count=practice_count, certificate=cert, pass_percent=PASS_PERCENT
    )


# ---------------------------------------------------------------------------
# Quizzes: a separate 7-question quiz for every module.
# Option order is shuffled per attempt, so the right answer is not always "A".
# ---------------------------------------------------------------------------
@app.route("/quizzes")
def quizzes():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    done = completed_topic_ids(user["id"])
    qs = quiz_status(user["id"])
    rows = []
    for m in MODULES:
        rows.append({
            "module": m,
            "topics_done": sum(t["id"] in done for t in m["topics"]),
            "topics_total": len(m["topics"]),
            "questions": len(QUIZ_BANK.get(str(m["id"]), [])),
            "status": qs.get(m["id"]),
        })
    passed = sum(1 for r in rows if r["status"] and r["status"]["passed"])
    return render_template("quizzes.html", rows=rows, passed=passed, pass_percent=PASS_PERCENT,
                           need=pass_mark(), total_q=QUESTIONS_PER_QUIZ)


def _quiz_orders(module_id, questions, fresh=False):
    key = f"quiz_order_{module_id}"
    orders = None if fresh else session.get(key)
    valid = (isinstance(orders, list) and len(orders) == len(questions) and
             all(isinstance(o, list) and sorted(o) == list(range(len(q["options"])))
                 for o, q in zip(orders, questions)))
    if not valid:
        orders = []
        for q in questions:
            order = list(range(len(q["options"])))
            random.shuffle(order)
            orders.append(order)
        session[key] = orders
    return orders


@app.route("/quiz/<int:module_id>", methods=["GET", "POST"])
def quiz(module_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    module = find_module(module_id)
    questions = QUIZ_BANK.get(str(module_id))
    if not module or not questions:
        return "Module not found", 404

    if request.method == "POST":
        key = f"quiz_order_{module_id}"
        orders = session.get(key)
        if not orders or len(orders) != len(questions):
            flash("Your quiz session expired. Please answer again.", "error")
            return redirect(url_for("quiz", module_id=module_id))
        score, review = 0, []
        for i, q in enumerate(questions):
            order = orders[i]                      # order[position] = original option index (0 = correct)
            chosen_text = None
            try:
                pos = int(request.form.get(f"q{i}", ""))
                if 0 <= pos < len(order):
                    chosen_text = q["options"][order[pos]]
                    is_right = order[pos] == 0
                else:
                    is_right = False
            except ValueError:
                is_right = False
            score += is_right
            review.append({"q": q["q"], "chosen": chosen_text, "correct": q["options"][0], "ok": is_right})
        session.pop(key, None)
        total = len(questions)
        conn = get_db()
        conn.execute(
            "INSERT INTO quiz_results(user_id,module_id,score,total,created_at) VALUES (?,?,?,?,?)",
            (user["id"], module_id, score, total, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
        passed = score * 100 / total >= PASS_PERCENT
        return render_template("quiz_result.html", module=module, score=score, total=total,
                               passed=passed, need=pass_mark(total), review=review,
                               pass_percent=PASS_PERCENT)

    orders = _quiz_orders(module_id, questions)
    shown = [{"q": q["q"], "options": [q["options"][j] for j in order]} for q, order in zip(questions, orders)]
    return render_template("quiz.html", module=module, questions=shown, need=pass_mark(), pass_percent=PASS_PERCENT)


# ---------------------------------------------------------------------------
# Certificate: all topics completed AND every module quiz passed
# ---------------------------------------------------------------------------
@app.route("/certificate")
def certificate():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    total = len(all_topics())
    done = len(completed_topic_ids(user["id"]))
    qs = quiz_status(user["id"])
    quiz_rows = [{"module": m, "status": qs.get(m["id"])} for m in MODULES]
    quizzes_passed = sum(1 for r in quiz_rows if r["status"] and r["status"]["passed"])
    eligible = done == total and quizzes_passed == len(MODULES)
    conn = get_db()
    cert = conn.execute("SELECT * FROM certificates WHERE user_id=?", (user["id"],)).fetchone()
    if eligible and not cert:
        cert_id = f"PYL-{datetime.now().year}-{user['id']:06d}"
        conn.execute(
            "INSERT INTO certificates(user_id,certificate_id,issued_at) VALUES (?,?,?)",
            (user["id"], cert_id, datetime.now().strftime("%d %B %Y"))
        )
        conn.commit()
        cert = conn.execute("SELECT * FROM certificates WHERE user_id=?", (user["id"],)).fetchone()
    conn.close()
    if not eligible:
        cert = None
    return render_template("certificate.html", user=user, certificate=cert, total=total, done=done,
                           quiz_rows=quiz_rows, quizzes_passed=quizzes_passed, modules_total=len(MODULES),
                           pass_percent=PASS_PERCENT)


@app.route("/about")
def about():
    return render_template("about.html")


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("PYLEARN_DEBUG") == "1", host="127.0.0.1", port=5000)
