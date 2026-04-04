"""Flask web application for Paper Storyteller Center."""
import sys
import os

# Ensure the project root is in path so service modules can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from flask import Flask
from flask_cors import CORS


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "paper-storyteller-dev-key-2026")
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB upload limit
    CORS(app)

    from webapp.routes.api import bp as api_bp
    app.register_blueprint(api_bp)

    from webapp.routes.pages import bp as pages_bp
    app.register_blueprint(pages_bp)

    return app
