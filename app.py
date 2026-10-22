"""Application entry point.

The original ``python app.py`` and ``from app import create_app`` interfaces
remain available while the application code lives in the backend package.
"""

import os

from backend.app import create_app

__all__ = ["create_app"]


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    create_app().run(host="127.0.0.1", port=5000, debug=debug)
