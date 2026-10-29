"""基于 MongoDB 的在线问卷系统（阶段一）。"""

from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from bson import ObjectId
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from . import db as mongo
from . import survey_logic as logic
from .config import Config

bp = Blueprint("main", __name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def app_timezone():
    return ZoneInfo(current_app.config["APP_TIMEZONE"])


def format_local_datetime(value):
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_timezone()).strftime("%Y-%m-%d %H:%M")


def local_input_value(value):
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_timezone()).strftime("%Y-%m-%dT%H:%M")


def parse_datetime_local(raw):
    raw = (raw or "").strip()
    if not raw:
        return None, None
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            local_value = datetime.strptime(raw, pattern)
            return (
                local_value.replace(tzinfo=app_timezone()).astimezone(
                    timezone.utc
                ),
                None,
            )
        except ValueError:
            continue
    return None, "截止时间格式不正确，请重新选择。"


def checked(form, name):
    return form.get(name) == "on"


def parse_object_id(value):
    parsed = logic.as_object_id(value)
    if parsed is None:
        abort(404)
    return parsed


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.dashboard")


def get_owned_survey(survey_id):
    survey = mongo.get_db().surveys.find_one(
        {"_id": parse_object_id(survey_id), "owner_id": g.user["_id"]}
    )
    if survey is None:
        abort(404)
    return survey


def get_survey_questions(survey_id):
    return list(
        mongo.get_db()
        .questions.find({"survey_id": parse_object_id(survey_id)})
        .sort("order", 1)
    )


def get_question(survey_id, question_id):
    question = mongo.get_db().questions.find_one(
        {
            "_id": parse_object_id(question_id),
            "survey_id": parse_object_id(survey_id),
        }
    )
    if question is None:
        abort(404)
    return question


def get_question_template(template_id):
    template = mongo.get_db().question_templates.find_one(
        {"_id": parse_object_id(template_id)}
    )
    if template is None:
        abort(404)
    return template


def can_access_template(template, user_id=None):
    user_id = user_id or g.user["_id"]
    if template["owner_id"] == user_id:
        return True
    return (
        mongo.get_db().question_shares.find_one(
            {
                "template_id": template["_id"],
                "shared_with_id": user_id,
            }
        )
        is not None
    )


def get_accessible_template(template_id):
    template = get_question_template(template_id)
    if not can_access_template(template):
        abort(404)
    return template


def get_owned_template(template_id):
    template = get_question_template(template_id)
    if template["owner_id"] != g.user["_id"]:
        abort(404)
    return template


def get_template_version(template_id, version_id):
    version = mongo.get_db().question_versions.find_one(
        {
            "_id": parse_object_id(version_id),
            "template_id": parse_object_id(template_id),
        }
    )
    if version is None:
        abort(404)
    return version


def get_latest_version(template_id):
    return mongo.get_db().question_versions.find_one(
        {"template_id": parse_object_id(template_id)},
        sort=[("version_no", -1)],
    )


def next_version_number(template_id):
    latest = get_latest_version(template_id)
    return (latest["version_no"] if latest else 0) + 1


def accessible_template_views(bank_only=False):
    database = mongo.get_db()
    owned = list(
        database.question_templates.find({"owner_id": g.user["_id"]}).sort(
            "updated_at", -1
        )
    )
    shared_ids = [
        item["template_id"]
        for item in database.question_shares.find(
            {"shared_with_id": g.user["_id"]}
        )
    ]
    shared = (
        list(
            database.question_templates.find(
                {"_id": {"$in": shared_ids}}
            ).sort("updated_at", -1)
        )
        if shared_ids
        else []
    )
    templates = owned + shared
    bank_ids = {
        item["template_id"]
        for item in database.question_bank_items.find(
            {"user_id": g.user["_id"]}
        )
    }
    if bank_only:
        templates = [item for item in templates if item["_id"] in bank_ids]
    views = []
    for template in templates:
        versions = list(
            database.question_versions.find({"template_id": template["_id"]}).sort(
                "version_no", -1
            )
        )
        views.append(
            {
                "template": template,
                "latest": versions[0] if versions else None,
                "versions": versions,
                "is_owner": template["owner_id"] == g.user["_id"],
                "in_bank": template["_id"] in bank_ids,
                "usage_count": database.questions.count_documents(
                    {"template_id": template["_id"]}
                ),
            }
        )
    return views


def template_name_from_form(form, content):
    name = (form.get("template_name") or "").strip()
    if not name:
        name = content["title"]
    return name[:200]


def create_template(
    content,
    name,
    owner_id,
    now=None,
    source_version_id=None,
    source_template_id=None,
    change_note="创建题目",
):
    now = now or logic.utcnow()
    template = {
        "_id": ObjectId(),
        "owner_id": owner_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "schema_version": 2,
    }
    mongo.get_db().question_templates.insert_one(template)
    version = logic.build_question_version(
        content,
        template["_id"],
        1,
        owner_id,
        now=now,
        source_version_id=source_version_id,
        source_template_id=source_template_id,
        change_note=change_note,
    )
    mongo.get_db().question_versions.insert_one(version)
    return template, version


def create_template_version(
    template,
    content,
    created_by,
    parent_version_id,
    source_version_id=None,
    source_template_id=None,
    change_note=logic.CHANGE_NOTE_DEFAULT,
):
    now = logic.utcnow()
    version = logic.build_question_version(
        content,
        template["_id"],
        next_version_number(template["_id"]),
        created_by,
        now=now,
        parent_version_id=parent_version_id,
        source_version_id=source_version_id,
        source_template_id=source_template_id,
        change_note=change_note,
    )
    mongo.get_db().question_versions.insert_one(version)
    mongo.get_db().question_templates.update_one(
        {"_id": template["_id"]},
        {"$set": {"updated_at": now}},
    )
    return version


