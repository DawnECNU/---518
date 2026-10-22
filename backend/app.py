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
                        "schema_version": 1,
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
            "schema_version": 1,
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
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=survey,
                data=question_form_data(form=request.form),
                existing=None,
            )
        mongo.get_db().questions.insert_one(document)
        flash("题目已添加。", "success")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    return render_template(
        "question_form.html",
        survey=survey,
        data=question_form_data(),
        existing=None,
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
    if request.method == "POST":
        document, errors = logic.build_question(
            request.form,
            survey["_id"],
            question["order"],
            existing=question,
        )
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "question_form.html",
                survey=survey,
                data=question_form_data(form=request.form),
                existing=question,
            )
        mongo.get_db().questions.replace_one(
            {"_id": question["_id"]}, document
        )
        flash("题目已更新。", "success")
        return redirect(url_for("main.survey_detail", survey_id=survey["_id"]))
    return render_template(
        "question_form.html",
        survey=survey,
        data=question_form_data(question=question),
        existing=question,
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
                "schema_version": 1,
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
