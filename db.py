# -*- coding: utf-8 -*-
"""MySQL 连接管理。

注意：本项目使用 PyMySQL，占位符是 %s（不是 SQLite 的 ?）。
"""
import pymysql
from pymysql.cursors import DictCursor


def connect(with_database=True):
    """按 config.py 建立一条 MySQL 连接。"""
    import config_loader

    config = config_loader.current_config()

    kwargs = dict(
        host=config.DB_HOST,
        port=int(config.DB_PORT),
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )
    if with_database:
        kwargs["database"] = config.DB_NAME
    return pymysql.connect(**kwargs)


def get_db():
    """取当前请求内复用的连接，断线自动重连。"""
    from flask import g

    db = g.get("db")
    if db is None:
        db = g.db = connect()
    else:
        try:
            db.ping(reconnect=True)
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            db = g.db = connect()
    return db


def close_db(exc=None):
    """请求结束时释放连接。"""
    from flask import g

    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