def add_bank_item(template_id, enabled=True):
    query = {
        "user_id": g.user["_id"],
        "template_id": parse_object_id(template_id),
    }
    if enabled:
        try:
            mongo.get_db().question_bank_items.insert_one(
                {
                    **query,
                    "created_at": logic.utcnow(),
                    "schema_version": 2,
                }
            )
        except DuplicateKeyError:
            pass
    else:
        mongo.get_db().question_bank_items.delete_one(query)


def question_content(document):
    return {
        "type": document["type"],
        "title": document["title"],
        "required": document["required"],
        "options": document.get("options", []),
        "constraints": document.get("constraints", {}),
    }


def survey_availability(survey):
    if survey["status"] == "draft":
        return False, "问卷尚未发布。"
    if survey["status"] == "closed":
        return False, "问卷已经关闭。"
    deadline = survey.get("deadline")
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= deadline:
            return False, "问卷已到截止时间。"
    return True, ""


def public_survey_url(survey):
    base = current_app.config.get("SURVEY_BASE_URL", "").rstrip("/")
    if not base:
        base = request.host_url.rstrip("/")
    return base + url_for(
        "main.public_survey", public_id=survey["public_id"]
    )


def new_public_id():
    database = mongo.get_db()
    for _ in range(20):
        candidate = secrets.token_urlsafe(9)
        if database.surveys.find_one({"public_id": candidate}) is None:
            return candidate
    raise RuntimeError("无法生成唯一的问卷链接。")


def get_fill_response(response_id):
    response = mongo.get_db().responses.find_one(
        {
            "_id": parse_object_id(response_id),
            "user_id": g.user["_id"],
        }
    )
    if response is None:
        abort(404)
    return response


def get_fill_survey(response):
    survey = mongo.get_db().surveys.find_one({"_id": response["survey_id"]})
    if survey is None:
        abort(404)
    return survey


def question_form_data(question=None, form=None):
    if form is not None:
        return {
            "type": form.get("type", "single"),
            "title": form.get("title", ""),
            "template_name": form.get("template_name", ""),
            "change_note": form.get("change_note", ""),
            "required": checked(form, "required"),
            "options": form.get("options", ""),
            "min_selections": form.get("min_selections", ""),
            "max_selections": form.get("max_selections", ""),
            "exact_selections": form.get("exact_selections", ""),
            "min_length": form.get("min_length", ""),
            "max_length": form.get("max_length", ""),
            "min_value": form.get("min_value", ""),
            "max_value": form.get("max_value", ""),
            "integer_only": checked(form, "integer_only"),
        }
    if question:
        constraints = question.get("constraints", {})
        return {
            "type": question["type"],
            "title": question["title"],
            "template_name": "",
            "change_note": "",
            "required": question["required"],
            "options": "\n".join(
                item["label"] for item in question.get("options", [])
            ),
            "min_selections": constraints.get("min_selections", ""),
            "max_selections": constraints.get("max_selections", ""),
            "exact_selections": constraints.get("exact_selections", ""),
            "min_length": constraints.get("min_length", ""),
            "max_length": constraints.get("max_length", ""),
            "min_value": constraints.get("min_value", ""),
            "max_value": constraints.get("max_value", ""),
            "integer_only": constraints.get("integer_only", False),
        }
    return {
        "type": "single",
        "title": "",
        "template_name": "",
        "change_note": "",
        "required": False,
        "options": "",
        "min_selections": "",
        "max_selections": "",
        "exact_selections": "",
        "min_length": "",
        "max_length": "",
        "min_value": "",
        "max_value": "",
        "integer_only": False,
    }


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


# ---------------------------------------------------------------------------
# 请求钩子
# ---------------------------------------------------------------------------


@bp.before_app_request
def load_logged_in_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        user = mongo.get_db().users.find_one(
            {"_id": logic.as_object_id(user_id)}
        )
        if user is None:
            session.clear()
        else:
            g.user = user


@bp.before_app_request
def protect_from_csrf():
    if request.method != "POST":
        return None
    if not current_app.config.get("CSRF_ENABLED", True):
        return None
    expected = session.get("_csrf", "")
    provided = request.form.get("_csrf", "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        abort(400, description="表单校验失败，请刷新页面后重试。")
    return None


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("main.login", next=target))
        return view(*args, **kwargs)

    return wrapped_view


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors = []
        if not 3 <= len(username) <= 32:
            errors.append("用户名长度需要为 3~32 个字符。")
        if any(char.isspace() for char in username):
            errors.append("用户名不能包含空格。")
        if len(password) < 6:
            errors.append("密码至少需要 6 个字符。")
        if len(password) > 128:
            errors.append("密码不能超过 128 个字符。")
        if password != confirm:
            errors.append("两次输入的密码不一致。")
        if not errors:
            try:
                result = mongo.get_db().users.insert_one(
                    {
                        "username": username,
                        "password_hash": generate_password_hash(password),
                        "created_at": logic.utcnow(),
                        "schema_version": 2,
                    }
                )
            except DuplicateKeyError:
                errors.append("该用户名已被注册。")
            else:
                session.clear()
                session["user_id"] = str(result.inserted_id)
                session.permanent = True
                flash("注册成功，已为你登录。", "success")
                return redirect(url_for("main.dashboard"))
        for error in errors:
            flash(error, "error")
    return render_template("register.html", username=username)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    next_url = request.args.get("next", "")
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.form.get("next", "")
        user = mongo.get_db().users.find_one({"username": username})
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码不正确。", "error")
        else:
            session.clear()
            session["user_id"] = str(user["_id"])
            session.permanent = True
            flash(f"欢迎回来，{user['username']}。", "success")
            return redirect(safe_next(next_url))
    return render_template("login.html", username=username, next_url=next_url)


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("已退出登录。", "success")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# 问卷管理
# ---------------------------------------------------------------------------


