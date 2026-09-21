from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

app = FastAPI(
    title="KRATOS'26 API",
    description=(
        "Authentication, User Profile, Events, Registration, and Payments/"
        "Refunds endpoints for the KRATOS'26 backend. Teams CRUD/invitations, "
        "QR generation, Attendance, Admin RBAC and Notifications live on "
        "other feature branches — see README.md for the split."
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


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
