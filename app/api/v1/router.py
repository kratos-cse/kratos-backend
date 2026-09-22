from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    admin_audit,
    admin_ops,
    attendance,
    auth,
    events,
    notifications,
    payments,
    profile,
    qr,
    registrations,
    teams,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(events.router)
api_router.include_router(registrations.router)
api_router.include_router(teams.router)
api_router.include_router(payments.router)
api_router.include_router(admin.router)
api_router.include_router(admin_audit.router)
api_router.include_router(admin_ops.router)
api_router.include_router(qr.router)
api_router.include_router(notifications.router)
api_router.include_router(attendance.router)
