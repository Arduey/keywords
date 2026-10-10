# -*- coding: utf-8 -*-
"""业务接口：/api/auth、/api/data、/api/import、/api/export

路径、请求体、响应结构与原 Cloudflare 版完全一致，
唯一区别：除 /api/auth 外，所有接口都强制校验登录令牌（X-Auth 请求头）。
"""
import base64
import functools
import hashlib
import hmac
import json
import re
import secrets
import time

from flask import Blueprint, Response, jsonify, request
from werkzeug.security import check_password_hash

import db as dbm
import excel

api_bp = Blueprint("api", __name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 非关键词的固定字段
_META_KEYS = ("评分", "评论数", "rank")


def _cfg():
    import config_loader

    return config_loader.current_config()


def _secret():
    return getattr(_cfg(), "TOKEN_SECRET", "") or ""


# ---------- 令牌 ----------

def issue_token():
    """签发 HMAC 签名令牌，格式：base64url(过期时间:随机数).签名"""
    ttl = int(getattr(_cfg(), "TOKEN_TTL_DAYS", 30) or 30)
    exp = int(time.time()) + ttl * 86400
    payload = "%d:%s" % (exp, secrets.token_urlsafe(12))
    sig = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + sig


def verify_token(token):
    """校验令牌的签名与有效期。"""
    secret = _secret()
    if not token or not secret or "." not in token:
        return False
    payload_b64, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(
            payload_b64 + "=" * (-len(payload_b64) % 4)
        ).decode()
    except Exception:
        return False
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        exp = int(payload.split(":", 1)[0])
    except (ValueError, IndexError):
        return False
    return exp > time.time()


def require_auth(fn):
    """接口鉴权装饰器。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return ("", 204)
        if not verify_token(request.headers.get("X-Auth", "")):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


# ---------- 取值辅助 ----------

def _num(value):
    """把 DECIMAL 还原成 0 / 4.5 这样的数字，与原 SQLite 输出一致。"""
    if value is None:
        return 0
    f = float(value)
    i = int(f)
    return i if f == i else f


def _iso(value):
    """把 MySQL DATE 统一成 'YYYY-MM-DD'。"""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _looks_hashed(text):
    """判断 settings 里存的是哈希还是明文（兼容手工改过的明文密码）。"""
    return "$" in text and ":" in text.split("$")[0]


# ---------- POST /api/auth ----------

@api_bp.route("/auth", methods=["POST", "OPTIONS"])
def auth():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(force=True, silent=True) or {}
    input_pwd = body.get("password") or ""

    conn = dbm.get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT `value` FROM settings WHERE `key` = %s", ("site_password",))
        row = cur.fetchone()

    stored = (row["value"] if row else "") or ""
    if not stored:
        return jsonify({"ok": False, "error": "尚未设置访问密钥"})

    if _looks_hashed(stored):
        ok = check_password_hash(stored, input_pwd)
    else:
        ok = hmac.compare_digest(stored, input_pwd)

    if ok:
        return jsonify({"ok": True, "token": issue_token()})
    return jsonify({"ok": False})


# ---------- /api/data ----------

@api_bp.route("/data", methods=["GET", "DELETE", "PATCH", "OPTIONS"])
@require_auth
def data():
    conn = dbm.get_db()

    # --- 修改产品名称 ---
    if request.method == "PATCH":
        body = request.get_json(force=True, silent=True) or {}
        asin = body.get("asin")
        name = body.get("name")
        if not asin or not name:
            return jsonify({"error": "Missing asin or name"}), 400
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET name = %s WHERE asin = %s", (name, asin))
        return jsonify({"status": "updated", "asin": asin, "name": name})

    # --- 删除 ---
    if request.method == "DELETE":
        asin = request.args.get("asin")
        date = request.args.get("date")
        keyword = request.args.get("keyword")

        # 按关键词删除（跨所有日期）
        if asin and keyword:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM rankings WHERE asin = %s AND keyword = %s",
                    (asin, keyword),
                )
                changes = cur.rowcount
            return jsonify(
                {"status": "deleted", "asin": asin, "keyword": keyword, "changes": changes}
            )

        # 按日期删除
        if asin and date:
            if not _DATE_RE.match(date):
                return jsonify({"error": "Invalid date"}), 400
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM rankings WHERE asin = %s AND `date` = %s", (asin, date)
                )
                changes = cur.rowcount
            return jsonify(
                {"status": "deleted", "asin": asin, "date": date, "changes": changes}
            )

        return jsonify({"error": "Missing parameters"}), 400

    # --- 查询 ---
    asin = request.args.get("asin")
    with conn.cursor() as cur:
        if asin:
            cur.execute("SELECT * FROM products WHERE asin = %s", (asin,))
            products = cur.fetchall()
            cur.execute(
                "SELECT * FROM rankings WHERE asin = %s ORDER BY `date` DESC, keyword ASC",
                (asin,),
            )
            rankings = cur.fetchall()
        else:
            cur.execute("SELECT * FROM products ORDER BY asin")
            products = cur.fetchall()
            cur.execute(
                "SELECT * FROM rankings ORDER BY asin, `date` DESC, keyword ASC"
            )
            rankings = cur.fetchall()

    grouped = {}
    for p in products:
        grouped[p["asin"]] = {"name": p["name"], "dates": {}}
    for r in rankings:
        a = r["asin"]
        if a not in grouped:
            grouped[a] = {"name": a, "dates": {}}
        d = _iso(r["date"])
        if d not in grouped[a]["dates"]:
            grouped[a]["dates"][d] = {
                "rating": _num(r["rating"]),
                "reviewCount": int(r["review_count"] or 0),
                "rank": r["rank"] or "",
                "keywords": {},
            }
        grouped[a]["dates"][d]["keywords"][r["keyword"]] = {
            "naturalPos": r["natural_pos"] or "",
            "adPos": r["ad_pos"] or "",
        }
    return jsonify(grouped)


# ---------- POST /api/import ----------

@api_bp.route("/import", methods=["POST", "OPTIONS"])
@require_auth
def import_data():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "请求体不是合法的 JSON 对象"}), 400

    conn = dbm.get_db()
    force = request.args.get("force") == "true"

    # --- 第 1 步：检测冲突（按 产品 + 日期）---
    asins = [a for a in body.keys() if a != "name"]
    existing = {}
    if asins:
        placeholders = ", ".join(["%s"] * len(asins))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, `date`, COUNT(*) AS cnt FROM rankings "
                "WHERE asin IN (%s) GROUP BY asin, `date`" % placeholders,
                asins,
            )
            for row in cur.fetchall():
                existing[(row["asin"], _iso(row["date"]))] = row["cnt"]

    conflicts = []
    for asin, product_data in body.items():
        if not isinstance(product_data, dict):
            continue
        product_name = product_data.get("name") or asin
        for date, _date_data in product_data.items():
            if date == "name":
                continue
            cnt = existing.get((asin, date), 0)
            if cnt > 0:
                conflicts.append(
                    {"asin": asin, "name": product_name, "date": date, "count": cnt}
                )

    if conflicts and not force:
        return jsonify(
            {
                "status": "conflict",
                "conflicts": conflicts,
                "message": "发现 %d 个产品/日期已有数据，是否覆盖？" % len(conflicts),
            }
        )

    # --- 第 2 步：校验日期格式 ---
    for asin, product_data in body.items():
        if not isinstance(product_data, dict):
            continue
        for date in product_data.keys():
            if date != "name" and not _DATE_RE.match(str(date)):
                return (
                    jsonify({"error": "日期格式不正确（应为 YYYY-MM-DD）：%s" % date}),
                    400,
                )

    # --- 第 3 步：写入 ---
    overwrite = len(conflicts) > 0
    product_rows, delete_rows, ranking_rows = [], [], []

    for asin, product_data in body.items():
        if not isinstance(product_data, dict):
            continue
        product_rows.append((asin, product_data.get("name") or asin))

        for date, date_data in product_data.items():
            if date == "name" or not isinstance(date_data, dict):
                continue

            rating = date_data.get("评分", 0)
            review_count = date_data.get("评论数", 0)
            rank = date_data.get("rank") or ""

            if overwrite:
                delete_rows.append((asin, date))

            for key, value in date_data.items():
                if key in _META_KEYS:
                    continue

                natural_pos, ad_pos = "", ""
                if isinstance(value, dict):
                    natural_pos = value.get("自然位含广告") or ""
                    ad_pos = value.get("广告位含自然") or ""

                # 规则：含「无排名」一律按空处理
                if natural_pos and "无排名" in natural_pos:
                    natural_pos = ""
                if ad_pos and "无排名" in ad_pos:
                    ad_pos = ""

                ranking_rows.append(
                    (asin, date, rating, review_count, rank, key, natural_pos, ad_pos)
                )

    try:
        conn.begin()
        with conn.cursor() as cur:
            if product_rows:
                cur.executemany(
                    "INSERT INTO products (asin, name) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE name = VALUES(name)",
                    product_rows,
                )
            if delete_rows:
                cur.executemany(
                    "DELETE FROM rankings WHERE asin = %s AND `date` = %s", delete_rows
                )
            if ranking_rows:
                cur.executemany(
                    "INSERT INTO rankings "
                    "(asin, `date`, rating, review_count, `rank`, keyword, natural_pos, ad_pos) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE rating = VALUES(rating), "
                    "review_count = VALUES(review_count), `rank` = VALUES(`rank`), "
                    "natural_pos = VALUES(natural_pos), ad_pos = VALUES(ad_pos)",
                    ranking_rows,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return jsonify(
        {
            "status": "success",
            "products": len(product_rows),
            "rankings": len(ranking_rows),
            "overwrite": overwrite,
        }
    )


# ---------- GET /api/export ----------

@api_bp.route("/export", methods=["GET", "OPTIONS"])
@require_auth
def export():
    asin_filter = request.args.get("asin") or ""
    conn = dbm.get_db()

    with conn.cursor() as cur:
        if asin_filter:
            cur.execute("SELECT * FROM products WHERE asin = %s", (asin_filter,))
            products = cur.fetchall()
            cur.execute(
                "SELECT * FROM rankings WHERE asin = %s ORDER BY `date`, keyword",
                (asin_filter,),
            )
            rankings = cur.fetchall()
        else:
            cur.execute("SELECT * FROM products ORDER BY asin")
            products = cur.fetchall()
            cur.execute("SELECT * FROM rankings ORDER BY asin, `date`, keyword")
            rankings = cur.fetchall()

    data = {}
    for p in products:
        data[p["asin"]] = {"name": p["name"], "dates": {}}
    for r in rankings:
        a = r["asin"]
        if a not in data:
            continue
        d = _iso(r["date"])
        if d not in data[a]["dates"]:
            data[a]["dates"][d] = {
                "rating": _num(r["rating"]),
                "reviewCount": int(r["review_count"] or 0),
                "rank": r["rank"] or "",
                "keywords": {},
            }
        data[a]["dates"][d]["keywords"][r["keyword"]] = {
            "n": r["natural_pos"] or "",
            "a": r["ad_pos"] or "",
        }

    try:
        kw_order = json.loads(request.args.get("order") or "{}")
    except (ValueError, TypeError):
        kw_order = {}
    if not isinstance(kw_order, dict):
        kw_order = {}

    xml = excel.generate_xml(data, kw_order)
    return Response(
        xml,
        status=200,
        headers={
            "Content-Type": "application/vnd.ms-excel; charset=UTF-8",
            "Content-Disposition": 'attachment; filename="keyword-rankings.xls"',
        },
    )
