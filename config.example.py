# -*- coding: utf-8 -*-
"""配置模板。

部署时【不需要】手动复制或修改这个文件：
访问站点后会自动跳转到安装向导 (/install)，由向导填写并生成同目录下的 config.py。

本文件仅作为占位。若 config.py 尚未生成，向导会自动以本文件为模板创建。
"""

# ---- MySQL 连接信息 ----
DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_NAME = "keyword_tracker"
DB_USER = "keyword_tracker"
DB_PASSWORD = ""

# ---- 站点 ----
# 安装向导会把这里改成随机长字符串，用于签发登录令牌。
# 留空时程序会自动生成一个并写回配置。
TOKEN_SECRET = ""

# 登录令牌有效期（天）
TOKEN_TTL_DAYS = 30

# 是否允许重复进入安装向导。
# 安装完成后建议保持 False；需要重装时改成 True，完成后改回。
ALLOW_REINSTALL = False
