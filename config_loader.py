# -*- coding: utf-8 -*-
"""config.py 的统一读取入口。

为什么需要这一层：生产环境用 gunicorn 多 worker 部署（见 DEPLOY.md）。
安装向导写 config.py 时，只有处理该请求的那个 worker 会立刻重新加载配置；
其余 worker 如果不主动比对文件变化，就会一直沿用启动时的旧配置，
表现为「刚装完就报数据库连接失败」或「登录令牌时好时坏」。

因此所有读取配置的地方都走 current_config()：它会检查 config.py 的
修改时间与大小，发现变化就重新加载，从而让每个 worker 都能自动跟上。

另外提供 read_installed_flag()：直接读磁盘上的 INSTALLED 标记，
不依赖模块内存状态，供 is_installed() 这类关键判断使用，
避免个别进程残留旧配置时把用户错误地导向安装向导。
"""
import importlib
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG_PATH = os.path.join(BASE_DIR, "config.py")

_INSTALLED_RE = re.compile(r"^\s*INSTALLED\s*=\s*True\b", re.M)

# 每个进程各自记录上一次看到的 config.py 状态
_last_signature = None


def _signature():
    """config.py 的 (修改时间, 大小)；文件不存在时返回 None。"""
    try:
        st = os.stat(CONFIG_PATH)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def current_config():
    """返回 config 模块；若磁盘上的 config.py 已变化则先重新加载。"""
    global _last_signature

    if "config" in sys.modules:
        cfg = sys.modules["config"]
    else:
        import config as cfg

    sig = _signature()
    if sig is None:
        return cfg

    if _last_signature is None:
        _last_signature = sig
    elif sig != _last_signature:
        cfg = importlib.reload(cfg)
        _last_signature = sig
    return cfg


def note_config_written():
    """写盘后调用，让本进程直接记住最新状态，省掉紧接着的一次多余重载。"""
    global _last_signature
    _last_signature = _signature()


def read_installed_flag():
    """直接读磁盘判断是否已完成安装。

    刻意不依赖模块内存状态：多 worker 部署时，个别进程可能仍持有旧配置，
    若用内存状态判断，会让一部分请求被错误地重定向到安装向导
    （表现为「首页时好时坏」「导入偶发返回 HTML」）。
    这里的判断只看磁盘文件，因此所有进程结论一致。
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return _INSTALLED_RE.search(f.read()) is not None
    except OSError:
        return False
