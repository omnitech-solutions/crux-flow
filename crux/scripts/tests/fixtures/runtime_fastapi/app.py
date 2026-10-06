"""FastAPI fixture app for the ADR-0075 runtime-introspection harness.

Exposes both grammars the executor supports for a `--app module:attr` target:
  * ``app``        — a FastAPI *instance*, introspected directly.
  * ``create_app`` — a zero-argument *factory* returning a fresh instance.
  * ``needs_args`` — a factory requiring an argument; the zero-arg call raises,
                     which the harness turns into an honest no-capture.

Import-time code runs when the harness imports this module — it registers the
routes below and nothing else. There is no ``if __name__ == "__main__"`` server
start, so nothing blocks on import.
"""

from fastapi import FastAPI

app = FastAPI()


@app.get("/users")
def list_users():
    return []


@app.post("/users")
def create_user():
    return {}


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}


def create_app() -> FastAPI:
    factory_app = FastAPI()

    @factory_app.get("/factory-health")
    def health():
        return {"ok": True}

    @factory_app.get("/factory-info")
    def info():
        return {"name": "fixture"}

    return factory_app


def needs_args(config):  # a factory that is NOT zero-argument
    return create_app()
