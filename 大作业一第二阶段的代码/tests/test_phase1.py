"""阶段一端到端测试。

运行前需启动 MongoDB。默认使用 mongodb://127.0.0.1:27018，
可通过 TEST_MONGO_URI 指定其他地址。
"""

import os
import re
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app import create_app


class PhaseOneFlowTest(unittest.TestCase):
    def setUp(self):
        database_name = "survey_phase1_test_" + uuid.uuid4().hex
        self.app = create_app(
            {
                "TESTING": True,
                "CSRF_ENABLED": False,
                "SECRET_KEY": "test-secret",
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
        client.drop_database(
            self.app.config["MONGO_DB_NAME"]
        )
        client.close()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def register(self, username="alice", password="secret123"):
        return self.client.post(
            "/register",
            data={
                "username": username,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )

    def create_survey(
        self,
        title="阶段一测试问卷",
        allow_anonymous=False,
        allow_multiple=True,
        deadline=None,
    ):
        data = {
            "title": title,
            "description": "用于自动化测试",
            "allow_multiple": "on" if allow_multiple else "",
        }
        if allow_anonymous:
            data["allow_anonymous"] = "on"
        if deadline:
            data["deadline"] = deadline
        response = self.client.post("/surveys", data=data)
        self.assertEqual(response.status_code, 302)
        return response.headers["Location"].rstrip("/").split("/")[-1]

    def add_question(self, survey_id, qtype, title, **fields):
        data = {
            "type": qtype,
            "title": title,
            "required": "on" if fields.pop("required", False) else "",
        }
        data.update(
            {
                key: "" if value is None else str(value)
                for key, value in fields.items()
                if key
                in {
                    "options",
                    "min_selections",
                    "max_selections",
                    "exact_selections",
                    "min_length",
                    "max_length",
                    "min_value",
                    "max_value",
                }
            }
        )
        if fields.get("integer_only"):
            data["integer_only"] = "on"
        response = self.client.post(
            f"/surveys/{survey_id}/questions", data=data
        )
        self.assertEqual(response.status_code, 302)
        question = self.db.questions.find_one(
            {"survey_id": ObjectId(survey_id), "title": title}
        )
        self.assertIsNotNone(question)
        return question["_id"]

    def add_rule(self, survey_id, question_id, **data):
        response = self.client.post(
            f"/surveys/{survey_id}/questions/{question_id}/rules", data=data
        )
        self.assertEqual(response.status_code, 302)
        return response

    def publish(self, survey_id):
        response = self.client.post(
            f"/surveys/{survey_id}/publish", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        survey = self.db.surveys.find_one({"_id": ObjectId(survey_id)})
        self.assertEqual(survey["status"], "published")
        return survey["public_id"]

    def start(self, public_id):
        response = self.client.post(f"/s/{public_id}/start")
        self.assertEqual(response.status_code, 302)
        return response.headers["Location"].rstrip("/").split("/")[-1]

    def questions(self, survey_id):
        return list(
            self.db.questions.find({"survey_id": ObjectId(survey_id)}).sort(
                "order", 1
            )
        )

    def answer(self, response_id, question_id, value=None, action="next"):
        data = {"qid": str(question_id), "action": action}
        if isinstance(value, list):
            data["answer"] = value
        elif value is not None:
            data["answer"] = str(value)
        return self.client.post(f"/fill/{response_id}/answer", data=data)

    # ------------------------------------------------------------------
    # 测试用例
    # ------------------------------------------------------------------

    def test_register_login_and_owner_isolation(self):
        response = self.register("alice")
        self.assertIn("我的问卷".encode(), response.data)

        survey_id = self.create_survey("Alice 的问卷")
        self.assertEqual(
            self.client.get(f"/surveys/{survey_id}").status_code, 200
        )

        self.client.post("/logout")
        self.register("bob")
        self.assertEqual(
            self.client.get(f"/surveys/{survey_id}").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/surveys/{survey_id}/stats").status_code, 404
        )
        self.assertEqual(
            self.db.surveys.count_documents({"owner_id": {"$exists": True}}), 1
        )

    def test_single_jump_validation_submit_and_stats(self):
        self.register()
        survey_id = self.create_survey()
        q1 = self.add_question(
            survey_id,
            "single",
            "你的性别",
            options="男\n女\n其他",
            required=True,
        )
        q2 = self.add_question(
            survey_id,
            "text",
            "请写一句说明",
            required=True,
            min_length=2,
            max_length=5,
        )
        q3 = self.add_question(
            survey_id,
            "number",
            "你的年龄",
            required=True,
            min_value=0,
            max_value=120,
            integer_only=True,
        )
        q4 = self.add_question(survey_id, "text", "补充意见", required=False)
        self.add_rule(
            survey_id,
            q1,
            kind="option_equals",
            value="A",
            target=str(q4),
        )
        public_id = self.publish(survey_id)

        # 第一次填写：选 A，跳过第 2、3 题，直接到第 4 题。
        response_id = self.start(public_id)
        missing = self.answer(response_id, q1)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("必答题".encode(), missing.data)

        jumped = self.answer(response_id, q1, "A")
        self.assertEqual(jumped.status_code, 302)
        self.assertTrue(jumped.headers["Location"].endswith(str(q4)))
        skipped = self.answer(response_id, q4, action="skip")
        self.assertEqual(skipped.status_code, 302)
        self.assertTrue(skipped.headers["Location"].endswith("/review"))
        submitted = self.client.post(
            f"/fill/{response_id}/submit", follow_redirects=True
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertIn("提交成功".encode(), submitted.data)

        # 第二次填写：选 B，按顺序经过文本题和数字题，逐项校验。
        second_id = self.start(public_id)
        to_q2 = self.answer(second_id, q1, "B")
        self.assertTrue(to_q2.headers["Location"].endswith(str(q2)))
        too_short = self.answer(second_id, q2, "x")
        self.assertEqual(too_short.status_code, 400)
        self.assertIn("至少需要 2 个字符".encode(), too_short.data)
        to_q3 = self.answer(second_id, q2, "hello")
        self.assertTrue(to_q3.headers["Location"].endswith(str(q3)))
        decimal = self.answer(second_id, q3, "12.5")
        self.assertEqual(decimal.status_code, 400)
        out_of_range = self.answer(second_id, q3, "130")
        self.assertEqual(out_of_range.status_code, 400)
        to_q4 = self.answer(second_id, q3, "30")
        self.assertTrue(to_q4.headers["Location"].endswith(str(q4)))
        review = self.answer(second_id, q4, action="skip")
        self.assertTrue(review.headers["Location"].endswith("/review"))
        self.client.post(f"/fill/{second_id}/submit")

        stats = self.client.get(f"/surveys/{survey_id}/stats")
        self.assertEqual(stats.status_code, 200)
        text = stats.data.decode("utf-8")
        self.assertIn("已提交答卷", text)
        self.assertIn("30", text)
        self.assertEqual(
            self.db.responses.count_documents(
                {"survey_id": ObjectId(survey_id), "status": "submitted"}
            ),
            2,
        )

        first = self.db.responses.find_one(
            {"_id": ObjectId(response_id)}
        )
        self.assertEqual(first["path"], [q1, q4])

    def test_multiple_choice_selection_limits(self):
        self.register()
        survey_id = self.create_survey()
        self.assertEqual(
            self.client.get(
                f"/surveys/{survey_id}/questions/new"
            ).status_code,
            200,
        )
        q1 = self.add_question(
            survey_id,
            "multiple",
            "你喜欢的水果",
            options="苹果\n香蕉\n西瓜\n葡萄",
            required=True,
            min_selections=2,
            max_selections=3,
        )
        public_id = self.publish(survey_id)
        response_id = self.start(public_id)
        self.assertEqual(
            self.client.get(
                f"/fill/{response_id}", follow_redirects=True
            ).status_code,
            200,
        )

        one = self.answer(response_id, q1, ["A"])
        self.assertEqual(one.status_code, 400)
        self.assertIn("至少选择 2 个选项".encode(), one.data)

        four = self.answer(response_id, q1, ["A", "B", "C", "D"])
        self.assertEqual(four.status_code, 400)
        self.assertIn("最多选择 3 个选项".encode(), four.data)

        ok = self.answer(response_id, q1, ["A", "C"])
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(ok.headers["Location"].endswith("/review"))
        self.assertEqual(
            self.client.get(f"/fill/{response_id}/review").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/surveys/{survey_id}/stats").status_code, 200
        )

    def test_anonymous_repeat_limit_and_close(self):
        self.register("alice")
        survey_id = self.create_survey(
            allow_anonymous=True, allow_multiple=False
        )
        q1 = self.add_question(
            survey_id, "single", "是否愿意参加", options="愿意\n不愿意"
        )
        public_id = self.publish(survey_id)
        response_id = self.start(public_id)
        self.answer(response_id, q1, "A")
        self.client.post(f"/fill/{response_id}/submit")

        response = self.db.responses.find_one({"_id": ObjectId(response_id)})
        self.assertTrue(response["anonymous"])
        self.assertNotIn("username", response)

        repeat = self.client.post(
            f"/s/{public_id}/start", follow_redirects=True
        )
        self.assertIn("不允许同一用户重复填写".encode(), repeat.data)

        self.client.post(f"/surveys/{survey_id}/close")
        closed = self.client.post(
            f"/s/{public_id}/start", follow_redirects=True
        )
        self.assertIn("问卷已经关闭".encode(), closed.data)

    def test_deadline_blocks_new_response(self):
        self.register()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).astimezone(
            timezone(timedelta(hours=8))
        )
        survey_id = self.create_survey(
            deadline=future.strftime("%Y-%m-%dT%H:%M")
        )
        self.add_question(survey_id, "single", "测试题", options="A项\nB项")
        public_id = self.publish(survey_id)
        self.db.surveys.update_one(
            {"_id": ObjectId(survey_id)},
            {"$set": {"deadline": datetime.now(timezone.utc) - timedelta(minutes=1)}},
        )
        response = self.client.post(
            f"/s/{public_id}/start", follow_redirects=True
        )
        self.assertIn("截止时间".encode(), response.data)


class CSRFTest(unittest.TestCase):
    def test_post_without_token_is_rejected(self):
        database_name = "survey_phase1_csrf_" + uuid.uuid4().hex
        app = create_app(
            {
                "TESTING": True,
                "CSRF_ENABLED": True,
                "SECRET_KEY": "csrf-test-secret",
                "MONGO_URI": os.environ.get(
                    "TEST_MONGO_URI", "mongodb://127.0.0.1:27018"
                ),
                "MONGO_DB_NAME": database_name,
            }
        )
        try:
            client = app.test_client()
            rejected = client.post(
                "/register",
                data={
                    "username": "csrfuser",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
            )
            self.assertEqual(rejected.status_code, 400)

            page = client.get("/register")
            match = re.search(
                rb'name="_csrf" value="([^"]+)"', page.data
            )
            self.assertIsNotNone(match)
            accepted = client.post(
                "/register",
                data={
                    "_csrf": match.group(1).decode(),
                    "username": "csrfuser",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
            )
            self.assertEqual(accepted.status_code, 302)
        finally:
            client = app.extensions["mongo_client"]
            client.drop_database(database_name)
            client.close()


if __name__ == "__main__":
    unittest.main()
