from fastapi import APIRouter

from app.api.v1.endpoints import admin_payments, auth, events, payments, profile, registrations

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(events.router)
api_router.include_router(registrations.router)
api_router.include_router(payments.router)
api_router.include_router(admin_payments.router)
