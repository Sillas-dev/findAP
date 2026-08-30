"""
Busca de pontos de interesse (mercado, farmácia, academia, transporte público)
próximos a um apartamento, usando a Overpass API (OpenStreetMap) — gratuita,
sem necessidade de chave de API.

Nota: a validação original desse recurso (Fase 2 do projeto) foi feita com uma
ferramenta de busca de lugares disponível só dentro do chat, não uma API pública.
Este módulo usa uma fonte pública equivalente para o site publicado funcionar
de forma independente.
"""
import requests
import logging
from distance import haversine_km

logger = logging.getLogger("poi")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

POI_FILTERS = {
    "market": '["shop"~"supermarket|convenience"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "gym": '["leisure"~"fitness_centre|sports_centre"]',
    "transit": '["highway"="bus_stop"]',
}

CAMPO_POR_CATEGORIA = {
    "market": "distance_market_m",
    "pharmacy": "distance_pharmacy_m",
    "gym": "distance_gym_m",
    "transit": "distance_transit_m",
}


def get_nearby_pois(lat: float, lng: float, radius_m: int = 1200) -> dict:
    """
    Retorna a distância (em metros, linha reta) até o ponto mais próximo de
    cada categoria, dentro do raio informado. Categorias sem nada encontrado
    no raio voltam como None (não fica inventando um valor).
    """
    resultado = {campo: None for campo in CAMPO_POR_CATEGORIA.values()}

    partes_query = []
    for filtro in POI_FILTERS.values():
        partes_query.append(f"node{filtro}(around:{radius_m},{lat},{lng});")
        partes_query.append(f"way{filtro}(around:{radius_m},{lat},{lng});")

    query = f"[out:json][timeout:20];({''.join(partes_query)});out center;"

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=25)
        if resp.status_code != 200:
            logger.warning(f"Overpass retornou status {resp.status_code}")
            return resultado

        elements = resp.json().get("elements", [])
        melhores = {}

        for el in elements:
            el_lat = el.get("lat") or el.get("center", {}).get("lat")
            el_lng = el.get("lon") or el.get("center", {}).get("lon")
            if el_lat is None or el_lng is None:
                continue
            dist_m = haversine_km(lat, lng, el_lat, el_lng) * 1000

            tags = el.get("tags", {})
            categoria = None
            if tags.get("shop") in ("supermarket", "convenience"):
                categoria = "market"
            elif tags.get("amenity") == "pharmacy":
                categoria = "pharmacy"
            elif tags.get("leisure") in ("fitness_centre", "sports_centre"):
                categoria = "gym"
            elif tags.get("highway") == "bus_stop":
                categoria = "transit"

            if categoria and (categoria not in melhores or dist_m < melhores[categoria]):
                melhores[categoria] = dist_m

        for categoria, campo in CAMPO_POR_CATEGORIA.items():
            if categoria in melhores:
                resultado[campo] = round(melhores[categoria], 0)

    except Exception as e:
        logger.warning(f"Erro ao consultar Overpass: {e}")

    return resultado