@bp.route("/dashboard")
@login_required
def dashboard():
    database = mongo.get_db()
    surveys = list(
        database.surveys.find({"owner_id": g.user["_id"]}).sort("created_at", -1)
    )
    survey_ids = [item["_id"] for item in surveys]
    response_counts = {}
    question_counts = {}
    if survey_ids:
        for row in database.responses.aggregate(
            [
                {
                    "$match": {
                        "survey_id": {"$in": survey_ids},
                        "status": "submitted",
                    }
                },
                {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
            ]
        ):
            response_counts[row["_id"]] = row["count"]
        for row in database.questions.aggregate(
            [
                {"$match": {"survey_id": {"$in": survey_ids}}},
                {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
            ]
        ):
            question_counts[row["_id"]] = row["count"]
    views = [
        {
            "survey": survey,
            "responses": response_counts.get(survey["_id"], 0),
            "questions": question_counts.get(survey["_id"], 0),
            "accepting": survey_availability(survey)[0],
        }
        for survey in surveys
    ]
    return render_template("dashboard.html", survey_views=views)


# ---------------------------------------------------------------------------
# 题目库、共享与版本
# ---------------------------------------------------------------------------


@bp.get("/questions")
@login_required
def question_library():
    scope = request.args.get("scope", "all")
    bank_only = scope == "bank"
    query = (request.args.get("q") or "").strip()
    views = accessible_template_views(bank_only=bank_only)
    if query:
        lowered = query.casefold()
        views = [
            item
            for item in views
            if lowered
            in item["template"]["name"].casefold()
            or (
                item["latest"]
                and lowered in item["latest"]["title"].casefold()
            )
        ]
    return render_template(
        "question_library.html",
        template_views=views,
        scope=scope,
        query=query,
        bank_count=len(accessible_template_views(bank_only=True)),
    )


@bp.route("/questions/new", methods=["GET", "POST"])
@login_required
def new_question_template():
    if request.method == "POST":
        content, errors = logic.build_question(
            request.form, ObjectId(), 1
        )
        name = template_name_from_form(request.form, content)
        if not name:
            errors.append("题目名称不能为空。")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=None,
                data=question_form_data(form=request.form),
                existing=None,
                form_mode="library",
            )
        template, _version = create_template(
            content, name, g.user["_id"]
        )
        if checked(request.form, "add_to_bank"):
            add_bank_item(template["_id"])
        flash("题目已保存到题目库。", "success")
        return redirect(
            url_for("main.question_template_detail", template_id=template["_id"])
        )
    return render_template(
        "question_form.html",
        survey=None,
        data=question_form_data(),
        existing=None,
        form_mode="library",
    )


@bp.get("/questions/<template_id>")
@login_required
def question_template_detail(template_id):
    database = mongo.get_db()
    template = get_accessible_template(template_id)
    is_owner = template["owner_id"] == g.user["_id"]
    versions = list(
        database.question_versions.find(
            {"template_id": template["_id"]}
        ).sort("version_no", -1)
    )
    if not versions:
        abort(404)
    version_by_id = {item["_id"]: item for item in versions}
    version_views = []
    for version in versions:
        parent = version_by_id.get(version.get("parent_version_id"))
        source = version_by_id.get(version.get("source_version_id"))
        version_views.append(
            {
                "version": version,
                "parent": parent,
                "source": source,
            }
        )
    usage_query = {"template_id": template["_id"]}
    if not is_owner:
        own_survey_ids = [
            item["_id"]
            for item in database.surveys.find(
                {"owner_id": g.user["_id"]}, {"_id": 1}
            )
        ]
        usage_query["survey_id"] = {"$in": own_survey_ids}
    usages = list(database.questions.find(usage_query).sort("created_at", -1))
    survey_ids = list({item["survey_id"] for item in usages})
    survey_by_id = {
        item["_id"]: item
        for item in database.surveys.find({"_id": {"$in": survey_ids}})
    }
    usage_views = [
        {
            "usage": item,
            "survey": survey_by_id.get(item["survey_id"]),
            "can_open": bool(
                survey_by_id.get(item["survey_id"])
                and survey_by_id[item["survey_id"]]["owner_id"] == g.user["_id"]
            ),
        }
        for item in usages
    ]
    shares = []
    if is_owner:
        share_rows = list(
            database.question_shares.find(
                {"template_id": template["_id"]}
            ).sort("created_at", -1)
        )
        user_by_id = {
            item["_id"]: item
            for item in database.users.find(
                {"_id": {"$in": [row["shared_with_id"] for row in share_rows]}}
            )
        }
        shares = [
            {
                "share": row,
                "user": user_by_id.get(row["shared_with_id"]),
            }
            for row in share_rows
        ]
    in_bank = (
        database.question_bank_items.find_one(
            {
                "user_id": g.user["_id"],
                "template_id": template["_id"],
            }
        )
        is not None
    )
    return render_template(
        "question_template_detail.html",
        template=template,
        versions=version_views,
        current_version=versions[0],
        usage_views=usage_views,
        shares=shares,
        is_owner=is_owner,
        in_bank=in_bank,
    )


@bp.post("/questions/<template_id>/rename")
@login_required
def rename_question_template(template_id):
    template = get_owned_template(template_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("题目名称不能为空。", "error")
    elif len(name) > 200:
        flash("题目名称不能超过 200 个字符。", "error")
    else:
        mongo.get_db().question_templates.update_one(
            {"_id": template["_id"]},
            {"$set": {"name": name, "updated_at": logic.utcnow()}},
        )
        flash("题目名称已更新。", "success")
    return redirect(
        url_for("main.question_template_detail", template_id=template["_id"])
    )


@bp.post("/questions/<template_id>/share")
@login_required
def share_question_template(template_id):
    template = get_owned_template(template_id)
    username = (request.form.get("username") or "").strip()
    user = mongo.get_db().users.find_one({"username": username})
    if user is None:
        flash("没有找到该用户名。", "error")
    elif user["_id"] == g.user["_id"]:
        flash("不能把题目共享给自己。", "error")
    else:
        try:
            mongo.get_db().question_shares.insert_one(
                {
                    "template_id": template["_id"],
                    "owner_id": g.user["_id"],
                    "shared_with_id": user["_id"],
                    "created_at": logic.utcnow(),
                    "schema_version": 2,
                }
            )
            flash(f"已共享给 {username}。", "success")
        except DuplicateKeyError:
            flash("该用户已经可以使用这道题。", "error")
    return redirect(
        url_for("main.question_template_detail", template_id=template["_id"])
    )


@bp.post("/questions/<template_id>/share/<share_id>/revoke")
@login_required
def revoke_question_share(template_id, share_id):
    template = get_owned_template(template_id)
    mongo.get_db().question_shares.delete_one(
        {
            "_id": parse_object_id(share_id),
            "template_id": template["_id"],
            "owner_id": g.user["_id"],
        }
    )
    flash("已撤销共享；已经加入问卷的题目快照不受影响。", "success")
    return redirect(
        url_for("main.question_template_detail", template_id=template["_id"])
    )


@bp.post("/questions/<template_id>/bank")
@login_required
def toggle_question_bank(template_id):
    template = get_accessible_template(template_id)
    action = request.form.get("action", "add")
    add_bank_item(template["_id"], enabled=action != "remove")
    flash(
        "已从我的题库移除。" if action == "remove" else "已加入我的题库。",
        "success",
    )
    return redirect(
        url_for("main.question_template_detail", template_id=template["_id"])
    )


@bp.route(
    "/questions/<template_id>/versions/new",
    methods=["GET", "POST"],
)
@login_required
def new_question_version(template_id):
    template = get_owned_template(template_id)
    current_version = get_latest_version(template["_id"])
    if current_version is None:
        abort(404)
    if request.method == "POST":
        content, errors = logic.build_question(
            request.form,
            template["_id"],
            current_version["version_no"],
            existing=current_version,
        )
        if content["type"] != current_version["type"]:
            errors.append("同一道题不能修改题型；如需换题型请创建新题目。")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=None,
                data=question_form_data(form=request.form),
                existing=current_version,
                form_mode="version",
                template=template,
            )
        create_template_version(
            template,
            content,
            g.user["_id"],
            parent_version_id=current_version["_id"],
            change_note=request.form.get("change_note", ""),
        )
        flash("新版本已创建；已加入问卷的旧版本保持不变。", "success")
        return redirect(
            url_for("main.question_template_detail", template_id=template["_id"])
        )
    return render_template(
        "question_form.html",
        survey=None,
        data=question_form_data(question=current_version),
        existing=current_version,
        form_mode="version",
        template=template,
    )


@bp.post(
    "/questions/<template_id>/versions/<version_id>/restore"
)
@login_required
def restore_question_version(template_id, version_id):
    template = get_owned_template(template_id)
    source = get_template_version(template["_id"], version_id)
    current = get_latest_version(template["_id"])
    version = create_template_version(
        template,
        question_content(source),
        g.user["_id"],
        parent_version_id=current["_id"],
        source_version_id=source["_id"],
        change_note=f"恢复自 v{source['version_no']}",
    )
    flash(
        f"已根据 v{source['version_no']} 创建 v{version['version_no']}；历史版本未被删除。",
        "success",
    )
    return redirect(
        url_for("main.question_template_detail", template_id=template["_id"])
    )


@bp.get("/questions/<template_id>/stats")
@login_required
def question_template_stats(template_id):
    database = mongo.get_db()
    template = get_accessible_template(template_id)
    is_owner = template["owner_id"] == g.user["_id"]
    versions = list(
        database.question_versions.find(
            {"template_id": template["_id"]}
        ).sort("version_no", -1)
    )
    if not versions:
        abort(404)
    selected_version = versions[0]
    version_id = (request.args.get("version") or "").strip()
    if version_id:
        selected_version = get_template_version(template["_id"], version_id)

    usage_query = {"template_id": template["_id"]}
    if not is_owner:
        own_survey_ids = [
            item["_id"]
            for item in database.surveys.find(
                {"owner_id": g.user["_id"]}, {"_id": 1}
            )
        ]
        usage_query["survey_id"] = {"$in": own_survey_ids}
    all_usages = list(database.questions.find(usage_query))
    survey_ids = list({item["survey_id"] for item in all_usages})
    responses = list(
        database.responses.find(
            {
                "survey_id": {"$in": survey_ids},
                "status": "submitted",
            }
        )
    )
    usages = [
        item for item in all_usages if item["version_id"] == selected_version["_id"]
    ]
    stats = logic.compute_template_stats(
        selected_version, usages, responses
    )
    survey_by_id = {
        item["_id"]: item
        for item in database.surveys.find({"_id": {"$in": survey_ids}})
    }
    usage_survey_counts = {}
    for usage in usages:
        usage_survey_counts[usage["survey_id"]] = (
            usage_survey_counts.get(usage["survey_id"], 0) + 1
        )
    survey_views = [
        {
            "survey": survey_by_id[survey_id],
            "usage_count": count,
            "response_count": sum(
                1
                for response in responses
                if response["survey_id"] == survey_id
            ),
            "can_open": survey_by_id[survey_id]["owner_id"] == g.user["_id"],
        }
        for survey_id, count in usage_survey_counts.items()
        if survey_id in survey_by_id
    ]
    return render_template(
        "question_template_stats.html",
        template=template,
        versions=versions,
        selected_version=selected_version,
        stats=stats,
        survey_views=survey_views,
        all_usage_count=len(all_usages),
    )


@bp.route("/surveys/new", methods=["GET"])
@login_required
def new_survey():
    return render_template("survey_form.html", data={})


@bp.post("/surveys")
@login_required
def create_survey():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    deadline, deadline_error = parse_datetime_local(
        request.form.get("deadline", "")
    )
    errors = []
    if not title:
        errors.append("问卷标题不能为空。")
    if len(title) > 200:
        errors.append("问卷标题不能超过 200 个字符。")
    if len(description) > 5000:
        errors.append("问卷说明不能超过 5000 个字符。")
    if deadline_error:
        errors.append(deadline_error)
    if deadline is not None and deadline <= datetime.now(timezone.utc):
        errors.append("截止时间必须晚于当前时间。")
    if errors:
        for error in errors:
            flash(error, "error")
        return render_template(
            "survey_form.html",
            data={
                "title": title,
                "description": description,
                "deadline": request.form.get("deadline", ""),
                "allow_anonymous": checked(request.form, "allow_anonymous"),
                "allow_multiple": checked(request.form, "allow_multiple"),
            },
        )

    now = logic.utcnow()
    result = mongo.get_db().surveys.insert_one(
        {
            "owner_id": g.user["_id"],
            "title": title,
            "description": description,
            "allow_anonymous": checked(request.form, "allow_anonymous"),
            "allow_multiple": checked(request.form, "allow_multiple"),
            "status": "draft",
            "public_id": new_public_id(),
            "created_at": now,
            "updated_at": now,
            "deadline": deadline,
            "published_at": None,
            "closed_at": None,
            "schema_version": 2,
        }
    )
    flash("问卷已创建，请继续添加题目。", "success")
    return redirect(url_for("main.survey_detail", survey_id=result.inserted_id))


@bp.get("/surveys/<survey_id>")
@login_required
def survey_detail(survey_id):
    database = mongo.get_db()
    survey = get_owned_survey(survey_id)
    questions = get_survey_questions(survey["_id"])
    question_views = []
    for question in questions:
        targets = [
            item for item in questions if item["order"] > question["order"]
        ]
        rule_views = [
            {
                "rule": rule,
                "condition": logic.condition_text(question, rule),
                "target": logic.target_text(rule, questions),
            }
            for rule in question.get("jump_rules", [])
        ]
        question_views.append(
            {
                "question": question,
                "targets": targets,
                "rules": rule_views,
                "requirement": logic.requirement_text(question),
                "template": get_question_template(question["template_id"]),
            }
        )
    response_count = database.responses.count_documents(
        {"survey_id": survey["_id"], "status": "submitted"}
    )
    draft_count = database.responses.count_documents(
        {"survey_id": survey["_id"], "status": "in_progress"}
    )
    accepting, note = survey_availability(survey)
    return render_template(
        "survey_detail.html",
        survey=survey,
        question_views=question_views,
        response_count=response_count,
        draft_count=draft_count,
        accepting=accepting,
        availability_note=note,
        public_url=public_survey_url(survey),
        deadline_input=local_input_value(survey.get("deadline")),
    )


@bp.post("/surveys/<survey_id>/settings")
@login_required
def update_survey(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改基础设置。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not title:
        flash("问卷标题不能为空。", "error")
    else:
        mongo.get_db().surveys.update_one(
            {"_id": survey["_id"]},
            {
                "$set": {
                    "title": title,
                    "description": description,
                    "allow_anonymous": checked(
                        request.form, "allow_anonymous"
                    ),
                    "allow_multiple": checked(request.form, "allow_multiple"),
                    "updated_at": logic.utcnow(),
                }
            },
        )
        flash("问卷设置已保存。", "success")
    return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/deadline")
@login_required
def update_deadline(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] == "closed":
        flash("已关闭的问卷不能修改截止时间。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    deadline, error = parse_datetime_local(request.form.get("deadline", ""))
    if error:
        flash(error, "error")
    elif deadline is not None and deadline <= datetime.now(timezone.utc):
        flash("截止时间必须晚于当前时间；如需结束问卷请使用“关闭问卷”。", "error")
    else:
        mongo.get_db().surveys.update_one(
            {"_id": survey["_id"]},
            {"$set": {"deadline": deadline, "updated_at": logic.utcnow()}},
        )
        flash("截止时间已更新。", "success")
    return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/publish")
@login_required
def publish_survey(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("只有草稿问卷可以发布。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    questions = get_survey_questions(survey["_id"])
    errors = []
    if not questions:
        errors.append("至少添加一道题目后才能发布。")
    for question in questions:
        for rule in question.get("jump_rules", []):
            errors.extend(logic.validate_rule(question, rule, questions))
    deadline = survey.get("deadline")
    if deadline is not None and deadline <= datetime.now(timezone.utc):
        errors.append("截止时间已经过去，请先修改截止时间。")
    if errors:
        for error in dict.fromkeys(errors):
            flash(error, "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    now = logic.utcnow()
    mongo.get_db().surveys.update_one(
        {"_id": survey["_id"]},
        {
            "$set": {
                "status": "published",
                "published_at": now,
                "updated_at": now,
            }
        },
    )
    flash("问卷已发布，可以复制链接分发了。", "success")
    return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))


@bp.post("/surveys/<survey_id>/close")
@login_required
def close_survey(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "published":
        flash("只有已发布的问卷可以关闭。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    now = logic.utcnow()
    mongo.get_db().surveys.update_one(
        {"_id": survey["_id"]},
        {
            "$set": {
                "status": "closed",
                "closed_at": now,
                "updated_at": now,
            }
        },
    )
    flash("问卷已关闭，新的填写和未提交答卷将不能继续。", "success")
    return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))


# ---------------------------------------------------------------------------
# 题目与跳转规则
# ---------------------------------------------------------------------------


@bp.post("/surveys/<survey_id>/questions")
@bp.route("/surveys/<survey_id>/questions/new", methods=["GET", "POST"])
@login_required
def new_question(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改题目。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    if request.method == "POST":
        questions = get_survey_questions(survey["_id"])
        order = max((item["order"] for item in questions), default=0) + 1
        document, errors = logic.build_question(
            request.form, survey["_id"], order
        )
        template_name = template_name_from_form(request.form, document)
        if not template_name:
            errors.append("题目名称不能为空。")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=survey,
                data=question_form_data(form=request.form),
                existing=None,
                form_mode="survey_create",
            )
        template, version = create_template(
            document, template_name, g.user["_id"]
        )
        usage = logic.build_question_usage(
            version, survey["_id"], order
        )
        mongo.get_db().questions.insert_one(usage)
        if checked(request.form, "add_to_bank"):
            add_bank_item(template["_id"])
        flash("题目已添加，并在题目库中生成 v1。", "success")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    return render_template(
        "question_form.html",
        survey=survey,
        data=question_form_data(),
        existing=None,
        form_mode="survey_create",
    )


@bp.route(
    "/surveys/<survey_id>/questions/<question_id>/edit",
    methods=["GET", "POST"],
)
@login_required
def edit_question(survey_id, question_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改题目。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    question = get_question(survey["_id"], question_id)
    template = get_question_template(question["template_id"])
    if not can_access_template(template):
        abort(404)
    if request.method == "POST":
        document, errors = logic.build_question(
            request.form,
            survey["_id"],
            question["order"],
            existing=question,
        )
        if document["type"] != question["type"]:
            errors.append("同一道题不能修改题型；如需换题型请新建题目。")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=survey,
                data=question_form_data(form=request.form),
                existing=question,
                form_mode="survey_edit",
                template=template,
            )
        if template["owner_id"] == g.user["_id"]:
            version = create_template_version(
                template,
                document,
                g.user["_id"],
                parent_version_id=question["version_id"],
                change_note=request.form.get("change_note", ""),
            )
            message = "题目新版本已创建；旧问卷内容保持不变。"
        else:
            template, version = create_template(
                document,
                f"{template['name']}（副本）"[:200],
                g.user["_id"],
                source_version_id=question["version_id"],
                source_template_id=template["_id"],
                change_note="复制共享题目后修改",
            )
            message = "已复制为你的题目并生成新版本；原共享题目未被修改。"
        usage_update = {
            **question,
            "template_id": template["_id"],
            "version_id": version["_id"],
            "version_no": version["version_no"],
            **question_content(version),
        }
        mongo.get_db().questions.replace_one(
            {"_id": question["_id"]}, usage_update
        )
        flash(message, "success")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    return render_template(
        "question_form.html",
        survey=survey,
        data=question_form_data(question=question),
        existing=question,
        form_mode="survey_edit",
        template=template,
    )


@bp.route(
    "/surveys/<survey_id>/questions/from-library",
    methods=["GET", "POST"],
)
@login_required
def use_library_question(survey_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改题目。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    scope = request.values.get("scope", "all")
    if request.method == "POST":
        template = get_accessible_template(request.form.get("template_id"))
        version = get_template_version(
            template["_id"], request.form.get("version_id")
        )
        questions = get_survey_questions(survey["_id"])
        order = max((item["order"] for item in questions), default=0) + 1
        usage = logic.build_question_usage(version, survey["_id"], order)
        mongo.get_db().questions.insert_one(usage)
        flash(
            f"已加入「{template['name']}」的 v{version['version_no']}。",
            "success",
        )
        return redirect(
            url_for("main.survey_detail", survey_id=survey["_id"])
        )
    views = accessible_template_views(bank_only=scope == "bank")
    return render_template(
        "question_picker.html",
        survey=survey,
        template_views=views,
        scope=scope,
    )


@bp.post("/surveys/<survey_id>/questions/<question_id>/rules")
@login_required
def add_rule(survey_id, question_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改跳转规则。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    question = get_question(survey["_id"], question_id)
    questions = get_survey_questions(survey["_id"])
    rule, errors = logic.build_jump_rule(question, request.form, questions)
    if errors:
        for error in errors:
            flash(error, "error")
    else:
        mongo.get_db().questions.update_one(
            {"_id": question["_id"]},
            {
                "$push": {"jump_rules": rule},
                "$set": {"updated_at": logic.utcnow()},
            },
        )
        flash("跳转规则已添加。", "success")
    return redirect(
        url_for(
            "main.survey_detail",
            survey_id=survey["_id"],
            _anchor=f"q-{question['_id']}",
        )
    )


@bp.post(
    "/surveys/<survey_id>/questions/<question_id>/rules/<rule_id>/delete"
)
@login_required
def delete_rule(survey_id, question_id, rule_id):
    survey = get_owned_survey(survey_id)
    if survey["status"] != "draft":
        flash("问卷发布后不能再修改跳转规则。", "error")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    question = get_question(survey["_id"], question_id)
    mongo.get_db().questions.update_one(
        {"_id": question["_id"]},
        {
            "$pull": {"jump_rules": {"_id": parse_object_id(rule_id)}},
            "$set": {"updated_at": logic.utcnow()},
        },
    )
    flash("跳转规则已删除。", "success")
    return redirect(
        url_for(
            "main.survey_detail",
            survey_id=survey["_id"],
            _anchor=f"q-{question['_id']}",
        )
    )


# ---------------------------------------------------------------------------
# 公开问卷与填写
# ---------------------------------------------------------------------------


@bp.get("/s/<public_id>")
def public_survey(public_id):
    database = mongo.get_db()
    survey = database.surveys.find_one({"public_id": public_id})
    if survey is None or survey["status"] == "draft":
        abort(404)
    accepting, note = survey_availability(survey)
    questions = get_survey_questions(survey["_id"])
    submitted = False
    if g.user:
        submitted = (
            database.responses.count_documents(
                {
                    "survey_id": survey["_id"],
                    "user_id": g.user["_id"],
                    "status": "submitted",
                }
            )
            > 0
        )
    return render_template(
        "public_survey.html",
        survey=survey,
        accepting=accepting,
        availability_note=note,
        question_count=len(questions),
        submitted=submitted,
        login_next=request.path,
    )


@bp.post("/s/<public_id>/start")
@login_required
def start_response(public_id):
    database = mongo.get_db()
    survey = database.surveys.find_one({"public_id": public_id})
    if survey is None or survey["status"] == "draft":
        abort(404)
    accepting, note = survey_availability(survey)
    if not accepting:
        flash(note, "error")
        return redirect(url_for("main.public_survey", public_id=public_id))

    existing = database.responses.find_one(
        {
            "survey_id": survey["_id"],
            "user_id": g.user["_id"],
            "status": "in_progress",
        },
        sort=[("started_at", -1)],
    )
    if existing:
        flash("已为你继续上次未提交的答卷。", "success")
        return redirect(url_for("main.fill_response", response_id=existing["_id"]))

    if not survey["allow_multiple"]:
        submitted = database.responses.count_documents(
            {
                "survey_id": survey["_id"],
                "user_id": g.user["_id"],
                "status": "submitted",
            }
        )
        if submitted:
            flash("该问卷不允许同一用户重复填写。", "error")
            return redirect(
                url_for("main.public_survey", public_id=public_id)
            )

    questions = get_survey_questions(survey["_id"])
    if not questions:
        flash("问卷没有题目，暂时无法填写。", "error")
        return redirect(url_for("main.public_survey", public_id=public_id))

    now = logic.utcnow()
    try:
        result = database.responses.insert_one(
            {
                "survey_id": survey["_id"],
                "user_id": g.user["_id"],
                "anonymous": survey["allow_anonymous"],
                "status": "in_progress",
                "answers": {},
                "path": [],
                "current_question_id": questions[0]["_id"],
                "started_at": now,
                "updated_at": now,
                "submitted_at": None,
                "schema_version": 2,
            }
        )
    except DuplicateKeyError:
        # 同一用户同时点开两个页面时，唯一部分索引保证只产生一份草稿。
        existing = database.responses.find_one(
            {
                "survey_id": survey["_id"],
                "user_id": g.user["_id"],
                "status": "in_progress",
            }
        )
        return redirect(
            url_for("main.fill_response", response_id=existing["_id"])
        )
    return redirect(
        url_for("main.fill_response", response_id=result.inserted_id)
    )


@bp.get("/fill/<response_id>")
@login_required
def fill_response(response_id):
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    if response["status"] == "submitted":
        return redirect(url_for("main.response_done", response_id=response["_id"]))
    if response.get("current_question_id") is None:
        return redirect(url_for("main.review_response", response_id=response["_id"]))
    return redirect(
        url_for(
            "main.fill_question",
            response_id=response["_id"],
            question_id=response["current_question_id"],
        )
    )


def render_closed_fill(survey, note):
    return render_template(
        "message.html",
        title="暂时无法填写",
        message=note,
        back_url=url_for("main.public_survey", public_id=survey["public_id"]),
        back_text="返回问卷首页",
    ), 409


@bp.get("/fill/<response_id>/q/<question_id>")
@login_required
def fill_question(response_id, question_id):
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    accepting, note = survey_availability(survey)
    if not accepting:
        return render_closed_fill(survey, note)
    if response["status"] == "submitted":
        return redirect(url_for("main.response_done", response_id=response["_id"]))
    question = get_question(survey["_id"], question_id)
    current = response.get("current_question_id")
    if current is None:
        return redirect(url_for("main.review_response", response_id=response["_id"]))
    if current != question["_id"]:
        return redirect(
            url_for(
                "main.fill_question",
                response_id=response["_id"],
                question_id=current,
            )
        )
    questions = get_survey_questions(survey["_id"])
    value = response.get("answers", {}).get(str(question["_id"]))
    return render_template(
        "fill.html",
        survey=survey,
        response=response,
        question=question,
        value=value,
        requirement=logic.requirement_text(question),
        errors=[],
        question_total=len(questions),
    )


@bp.post("/fill/<response_id>/answer")
@login_required
def submit_answer(response_id):
    database = mongo.get_db()
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    accepting, note = survey_availability(survey)
    if not accepting:
        return render_closed_fill(survey, note)
    if response["status"] == "submitted":
        return redirect(url_for("main.response_done", response_id=response["_id"]))

    question_id = parse_object_id(request.form.get("qid"))
    question = get_question(survey["_id"], question_id)
    if response.get("current_question_id") != question["_id"]:
        flash("题目状态已更新，请从当前题目继续。", "error")
        return redirect(url_for("main.fill_response", response_id=response["_id"]))

    action = request.form.get("action", "next")
    skip = action == "skip"
    errors = []
    value = None
    if skip:
        if question["required"]:
            errors.append("必答题不能跳过。")
    else:
        try:
            value = logic.parse_answer(request.form, question)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(logic.validate_answer(question, value))

    if errors:
        questions = get_survey_questions(survey["_id"])
        return (
            render_template(
                "fill.html",
                survey=survey,
                response=response,
                question=question,
                value=(
                    request.form.getlist("answer")
                    if question["type"] == "multiple"
                    else request.form.get("answer", "")
                ),
                requirement=logic.requirement_text(question),
                errors=errors,
                question_total=len(questions),
            ),
            400,
        )

    questions = get_survey_questions(survey["_id"])
    next_id = None if skip else logic.next_question_id(question, value, questions)
    now = logic.utcnow()
    update = {
        "$set": {
            "updated_at": now,
            "current_question_id": next_id,
        },
        "$addToSet": {"path": question["_id"]},
    }
    if not skip:
        update["$set"][f"answers.{question['_id']}"] = value

    result = database.responses.update_one(
        {
            "_id": response["_id"],
            "user_id": g.user["_id"],
            "status": "in_progress",
            "current_question_id": question["_id"],
        },
        update,
    )
    if result.modified_count == 0:
        flash("答卷已被其他页面更新，请从当前题目继续。", "error")
        return redirect(url_for("main.fill_response", response_id=response["_id"]))

    if next_id is None:
        return redirect(
            url_for("main.review_response", response_id=response["_id"])
        )
    return redirect(
        url_for(
            "main.fill_question",
            response_id=response["_id"],
            question_id=next_id,
        )
    )


@bp.get("/fill/<response_id>/review")
@login_required
def review_response(response_id):
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    if response["status"] == "submitted":
        return redirect(url_for("main.response_done", response_id=response["_id"]))
    if response.get("current_question_id") is not None:
        return redirect(url_for("main.fill_response", response_id=response["_id"]))
    accepting, note = survey_availability(survey)
    if not accepting:
        return render_closed_fill(survey, note)
    questions = get_survey_questions(survey["_id"])
    by_id = {item["_id"]: item for item in questions}
    items = []
    for question_id in response.get("path", []):
        question = by_id.get(question_id)
        if question is None:
            continue
        value = response.get("answers", {}).get(str(question_id))
        items.append(
            {
                "question": question,
                "answer": logic.answer_display(question, value),
                "answered": not logic.empty_answer(value),
            }
        )
    items.sort(key=lambda item: item["question"]["order"])
    return render_template(
        "review.html",
        survey=survey,
        response=response,
        items=items,
    )


@bp.post("/fill/<response_id>/submit")
@login_required
def submit_response(response_id):
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    if response["status"] == "submitted":
        return redirect(url_for("main.response_done", response_id=response["_id"]))
    if response.get("current_question_id") is not None:
        return redirect(url_for("main.fill_response", response_id=response["_id"]))
    accepting, note = survey_availability(survey)
    if not accepting:
        return render_closed_fill(survey, note)
    questions = get_survey_questions(survey["_id"])
    errors = logic.validate_for_submit(response, questions)
    if errors:
        for error in dict.fromkeys(errors):
            flash(error, "error")
        return redirect(
            url_for("main.review_response", response_id=response["_id"])
        )
    now = logic.utcnow()
    mongo.get_db().responses.update_one(
        {
            "_id": response["_id"],
            "user_id": g.user["_id"],
            "status": "in_progress",
        },
        {
            "$set": {
                "status": "submitted",
                "submitted_at": now,
                "updated_at": now,
            }
        },
    )
    flash("问卷提交成功，感谢你的填写。", "success")
    return redirect(url_for("main.response_done", response_id=response["_id"]))


@bp.get("/fill/<response_id>/done")
@login_required
def response_done(response_id):
    response = get_fill_response(response_id)
    survey = get_fill_survey(response)
    if response["status"] != "submitted":
        return redirect(url_for("main.fill_response", response_id=response["_id"]))
    return render_template(
        "done.html",
        survey=survey,
        response=response,
        can_retake=survey["allow_multiple"] and survey_availability(survey)[0],
    )


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def submitted_responses(survey_id):
    return list(
        mongo.get_db().responses.find(
            {"survey_id": survey_id, "status": "submitted"}
        )
    )


def render_stats(survey, questions, responses, focus=None):
    stats = [
        logic.compute_question_stats(question, responses) for question in questions
    ]
    if focus is not None:
        stats = [item for item in stats if item["question"]["_id"] == focus["_id"]]
    return render_template(
        "stats.html",
        survey=survey,
        stats=stats,
        response_count=len(responses),
        focus=focus,
        all_question_count=len(questions),
    )


@bp.get("/surveys/<survey_id>/stats")
@login_required
def survey_stats(survey_id):
    survey = get_owned_survey(survey_id)
    questions = get_survey_questions(survey["_id"])
    return render_stats(survey, questions, submitted_responses(survey["_id"]))


@bp.get("/surveys/<survey_id>/stats/<question_id>")
@login_required
def question_stats(survey_id, question_id):
    survey = get_owned_survey(survey_id)
    questions = get_survey_questions(survey["_id"])
    focus = get_question(survey["_id"], question_id)
    return render_stats(
        survey,
        questions,
        submitted_responses(survey["_id"]),
        focus=focus,
    )


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(test_config=None):
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "frontend" / "templates"),
        static_folder=str(PROJECT_ROOT / "frontend" / "static"),
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    mongo.init_app(app)
    app.register_blueprint(bp)

    app.jinja_env.filters["localdt"] = format_local_datetime
    app.jinja_env.filters["dtinput"] = local_input_value
    app.jinja_env.filters["question_type"] = lambda value: logic.QUESTION_TYPES.get(
        value, value
    )
    app.jinja_env.filters["status_label"] = lambda value: logic.STATUS_LABELS.get(
        value, value
    )
    app.jinja_env.filters["answer_display"] = logic.answer_display

    @app.context_processor
    def inject_globals():
        return {
            "current_user": getattr(g, "user", None),
            "csrf_token": csrf_token,
            "question_types": logic.QUESTION_TYPES,
            "status_labels": logic.STATUS_LABELS,
        }

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "error.html",
                code=400,
                title="请求无效",
                message=getattr(error, "description", "请求参数不正确。"),
            ),
            400,
        )

    @app.errorhandler(404)
    def not_found(error):
        return (
            render_template(
                "error.html",
                code=404,
                title="页面不存在",
                message="你访问的页面不存在，或你没有权限查看它。",
            ),
            404,
        )

    return app


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    create_app().run(host="127.0.0.1", port=5000, debug=debug)
