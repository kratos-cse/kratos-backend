from fastapi import FastAPI
from app.api.v1 import team_invitations, team_members, teams
from app.core.database import Base, engine
from app.models import team as _team_models       
from app.models import temp_external_subs as _stub_models

# Create the database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Kratos API",
    version="0.1.0",
)
app.include_router(teams.router, prefix="/api/v1")
app.include_router(team_members.router,prefix="/api/v1")
app.include_router(team_invitations.router,prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Kratos API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
