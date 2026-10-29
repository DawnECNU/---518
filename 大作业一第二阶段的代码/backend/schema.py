"""MongoDB 集合校验规则。

阶段二增加题目模板、不可变版本、共享和题库集合。阶段一文档继续以
schema_version = 1 保存，阶段二新建文档使用版本 2。
"""

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = [1, SCHEMA_VERSION]

COLLECTION_VALIDATORS = {
    "users": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["username", "password_hash", "created_at", "schema_version"],
            "properties": {
                "username": {"bsonType": "string", "minLength": 3, "maxLength": 32},
                "password_hash": {"bsonType": "string", "minLength": 20},
                "created_at": {"bsonType": "date"},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
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
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
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
                "template_id": {"bsonType": "objectId"},
                "version_id": {"bsonType": "objectId"},
                "version_no": {"bsonType": "int", "minimum": 1},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
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
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
            },
        }
    },
    "question_templates": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "owner_id",
                "name",
                "created_at",
                "updated_at",
                "schema_version",
            ],
            "properties": {
                "owner_id": {"bsonType": "objectId"},
                "name": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
            },
        }
    },
    "question_versions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "template_id",
                "version_no",
                "type",
                "title",
                "required",
                "options",
                "constraints",
                "created_by",
                "created_at",
                "change_note",
                "schema_version",
            ],
            "properties": {
                "template_id": {"bsonType": "objectId"},
                "version_no": {"bsonType": "int", "minimum": 1},
                "parent_version_id": {"bsonType": ["objectId", "null"]},
                "source_version_id": {"bsonType": ["objectId", "null"]},
                "source_template_id": {"bsonType": ["objectId", "null"]},
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
                "created_by": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
                "change_note": {"bsonType": "string", "maxLength": 500},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
            },
        }
    },
    "question_shares": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "template_id",
                "owner_id",
                "shared_with_id",
                "created_at",
                "schema_version",
            ],
            "properties": {
                "template_id": {"bsonType": "objectId"},
                "owner_id": {"bsonType": "objectId"},
                "shared_with_id": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
            },
        }
    },
    "question_bank_items": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["user_id", "template_id", "created_at", "schema_version"],
            "properties": {
                "user_id": {"bsonType": "objectId"},
                "template_id": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
                "schema_version": {
                    "bsonType": "int",
                    "enum": SUPPORTED_SCHEMA_VERSIONS,
                },
            },
        }
    },
}
