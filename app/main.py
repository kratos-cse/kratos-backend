import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import INTERNAL_ERROR, AppError, http_code_from_detail
from app.core.middleware import RequestLoggingMiddleware

logger = logging.getLogger(__name__)

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
app.add_middleware(RequestLoggingMiddleware)

app.include_router(api_router, prefix="/api/v1")


def _participant_error_payload(
    request: Request,
    code: str,
    message: str,
    *,
    details=None,
) -> dict:
    request_id = getattr(request.state, "request_id", None)
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_participant_error_payload(request, exc.code, exc.message),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
        code = http_code_from_detail(exc.status_code, detail)
        details = None
    elif isinstance(detail, dict) and "code" in detail:
        code = str(detail.get("code"))
        message = str(detail.get("message") or detail.get("code"))
        details = detail.get("details")
    elif isinstance(detail, list):
        message = ", ".join(
            str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail
        )
        code = http_code_from_detail(exc.status_code, message)
        details = detail
    else:
        message = str(detail)
        code = http_code_from_detail(exc.status_code, message)
        details = None
    return JSONResponse(
        status_code=exc.status_code,
        content=_participant_error_payload(request, code, message, details=details),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_exception request_id=%s path=%s",
        getattr(request.state, "request_id", None),
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content=_participant_error_payload(
            request,
            INTERNAL_ERROR,
            "Something went wrong. Please try again. If the problem continues, contact support.",
        ),
    )


@app.get("/", tags=["Health"])
@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
