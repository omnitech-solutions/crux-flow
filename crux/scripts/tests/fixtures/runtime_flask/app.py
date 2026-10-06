"""Flask fixture app for the ADR-0075 runtime-introspection harness.

  * ``app``        — a Flask *instance*, introspected via ``app.url_map``.
  * ``create_app`` — a zero-argument factory returning a fresh Flask app.

No ``app.run()`` at module scope, so nothing blocks on import.
"""

from flask import Flask

app = Flask(__name__)


@app.route("/ping")
def ping():
    return "pong"


@app.route("/submit", methods=["POST"])
def submit():
    return "ok"


@app.route("/widgets/<int:wid>")
def widget(wid):
    return str(wid)


def create_app() -> Flask:
    factory_app = Flask("runtime_flask_factory")

    @factory_app.route("/factory")
    def factory_root():
        return "factory"

    return factory_app
