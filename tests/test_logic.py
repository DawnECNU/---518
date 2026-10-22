"""跳转与校验领域逻辑单元测试。"""

import unittest

from bson import ObjectId

from backend.survey_logic import (
    next_question_id,
    rule_matches,
    validate_answer,
)


def question(qid, order, qtype="single", **overrides):
    base = {
        "_id": qid,
        "order": order,
        "type": qtype,
        "title": f"题目 {order}",
        "required": True,
        "options": [
            {"key": "A", "label": "A项"},
            {"key": "B", "label": "B项"},
            {"key": "C", "label": "C项"},
        ],
        "constraints": {},
        "jump_rules": [],
    }
    base.update(overrides)
    return base


class SurveyLogicTest(unittest.TestCase):
    def test_single_option_and_number_jump(self):
        q1 = question(ObjectId(), 1)
        q2 = question(ObjectId(), 2, "text")
        q3 = question(ObjectId(), 3, "number")
        q4 = question(ObjectId(), 4)
        q1["jump_rules"] = [
            {
                "condition": {"kind": "option_equals", "value": "A"},
                "target": q4["_id"],
            }
        ]
        questions = [q1, q2, q3, q4]
        self.assertEqual(next_question_id(q1, "A", questions), q4["_id"])
        self.assertEqual(next_question_id(q1, "B", questions), q2["_id"])

        q3["jump_rules"] = [
            {
                "condition": {"kind": "number_range", "min": 18, "max": 60},
                "target": q4["_id"],
            }
        ]
        self.assertEqual(next_question_id(q3, 30, questions), q4["_id"])
        self.assertIsNone(next_question_id(q4, "A", questions))

    def test_multiple_count_and_contains(self):
        q1 = question(ObjectId(), 1, "multiple")
        q2 = question(ObjectId(), 2)
        rule_count = {
            "condition": {
                "kind": "selection_count",
                "operator": "gte",
                "count": 2,
            },
            "target": q2["_id"],
        }
        self.assertTrue(rule_matches(rule_count, ["A", "B"]))
        self.assertFalse(rule_matches(rule_count, ["A"]))
        self.assertTrue(
            rule_matches(
                {
                    "condition": {"kind": "option_contains", "value": "C"},
                    "target": q2["_id"],
                },
                ["A", "C"],
            )
        )

    def test_multiple_choice_validation(self):
        q = question(
            ObjectId(),
            1,
            "multiple",
            constraints={
                "min_selections": 2,
                "max_selections": 3,
                "exact_selections": None,
            },
        )
        self.assertEqual(validate_answer(q, ["A"]), ["本题至少选择 2 个选项。"])
        self.assertEqual(validate_answer(q, ["A", "B"]), [])

        exact = question(
            ObjectId(),
            2,
            "multiple",
            constraints={
                "min_selections": None,
                "max_selections": None,
                "exact_selections": 2,
            },
        )
        self.assertEqual(validate_answer(exact, ["A"]), ["本题必须选择 2 个选项。"])

    def test_text_and_number_validation(self):
        text = question(
            ObjectId(),
            1,
            "text",
            constraints={"min_length": 2, "max_length": 4},
        )
        self.assertIn("至少需要 2 个字符", validate_answer(text, "a")[0])
        number = question(
            ObjectId(),
            2,
            "number",
            constraints={
                "min_value": 0,
                "max_value": 120,
                "integer_only": True,
            },
        )
        self.assertEqual(validate_answer(number, 30), [])
        self.assertIn("只接受整数", validate_answer(number, 3.5)[0])
        self.assertIn("不能大于 120", validate_answer(number, 130)[0])


if __name__ == "__main__":
    unittest.main()
