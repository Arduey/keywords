# -*- coding: utf-8 -*-
"""Web 安装向导。

用途：让整个部署过程不需要命令行、也不需要手动创建/修改任何文件。
访问站点根路径时会自动跳到这里，向导负责：
  1. 检查运行环境
  2. 测试 MySQL 连接
  3. 建表（执行 schema.sql）
  4. 生成 config.py
  5. 设置站点访问密钥
"""
import hmac
import os
import re
import secrets
import time

import pymysql
from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

import config_loader
import db as dbm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.py")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

install_bp = Blueprint("install", __name__)

_MIN_PWD_LEN = 8


def _check_password_strength(pwd):
    """校验访问密钥强度，返回错误提示；通过时返回 None。"""
    if len(pwd) < _MIN_PWD_LEN:
        return "访问密钥至少 %d 位" % _MIN_PWD_LEN
    if not re.search(r"[A-Za-z]", pwd) or not re.search(r"\d", pwd):
        return "访问密钥需同时包含字母和数字"
    return None


# 重置密钥接口的失败次数记录：{ip: [失败时间戳, ...]}
_RESET_ATTEMPTS = {}
_RESET_MAX_ATTEMPTS = 5
_RESET_WINDOW_SECONDS = 15 * 60


def _reset_rate_limited(ip):
    """同一 IP 在 15 分钟内失败 5 次后锁定。"""
    now = time.time()
    hits = [t for t in _RESET_ATTEMPTS.get(ip, []) if now - t < _RESET_WINDOW_SECONDS]
    _RESET_ATTEMPTS[ip] = hits
    return len(hits) >= _RESET_MAX_ATTEMPTS


def _record_reset_attempt(ip):
    now = time.time()
    hits = [t for t in _RESET_ATTEMPTS.get(ip, []) if now - t < _RESET_WINDOW_SECONDS]
    hits.append(now)
    _RESET_ATTEMPTS[ip] = hits


# 重新安装的解锁记录：{ip: 解锁时间戳}
# 只存在内存中、且有有效期，因此不需要（也不应该）改动任何文件。
_REINSTALL_UNLOCK = {}
_REINSTALL_UNLOCK_SECONDS = 15 * 60


def _reinstall_unlocked(ip):
    """该 IP 是否已在有效期内通过数据库密码解锁重新安装。"""
    ts = _REINSTALL_UNLOCK.get(ip)
    if not ts:
        return False
    if time.time() - ts > _REINSTALL_UNLOCK_SECONDS:
        _REINSTALL_UNLOCK.pop(ip, None)
        return False
    return True


# ---------- 安装状态 ----------

def is_installed():
    """config.py 存在且标记为已安装。

    优先读取模块属性而非匹配文本，这样 INSTALLED 两侧有没有空格都能正确识别；
    只有在 config.py 无法导入（例如语法错误）时才退回文本判断。
    """
    if not os.path.exists(CONFIG_PATH):
        return False
    try:
        return bool(getattr(config_loader.current_config(), "INSTALLED", False))
    except Exception:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                text = f.read()
            return re.search(r"^\s*INSTALLED\s*=\s*True\b", text, re.M) is not None
        except OSError:
            return False


def _reload_config():
    """把新写入的 config.py 立即加载进当前进程。"""
    return config_loader.current_config()


def _current_config():
    """读取当前配置（会感知 config.py 的变化，适配 gunicorn 多 worker）。"""
    return config_loader.current_config()


# ---------- 写配置文件 ----------

_CONFIG_TEMPLATE = '''# -*- coding: utf-8 -*-
"""由 Web 安装向导自动生成，请勿手动编辑。

需要重新配置时，访问 /install 用数据库密码解锁即可，不必修改本文件。
"""

# ---- MySQL 连接信息 ----
DB_HOST = {db_host!r}
DB_PORT = {db_port!r}
DB_NAME = {db_name!r}
DB_USER = {db_user!r}
DB_PASSWORD = {db_password!r}

# ---- 站点 ----
# 登录令牌签名密钥，泄露后需重新安装以更换。
TOKEN_SECRET = {token_secret!r}

# 登录令牌有效期（天）
TOKEN_TTL_DAYS = {token_ttl!r}

# 是否允许重复进入安装向导
ALLOW_REINSTALL = False

# 安装完成标记（由安装向导写入，请勿手工改动）
INSTALLED = {installed!r}
'''


