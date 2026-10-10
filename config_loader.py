# -*- coding: utf-8 -*-
"""config.py 的统一读取入口。

为什么需要这一层：生产环境用 gunicorn 多 worker 部署（见 DEPLOY.md）。
安装向导写 config.py 时，只有处理该请求的那个 worker 会立刻重新加载配置；
其余 worker 如果不主动比对文件变化，就会一直沿用启动时的旧配置，
表现为「刚装完就报数据库连接失败」或「登录令牌时好时坏」。

因此所有读取配置的地方都走 current_config()：它会检查 config.py 的
修改时间与大小，发现变化就重新加载，从而让每个 worker 都能自动跟上。
"""
import importlib
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG_PATH = os.path.join(BASE_DIR, "config.py")

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
