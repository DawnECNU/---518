"""MongoDB 集合校验规则。

这些规则由 db.py 在应用启动时安装到 MongoDB，数据库本身也会拒绝不合法的
文档。schema_version 为后续阶段的数据迁移预留。
"""

SCHEMA_VERSION = 1

COLLECTION_VALIDATORS = {
    "users": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["username", "password_hash", "created_at", "schema_version"],
            "properties": {
                "username": {"bsonType": "string", "minLength": 3, "maxLength": 32},
                "password_hash": {"bsonType": "string", "minLength": 20},
                "created_at": {"bsonType": "date"},
                "schema_version": {"bsonType": "int", "enum": [SCHEMA_VERSION]},
            },
        }
    },
    "surveys": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "owner_id",
                "title",
                "description",
                "allow_anonymous",
                "allow_multiple",
                "status",
                "public_id",
                "created_at",
                "updated_at",
                "deadline",
                "published_at",
                "closed_at",
                "schema_version",
            ],
            "properties": {
                "owner_id": {"bsonType": "objectId"},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "description": {"bsonType": "string", "maxLength": 5000},
                "allow_anonymous": {"bsonType": "bool"},
                "allow_multiple": {"bsonType": "bool"},
                "status": {
                    "bsonType": "string",
                    "enum": ["draft", "published", "closed"],
                },
                "public_id": {
                    "bsonType": ["string", "null"],
                    "minLength": 8,
                    "maxLength": 32,
                },
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "deadline": {"bsonType": ["date", "null"]},
                "published_at": {"bsonType": ["date", "null"]},
                "closed_at": {"bsonType": ["date", "null"]},
                "schema_version": {"bsonType": "int", "enum": [SCHEMA_VERSION]},
            },
        }
    },
    "questions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "survey_id",
                "order",
                "type",
                "title",
                "required",
                "options",
                "constraints",
                "jump_rules",
                "created_at",
                "updated_at",
                "schema_version",
            ],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                "order": {"bsonType": "int", "minimum": 1},
                "type": {
                    "bsonType": "string",
                    "enum": ["single", "multiple", "text", "number"],
                },
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                "required": {"bsonType": "bool"},
                "options": {
                    "bsonType": "array",
                    "maxItems": 20,
                    "items": {
                        "bsonType": "object",
                        "required": ["key", "label"],
                        "properties": {
                            "key": {"bsonType": "string", "minLength": 1},
                            "label": {"bsonType": "string", "minLength": 1},
                        },
                    },
                },
                "constraints": {"bsonType": "object"},
                "jump_rules": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["_id", "condition", "target", "created_at"],
                        "properties": {
                            "_id": {"bsonType": "objectId"},
                            "condition": {"bsonType": "object"},
                            "target": {"bsonType": ["objectId", "null"]},
                            "created_at": {"bsonType": "date"},
                        },
                    },
                },
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "schema_version": {"bsonType": "int", "enum": [SCHEMA_VERSION]},
            },
        }
    },
    "responses": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "survey_id",
                "user_id",
                "anonymous",
                "status",
                "answers",
                "path",
                "current_question_id",
                "started_at",
                "updated_at",
                "submitted_at",
                "schema_version",
            ],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                # user_id 仅用于登录校验与“是否允许重复填写”，不会出现在统计结果中。
                "user_id": {"bsonType": "objectId"},
                "anonymous": {"bsonType": "bool"},
                "status": {
                    "bsonType": "string",
                    "enum": ["in_progress", "submitted"],
                },
                "answers": {"bsonType": "object"},
                "path": {
                    "bsonType": "array",
                    "items": {"bsonType": "objectId"},
                },
                "current_question_id": {"bsonType": ["objectId", "null"]},
                "started_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "submitted_at": {"bsonType": ["date", "null"]},
                "schema_version": {"bsonType": "int", "enum": [SCHEMA_VERSION]},
            },
        }
    },
}
