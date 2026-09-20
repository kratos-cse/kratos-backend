"""
*** TEMPORARY ***
No shared database.py exists in the repo yet. This is a minimal
SQLAlchemy engine/session setup so the Teams module can run and be
tested standalone.

DELETE THIS FILE once a teammate merges the real database setup, and
change every `from app.core.database import ...` in this module to
point at theirs instead. Nothing else should need to change.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

DATABASE_URL = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}?sslmode=require"


# echo=True is handy while developing locally; turn off before merging.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

try:
    with engine.connect() as connection:
        print("Connection successful!")
except Exception as e:
    print(f"Failed to connect: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()