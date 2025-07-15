from quart import Quart

from .routes import register_all_routes


def create_app() -> Quart:
    app = Quart(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "huhu"
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    register_all_routes(app)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000)
