"""
API do AptoFinder Salvador.
Executar localmente: uvicorn main:app --reload
"""
from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Optional
import json

from database import get_db, init_db
from models import Apartment
from scraper import scrape_all_sources, build_search_url, debug_fetch
from distance import geocode_address, distance_to_work_km, get_driving_time_minutes, WORK_LAT_DEFAULT, WORK_LNG_DEFAULT
from poi import get_nearby_pois

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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Mostra o erro real em vez de um '500 Internal Server Error' genérico — ajuda a diagnosticar sem precisar dos logs do Render."""
    return JSONResponse(
        status_code=500,
        content={"erro": str(exc)[:500], "tipo": type(exc).__name__},
    )


def recompute_market_comparison(db: Session, min_amostra: int = 3):
    """
    Recalcula o preço médio por m² de cada grupo (cidade + bairro + quartos)
    e atualiza o below_market_pct de cada imóvel com base nisso.

    Usa preço/m² em vez de preço total — mais preciso, já que compara imóveis
    de tamanhos diferentes de forma justa (limitação identificada na Fase 4
    do protótipo). Só calcula para grupos com pelo menos `min_amostra`
    imóveis — com menos que isso, a média não é confiável o suficiente
    (imóveis ficam com below_market_pct = None, sem selo, em vez de mostrar
    um número enganoso baseado em pouquíssimos dados).
    """
    apartamentos = db.query(Apartment).filter(
        Apartment.active == True, Apartment.price.isnot(None), Apartment.area_total > 0
    ).all()

    grupos = {}
    for apt in apartamentos:
        chave = (apt.city, apt.neighborhood, apt.rooms)
        grupos.setdefault(chave, []).append(apt)

    atualizados = 0
    for chave, membros in grupos.items():
        if len(membros) < min_amostra:
            for apt in membros:
                apt.neighborhood_avg_price = None
                apt.below_market_pct = None
            continue

        precos_m2 = [m.price / m.area_total for m in membros]
        media_m2 = sum(precos_m2) / len(precos_m2)

        for apt in membros:
            preco_m2_apt = apt.price / apt.area_total
            apt.neighborhood_avg_price = round(media_m2, 2)  # preço médio por m² da região (bairro + quartos)
            apt.below_market_pct = round(((preco_m2_apt - media_m2) / media_m2) * 100, 1)
            atualizados += 1

    db.commit()
    return {"grupos_com_media": sum(1 for m in grupos.values() if len(m) >= min_amostra), "imoveis_atualizados": atualizados}


def apartment_to_dict(apt: Apartment, work_coords: Optional[tuple] = None) -> dict:
    distance_work_km = apt.distance_work_km
    if work_coords and apt.latitude and apt.longitude:
        distance_work_km = distance_to_work_km(apt.latitude, apt.longitude, work_coords[0], work_coords[1])

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
        "distance_work_km": distance_work_km,
        "time_work_minutes": apt.time_work_minutes,
        "distance_market_m": apt.distance_market_m,
        "distance_pharmacy_m": apt.distance_pharmacy_m,
        "distance_gym_m": apt.distance_gym_m,
        "distance_transit_m": apt.distance_transit_m,
        "price_per_m2": round(apt.price / apt.area_total, 2) if apt.price and apt.area_total else None,
        "neighborhood_avg_price_per_m2": apt.neighborhood_avg_price,
        "below_market_pct": apt.below_market_pct,
    }


@app.get("/apartments")
def list_apartments(
    city: Optional[str] = Query("Salvador", description="Cidade da busca"),
    min_area: Optional[float] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    rooms: Optional[int] = Query(None),
    min_parking: Optional[int] = Query(None),
    work_address: Optional[str] = Query(None, description="Endereço de referência do trabalho, informado pelo usuário"),
    max_distance_work_km: Optional[float] = Query(None, description="Filtro de distância até o trabalho, em km"),
    below_market_only: bool = Query(False, description="Mostrar só imóveis abaixo da média do bairro"),
    neighborhood: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Endpoint principal de busca. Todos os filtros são opcionais e combináveis —
    reflete os critérios validados no protótipo (Fases 1-4).

    Se `work_address` for informado, a distância até o trabalho é calculada
    na hora (geocodificando o endereço) em vez de usar o endereço fixo
    definido durante a coleta — permite que cada usuário use seu próprio
    endereço de referência sem precisar re-rodar o scraper.
    """
    query = db.query(Apartment).filter(Apartment.active == True)

    if city:
        query = query.filter(Apartment.city.ilike(f"%{city}%"))
    if min_area is not None:
        query = query.filter(Apartment.area_total >= min_area)
    if min_price is not None:
        query = query.filter(Apartment.price >= min_price)
    if max_price is not None:
        query = query.filter(Apartment.price <= max_price)
    if rooms is not None:
        query = query.filter(Apartment.rooms == rooms)
    if min_parking is not None:
        query = query.filter(Apartment.parking_spaces >= min_parking)
    if below_market_only:
        query = query.filter(Apartment.below_market_pct < 0)
    if neighborhood:
        query = query.filter(Apartment.neighborhood.ilike(f"%{neighborhood}%"))

    # Endereço de trabalho customizado: geocodifica uma vez por requisição
    work_coords = None
    if work_address:
        work_coords = geocode_address(work_address, city=f"{city}, Brasil" if city else "Brasil")
        if not work_coords:
            return {"count": 0, "results": [], "aviso": f"Não foi possível localizar o endereço '{work_address}'. Tente ser mais específico (rua, bairro, cidade)."}

    resultados_brutos = query.all()

    # Se há endereço dinâmico e filtro de distância, aplica em memória (não dá pra filtrar isso no SQL)
    if work_coords and max_distance_work_km is not None:
        resultados_brutos = [
            a for a in resultados_brutos
            if a.latitude and a.longitude
            and distance_to_work_km(a.latitude, a.longitude, work_coords[0], work_coords[1]) <= max_distance_work_km
        ]
    elif max_distance_work_km is not None:
        resultados_brutos = [a for a in resultados_brutos if a.distance_work_km is not None and a.distance_work_km <= max_distance_work_km]

    resultados_dict = [apartment_to_dict(a, work_coords) for a in resultados_brutos]
    resultados_dict.sort(key=lambda a: (a["below_market_pct"] is None, a["below_market_pct"] or 0))

    return {"count": len(resultados_dict), "results": resultados_dict}


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
    Dispara uma coleta manual nas 3 fontes (ImovelWeb, OLX, Zap Imóveis).
    Em produção isso normalmente seria agendado (ex. cron job diário), mas
    começamos com disparo manual via API para manter o controle total sobre
    quando o scraper roda.
    """
    resultado_scraping = scrape_all_sources(cidade_slug, filtros_slug, neighborhood, city, max_paginas)
    dados = resultado_scraping["itens"]
    novos, atualizados, ignorados = 0, 0, 0
    erros = []

    for item in dados:
        # Campos obrigatórios no banco — pula o anúncio se algum não foi capturado
        # pelo parser (evita erro de constraint e derrubar a coleta inteira)
        if not item.get("price") or not item.get("area_total") or not item.get("rooms"):
            ignorados += 1
            continue

        try:
            existente = db.query(Apartment).filter(Apartment.source_url == item["source_url"]).first()
            if existente:
                for campo, valor in item.items():
                    setattr(existente, campo, valor)
                atualizados += 1
            else:
                apt = Apartment(**item)
                if apt.address:
                    coords = geocode_address(apt.address)
                    if coords:
                        apt.latitude, apt.longitude = coords
                        apt.distance_work_km = distance_to_work_km(*coords)
                        apt.time_work_minutes = get_driving_time_minutes(*coords)
                        pois = get_nearby_pois(*coords)
                        apt.distance_market_m = pois["distance_market_m"]
                        apt.distance_pharmacy_m = pois["distance_pharmacy_m"]
                        apt.distance_gym_m = pois["distance_gym_m"]
                        apt.distance_transit_m = pois["distance_transit_m"]
                db.add(apt)
                db.commit()
                novos += 1
        except Exception as e:
            db.rollback()
            erros.append(str(e)[:200])
            ignorados += 1

    resumo_mercado = recompute_market_comparison(db)

    return {
        "coletados": len(dados),
        "novos": novos,
        "atualizados": atualizados,
        "ignorados": ignorados,
        "resumo_por_fonte": resultado_scraping["resumo_por_fonte"],
        "resumo_comparacao_mercado": resumo_mercado,
        "erros_amostra": erros[:5],
    }


@app.post("/market/recompute")
def recompute_market(db: Session = Depends(get_db)):
    """
    Recalcula o preço médio por bairro/quartos e o percentual de cada imóvel
    em relação a essa média, sem precisar rodar o scraper de novo — útil
    depois de rodar múltiplas fontes/páginas separadamente.
    """
    return recompute_market_comparison(db)


@app.get("/scrape/debug")
def scrape_debug(
    url: Optional[str] = None,
    cidade_slug: str = "salvador-ba",
    filtros_slug: str = "3-quartos",
):
    """
    Endpoint temporário de diagnóstico — mostra o que uma página real está
    retornando a partir do servidor, para identificar bloqueios de anti-bot.
    Passe ?url=... para testar qualquer site diretamente (ex. OLX, Zap).
    Remova este endpoint depois que o scraper estiver funcionando de forma estável.
    """
    url_testada = url or build_search_url(cidade_slug, filtros_slug)
    return debug_fetch(url_testada)


@app.get("/health")
def health():
    return {"status": "ok"}
