import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import AppError, http_code_from_detail

logger = logging.getLogger("kratos.api")

app = FastAPI(
    title="KRATOS'26 API",
    description=(
        "KRATOS'26 backend under /api/v1: Auth, Profile, Events, Registration, "
        "Teams, Payments (solo + team-leader only), QR, Receipts, Notifications (SMTP), "
        "Attendance, and Admin. Team members never pay. "
        "Deferred: WebSocket teams, multi-event cart."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    path = request.url.path
    if path.startswith("/api/v1"):
        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


app.include_router(api_router, prefix="/api/v1")

_receipt_dir = Path(settings.RECEIPT_STORAGE_DIR)
_receipt_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/receipts", StaticFiles(directory=str(_receipt_dir)), name="receipts")


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
        code = http_code_from_detail(exc.status_code, detail)
    elif isinstance(detail, dict) and "code" in detail:
        code = str(detail.get("code"))
        message = str(detail.get("message") or detail.get("code"))
    elif isinstance(detail, list):
        message = str(detail)
        code = http_code_from_detail(exc.status_code, message)
    else:
        message = str(detail)
        code = http_code_from_detail(exc.status_code, message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
