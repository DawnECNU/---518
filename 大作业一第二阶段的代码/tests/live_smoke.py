"""对真实运行中的服务做一次带 CSRF 的完整冒烟检查。

先启动应用，然后运行：
    python tests/live_smoke.py
可用 LIVE_BASE_URL 指定地址，默认 http://127.0.0.1:5000。
"""

import os
import re
import time

import requests


BASE = os.environ.get("LIVE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def csrf(html):
    match = re.search(r'name="_csrf" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("页面中没有找到 CSRF Token")
    return match.group(1)


def expect(response, code=200):
    if response.status_code != code:
        raise RuntimeError(
            f"{response.request.method} {response.url} -> "
            f"{response.status_code}\n{response.text[:800]}"
        )
    return response


def main():
    session = requests.Session()
    username = "smoke_" + str(int(time.time()))

    page = expect(session.get(BASE + "/register"))
    expect(
        session.post(
            BASE + "/register",
            data={
                "_csrf": csrf(page.text),
                "username": username,
                "password": "secret123",
                "confirm_password": "secret123",
            },
        )
    )

    page = expect(session.get(BASE + "/surveys/new"))
    created = session.post(
        BASE + "/surveys",
        data={
            "_csrf": csrf(page.text),
            "title": "真实服务冒烟问卷",
            "description": "由 tests/live_smoke.py 创建",
            "allow_anonymous": "on",
            "allow_multiple": "on",
        },
        allow_redirects=False,
    )
    expect(created, 302)
    survey_id = created.headers["Location"].rstrip("/").split("/")[-1]

    page = expect(session.get(f"{BASE}/surveys/{survey_id}"))
    added = session.post(
        f"{BASE}/surveys/{survey_id}/questions",
        data={
            "_csrf": csrf(page.text),
            "type": "single",
            "title": "你是否能完成这次冒烟测试？",
            "required": "on",
            "options": "能\n不能",
        },
        allow_redirects=False,
    )
    expect(added, 302)

    page = expect(session.get(f"{BASE}/surveys/{survey_id}"))
    expect(
        session.post(
            f"{BASE}/surveys/{survey_id}/publish",
            data={"_csrf": csrf(page.text)},
        )
    )
    page = expect(session.get(f"{BASE}/surveys/{survey_id}"))
    public_match = re.search(
        r'id="public-url" readonly value="([^"]+)"', page.text
    )
    if not public_match:
        raise RuntimeError("发布后没有找到公开链接")
    public_url = public_match.group(1)

    page = expect(session.get(public_url))
    started = session.post(
        public_url + "/start",
        data={"_csrf": csrf(page.text)},
        allow_redirects=False,
    )
    expect(started, 302)
    response_id = started.headers["Location"].rstrip("/").split("/")[-1]

    page = expect(session.get(f"{BASE}/fill/{response_id}"))
    qid = re.search(r'name="qid" value="([^"]+)"', page.text).group(1)
    expect(
        session.post(
            f"{BASE}/fill/{response_id}/answer",
            data={
                "_csrf": csrf(page.text),
                "qid": qid,
                "answer": "A",
                "action": "next",
            },
        )
    )
    page = expect(session.get(f"{BASE}/fill/{response_id}/review"))
    expect(
        session.post(
            f"{BASE}/fill/{response_id}/submit",
            data={"_csrf": csrf(page.text)},
        )
    )
    page = expect(session.get(f"{BASE}/surveys/{survey_id}/stats"))
    if "已提交答卷" not in page.text:
        raise RuntimeError("统计页面缺少答卷摘要")
    print(
        "live smoke ok:",
        {"user": username, "survey_id": survey_id, "response_id": response_id},
    )


if __name__ == "__main__":
    main()
