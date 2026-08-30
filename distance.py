"""
Geocodificação e cálculo de distância — inclui o filtro de distância até o trabalho.

IMPORTANTE (achado da validação da Fase 2):
A distância calculada aqui é em LINHA RETA (fórmula de Haversine), não é tempo real
de carro/ônibus. Validamos no protótipo que nenhuma fonte gratuita dá tempo de
viagem ponto-a-ponto confiável — isso exige uma API paga de rotas (Google Distance
Matrix, Mapbox Directions, OpenRouteService). A função `get_driving_time_minutes`
abaixo é um placeholder pronto para receber essa integração quando você decidir
pagar por ela; até lá, o filtro funciona com distância em linha reta, que já é
útil para uma primeira triagem (descarta bairros claramente longe demais).
"""
import math
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger("distance")

# Endereço padrão (usado se o usuário não informar outro na busca)
WORK_ADDRESS_DEFAULT = "Banco do Nordeste, Centro Industrial de Aratu, Simões Filho, BA"
WORK_LAT_DEFAULT = -12.8267507
WORK_LNG_DEFAULT = -38.4009075

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim (OpenStreetMap) é gratuito mas pede rate limit de 1 req/segundo e um User-Agent identificável.
NOMINATIM_HEADERS = {"User-Agent": "AptoFinderSalvador/1.0 (uso pessoal)"}


def geocode_address(address: str, city: str = "Salvador, BA, Brasil") -> Optional[tuple[float, float]]:
    """
    Converte um endereço em (latitude, longitude) usando Nominatim (OpenStreetMap), gratuito.
    Retorna None se não encontrar — nesse caso, o imóvel fica sem distância calculada
    (mesma filosofia de transparência: não inventar dado que não existe).
    """
    query = f"{address}, {city}"
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        time.sleep(1)  # respeita o rate limit de 1 req/s do Nominatim
        results = resp.json()
        if not results:
            logger.info(f"Endereço não geocodificado: {query}")
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        logger.warning(f"Erro ao geocodificar '{query}': {e}")
        return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância em linha reta entre duas coordenadas, em km."""
    R = 6371.0  # raio da Terra em km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_to_work_km(lat: float, lng: float, work_lat: float = WORK_LAT_DEFAULT, work_lng: float = WORK_LNG_DEFAULT) -> float:
    """Distância em linha reta do apartamento até o endereço de trabalho informado (ou o padrão do projeto)."""
    return round(haversine_km(lat, lng, work_lat, work_lng), 1)


def get_driving_time_minutes(lat: float, lng: float) -> Optional[float]:
    """
    PLACEHOLDER — retorna None até uma API de rotas paga ser configurada.

    Para ativar tempo real de carro, integre aqui uma dessas opções (validadas
    como necessárias na Fase 2 do projeto):
      - Google Distance Matrix API (mais precisa, paga por requisição)
      - OpenRouteService (tem um nível gratuito limitado)
      - Mapbox Directions API

    Exemplo de implementação com OpenRouteService (deixe pronto, só falta a chave):

        import requests
        ORS_API_KEY = "SUA_CHAVE_AQUI"
        resp = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car",
            headers={"Authorization": ORS_API_KEY},
            json={"coordinates": [[lng, lat], [WORK_LNG, WORK_LAT]]},
        )
        duration_seconds = resp.json()["routes"][0]["summary"]["duration"]
        return duration_seconds / 60
    """
    return None
