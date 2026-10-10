# -*- coding: utf-8 -*-
"""关键词排名管理系统 —— Flask 入口。

本地调试：  python app.py
生产环境：  由宝塔面板「Python 项目」以 gunicorn 启动（见 DEPLOY.md）
"""
import os
import secrets
import shutil
import sys

from flask import Flask, jsonify, redirect, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG_PATH = os.path.join(BASE_DIR, "config.py")
CONFIG_TEMPLATE = os.path.join(BASE_DIR, "config.example.py")


def _bootstrap_config():
    """首次启动时用模板生成 config.py，随后由安装向导覆盖。

    这样部署时无需手动创建任何文件。
    """
    if not os.path.exists(CONFIG_PATH) and os.path.exists(CONFIG_TEMPLATE):
        shutil.copyfile(CONFIG_TEMPLATE, CONFIG_PATH)


_bootstrap_config()

import config_loader  # noqa: E402

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 导入大 JSON 时放宽到 64MB
app.json.ensure_ascii = False  # 中文 JSON 直接输出，不转义成 \uXXXX


# ---------- 前端静态文件 ----------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/app.js")
def app_js():
    return send_from_directory(BASE_DIR, "app.js")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


# ---------- 安装向导 ----------
from install import install_bp, is_installed  # noqa: E402

app.register_blueprint(install_bp)


@app.before_request
def _force_install():
    """尚未完成安装时，把所有请求引导到安装向导。"""
    from flask import request

    if is_installed():
        return None
    path = request.path
    if path.startswith("/install") or path == "/healthz":
        return None
    return redirect("/install")


# ---------- 业务接口 ----------
from api import api_bp  # noqa: E402

app.register_blueprint(api_bp, url_prefix="/api")


def _ensure_token_secret():
    """确保 TOKEN_SECRET 存在；缺失时生成并写回 config.py。

    注意：write_config 默认保留当前的安装标记，不会把未安装状态误标成已安装。
    """
    config_module = config_loader.current_config()
    if getattr(config_module, "TOKEN_SECRET", ""):
        return
    config_module.TOKEN_SECRET = secrets.token_urlsafe(48)
    try:
        import install

        install.write_config(config_module)
    except Exception:
        pass


_ensure_token_secret()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
