from fastapi import FastAPI

app = FastAPI(
    title="Kratos API",
    version="0.1.0",
)


@app.get("/")
def root():
    return {"message": "Kratos API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
