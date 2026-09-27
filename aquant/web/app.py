from __future__ import annotations

from flask import Flask

from aquant.web.routes import bp


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.register_blueprint(bp)
    return app
