"""
Modelo de dados dos apartamentos — schema validado nas Fases 1-4 do projeto.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class Apartment(Base):
    __tablename__ = "apartments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Origem
    source = Column(String, nullable=False)          # "imovelweb", "olx", "zap"
    source_url = Column(String, nullable=False, unique=True)
    external_id = Column(String)                       # id do anúncio na fonte, usado para deduplicação

    # Filtros básicos (Fase 1)
    price = Column(Float, nullable=False)
    condo_fee = Column(Float, nullable=True)
    iptu = Column(Float, nullable=True)
    area_total = Column(Float, nullable=False)
    rooms = Column(Integer, nullable=False)
    bathrooms = Column(Integer, nullable=True)
    parking_spaces = Column(Integer, nullable=True)

    # Endereço / geolocalização (base para Fase 2)
    address = Column(String, nullable=True)
    neighborhood = Column(String, nullable=False)
    city = Column(String, default="Salvador")
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Fase 2 — proximidade e trajeto (preenchido depois do scraping via geocode.py / distance.py)
    distance_market_m = Column(Float, nullable=True)
    distance_pharmacy_m = Column(Float, nullable=True)
    distance_gym_m = Column(Float, nullable=True)
    distance_transit_m = Column(Float, nullable=True)
    distance_work_km = Column(Float, nullable=True)     # em linha reta (Haversine) — ver nota em distance.py
    time_work_minutes = Column(Float, nullable=True)    # só preenchido se houver integração paga de rotas

    # Fase 3 — tipo de vaga e estrutura (extraído do texto livre)
    parking_type = Column(String, nullable=True)         # "coberta", "descoberta", "solta", "presa", None = não informado
    parking_type_source = Column(String, nullable=True)  # "tags" ou "prosa" ou None — indica confiança da extração
    amenities = Column(Text, nullable=True)              # JSON serializado: ["piscina", "academia", "salão de festas", ...]

    # Fase 4 — comparação de mercado
    neighborhood_avg_price = Column(Float, nullable=True)
    below_market_pct = Column(Float, nullable=True)      # negativo = abaixo da média, positivo = acima

    # Texto bruto (auditoria / fallback de extração)
    raw_description = Column(Text, nullable=True)

    # Metadados
    scraped_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True)                # False quando o anúncio some da fonte (não deletamos, só marcamos)
