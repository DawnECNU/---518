"""WSGI 入口：flask --app wsgi run 或生产服务器均可使用。"""

from app import create_app

app = create_app()
