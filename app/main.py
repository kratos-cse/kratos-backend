from fastapi import FastAPI

from app.payments.routes import router as payments_router

app = FastAPI(
    title="Kratos API",
    version="0.1.0",
)

app.include_router(payments_router)


@app.get("/")
def root():
    return {"message": "Kratos API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
