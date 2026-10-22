"""阶段一应用的运行配置。"""

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get(
        "SECRET_KEY", "phase1-dev-secret-change-me-before-deployment"
    )
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "survey_phase1")

    # 阶段一为教学项目，默认开启 CSRF 防护；自动化测试中会关闭。
    CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    SURVEY_BASE_URL = os.environ.get("SURVEY_BASE_URL", "")
