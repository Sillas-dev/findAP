"""
API do AptoFinder Salvador.
Executar localmente: uvicorn main:app --reload
"""
from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Optional
import json

from database import get_db, init_db
from models import Apartment
from scraper import scrape_search, build_search_url, debug_fetch
from distance import geocode_address, distance_to_work_km, get_driving_time_minutes

app = FastAPI(title="AptoFinder Salvador API")

# Libera acesso do frontend (ajuste a origem quando o site for hospedado de verdade)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


def apartment_to_dict(apt: Apartment) -> dict:
    return {
        "id": apt.id,
        "source": apt.source,
        "source_url": apt.source_url,
        "price": apt.price,
        "condo_fee": apt.condo_fee,
        "area_total": apt.area_total,
        "rooms": apt.rooms,
        "bathrooms": apt.bathrooms,
        "parking_spaces": apt.parking_spaces,
        "parking_type": apt.parking_type or "Não informado",
        "address": apt.address,
        "neighborhood": apt.neighborhood,
        "city": apt.city,
        "amenities": json.loads(apt.amenities) if apt.amenities else [],
        "distance_work_km": apt.distance_work_km,
        "time_work_minutes": apt.time_work_minutes,
        "below_market_pct": apt.below_market_pct,
    }


@app.get("/apartments")
def list_apartments(
    city: Optional[str] = Query("Salvador", description="Cidade da busca"),
    min_area: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    rooms: Optional[int] = Query(None),
    min_parking: Optional[int] = Query(None),
    max_distance_work_km: Optional[float] = Query(None, description="Filtro de distância até o trabalho, em km"),
    below_market_only: bool = Query(False, description="Mostrar só imóveis abaixo da média do bairro"),
    neighborhood: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Endpoint principal de busca. Todos os filtros são opcionais e combináveis —
    reflete os critérios validados no protótipo (Fases 1-4).
    """
    query = db.query(Apartment).filter(Apartment.active == True)

    if city:
        query = query.filter(Apartment.city.ilike(f"%{city}%"))
    if min_area is not None:
        query = query.filter(Apartment.area_total >= min_area)
    if max_price is not None:
        query = query.filter(Apartment.price <= max_price)
    if rooms is not None:
        query = query.filter(Apartment.rooms == rooms)
    if min_parking is not None:
        query = query.filter(Apartment.parking_spaces >= min_parking)
    if max_distance_work_km is not None:
        query = query.filter(Apartment.distance_work_km <= max_distance_work_km)
    if below_market_only:
        query = query.filter(Apartment.below_market_pct < 0)
    if neighborhood:
        query = query.filter(Apartment.neighborhood.ilike(f"%{neighborhood}%"))

    results = query.order_by(Apartment.below_market_pct.asc().nullslast()).all()
    return {"count": len(results), "results": [apartment_to_dict(a) for a in results]}


@app.get("/apartments/{apartment_id}")
def get_apartment(apartment_id: int, db: Session = Depends(get_db)):
    apt = db.query(Apartment).filter(Apartment.id == apartment_id).first()
    if not apt:
        return {"error": "Imóvel não encontrado"}
    return apartment_to_dict(apt)


@app.post("/scrape/run")
def run_scrape(
    cidade_slug: str = "salvador-ba",
    city: str = "Salvador",
    filtros_slug: str = "3-quartos",
    neighborhood: str = "Salvador (geral)",
    max_paginas: int = 3,
    db: Session = Depends(get_db),
):
    """
    Dispara uma coleta manual. Em produção isso normalmente seria agendado
    (ex. cron job diário), mas começamos com disparo manual via API para
    manter o controle total sobre quando o scraper roda.
    """
    dados = scrape_search(cidade_slug, filtros_slug, neighborhood, city, max_paginas)
    novos, atualizados = 0, 0

    for item in dados:
        existente = db.query(Apartment).filter(Apartment.source_url == item["source_url"]).first()
        if existente:
            for campo, valor in item.items():
                setattr(existente, campo, valor)
            atualizados += 1
        else:
            apt = Apartment(**item)
            # Geocodifica e calcula distância até o trabalho no momento da inserção
            if apt.address:
                coords = geocode_address(apt.address)
                if coords:
                    apt.latitude, apt.longitude = coords
                    apt.distance_work_km = distance_to_work_km(*coords)
                    apt.time_work_minutes = get_driving_time_minutes(*coords)  # None até API paga ser configurada
            db.add(apt)
            novos += 1

    db.commit()
    return {"coletados": len(dados), "novos": novos, "atualizados": atualizados}


@app.get("/scrape/debug")
def scrape_debug(
    cidade_slug: str = "salvador-ba",
    filtros_slug: str = "3-quartos",
):
    """
    Endpoint temporário de diagnóstico — mostra o que a página real está
    retornando, para ajustar os seletores do scraper com precisão.
    Remova este endpoint depois que o scraper estiver funcionando de forma estável.
    """
    url = build_search_url(cidade_slug, filtros_slug)
    return {"url_testada": url, "diagnostico": debug_fetch(url)}


@app.get("/health")
def health():
    return {"status": "ok"}
