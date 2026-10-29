"""问卷领域逻辑：题目构造、答案校验、跳转、统计。

这一层不依赖 Flask 和数据库连接，方便单元测试，也避免把规则写死在路由中。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from bson import ObjectId

QUESTION_TYPES = {
    "single": "单选题",
    "multiple": "多选题",
    "text": "文本填空题",
    "number": "数字填空题",
}

STATUS_LABELS = {
    "draft": "草稿",
    "published": "已发布",
    "closed": "已关闭",
}

OPERATORS = {
    "eq": "等于",
    "gte": "至少",
    "lte": "至多",
}

OPTION_KINDS = {"option_equals", "option_contains"}

CHANGE_NOTE_DEFAULT = "创建新版本"


def utcnow():
    return datetime.now(timezone.utc)


def as_object_id(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def option_key(index):
    """0 -> A, 25 -> Z, 26 -> AA。"""
    index += 1
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def empty_answer(value):
    return value is None or value == "" or value == []


def _int_value(raw, label, errors, minimum=None):
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{label}必须是整数。")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{label}不能小于 {minimum}。")
    return value


def _float_value(raw, label, errors):
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{label}必须是数字。")
        return None
    if not math.isfinite(value):
        errors.append(f"{label}必须是有限数字。")
        return None
    return value


def parse_option_labels(raw):
    labels = []
    for line in (raw or "").splitlines():
        label = line.strip()
        if label:
            labels.append(label)
    return labels


def merge_option_keys(labels, existing_options=None):
    """编辑题目时尽量复用原选项 key，避免破坏已有跳转规则。"""
    key_by_label = {}
    used = set()
    for option in existing_options or []:
        key_by_label.setdefault(option["label"], option["key"])

    options = []
    next_index = 0
    for label in labels:
        key = key_by_label.get(label)
        if not key or key in used:
            while option_key(next_index) in used:
                next_index += 1
            key = option_key(next_index)
        used.add(key)
        options.append({"key": key, "label": label})
    return options


def build_question(form, survey_id, order, now=None, existing=None):
    """从表单构造题目文档，返回 (document, errors)。"""
    now = now or utcnow()
    errors = []
    qtype = (form.get("type") or "").strip()
    if qtype not in QUESTION_TYPES:
        errors.append("请选择有效的题目类型。")
    title = (form.get("title") or "").strip()
    if not title:
        errors.append("题目标题不能为空。")
    elif len(title) > 500:
        errors.append("题目标题不能超过 500 个字符。")
    required = form.get("required") == "on"

    options = []
    constraints = {}
    if qtype in ("single", "multiple"):
        labels = parse_option_labels(form.get("options"))
        if len(labels) < 2:
            errors.append("选择题至少需要 2 个选项。")
        if len(labels) > 20:
            errors.append("选择题最多支持 20 个选项。")
        if len(set(labels)) != len(labels):
            errors.append("选项内容不能重复。")
        old_options = existing.get("options", []) if existing else []
        options = merge_option_keys(labels[:20], old_options)

    if qtype == "multiple":
        min_sel = _int_value(
            form.get("min_selections"), "最少选择数量", errors, minimum=0
        )
        max_sel = _int_value(
            form.get("max_selections"), "最多选择数量", errors, minimum=0
        )
        exact_sel = _int_value(
            form.get("exact_selections"), "必须选择数量", errors, minimum=0
        )
        if min_sel is not None and max_sel is not None and min_sel > max_sel:
            errors.append("最少选择数量不能大于最多选择数量。")
        if exact_sel is not None:
            if exact_sel > len(options):
                errors.append("必须选择数量不能超过选项总数。")
            if min_sel is not None and exact_sel < min_sel:
                errors.append("必须选择数量不能小于最少选择数量。")
            if max_sel is not None and exact_sel > max_sel:
                errors.append("必须选择数量不能大于最多选择数量。")
            if required and exact_sel == 0:
                errors.append("必答多选题的“必须选择数量”不能为 0。")
        constraints = {
            "min_selections": min_sel,
            "max_selections": max_sel,
            "exact_selections": exact_sel,
        }
    elif qtype == "text":
        min_len = _int_value(
            form.get("min_length"), "最少字数", errors, minimum=0
        )
        max_len = _int_value(
            form.get("max_length"), "最多字数", errors, minimum=0
        )
        if min_len is not None and max_len is not None and min_len > max_len:
            errors.append("最少字数不能大于最多字数。")
        constraints = {"min_length": min_len, "max_length": max_len}
    elif qtype == "number":
        min_value = _float_value(form.get("min_value"), "最小值", errors)
        max_value = _float_value(form.get("max_value"), "最大值", errors)
        if (
            min_value is not None
            and max_value is not None
            and min_value > max_value
        ):
            errors.append("最小值不能大于最大值。")
        constraints = {
            "min_value": min_value,
            "max_value": max_value,
            "integer_only": form.get("integer_only") == "on",
        }

    old_rules = existing.get("jump_rules", []) if existing else []
    if existing and qtype != existing.get("type") and old_rules:
        errors.append("该题已有跳转规则，不能修改题型；请先删除相关规则。")
    if qtype in ("single", "multiple"):
        valid_keys = {item["key"] for item in options}
        for rule in old_rules:
            condition = rule.get("condition", {})
            if condition.get("kind") in OPTION_KINDS and condition.get("value") not in valid_keys:
                errors.append("修改选项会破坏已有跳转规则，请保留被规则引用的选项。")
                break

    doc = {
        "_id": existing["_id"] if existing else ObjectId(),
        "survey_id": as_object_id(survey_id),
        "order": order,
        "type": qtype,
        "title": title,
        "required": required,
        "options": options,
        "constraints": constraints,
        "jump_rules": old_rules,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "schema_version": 2,
    }
    return doc, errors


def build_question_version(
    content,
    template_id,
    version_no,
    created_by,
    now=None,
    parent_version_id=None,
    source_version_id=None,
    source_template_id=None,
    change_note=CHANGE_NOTE_DEFAULT,
):
    """从已校验的题目内容生成不可变版本文档。"""
    now = now or utcnow()
    return {
        "_id": ObjectId(),
        "template_id": as_object_id(template_id),
        "version_no": int(version_no),
        "parent_version_id": parent_version_id,
        "source_version_id": source_version_id,
        "source_template_id": source_template_id,
        "type": content["type"],
        "title": content["title"],
        "required": content["required"],
        "options": content.get("options", []),
        "constraints": content.get("constraints", {}),
        "created_by": as_object_id(created_by),
        "created_at": now,
        "change_note": (change_note or CHANGE_NOTE_DEFAULT).strip()[:500],
        "schema_version": 2,
    }


def build_question_usage(version, survey_id, order, now=None):
    """把某个不可变版本加入问卷，并复制一份填写时使用的快照。"""
    now = now or utcnow()
    return {
        "_id": ObjectId(),
        "survey_id": as_object_id(survey_id),
        "template_id": version["template_id"],
        "version_id": version["_id"],
        "version_no": version["version_no"],
        "order": int(order),
        "type": version["type"],
        "title": version["title"],
        "required": version["required"],
        "options": version.get("options", []),
        "constraints": version.get("constraints", {}),
        "jump_rules": [],
        "created_at": now,
        "updated_at": now,
        "schema_version": 2,
    }


def build_jump_rule(question, form, questions, now=None):
    """根据表单构造一条跳转规则。target=None 表示跳转到问卷结束。"""
    now = now or utcnow()
    errors = []
    qtype = question["type"]
    kind = form.get("kind", "")
    condition = None

    if qtype == "single" and kind == "option_equals":
        value = form.get("value", "")
        if value not in {item["key"] for item in question["options"]}:
            errors.append("请选择有效的跳转选项。")
        condition = {"kind": kind, "value": value}
    elif qtype == "multiple" and kind == "option_contains":
        value = form.get("value", "")
        if value not in {item["key"] for item in question["options"]}:
            errors.append("请选择有效的跳转选项。")
        condition = {"kind": kind, "value": value}
    elif qtype == "multiple" and kind == "selection_count":
        operator = form.get("operator", "")
        if operator not in OPERATORS:
            errors.append("请选择有效的数量比较方式。")
        count = _int_value(form.get("count"), "选择数量", errors, minimum=0)
        if count is not None and count > len(question["options"]):
            errors.append("选择数量不能超过选项总数。")
        condition = {"kind": kind, "operator": operator, "count": count}
    elif qtype == "number" and kind == "number_range":
        minimum = _float_value(form.get("min"), "跳转最小值", errors)
        maximum = _float_value(form.get("max"), "跳转最大值", errors)
        if minimum is None and maximum is None:
            errors.append("数值跳转至少需要填写最小值或最大值。")
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append("跳转最小值不能大于跳转最大值。")
        condition = {"kind": kind, "min": minimum, "max": maximum}
    else:
        errors.append("该题不支持所选的跳转条件。")

    target_raw = (form.get("target") or "").strip()
    target = None
    if target_raw != "end":
        target = as_object_id(target_raw)
        target_question = next(
            (item for item in questions if item["_id"] == target), None
        )
        if target_question is None:
            errors.append("请选择有效的跳转目标题目。")
        elif target_question["order"] <= question["order"]:
            errors.append("跳转目标必须位于当前题目之后。")

    rule = {
        "_id": ObjectId(),
        "condition": condition or {},
        "target": target,
        "created_at": now,
    }
    return rule, errors


def validate_rule(question, rule, questions):
    errors = []
    by_id = {item["_id"]: item for item in questions}
    condition = rule.get("condition", {})
    kind = condition.get("kind")
    if kind in OPTION_KINDS:
        if condition.get("value") not in {
            item["key"] for item in question.get("options", [])
        }:
            errors.append("跳转规则引用了不存在的选项。")
    elif kind == "selection_count":
        if condition.get("operator") not in OPERATORS:
            errors.append("跳转规则的数量比较方式无效。")
        count = condition.get("count")
        if not isinstance(count, int) or count < 0:
            errors.append("跳转规则的选择数量无效。")
    elif kind == "number_range":
        minimum = condition.get("min")
        maximum = condition.get("max")
        if minimum is None and maximum is None:
            errors.append("数值跳转规则至少需要一个边界。")
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append("数值跳转规则的最小值大于最大值。")
    else:
        errors.append("未知的跳转条件。")

    target = rule.get("target")
    if target is not None:
        target_question = by_id.get(target)
        if target_question is None:
            errors.append("跳转目标题目不存在。")
        elif target_question["order"] <= question["order"]:
            errors.append("跳转目标必须位于当前题目之后。")
    return errors


def rule_matches(rule, value):
    if empty_answer(value):
        return False
    condition = rule.get("condition", {})
    kind = condition.get("kind")
    if kind == "option_equals":
        return value == condition.get("value")
    if kind == "option_contains":
        return isinstance(value, list) and condition.get("value") in value
    if kind == "selection_count":
        if not isinstance(value, list):
            return False
        count = len(value)
        operator = condition.get("operator")
        expected = condition.get("count")
        return (
            (operator == "eq" and count == expected)
            or (operator == "gte" and count >= expected)
            or (operator == "lte" and count <= expected)
        )
    if kind == "number_range":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        minimum = condition.get("min")
        maximum = condition.get("max")
        return (minimum is None or value >= minimum) and (
            maximum is None or value <= maximum
        )
    return False


def next_question_id(question, value, questions):
    """返回下一题 ID；None 表示问卷结束。

    规则按列表顺序匹配，第一条命中的规则生效；没有命中则按题目顺序进入下一题。
    """
    ordered = sorted(questions, key=lambda item: item["order"])
    by_id = {item["_id"]: item for item in ordered}
    for rule in question.get("jump_rules", []):
        if rule_matches(rule, value):
            target = rule.get("target")
            if target is None:
                return None
            if target in by_id and by_id[target]["order"] > question["order"]:
                return target
    for index, item in enumerate(ordered):
        if item["_id"] == question["_id"]:
            return ordered[index + 1]["_id"] if index + 1 < len(ordered) else None
    return None


def parse_answer(form, question):
    qtype = question["type"]
    if qtype == "single":
        return (form.get("answer") or "").strip() or None
    if qtype == "multiple":
        values = []
        for value in form.getlist("answer"):
            value = value.strip()
            if value and value not in values:
                values.append(value)
        return values
    if qtype == "text":
        return (form.get("answer") or "").strip()
    if qtype == "number":
        raw = (form.get("answer") or "").strip()
        if raw == "":
            return None
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("请输入有效数字。")
        if question["constraints"].get("integer_only") and not value.is_integer():
            raise ValueError("此题只接受整数。")
        return int(value) if value.is_integer() else value
    return None


def validate_answer(question, value):
    errors = []
    required = question.get("required", False)
    if empty_answer(value):
        if required:
            errors.append("本题为必答题，请填写后再继续。")
        return errors

    qtype = question["type"]
    options = question.get("options", [])
    valid_keys = {item["key"] for item in options}
    constraints = question.get("constraints", {})

    if qtype == "single":
        if value not in valid_keys:
            errors.append("请选择有效选项。")
    elif qtype == "multiple":
        if not isinstance(value, list):
            errors.append("多选题答案格式无效。")
            return errors
        if any(item not in valid_keys for item in value):
            errors.append("答案中包含无效选项。")
        if len(set(value)) != len(value):
            errors.append("多选题答案不能重复。")
        count = len(value)
        exact = constraints.get("exact_selections")
        minimum = constraints.get("min_selections")
        maximum = constraints.get("max_selections")
        if exact is not None and count != exact:
            errors.append(f"本题必须选择 {exact} 个选项。")
        if minimum is not None and count < minimum:
            errors.append(f"本题至少选择 {minimum} 个选项。")
        if maximum is not None and count > maximum:
            errors.append(f"本题最多选择 {maximum} 个选项。")
    elif qtype == "text":
        if not isinstance(value, str):
            errors.append("文本答案格式无效。")
            return errors
        length = len(value)
        minimum = constraints.get("min_length")
        maximum = constraints.get("max_length")
        if minimum is not None and length < minimum:
            errors.append(f"文本至少需要 {minimum} 个字符。")
        if maximum is not None and length > maximum:
            errors.append(f"文本最多允许 {maximum} 个字符。")
    elif qtype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append("数字答案格式无效。")
            return errors
        if constraints.get("integer_only") and not float(value).is_integer():
            errors.append("本题只接受整数。")
        minimum = constraints.get("min_value")
        maximum = constraints.get("max_value")
        if minimum is not None and value < minimum:
            errors.append(f"数值不能小于 {minimum:g}。")
        if maximum is not None and value > maximum:
            errors.append(f"数值不能大于 {maximum:g}。")
    else:
        errors.append("未知题型。")
    return errors


def validate_for_submit(response, questions):
    errors = []
    by_id = {item["_id"]: item for item in questions}
    visited = [by_id[item] for item in response.get("path", []) if item in by_id]
    if not visited:
        errors.append("答卷中没有任何题目记录。")
    for question in sorted(visited, key=lambda item: item["order"]):
        key = str(question["_id"])
        value = response.get("answers", {}).get(key)
        errors.extend(validate_answer(question, value))
    return errors


def option_label(question, key):
    for item in question.get("options", []):
        if item["key"] == key:
            return item["label"]
    return key


def answer_display(question, value):
    if empty_answer(value):
        return "（未回答）"
    if question["type"] == "single":
        return option_label(question, value)
    if question["type"] == "multiple":
        return "、".join(option_label(question, item) for item in value)
    if question["type"] == "number":
        return f"{value:g}" if isinstance(value, float) else str(value)
    return str(value)


def condition_text(question, rule):
    condition = rule.get("condition", {})
    kind = condition.get("kind")
    if kind == "option_equals":
        return f"选择「{option_label(question, condition.get('value'))}」"
    if kind == "option_contains":
        return f"包含「{option_label(question, condition.get('value'))}」"
    if kind == "selection_count":
        return (
            f"选择数量{OPERATORS.get(condition.get('operator'), '')} "
            f"{condition.get('count')} 个"
        )
    if kind == "number_range":
        minimum = condition.get("min")
        maximum = condition.get("max")
        if minimum is not None and maximum is not None:
            return f"数值在 {minimum:g} ~ {maximum:g} 之间"
        if minimum is not None:
            return f"数值 ≥ {minimum:g}"
        return f"数值 ≤ {maximum:g}"
    return "未知条件"


def target_text(rule, questions):
    target = rule.get("target")
    if target is None:
        return "结束问卷"
    for item in questions:
        if item["_id"] == target:
            return f"第 {item['order']} 题「{item['title']}」"
    return "未知题目"


def requirement_text(question):
    parts = ["必答" if question["required"] else "选答"]
    constraints = question.get("constraints", {})
    if question["type"] == "multiple":
        if constraints.get("exact_selections") is not None:
            parts.append(f"必须选 {constraints['exact_selections']} 项")
        else:
            if constraints.get("min_selections") is not None:
                parts.append(f"至少选 {constraints['min_selections']} 项")
            if constraints.get("max_selections") is not None:
                parts.append(f"最多选 {constraints['max_selections']} 项")
    elif question["type"] == "text":
        if constraints.get("min_length") is not None:
            parts.append(f"至少 {constraints['min_length']} 字")
        if constraints.get("max_length") is not None:
            parts.append(f"最多 {constraints['max_length']} 字")
    elif question["type"] == "number":
        if constraints.get("min_value") is not None:
            parts.append(f"最小 {constraints['min_value']:g}")
        if constraints.get("max_value") is not None:
            parts.append(f"最大 {constraints['max_value']:g}")
        if constraints.get("integer_only"):
            parts.append("仅整数")
    return " · ".join(parts)


def compute_question_stats(question, responses):
    key = str(question["_id"])
    values = []
    for response in responses:
        answers = response.get("answers", {})
        if key in answers and not empty_answer(answers[key]):
            values.append(answers[key])

    result = {
        "question": question,
        "answered": len(values),
        "total": len(responses),
    }
    if question["type"] == "single":
        counts = {item["key"]: 0 for item in question["options"]}
        for value in values:
            if value in counts:
                counts[value] += 1
        result["options"] = [
            {
                "key": item["key"],
                "label": item["label"],
                "count": counts[item["key"]],
                "percent": round(
                    counts[item["key"]] * 100 / len(values), 1
                )
                if values
                else 0,
            }
            for item in question["options"]
        ]
    elif question["type"] == "multiple":
        counts = {item["key"]: 0 for item in question["options"]}
        for value in values:
            for selected in value:
                if selected in counts:
                    counts[selected] += 1
        result["options"] = [
            {
                "key": item["key"],
                "label": item["label"],
                "count": counts[item["key"]],
                "percent": round(
                    counts[item["key"]] * 100 / len(values), 1
                )
                if values
                else 0,
            }
            for item in question["options"]
        ]
        result["average_selected"] = (
            round(sum(len(item) for item in values) / len(values), 2)
            if values
            else 0
        )
    elif question["type"] == "text":
        result["values"] = values
    elif question["type"] == "number":
        result["values"] = values
        result["average"] = round(sum(values) / len(values), 2) if values else None
        result["minimum"] = min(values) if values else None
        result["maximum"] = max(values) if values else None
    return result


def compute_template_stats(display_version, usages, responses):
    """跨问卷汇总同一逻辑题目的已提交答案。

    每个问卷题目保存自己的选项 key 到 label 快照。这里逐 usage 映射后再
    汇总，因此选项文字在新旧版本中变化时仍能按可读文字统计。
    """
    qtype = display_version["type"]
    values = []
    selection_counts = []
    answered_count = 0
    label_order = []
    for candidate in [display_version] + [item for item in usages]:
        for option in candidate.get("options", []):
            if option["label"] not in label_order:
                label_order.append(option["label"])

    for response in responses:
        response_answers = response.get("answers", {})
        for usage in usages:
            if usage["survey_id"] != response["survey_id"]:
                continue
            if usage.get("type") != qtype:
                continue
            value = response_answers.get(str(usage["_id"]))
            if empty_answer(value):
                continue
            if qtype == "single":
                values.append(option_label(usage, value))
                answered_count += 1
            elif qtype == "multiple":
                labels = [option_label(usage, item) for item in value]
                values.extend(labels)
                selection_counts.append(len(labels))
                answered_count += 1
            else:
                values.append(value)
                answered_count += 1

    result = {
        "question": display_version,
        "answered": answered_count,
        "total": len(responses),
        "usage_count": len(usages),
    }
    if qtype in ("single", "multiple"):
        counts = {label: 0 for label in label_order}
        for label in values:
            counts[label] = counts.get(label, 0) + 1
        result["options"] = [
            {
                "key": str(index + 1),
                "label": label,
                "count": counts[label],
                "percent": round(
                    counts[label] * 100 / answered_count, 1
                )
                if answered_count
                else 0,
            }
            for index, label in enumerate(counts)
        ]
        if qtype == "multiple":
            result["average_selected"] = (
                round(sum(selection_counts) / len(selection_counts), 2)
                if selection_counts
                else 0
            )
    elif qtype == "text":
        result["values"] = values
    elif qtype == "number":
        numeric_values = [
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        result["values"] = numeric_values
        result["average"] = (
            round(sum(numeric_values) / len(numeric_values), 2)
            if numeric_values
            else None
        )
        result["minimum"] = min(numeric_values) if numeric_values else None
        result["maximum"] = max(numeric_values) if numeric_values else None
    return result
