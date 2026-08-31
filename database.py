"""
Configuração do banco. Usa SQLite por padrão — arquivo único, zero configuração,
perfeito para uso pessoal e fácil de trocar por Postgres depois se o site crescer.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./aptofinder.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping: testa a conexão antes de reutilizá-la do pool e a descarta/reabre
# se estiver morta. Necessário porque o Neon (Postgres gratuito) suspende o
# compute por inatividade — sem isso, a primeira query após o banco "acordar"
# falha com "SSL connection has been closed unexpectedly" em vez de reconectar.
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Cria as tabelas se ainda não existirem. Chamado uma vez ao iniciar a aplicação."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency do FastAPI — abre e fecha a sessão do banco por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
