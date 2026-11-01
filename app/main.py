"""APEX API — app factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import db
from app.routers import (
    actions, ads, agents, approvals, assets, campaigns, dashboard, health,
    leads, qa, questions, seo, webhooks,
)

log = logging.getLogger("apex")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_pool()
    log.info("APEX API up (db pool ready)")
    yield
    await db.close_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="APEX COMMAND AI",
        version="0.1.0",
        description="AIMBEX lead-to-margin automation. Odoo CRM (no HighLevel), "
                    "swappable provider adapters, RLS multi-tenant, approval-gated execution.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_meta(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id", "req-" + str(id(request)))
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"error": "internal_error", "request_id": request.state.request_id})

    app.include_router(health.router)
    app.include_router(leads.router, prefix="/v1")
    app.include_router(dashboard.router, prefix="/v1")
    app.include_router(questions.router, prefix="/v1")
    app.include_router(seo.router, prefix="/v1")
    app.include_router(qa.router, prefix="/v1")
    app.include_router(ads.router, prefix="/v1")
    app.include_router(campaigns.router, prefix="/v1")
    app.include_router(agents.router, prefix="/v1")
    app.include_router(assets.router, prefix="/v1")
    app.include_router(approvals.router, prefix="/v1")
    app.include_router(actions.router, prefix="/v1")
    app.include_router(webhooks.router, prefix="/v1")

    @app.get("/", include_in_schema=False)
    async def root():
        return {"service": "APEX COMMAND AI", "docs": "/docs", "health": "/healthz"}

    return app
