"""阶段二题目复用、版本、共享、题库和跨问卷统计测试。"""

import os
import unittest
import uuid

from bson import ObjectId

from app import create_app


class PhaseTwoFlowTest(unittest.TestCase):
    def setUp(self):
        database_name = "survey_phase2_test_" + uuid.uuid4().hex
        self.app = create_app(
            {
                "TESTING": True,
                "CSRF_ENABLED": False,
                "SECRET_KEY": "phase2-test-secret",
                "MONGO_URI": os.environ.get(
                    "TEST_MONGO_URI", "mongodb://127.0.0.1:27018"
                ),
                "MONGO_DB_NAME": database_name,
            }
        )
        self.db = self.app.extensions["mongo_db"]
        self.client = self.app.test_client()

    def tearDown(self):
        client = self.app.extensions["mongo_client"]
        client.drop_database(self.app.config["MONGO_DB_NAME"])
        client.close()

    def register(self, username):
        response = self.client.post(
            "/register",
            data={
                "username": username,
                "password": "secret123",
                "confirm_password": "secret123",
            },
        )
        self.assertEqual(response.status_code, 302)

    def logout(self):
        self.assertEqual(self.client.post("/logout").status_code, 302)

    def login(self, username):
        response = self.client.post(
            "/login",
            data={"username": username, "password": "secret123"},
        )
        self.assertEqual(response.status_code, 302)

    def create_survey(self, title):
        response = self.client.post(
            "/surveys",
            data={
                "title": title,
                "description": "阶段二测试",
                "allow_multiple": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        return ObjectId(response.headers["Location"].rstrip("/").split("/")[-1])

    def create_number_template(self):
        response = self.client.post(
            "/questions/new",
            data={
                "type": "number",
                "template_name": "常用年龄",
                "title": "你的年龄",
                "required": "on",
                "min_value": "0",
                "max_value": "120",
                "integer_only": "on",
                "add_to_bank": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        return ObjectId(response.headers["Location"].rstrip("/").split("/")[-1])

    def add_from_library(self, survey_id, template_id, version_id):
        response = self.client.post(
            f"/surveys/{survey_id}/questions/from-library",
            data={
                "template_id": str(template_id),
                "version_id": str(version_id),
            },
        )
        self.assertEqual(response.status_code, 302)
        return self.db.questions.find_one(
            {"survey_id": survey_id, "template_id": template_id}
        )

    def publish(self, survey_id):
        response = self.client.post(
            f"/surveys/{survey_id}/publish", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        survey = self.db.surveys.find_one({"_id": survey_id})
        self.assertEqual(survey["status"], "published")
        return survey["public_id"]

    def answer_and_submit(self, public_id, question_id, value):
        started = self.client.post(f"/s/{public_id}/start")
        self.assertEqual(started.status_code, 302)
        response_id = started.headers["Location"].rstrip("/").split("/")[-1]
        answered = self.client.post(
            f"/fill/{response_id}/answer",
            data={"qid": str(question_id), "answer": str(value), "action": "next"},
        )
        self.assertEqual(answered.status_code, 302)
        submitted = self.client.post(f"/fill/{response_id}/submit")
        self.assertEqual(submitted.status_code, 302)

    def test_reuse_versions_restore_usage_and_cross_survey_stats(self):
        self.register("alice")
        template_id = self.create_number_template()
        template = self.db.question_templates.find_one({"_id": template_id})
        version1 = self.db.question_versions.find_one(
            {"template_id": template_id, "version_no": 1}
        )
        self.assertEqual(template["name"], "常用年龄")
        self.assertEqual(template["owner_id"], self.db.users.find_one()["_id"])
        self.assertEqual(
            self.db.question_bank_items.count_documents(
                {"template_id": template_id}
            ),
            1,
        )
        self.assertEqual(self.client.get("/questions").status_code, 200)
        self.assertEqual(
            self.client.get("/questions?scope=bank").status_code, 200
        )
        self.assertEqual(
            self.client.get("/questions/new").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/questions/{template_id}").status_code, 200
        )

        survey1 = self.create_survey("问卷 A")
        self.assertEqual(
            self.client.get(
                f"/surveys/{survey1}/questions/from-library"
            ).status_code,
            200,
        )
        usage1 = self.add_from_library(survey1, template_id, version1["_id"])
        public1 = self.publish(survey1)
        self.answer_and_submit(public1, usage1["_id"], 20)

        survey2 = self.create_survey("问卷 B")
        usage2 = self.add_from_library(survey2, template_id, version1["_id"])
        public2 = self.publish(survey2)
        self.answer_and_submit(public2, usage2["_id"], 40)

        response = self.client.post(
            f"/questions/{template_id}/versions/new",
            data={
                "type": "number",
                "title": "你目前的年龄",
                "required": "on",
                "min_value": "0",
                "max_value": "120",
                "integer_only": "on",
                "change_note": "优化题干",
            },
        )
        self.assertEqual(response.status_code, 302)
        version2 = self.db.question_versions.find_one(
            {"template_id": template_id, "version_no": 2}
        )
        self.assertEqual(version2["parent_version_id"], version1["_id"])
        self.assertEqual(version2["title"], "你目前的年龄")
        self.assertEqual(
            self.db.questions.find_one({"_id": usage1["_id"]})["version_id"],
            version1["_id"],
        )
        self.assertEqual(
            self.client.get(
                f"/questions/{template_id}/versions/new"
            ).status_code,
            200,
        )

        survey3 = self.create_survey("问卷 C")
        usage3 = self.add_from_library(survey3, template_id, version2["_id"])
        public3 = self.publish(survey3)
        self.answer_and_submit(public3, usage3["_id"], 60)

        stats_v1 = self.client.get(
            f"/questions/{template_id}/stats?version={version1['_id']}"
        )
        self.assertEqual(stats_v1.status_code, 200)
        text = stats_v1.data.decode("utf-8")
        self.assertIn("问卷 A", text)
        self.assertIn("问卷 B", text)
        self.assertIn("30.0", text)
        self.assertNotIn("问卷 C", text)

        restore = self.client.post(
            f"/questions/{template_id}/versions/{version1['_id']}/restore"
        )
        self.assertEqual(restore.status_code, 302)
        version3 = self.db.question_versions.find_one(
            {"template_id": template_id, "version_no": 3}
        )
        self.assertEqual(version3["parent_version_id"], version2["_id"])
        self.assertEqual(version3["source_version_id"], version1["_id"])
        self.assertEqual(version3["title"], "你的年龄")

        detail = self.client.get(f"/questions/{template_id}")
        self.assertEqual(detail.status_code, 200)
        detail_text = detail.data.decode("utf-8")
        self.assertIn("恢复自 v1", detail_text)
        self.assertIn("问卷 A", detail_text)

    def test_sharing_and_editing_creates_private_fork(self):
        self.register("alice")
        self.logout()
        self.register("bob")
        self.logout()
        self.login("alice")
        template_id = self.create_number_template()
        original_version = self.db.question_versions.find_one(
            {"template_id": template_id}
        )
        shared = self.client.post(
            f"/questions/{template_id}/share",
            data={"username": "bob"},
        )
        self.assertEqual(shared.status_code, 302)

        self.logout()
        self.login("bob")
        image = self.client.get(f"/questions/{template_id}")
        self.assertEqual(image.status_code, 200)
        self.assertIn("共享给我".encode(), image.data)
        self.assertEqual(
            self.client.get(
                f"/questions/{template_id}/versions/new"
            ).status_code,
            404,
        )

        survey_id = self.create_survey("Bob 的问卷")
        usage = self.add_from_library(
            survey_id, template_id, original_version["_id"]
        )
        edited = self.client.post(
            f"/surveys/{survey_id}/questions/{usage['_id']}/edit",
            data={
                "type": "number",
                "title": "你的年龄（整数）",
                "required": "on",
                "min_value": "18",
                "max_value": "100",
                "integer_only": "on",
                "change_note": "按照班级要求调整",
            },
        )
        self.assertEqual(edited.status_code, 302)
        edited_usage = self.db.questions.find_one({"_id": usage["_id"]})
        self.assertNotEqual(edited_usage["template_id"], template_id)
        private_template = self.db.question_templates.find_one(
            {"_id": edited_usage["template_id"]}
        )
        private_version = self.db.question_versions.find_one(
            {"template_id": private_template["_id"]}
        )
        self.assertEqual(private_template["name"], "常用年龄（副本）")
        self.assertEqual(private_version["source_template_id"], template_id)
        self.assertEqual(private_version["source_version_id"], original_version["_id"])
        self.assertEqual(private_version["title"], "你的年龄（整数）")
        self.assertEqual(
            self.db.question_versions.count_documents(
                {"template_id": template_id}
            ),
            1,
        )
        self.assertEqual(
            self.db.question_versions.find_one(
                {"template_id": template_id}
            )["title"],
            "你的年龄",
        )

        banked = self.client.post(
            f"/questions/{template_id}/bank",
            data={"action": "add"},
        )
        self.assertEqual(banked.status_code, 302)
        self.assertIsNotNone(
            self.db.question_bank_items.find_one(
                {
                    "user_id": private_template["owner_id"],
                    "template_id": template_id,
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
