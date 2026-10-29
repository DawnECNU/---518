"""MongoDB 连接、集合校验规则、索引和阶段二数据迁移。"""

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError

from . import survey_logic as logic
from .schema import COLLECTION_VALIDATORS


def init_app(app):
    """连接 MongoDB，并把 schema validator 与索引安装到数据库。"""
    try:
        client = MongoClient(
            app.config["MONGO_URI"],
            tz_aware=True,
            serverSelectionTimeoutMS=3000,
            uuidRepresentation="standard",
        )
        client.admin.command("ping")
        database = client[app.config["MONGO_DB_NAME"]]
        install_schemas(database)
        install_indexes(database)
        migrate_legacy_questions(database)
    except PyMongoError as exc:
        raise RuntimeError(
            "无法连接或初始化 MongoDB。请先启动 MongoDB，并检查 MONGO_URI 配置。"
        ) from exc

    app.extensions["mongo_client"] = client
    app.extensions["mongo_db"] = database


def get_db():
    from flask import current_app

    return current_app.extensions["mongo_db"]


def install_schemas(database):
    existing = set(database.list_collection_names())
    for name, validator in COLLECTION_VALIDATORS.items():
        options = {
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
        if name in existing:
            database.command({"collMod": name, **options})
        else:
            try:
                database.create_collection(name, **options)
            except CollectionInvalid:
                # 并发启动时另一个进程可能已经创建了集合。
                database.command({"collMod": name, **options})


def install_indexes(database):
    database.users.create_index(
        [("username", ASCENDING)], unique=True, name="uniq_username"
    )
    database.surveys.create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)],
        name="owner_created",
    )
    database.surveys.create_index(
        [("public_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"public_id": {"$type": "string"}},
        name="uniq_public_id",
    )
    database.questions.create_index(
        [("survey_id", ASCENDING), ("order", ASCENDING)],
        unique=True,
        name="survey_order",
    )
    database.questions.create_index(
        [("template_id", ASCENDING), ("version_id", ASCENDING)],
        name="question_template_version",
    )
    database.question_templates.create_index(
        [("owner_id", ASCENDING), ("updated_at", DESCENDING)],
        name="template_owner_updated",
    )
    database.question_versions.create_index(
        [("template_id", ASCENDING), ("version_no", ASCENDING)],
        unique=True,
        name="template_version_no",
    )
    database.question_versions.create_index(
        [("parent_version_id", ASCENDING)],
        name="version_parent",
    )
    database.question_shares.create_index(
        [("template_id", ASCENDING), ("shared_with_id", ASCENDING)],
        unique=True,
        name="unique_question_share",
    )
    database.question_shares.create_index(
        [("shared_with_id", ASCENDING), ("created_at", DESCENDING)],
        name="share_recipient_created",
    )
    database.question_bank_items.create_index(
        [("user_id", ASCENDING), ("template_id", ASCENDING)],
        unique=True,
        name="unique_bank_item",
    )
    database.responses.create_index(
        [("survey_id", ASCENDING), ("status", ASCENDING)],
        name="survey_status",
    )
    database.responses.create_index(
        [("survey_id", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)],
        name="survey_user_status",
    )
    database.responses.create_index(
        [("user_id", ASCENDING), ("updated_at", DESCENDING)],
        name="user_updated",
    )


def migrate_legacy_questions(database):
    """把阶段一问卷题目补齐为可追溯的模板与 v1 版本。

    题目 usage 仍保留原来的 _id，因此历史答卷中的 answers 字段和 path
    不需要改写。迁移可重复执行，只会处理缺少 template_id 的题目。
    """
    legacy_questions = database.questions.find(
        {"template_id": {"$exists": False}}
    )
    for question in legacy_questions:
        survey = database.surveys.find_one({"_id": question["survey_id"]})
        if survey is None:
            continue
        now = logic.utcnow()
        template_id = ObjectId()
        version_id = ObjectId()
        created_at = question.get("created_at", now)
        database.question_templates.insert_one(
            {
                "_id": template_id,
                "owner_id": survey["owner_id"],
                "name": question["title"][:200],
                "created_at": created_at,
                "updated_at": question.get("updated_at", created_at),
                "schema_version": 2,
            }
        )
        database.question_versions.insert_one(
            {
                "_id": version_id,
                "template_id": template_id,
                "version_no": 1,
                "parent_version_id": None,
                "source_version_id": None,
                "source_template_id": None,
                "type": question["type"],
                "title": question["title"],
                "required": question["required"],
                "options": question.get("options", []),
                "constraints": question.get("constraints", {}),
                "created_by": survey["owner_id"],
                "created_at": created_at,
                "change_note": "从阶段一问卷题目迁移",
                "schema_version": 2,
            }
        )
        database.questions.update_one(
            {
                "_id": question["_id"],
                "template_id": {"$exists": False},
            },
            {
                "$set": {
                    "template_id": template_id,
                    "version_id": version_id,
                    "version_no": 1,
                }
            },
        )
