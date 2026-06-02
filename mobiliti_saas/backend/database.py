from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Para desarrollo: SQLite local
# Para produccion: cambiar a PostgreSQL/Supabase
# Ejemplo Supabase: postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./mobiliti_saas.db"
)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL (Supabase)
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
