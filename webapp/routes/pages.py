"""Page routes — serve the SPA and paper viewer."""
from flask import Blueprint, render_template, send_from_directory, current_app

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    return render_template("index.html")
