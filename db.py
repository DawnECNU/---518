"""MongoDB 连接、集合校验规则和索引初始化。"""

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError

from schema import COLLECTION_VALIDATORS


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
