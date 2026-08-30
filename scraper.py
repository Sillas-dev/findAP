"""
Scraper do ImovelWeb — fonte principal validada no protótipo manual.

Padrão de URL confirmado (Fases 1 e refinamento):
  Página 1: https://www.imovelweb.com.br/apartamentos-venda-{cidade}-{filtros}.html
  Página N: https://www.imovelweb.com.br/apartamentos-venda-{cidade}-{filtros}-pagina-{N}.html

Este módulo NÃO foi testado contra a internet real neste ambiente (sandbox sem
acesso à rede) — foi escrito com base no HTML validado manualmente durante o
protótipo. Rode uma vez com poucas páginas e confira a saída antes de usar em
escala, seguindo o mesmo espírito de "testar e validar" do resto do projeto.
"""
import re
import json
import time
import logging
from typing import Optional
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("imovelweb_scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

BASE_URL = "https://www.imovelweb.com.br"

# Palavras-chave usadas na extração de tipo de vaga (Fase 3, validado em ~55-60% dos anúncios)
PARKING_TYPE_PATTERNS = {
    "coberta": r"vaga[s]?\s+(de\s+garagem\s+)?coberta[s]?|coberta[s]?",
    "descoberta": r"vaga[s]?\s+(de\s+garagem\s+)?descoberta[s]?|descoberta[s]?",
    "solta": r"vaga[s]?\s+(de\s+garagem\s+)?solta[s]?|solta[s]?",
    "presa": r"vaga[s]?\s+(de\s+garagem\s+)?presa[s]?|presa[s]?",
}

# Amenidades comuns identificadas na validação da Fase 3
AMENITY_KEYWORDS = [
    "piscina", "academia", "salão de festas", "salão de jogos", "elevador",
    "portaria 24h", "churrasqueira", "quadra", "playground", "brinquedoteca",
    "sauna", "espaço gourmet", "cinema", "coworking", "pet place", "bicicletário",
]


def build_search_url(cidade_slug: str, filtros_slug: str, pagina: int = 1) -> str:
    """
    Monta a URL de busca seguindo o padrão validado.
    Exemplo: build_search_url("salvador-ba", "3-quartos", 2)
      -> https://www.imovelweb.com.br/apartamentos-venda-salvador-ba-3-quartos-pagina-2.html
    """
    base = f"{BASE_URL}/apartamentos-venda-{cidade_slug}-{filtros_slug}"
    if pagina <= 1:
        return f"{base}.html"
    return f"{base}-pagina-{pagina}.html"


def fetch_page(url: str, retries: int = 3, delay_seconds: float = 2.0) -> Optional[str]:
    """Busca uma página com retentativas e um atraso educado entre requisições."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"Status {resp.status_code} em {url} (tentativa {attempt})")
        except requests.RequestException as e:
            logger.warning(f"Erro de rede em {url}: {e} (tentativa {attempt})")
        time.sleep(delay_seconds * attempt)  # backoff progressivo
    logger.error(f"Falhou após {retries} tentativas: {url}")
    return None


def extract_parking_type(description: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extrai o tipo de vaga do texto. Retorna (tipo, fonte).
    fonte = "prosa" (regex sobre texto livre) — a detecção de "tags" estruturadas
    acontece em parse_listing_card, que chama esta função só como fallback.
    """
    if not description:
        return None, None
    desc_lower = description.lower()
    for tipo, pattern in PARKING_TYPE_PATTERNS.items():
        if re.search(pattern, desc_lower):
            return tipo, "prosa"
    return None, None  # não informado — nunca inferir silenciosamente (regra definida no projeto)


def extract_amenities(description: str) -> list[str]:
    """Extrai lista de amenidades mencionadas na descrição."""
    if not description:
        return []
    desc_lower = description.lower()
    return [a for a in AMENITY_KEYWORDS if a in desc_lower]


def parse_price(text: str) -> Optional[float]:
    """Converte 'R$ 350.000' -> 350000.0"""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return float(digits) if digits else None


def parse_area(text: str) -> Optional[float]:
    """Converte '124 m² tot.' -> 124.0"""
    if not text:
        return None
    match = re.search(r"(\d+)\s*m", text)
    return float(match.group(1)) if match else None


def parse_int_field(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def parse_listing_card(card, source_url_base: str = BASE_URL) -> Optional[dict]:
    """
    Extrai os campos de um card de anúncio na página de listagem.
    A estrutura de classes CSS abaixo é baseada no HTML observado durante a
    validação manual — sites mudam o HTML com frequência, então esta função
    é o ponto mais provável de precisar de manutenção.
    """
    try:
        link_el = card.select_one("a[href*='/propriedades/']")
        if not link_el:
            return None
        source_url = link_el.get("href", "")
        if source_url.startswith("/"):
            source_url = source_url_base + source_url

        price_el = card.select_one("[data-qa='POSTING_CARD_PRICE'], .price-items")
        price = parse_price(price_el.get_text() if price_el else "")

        features_el = card.select_one("[data-qa='POSTING_CARD_FEATURES'], .card-features")
        features_text = features_el.get_text(" ", strip=True) if features_el else ""

        area = parse_area(features_text)
        rooms_match = re.search(r"(\d+)\s*quarto", features_text)
        bathrooms_match = re.search(r"(\d+)\s*ban", features_text)
        parking_match = re.search(r"(\d+)\s*vaga", features_text)

        address_el = card.select_one("[data-qa='POSTING_CARD_LOCATION'], .card-address")
        address = address_el.get_text(strip=True) if address_el else None

        description_el = card.select_one("[data-qa='POSTING_CARD_DESCRIPTION'], .card-description")
        description = description_el.get_text(" ", strip=True) if description_el else ""

        # Tenta detectar bloco de tags estruturadas (~30% dos anúncios, validado na Fase 3)
        has_structured_tags = bool(re.search(r"(Lazer|Estrutura|Serviços Próximos)\s*:", description))

        parking_type, parking_source = extract_parking_type(description)
        if has_structured_tags and parking_type:
            parking_source = "tags"

        return {
            "source": "imovelweb",
            "source_url": source_url,
            "price": price,
            "area_total": area,
            "rooms": int(rooms_match.group(1)) if rooms_match else None,
            "bathrooms": int(bathrooms_match.group(1)) if bathrooms_match else None,
            "parking_spaces": int(parking_match.group(1)) if parking_match else None,
            "address": address,
            "raw_description": description,
            "parking_type": parking_type,
            "parking_type_source": parking_source,
            "amenities": json.dumps(extract_amenities(description), ensure_ascii=False),
        }
    except Exception as e:
        logger.warning(f"Falha ao parsear um card: {e}")
        return None


def scrape_search(cidade_slug: str, filtros_slug: str, neighborhood: str, city: str = "Salvador",
                   max_paginas: int = 5, delay_seconds: float = 2.0) -> list[dict]:
    """
    Coleta anúncios de uma busca, percorrendo a paginação validada.
    Para de paginar se uma página não trouxer nenhum card novo (fim dos resultados).
    """
    resultados = []
    for pagina in range(1, max_paginas + 1):
        url = build_search_url(cidade_slug, filtros_slug, pagina)
        logger.info(f"Buscando página {pagina}: {url}")
        html = fetch_page(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-qa='POSTING_CARD'], .posting-card, article")
        if not cards:
            logger.info(f"Nenhum card encontrado na página {pagina} — fim dos resultados.")
            break

        pagina_resultados = 0
        for card in cards:
            item = parse_listing_card(card)
            if item:
                item["neighborhood"] = neighborhood
                item["city"] = city
                resultados.append(item)
                pagina_resultados += 1

        logger.info(f"Página {pagina}: {pagina_resultados} anúncios extraídos.")
        time.sleep(delay_seconds)  # respeita o site entre requisições

    return resultados


def debug_fetch(url: str) -> dict:
    """
    Função de diagnóstico — não faz parte do fluxo normal de scraping.
    Faz sua própria requisição (sem usar fetch_page) para capturar o status
    HTTP e qualquer erro exato, já que fetch_page normalmente engole esses
    detalhes ao tentar novamente.
    """
    resultado = {"url_testada": url}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resultado["status_code"] = resp.status_code
        resultado["url_final_apos_redirect"] = resp.url
        resultado["tamanho_resposta"] = len(resp.text)
        resultado["primeiros_500_caracteres"] = resp.text[:500]
        resultado["headers_resposta"] = dict(resp.headers)

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            candidatos_card = {
                "[data-qa='POSTING_CARD']": len(soup.select("[data-qa='POSTING_CARD']")),
                ".posting-card": len(soup.select(".posting-card")),
                "article": len(soup.select("article")),
                "[class*='card']": len(soup.select("[class*='card']")),
                "a[href*='/propriedades/']": len(soup.select("a[href*='/propriedades/']")),
            }
            resultado["candidatos_de_card_encontrados"] = candidatos_card

    except requests.exceptions.Timeout:
        resultado["erro"] = "timeout — o servidor do ImovelWeb não respondeu a tempo"
    except requests.exceptions.ConnectionError as e:
        resultado["erro"] = f"erro de conexão — provável bloqueio de rede/firewall: {str(e)[:300]}"
    except requests.exceptions.RequestException as e:
        resultado["erro"] = f"erro de requisição: {str(e)[:300]}"
    except Exception as e:
        resultado["erro"] = f"erro inesperado: {str(e)[:300]}"

    return resultado


if __name__ == "__main__":
    # Exemplo de uso — rode com poucas páginas primeiro para validar antes de escalar.
    dados = scrape_search(
        cidade_slug="salvador-ba",
        filtros_slug="3-quartos",
        neighborhood="Salvador (geral)",
        max_paginas=2,
    )
    print(f"Total coletado: {len(dados)} anúncios")
    print(json.dumps(dados[:2], indent=2, ensure_ascii=False))