def write_config(cfg=None, installed=None, **overrides):
    """生成 config.py。

    cfg 可以是 config 模块，也可以是 dict；缺省字段沿用当前配置。
    installed 为 None 时保持当前的安装标记不变，避免误把未安装状态改成已安装。
    """
    cur = _current_config()

    def pick(key, default):
        if overrides.get(key) is not None:
            return overrides[key]
        if cfg is None:
            return getattr(cur, key, default)
        if isinstance(cfg, dict):
            return cfg.get(key, getattr(cur, key, default))
        return getattr(cfg, key, getattr(cur, key, default))

    if installed is None:
        installed = bool(getattr(cur, "INSTALLED", False))

    values = {
        "db_host": pick("DB_HOST", "127.0.0.1"),
        "db_port": int(pick("DB_PORT", 3306) or 3306),
        "db_name": pick("DB_NAME", "keyword_tracker"),
        "db_user": pick("DB_USER", "keyword_tracker"),
        "db_password": pick("DB_PASSWORD", ""),
        "token_secret": pick("TOKEN_SECRET", "") or secrets.token_urlsafe(48),
        "token_ttl": int(pick("TOKEN_TTL_DAYS", 30) or 30),
        "installed": bool(installed),
    }

    content = _CONFIG_TEMPLATE.format(**values)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    config_loader.note_config_written()
    _reload_config()
    return values


# ---------- 建表 ----------

def _split_sql(text):
    """把 schema.sql 拆成一条条语句（跳过注释行）。"""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(raw)
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _apply_schema(conn):
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        statements = _split_sql(f.read())
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()
    return len(statements)


# ---------- 页面 ----------

@install_bp.route("/install", methods=["GET"])
def install_page():
    if is_installed():
        cur = _current_config()
        ip = request.remote_addr or "unknown"
        # 已安装时：默认显示「已完成」页；仅在 ALLOW_REINSTALL 打开、
        # 或本机已用数据库密码临时解锁时，才显示完整安装向导。
        if not getattr(cur, "ALLOW_REINSTALL", False) and not _reinstall_unlocked(ip):
            return _page("已完成安装", _DONE_BODY)
    return _page("安装向导", _WIZARD_BODY)


# ---------- 接口 ----------

