"""阶段二最终验收脚本，可从项目根目录直接执行。

脚本使用 Flask test client 和独立临时数据库，通过实际页面路由创建问卷、
题目、答卷、版本、共享和题库关系，并直接核对 MongoDB 文档。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

from app import create_app


PASSWORD = "Acceptance123!"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "final_acceptance_results.json"


def object_id_from_location(response):
    return ObjectId(response.headers["Location"].rstrip("/").split("/")[-1])


def response_id_from_location(response):
    return ObjectId(response.headers["Location"].rstrip("/").split("/")[-1])


def main():
    database_name = "survey_phase2_acceptance_" + uuid.uuid4().hex
    app = create_app(
        {
            "TESTING": True,
            "CSRF_ENABLED": False,
            "SECRET_KEY": "final-acceptance-secret",
            "MONGO_URI": os.environ.get(
                "ACCEPTANCE_MONGO_URI", "mongodb://127.0.0.1:27018"
            ),
            "MONGO_DB_NAME": database_name,
        }
    )
    db = app.extensions["mongo_db"]
    alice = app.test_client()
    bob = app.test_client()
    checks = []

    def check(check_id, requirement, action, expected, condition):
        passed = bool(condition)
        checks.append(
            {
                "id": check_id,
                "requirement": requirement,
                "action": action,
                "expected": expected,
                "actual": "符合预期" if passed else "与预期不符",
                "passed": passed,
            }
        )
        if not passed:
            raise AssertionError(f"{check_id}: {expected}")

    def register(client, username):
        response = client.post(
            "/register",
            data={
                "username": username,
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )
        assert response.status_code == 302, response.status_code

    def create_survey(client, title, description="最终验收测试问卷"):
        response = client.post(
            "/surveys",
            data={
                "title": title,
                "description": description,
                "allow_multiple": "on",
            },
        )
        assert response.status_code == 302
        return object_id_from_location(response)

    def add_question(client, survey_id, qtype, title, **fields):
        data = {
            "type": qtype,
            "title": title,
            "required": "on" if fields.pop("required", False) else "",
            "template_name": fields.pop("template_name", title),
            "add_to_bank": "on" if fields.pop("add_to_bank", False) else "",
        }
        for key in (
            "options",
            "min_selections",
            "max_selections",
            "exact_selections",
            "min_length",
            "max_length",
            "min_value",
            "max_value",
        ):
            if key in fields:
                value = fields.pop(key)
                data[key] = "" if value is None else str(value)
        if fields.pop("integer_only", False):
            data["integer_only"] = "on"
        response = client.post(f"/surveys/{survey_id}/questions", data=data)
        assert response.status_code == 302
        question = db.questions.find_one(
            {"survey_id": survey_id, "title": title},
            sort=[("order", -1)],
        )
        assert question is not None
        return question

    def add_rule(client, survey_id, question_id, data):
        response = client.post(
            f"/surveys/{survey_id}/questions/{question_id}/rules",
            data=data,
        )
        assert response.status_code == 302

    def publish(client, survey_id):
        response = client.post(
            f"/surveys/{survey_id}/publish", follow_redirects=True
        )
        assert response.status_code == 200
        survey = db.surveys.find_one({"_id": survey_id})
        assert survey["status"] == "published"
        return survey["public_id"]

    def start(client, public_id):
        response = client.post(f"/s/{public_id}/start")
        assert response.status_code == 302
        return response_id_from_location(response)

    def answer(client, response_id, question_id, value=None, action="next"):
        data = {"qid": str(question_id), "action": action}
        if isinstance(value, list):
            data["answer"] = value
        elif value is not None:
            data["answer"] = str(value)
        return client.post(f"/fill/{response_id}/answer", data=data)

    def submit(client, response_id):
        response = client.post(f"/fill/{response_id}/submit")
        assert response.status_code == 302

    def add_from_library(client, survey_id, template_id, version_id):
        response = client.post(
            f"/surveys/{survey_id}/questions/from-library",
            data={
                "template_id": str(template_id),
                "version_id": str(version_id),
            },
        )
        assert response.status_code == 302
        return db.questions.find_one(
            {
                "survey_id": survey_id,
                "template_id": template_id,
                "version_id": version_id,
            }
        )

    try:
        register(alice, "acceptance_alice")
        register(bob, "acceptance_bob")
        check(
            "ACC-01",
            "阶段一账号",
            "注册 Alice 与 Bob",
            "两个独立会话均注册成功",
            db.users.count_documents({"username": {"$in": ["acceptance_alice", "acceptance_bob"]}}) == 2,
        )

        main_survey = create_survey(
            alice,
            "2026级新生学习与生活调查",
            "覆盖四类题型、必答、限制、跳题、检查与提交的验收问卷",
        )
        q_single = add_question(
            alice,
            main_survey,
            "single",
            "最近一周是否使用校园图书馆",
            options="是\n否",
            required=True,
        )
        q_multiple = add_question(
            alice,
            main_survey,
            "multiple",
            "使用的学习资源",
            options="纸质书\n电子书\n数据库\n自习室",
            required=True,
            min_selections=1,
            max_selections=4,
        )
        q_text = add_question(
            alice,
            main_survey,
            "text",
            "推荐一本专业书",
            required=False,
            min_length=2,
            max_length=50,
        )
        q_number = add_question(
            alice,
            main_survey,
            "number",
            "每周学习时长（小时）",
            required=True,
            min_value=0,
            max_value=168,
            integer_only=True,
        )
        add_rule(
            alice,
            main_survey,
            q_single["_id"],
            {
                "kind": "option_equals",
                "value": "A",
                "target": str(q_number["_id"]),
            },
        )
        check(
            "ACC-02",
            "阶段一题目",
            "创建单选、多选、文本、数字四类题目并设置限制",
            "四道题按 1 至 4 排序，约束和跳转规则写入",
            db.questions.count_documents({"survey_id": main_survey}) == 4
            and len(db.questions.find_one({"_id": q_single["_id"]})["jump_rules"]) == 1,
        )

        main_public_id = publish(alice, main_survey)
        first_response = start(alice, main_public_id)
        jumped = answer(alice, first_response, q_single["_id"], "A")
        check(
            "ACC-03",
            "阶段一条件跳转",
            "第 1 题选择 A",
            "跳过第 2、3 题并直接进入第 4 题",
            jumped.status_code == 302
            and jumped.headers["Location"].endswith(str(q_number["_id"])),
        )
        answer(alice, first_response, q_number["_id"], 12)
        submit(alice, first_response)
        first_path = db.responses.find_one({"_id": first_response})["path"]
        check(
            "ACC-04",
            "阶段一提交",
            "回答第 4 题后提交",
            "状态为 submitted，path 只含第 1、4 题",
            db.responses.find_one({"_id": first_response})["status"] == "submitted"
            and first_path == [q_single["_id"], q_number["_id"]],
        )

        second_response = start(alice, main_public_id)
        answer(alice, second_response, q_single["_id"], "B")
        answer(alice, second_response, q_multiple["_id"], ["A", "C"])
        answer(alice, second_response, q_text["_id"], "数据库系统概念")
        answer(alice, second_response, q_number["_id"], 20)
        submit(alice, second_response)
        second_path = db.responses.find_one({"_id": second_response})["path"]
        check(
            "ACC-05",
            "阶段一完整路径",
            "第二份答卷依次回答四道题",
            "path 包含四道题且答卷提交成功",
            second_path == [
                q_single["_id"],
                q_multiple["_id"],
                q_text["_id"],
                q_number["_id"],
            ],
        )
        stats = alice.get(f"/surveys/{main_survey}/stats")
        stats_text = stats.data.decode("utf-8")
        check(
            "ACC-06",
            "阶段一统计",
            "打开主问卷整卷统计",
            "页面显示 2 份提交答卷且数字平均值 16",
            stats.status_code == 200
            and "<strong>2</strong>" in stats_text
            and "16.0" in stats_text,
        )

        create_number_template = alice.post(
            "/questions/new",
            data={
                "type": "number",
                "template_name": "跨问卷年龄验收题",
                "title": "你的年龄",
                "required": "on",
                "min_value": "0",
                "max_value": "120",
                "integer_only": "on",
                "add_to_bank": "on",
            },
        )
        assert create_number_template.status_code == 302
        age_template_id = object_id_from_location(create_number_template)
        age_template = db.question_templates.find_one({"_id": age_template_id})
        age_v1 = db.question_versions.find_one(
            {"template_id": age_template_id, "version_no": 1}
        )
        check(
            "ACC-07",
            "阶段二题目库",
            "创建年龄题并加入题库",
            "生成逻辑题目、v1 和题库关系",
            age_template is not None
            and age_v1 is not None
            and db.question_bank_items.count_documents(
                {"template_id": age_template_id}
            )
            == 1,
        )

        age_survey_a = create_survey(alice, "年龄题验证问卷 A")
        age_survey_b = create_survey(alice, "年龄题验证问卷 B")
        age_usage_a = add_from_library(
            alice, age_survey_a, age_template_id, age_v1["_id"]
        )
        age_usage_b = add_from_library(
            alice, age_survey_b, age_template_id, age_v1["_id"]
        )
        public_a = publish(alice, age_survey_a)
        public_b = publish(alice, age_survey_b)
        response_a = start(alice, public_a)
        answer(alice, response_a, age_usage_a["_id"], 20)
        submit(alice, response_a)
        response_b = start(alice, public_b)
        answer(alice, response_b, age_usage_b["_id"], 40)
        submit(alice, response_b)
        check(
            "ACC-08",
            "阶段二题目复用",
            "同一模板 v1 加入问卷 A、B",
            "两份问卷拥有独立题目快照并提交 20、40",
            age_usage_a["_id"] != age_usage_b["_id"]
            and age_usage_a["version_id"] == age_v1["_id"]
            and age_usage_b["version_id"] == age_v1["_id"],
        )

        create_v2 = alice.post(
            f"/questions/{age_template_id}/versions/new",
            data={
                "type": "number",
                "title": "请填写你的年龄",
                "required": "on",
                "min_value": "0",
                "max_value": "120",
                "integer_only": "on",
                "change_note": "最终验收创建 v2",
            },
        )
        assert create_v2.status_code == 302
        age_v2 = db.question_versions.find_one(
            {"template_id": age_template_id, "version_no": 2}
        )
        check(
            "ACC-09",
            "阶段二版本固定",
            "题目库创建 v2，并检查 A、B 快照",
            "v2 父版本为 v1，A、B 仍使用 v1 原题干",
            age_v2["parent_version_id"] == age_v1["_id"]
            and db.questions.find_one({"_id": age_usage_a["_id"]})["title"]
            == "你的年龄"
            and db.questions.find_one({"_id": age_usage_b["_id"]})["version_id"]
            == age_v1["_id"],
        )

        age_survey_c = create_survey(alice, "年龄题验证问卷 C")
        age_usage_c = add_from_library(
            alice, age_survey_c, age_template_id, age_v2["_id"]
        )
        public_c = publish(alice, age_survey_c)
        response_c = start(alice, public_c)
        answer(alice, response_c, age_usage_c["_id"], 60)
        submit(alice, response_c)
        cross_stats = alice.get(
            f"/questions/{age_template_id}/stats?version={age_v1['_id']}"
        )
        cross_text = cross_stats.data.decode("utf-8")
        check(
            "ACC-10",
            "阶段二跨问卷统计",
            "按 v1 统计年龄题",
            "包含问卷 A、B，平均值 30，不包含 v2 的问卷 C",
            cross_stats.status_code == 200
            and "年龄题验证问卷 A" in cross_text
            and "年龄题验证问卷 B" in cross_text
            and "年龄题验证问卷 C" not in cross_text
            and "30.0" in cross_text,
        )

        share = alice.post(
            f"/questions/{age_template_id}/share",
            data={"username": "acceptance_bob"},
        )
        check(
            "ACC-11",
            "阶段二共享",
            "Alice 把年龄题共享给 Bob",
            "Bob 可以读取题目并加入自己的题库",
            share.status_code == 302
            and bob.get(f"/questions/{age_template_id}").status_code == 200
            and bob.post(
                f"/questions/{age_template_id}/bank", data={"action": "add"}
            ).status_code
            == 302,
        )

        bob_survey = create_survey(bob, "Bob 共享题目验证问卷")
        bob_usage = add_from_library(
            bob, bob_survey, age_template_id, age_v1["_id"]
        )
        forbidden_version = bob.get(
            f"/questions/{age_template_id}/versions/new"
        )
        check(
            "ACC-12",
            "阶段二共享权限",
            "Bob 尝试创建 Alice 原题的新版本",
            "请求返回 404，原题不被修改",
            forbidden_version.status_code == 404
            and db.question_versions.count_documents(
                {"template_id": age_template_id}
            )
            == 2,
        )

        bob_edit = bob.post(
            f"/surveys/{bob_survey}/questions/{bob_usage['_id']}/edit",
            data={
                "type": "number",
                "title": "请填写你的年龄（Bob 副本）",
                "required": "on",
                "min_value": "18",
                "max_value": "100",
                "integer_only": "on",
                "change_note": "Bob 根据群体调整范围",
            },
        )
        assert bob_edit.status_code == 302
        bob_usage_after = db.questions.find_one({"_id": bob_usage["_id"]})
        bob_template = db.question_templates.find_one(
            {"_id": bob_usage_after["template_id"]}
        )
        bob_version = db.question_versions.find_one(
            {"template_id": bob_usage_after["template_id"]}
        )
        check(
            "ACC-13",
            "阶段二私有副本",
            "Bob 修改共享题目",
            "Bob 获得私有模板，原题仍只有 v1、v2",
            bob_template["owner_id"] == db.users.find_one(
                {"username": "acceptance_bob"}
            )["_id"]
            and bob_version["source_template_id"] == age_template_id
            and db.question_versions.count_documents(
                {"template_id": age_template_id}
            )
            == 2,
        )

        restore = alice.post(
            f"/questions/{age_template_id}/versions/{age_v1['_id']}/restore"
        )
        assert restore.status_code == 302
        age_v3 = db.question_versions.find_one(
            {"template_id": age_template_id, "version_no": 3}
        )
        check(
            "ACC-14",
            "阶段二版本恢复",
            "Alice 根据 v1 恢复旧内容",
            "创建 v3，父版本 v2，来源版本 v1",
            age_v3["parent_version_id"] == age_v2["_id"]
            and age_v3["source_version_id"] == age_v1["_id"]
            and age_v3["title"] == "你的年龄",
        )

        share_id = db.question_shares.find_one(
            {"template_id": age_template_id}
        )["_id"]
        revoke = alice.post(
            f"/questions/{age_template_id}/share/{share_id}/revoke"
        )
        bob_public = publish(bob, bob_survey)
        bob_response = start(bob, bob_public)
        answer(bob, bob_response, bob_usage_after["_id"], 30)
        submit(bob, bob_response)
        check(
            "ACC-15",
            "阶段二撤销共享兼容性",
            "撤销共享后继续填写 Bob 已创建的问卷",
            "已有问卷快照仍可完成提交，Bob 再次读取原题返回 404",
            revoke.status_code == 302
            and bob.get(f"/questions/{age_template_id}").status_code == 404
            and db.responses.find_one({"_id": bob_response})["status"]
            == "submitted",
        )

        usage_page = alice.get(f"/questions/{age_template_id}")
        check(
            "ACC-16",
            "阶段二使用位置",
            "Alice 打开年龄题详情",
            "页面显示使用问卷和版本历史",
            usage_page.status_code == 200
            and "年龄题验证问卷 A" in usage_page.data.decode("utf-8")
            and "恢复自 v1" in usage_page.data.decode("utf-8"),
        )

        main_survey_doc = db.surveys.find_one({"_id": main_survey})
        edit_published = alice.post(
            f"/surveys/{main_survey}/questions/{q_single['_id']}/edit",
            data={
                "type": "single",
                "title": "不应生效的标题",
                "required": "on",
                "options": "是\n否",
            },
        )
        check(
            "ACC-17",
            "阶段一发布锁定",
            "发布后尝试修改主问卷题目",
            "请求被重定向，题目仍保留原题干",
            main_survey_doc["status"] == "published"
            and edit_published.status_code == 302
            and db.questions.find_one({"_id": q_single["_id"]})["title"]
            == "最近一周是否使用校园图书馆",
        )

        result = {
            "database": database_name,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "users": ["acceptance_alice", "acceptance_bob"],
            "main_survey": "2026级新生学习与生活调查",
            "generated_surveys": [
                "年龄题验证问卷 A",
                "年龄题验证问卷 B",
                "年龄题验证问卷 C",
                "Bob 共享题目验证问卷",
            ],
            "checks": checks,
            "passed": sum(1 for item in checks if item["passed"]),
            "failed": sum(1 for item in checks if not item["passed"]),
            "original_template_versions": db.question_versions.count_documents(
                {"template_id": age_template_id}
            ),
            "bob_private_template_id": str(bob_template["_id"]),
            "age_v1_average": 30,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        mongo_client = app.extensions["mongo_client"]
        mongo_client.drop_database(database_name)
        mongo_client.close()


if __name__ == "__main__":
    main()
