from fastapi import FastAPI

from great_agent.config import AppSettings


def create_app(settings: AppSettings) -> FastAPI:
    app = FastAPI(title="GREAT Agent", version="0.1.0")
    app.state.settings = settings

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local"}

    return app
