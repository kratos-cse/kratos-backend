from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import FORBIDDEN, NOT_FOUND, VALIDATION_ERROR, AppError

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

app.include_router(api_router, prefix="/api/v1")

# Ensure receipt storage directory exists for PDF / HTML persistence
Path(settings.RECEIPT_STORAGE_DIR).mkdir(parents=True, exist_ok=True)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 403:
        code = FORBIDDEN
    elif exc.status_code == 404:
        code = NOT_FOUND
    else:
        code = VALIDATION_ERROR
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, list):
        message = str(detail)
    else:
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