@install_bp.route("/install/api/reset-password", methods=["POST"])
def api_reset_password():
    """重置站点访问密钥。

    身份验证方式：要求提供正确的数据库密码。
    该密码由服务器管理员掌握（宝塔面板 → 数据库 可查），
    因此无需手工编辑 config.py 即可自助重置。
    """
    ip = request.remote_addr or "unknown"
    if _reset_rate_limited(ip):
        return jsonify({"ok": False, "error": "尝试次数过多，请 15 分钟后再试"})

    cur = _current_config()
    body = request.get_json(force=True, silent=True) or {}
    db_password = body.get("db_password") or ""
    new_password = body.get("site_password") or ""

    pwd_error = _check_password_strength(new_password)
    if pwd_error:
        return jsonify({"ok": False, "error": pwd_error})

    if not db_password:
        return jsonify({"ok": False, "error": "请输入数据库密码以验证身份"})

    expected = getattr(cur, "DB_PASSWORD", "")
    if not expected:
        return jsonify({"ok": False, "error": "配置中未记录数据库密码，无法自助重置"})
    if not hmac.compare_digest(str(db_password), str(expected)):
        _record_reset_attempt(ip)
        return jsonify({"ok": False, "error": "数据库密码不正确，无法重置访问密钥"})

    try:
        conn = dbm.connect()
        with conn.cursor() as cur_db:
            cur_db.execute(
                "INSERT INTO settings (`key`, `value`) VALUES ('site_password', %s) "
                "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
                (generate_password_hash(new_password, method="pbkdf2:sha256"),),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": "更新访问密钥失败：%s" % _friendly_error(exc)})

    _RESET_ATTEMPTS.pop(ip, None)
    return jsonify({"ok": True})


@install_bp.route("/install/api/unlock-reinstall", methods=["POST"])
def api_unlock_reinstall():
    """用当前数据库密码确认管理员身份，临时解锁「重新安装」。

    解锁状态只保存在内存里、15 分钟后自动失效，因此全程无需改动任何文件。
    """
    ip = request.remote_addr or "unknown"
    if _reset_rate_limited(ip):
        return jsonify({"ok": False, "error": "尝试次数过多，请 15 分钟后再试"})

    cur = _current_config()
    body = request.get_json(force=True, silent=True) or {}
    db_password = body.get("db_password") or ""

    if not db_password:
        return jsonify({"ok": False, "error": "请输入当前数据库密码以确认身份"})

    expected = getattr(cur, "DB_PASSWORD", "")
    if not expected or not hmac.compare_digest(str(db_password), str(expected)):
        _record_reset_attempt(ip)
        return jsonify({"ok": False, "error": "数据库密码不正确"})

    _REINSTALL_UNLOCK[ip] = time.time()
    return jsonify({"ok": True, "minutes": _REINSTALL_UNLOCK_SECONDS // 60})

@install_bp.route("/install/api/test", methods=["POST"])
def api_test():
    """测试 MySQL 连接，并报告目标库是否存在。"""
    body = request.get_json(force=True, silent=True) or {}
    host = (body.get("host") or "127.0.0.1").strip()
    port = int(body.get("port") or 3306)
    name = (body.get("name") or "").strip()
    user = (body.get("user") or "").strip()
    password = body.get("password") or ""

    if not name or not user:
        return jsonify({"ok": False, "error": "数据库名和用户名不能为空"})

    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            charset="utf8mb4", connect_timeout=8, autocommit=True,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": _friendly_error(exc)})

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (name,),
            )
            exists = cur.fetchone() is not None
    finally:
        conn.close()

    return jsonify({"ok": True, "database_exists": exists, "server": _server_version(host, port, user, password)})


def _server_version(host, port, user, password):
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            charset="utf8mb4", connect_timeout=8,
        )
        try:
            return conn.get_server_info()
        finally:
            conn.close()
    except Exception:
        return ""


def _friendly_error(exc):
    """把常见的 MySQL 报错翻译成能直接照做的提示。"""
    code = exc.args[0] if exc.args else None
    # pymysql 的错误信息是 (错误码, 描述) 形式；str(exc) 会带上元组外壳，
    # 因此这里优先取描述部分，提示更干净。
    msg = exc.args[1] if len(exc.args) > 1 else (exc.args[0] if exc.args else str(exc))
    msg = str(msg)
    if code == 1045:
        return "用户名或密码错误（1045）。请核对宝塔面板 → 数据库 中的账号密码。"
    if code == 2003:
        return "连接不上 MySQL 服务（2003）。请确认数据库已启动，且主机端口填写正确。"
    if code == 1049:
        return "数据库不存在（1049）。请先在宝塔面板 → 数据库 中创建该数据库。"
    if code == 1044:
        return "该账号没有访问此数据库的权限（1044）。请在宝塔面板中把账号授权到该数据库。"
    return msg


@install_bp.route("/install/api/save", methods=["POST"])
def api_save():
    """执行安装：建表 → 写配置 → 设置访问密钥。"""
    ip = request.remote_addr or "unknown"
    if is_installed():
        cur = _current_config()
        if not getattr(cur, "ALLOW_REINSTALL", False) and not _reinstall_unlocked(ip):
            return jsonify({
                "ok": False,
                "error": "系统已安装完成。如需重新安装，请在上方用数据库密码解锁。",
            })

    body = request.get_json(force=True, silent=True) or {}
    host = (body.get("host") or "127.0.0.1").strip()
    port = int(body.get("port") or 3306)
    name = (body.get("name") or "").strip()
    user = (body.get("user") or "").strip()
    password = body.get("password") or ""
    site_password = body.get("site_password") or ""

    # 校验
    if not name or not user:
        return jsonify({"ok": False, "error": "数据库名和用户名不能为空"})
    pwd_error = _check_password_strength(site_password)
    if pwd_error:
        return jsonify({"ok": False, "error": pwd_error})

    # 1) 连接（先不指定库，必要时创建库）
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            charset="utf8mb4", connect_timeout=8, autocommit=True,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": _friendly_error(exc)})

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (name,),
            )
            if cur.fetchone() is None:
                # 尝试自动建库；宝塔的库账号通常没有该权限，失败则给出明确指引
                try:
                    cur.execute(
                        "CREATE DATABASE `%s` DEFAULT CHARACTER SET utf8mb4 "
                        "COLLATE utf8mb4_unicode_ci" % name.replace("`", "")
                    )
                except Exception:
                    return jsonify({
                        "ok": False,
                        "error": "数据库 “%s” 不存在，且当前账号无权创建。"
                                 "请先到 宝塔面板 → 数据库 → 添加数据库 创建它，"
                                 "再把生成的账号密码填到这里。" % name,
                    })

        conn.select_db(name)

        # 2) 建表
        table_count = _apply_schema(conn)

        # 3) 写入访问密钥（哈希存储）
        # 显式指定 pbkdf2:sha256：不依赖 OpenSSL 的 scrypt 支持，任何环境都能用。
        pwd_hash = generate_password_hash(site_password, method="pbkdf2:sha256")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (`key`, `value`) VALUES ('site_password', %s) "
                "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
                (pwd_hash,),
            )
        conn.commit()
    except Exception as exc:
        return jsonify({"ok": False, "error": "初始化数据表失败：%s" % exc})
    finally:
        conn.close()

    # 4) 写 config.py（这一步写入 INSTALLED = True，安装才真正生效）
    try:
        write_config(
            DB_HOST=host,
            DB_PORT=port,
            DB_NAME=name,
            DB_USER=user,
            DB_PASSWORD=password,
            installed=True,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": "写入配置文件失败：%s" % exc})

    return jsonify({"ok": True, "tables": table_count, "database": name})


# ---------- 页面骨架 ----------

_PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: #f0f2f5;
       color: #333; min-height: 100vh; padding: 40px 16px; }
.wrap { max-width: 620px; margin: 0 auto; }
.header { background: linear-gradient(135deg, #1a73e8, #0d47a1); color: #fff;
          padding: 24px 28px; border-radius: 10px 10px 0 0; }
.header h1 { font-size: 20px; font-weight: 600; }
.header p { font-size: 13px; opacity: .85; margin-top: 6px; }
.card { background: #fff; border-radius: 0 0 10px 10px; padding: 28px;
        box-shadow: 0 2px 12px rgba(0,0,0,.08); }
.field { margin-bottom: 16px; }
.field label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.field .hint { font-size: 12px; color: #888; font-weight: 400; margin-left: 6px; }
input { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px;
        font-size: 14px; font-family: inherit; }
input:focus { outline: none; border-color: #1a73e8; box-shadow: 0 0 0 2px rgba(26,115,232,.15); }
.grid2 { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px;
       padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer;
       font-size: 14px; font-family: inherit; transition: all .2s; }
.btn-primary { background: #1a73e8; color: #fff; }
.btn-primary:hover { background: #1557b0; }
.btn-outline { background: #fff; color: #1a73e8; border: 1px solid #1a73e8; }
.btn-outline:hover { background: #e8f0fe; }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.alert { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; line-height: 1.6; }
.alert-success { background: #e6f4ea; color: #0d652d; border: 1px solid #a8dab5; }
.alert-error { background: #fce8e6; color: #a50e0e; border: 1px solid #f6b8b0; }
.alert-info { background: #e8f0fe; color: #174ea6; border: 1px solid #a8c7fa; }
.steps { display: flex; gap: 8px; margin-bottom: 24px; font-size: 12px; }
.step { flex: 1; text-align: center; padding: 8px 4px; border-radius: 6px;
        background: #f1f3f4; color: #888; }
.step.active { background: #e8f0fe; color: #1a73e8; font-weight: 600; }
.step.done { background: #e6f4ea; color: #0d652d; }
.row { display: flex; gap: 10px; align-items: center; margin-top: 20px; }
.sep { border-top: 1px solid #eee; margin: 24px 0 20px; }
.sep-title { font-size: 13px; font-weight: 600; color: #1a73e8; margin-bottom: 14px; }
code { background: #f1f3f4; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
a { color: #1a73e8; }
"""

_WIZARD_BODY = """
<div class="steps">
  <div class="step active" id="st1">1 环境检查</div>
  <div class="step" id="st2">2 数据库</div>
  <div class="step" id="st3">3 访问密钥</div>
  <div class="step" id="st4">4 完成</div>
</div>

<div id="msg"></div>

<div class="sep-title">数据库连接信息</div>
<div class="alert alert-info">
  在 <b>宝塔面板 → 数据库 → 添加数据库</b> 创建好数据库后，把面板给出的
  数据库名、用户名、密码填到这里。数据库字符集请选择 <code>utf8mb4</code>。
</div>

<div class="field">
  <label>数据库主机</label>
  <div class="grid2">
    <input id="host" value="127.0.0.1" placeholder="127.0.0.1">
    <input id="port" value="3306" placeholder="3306">
  </div>
</div>
<div class="field">
  <label>数据库名 <span class="hint">宝塔创建数据库时填写的名称</span></label>
  <input id="name" placeholder="例如 keyword_tracker">
</div>
<div class="field">
  <label>数据库用户名</label>
  <input id="user" placeholder="例如 keyword_tracker">
</div>
<div class="field">
  <label>数据库密码</label>
  <input id="dbpwd" type="password" placeholder="宝塔面板中该数据库的密码">
</div>

<div class="row">
  <button class="btn btn-outline" id="testBtn" onclick="testConn()">测试连接</button>
</div>

<div class="sep"></div>
<div class="sep-title">站点访问密钥</div>
<div class="alert alert-info">
  这是进入本系统的登录口令，请设置一个别人猜不到的：至少 8 位，且同时包含字母和数字。
</div>
<div class="field">
  <label>访问密钥</label>
  <input id="sitepwd" type="password" placeholder="至少 8 位，含字母和数字">
</div>
<div class="field">
  <label>确认访问密钥</label>
  <input id="sitepwd2" type="password" placeholder="再输入一次">
</div>

<div class="row">
  <button class="btn btn-primary" id="saveBtn" onclick="doInstall()">开始安装</button>
</div>

<script>
function show(type, html) {
  document.getElementById('msg').innerHTML = '<div class="alert alert-' + type + '">' + html + '</div>';
}
function mark(n) {
  for (var i = 1; i <= 4; i++) {
    var el = document.getElementById('st' + i);
    el.className = 'step' + (i < n ? ' done' : (i === n ? ' active' : ''));
  }
}
function payload() {
  return {
    host: document.getElementById('host').value.trim(),
    port: document.getElementById('port').value.trim(),
    name: document.getElementById('name').value.trim(),
    user: document.getElementById('user').value.trim(),
    password: document.getElementById('dbpwd').value,
    site_password: document.getElementById('sitepwd').value
  };
}
async function testConn() {
  var btn = document.getElementById('testBtn');
  btn.disabled = true; btn.textContent = '测试中…';
  show('info', '正在连接数据库…');
  try {
    var r = await fetch('/install/api/test', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload())
    });
    var d = await r.json();
    if (d.ok) {
      mark(3);
      show('success', '✅ 连接成功！MySQL 版本 ' + (d.server || '未知') + '。<br>' +
        (d.database_exists
          ? '数据库已存在，安装时会直接建表。'
          : '数据库尚不存在，安装时会尝试自动创建；若提示无权限，请先到宝塔面板创建。'));
    } else {
      show('error', '❌ ' + d.error);
    }
  } catch (e) {
    show('error', '❌ 请求失败：' + e.message);
  }
  btn.disabled = false; btn.textContent = '测试连接';
}
async function doInstall() {
  var p = payload();
  var s1 = document.getElementById('sitepwd').value;
  var s2 = document.getElementById('sitepwd2').value;
  if (!p.name || !p.user) return show('error', '请先填写数据库名和用户名');
  if (s1.length < 8) return show('error', '访问密钥至少 8 位');
  if (s1 !== s2) return show('error', '两次输入的访问密钥不一致');

  var btn = document.getElementById('saveBtn');
  btn.disabled = true; btn.textContent = '安装中…';
  show('info', '正在创建数据表并写入配置，请稍候…');
  try {
    var r = await fetch('/install/api/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(p)
    });
    var d = await r.json();
    if (d.ok) {
      mark(5);
      show('success', '🎉 安装完成！数据库 “' + d.database + '” 已初始化 ' + d.tables +
        ' 张表。<br>请点击下方按钮进入系统，用刚才设置的访问密钥登录。');
      document.getElementById('saveBtn').outerHTML =
        '<a class="btn btn-primary" href="/" style="text-decoration:none;">进入系统</a>';
    } else {
      show('error', '❌ ' + d.error);
      btn.disabled = false; btn.textContent = '开始安装';
    }
  } catch (e) {
    show('error', '❌ 请求失败：' + e.message);
    btn.disabled = false; btn.textContent = '开始安装';
  }
}
mark(2);
</script>
"""

_DONE_BODY = """
<div class="alert alert-success">✅ 系统已安装完成，无需重复安装。</div>
<p style="color:#666;font-size:13px;line-height:1.7;margin-bottom:20px;">
  直接进入系统即可。下面可以自助重置访问密钥，<b>不需要修改任何文件</b>。
</p>

<div class="row" style="margin-top:0;">
  <a class="btn btn-primary" href="/" style="text-decoration:none;">进入系统</a>
</div>

<div class="sep"></div>
<div class="sep-title">重置访问密钥</div>
<div class="alert alert-info">
  为确认你有服务器管理权限，需要填写<b>数据库密码</b>（可在
  <b>宝塔面板 → 数据库</b> 中查看）。数据不会受到影响。
</div>

<div id="resetMsg"></div>

<div class="field">
  <label>数据库密码 <span class="hint">用于验证身份</span></label>
  <input id="rdb" type="password" placeholder="宝塔面板 → 数据库 中的密码">
</div>
<div class="field">
  <label>新的访问密钥</label>
  <input id="rnew" type="password" placeholder="至少 8 位，含字母和数字">
</div>
<div class="field">
  <label>确认新的访问密钥</label>
  <input id="rnew2" type="password" placeholder="再输入一次">
</div>
<div class="row">
  <button class="btn btn-outline" id="resetBtn" onclick="doReset()">重置访问密钥</button>
</div>

<div class="sep"></div>
<div class="sep-title">重新安装</div>
<div class="alert alert-info">
  仅在需要更换数据库连接信息（例如换了数据库）时才用到。<br>
  同样填写<b>当前数据库密码</b>即可解锁，<b>无需修改任何文件</b>；
  解锁状态 15 分钟后自动失效。重新安装不会删除已有的排名数据。
</div>

<div id="unlockMsg"></div>
<div class="field">
  <label>当前数据库密码</label>
  <input id="udb" type="password" placeholder="宝塔面板 → 数据库 中的密码">
</div>
<div class="row">
  <button class="btn btn-outline" id="unlockBtn" onclick="doUnlock()">解锁重新安装</button>
</div>

<script>
function rshow(type, html) {
  document.getElementById('resetMsg').innerHTML =
    '<div class="alert alert-' + type + '">' + html + '</div>';
}
async function doReset() {
  var dbp = document.getElementById('rdb').value;
  var n1 = document.getElementById('rnew').value;
  var n2 = document.getElementById('rnew2').value;
  if (!dbp) return rshow('error', '请输入数据库密码以验证身份');
  if (n1.length < 8) return rshow('error', '访问密钥至少 8 位');
  if (n1 !== n2) return rshow('error', '两次输入的新密钥不一致');

  var btn = document.getElementById('resetBtn');
  btn.disabled = true; btn.textContent = '重置中…';
  try {
    var r = await fetch('/install/api/reset-password', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({db_password: dbp, site_password: n1})
    });
    var d = await r.json();
    if (d.ok) {
      rshow('success', '🎉 访问密钥已更新。请用新密钥登录系统。');
      document.getElementById('rdb').value = '';
      document.getElementById('rnew').value = '';
      document.getElementById('rnew2').value = '';
    } else {
      rshow('error', '❌ ' + d.error);
    }
  } catch (e) {
    rshow('error', '❌ 请求失败：' + e.message);
  }
  btn.disabled = false; btn.textContent = '重置访问密钥';
}
function ushow(type, html) {
  document.getElementById('unlockMsg').innerHTML =
    '<div class="alert alert-' + type + '">' + html + '</div>';
}
async function doUnlock() {
  var dbp = document.getElementById('udb').value;
  if (!dbp) return ushow('error', '请输入当前数据库密码');
  var btn = document.getElementById('unlockBtn');
  btn.disabled = true; btn.textContent = '解锁中…';
  try {
    var r = await fetch('/install/api/unlock-reinstall', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({db_password: dbp})
    });
    var d = await r.json();
    if (d.ok) {
      ushow('success', '✅ 已解锁，正在打开安装向导…');
      setTimeout(function () { location.href = '/install'; }, 800);
    } else {
      ushow('error', '❌ ' + d.error);
      btn.disabled = false; btn.textContent = '解锁重新安装';
    }
  } catch (e) {
    ushow('error', '❌ 请求失败：' + e.message);
    btn.disabled = false; btn.textContent = '解锁重新安装';
  }
}
</script>
"""


def _page(title, body):
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "<title>%s - 关键词排名管理系统</title>\n<style>%s</style>\n</head>\n<body>\n"
        '<div class="wrap">\n'
        '<div class="header"><h1>关键词排名管理系统</h1><p>%s</p></div>\n'
        '<div class="card">%s</div>\n'
        "</div>\n</body>\n</html>"
    ) % (title, _PAGE_CSS, title, body)
