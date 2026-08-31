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
import os
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

# ImovelWeb, OLX e Zap Imóveis usam proteção Cloudflare que bloqueia servidores
# de nuvem (Render, Railway, etc.) — confirmado via diagnóstico em produção.
# Uma requisição simples (requests.get) não passa. A solução é rotear através
# de uma API de scraping gerenciada, que resolve o desafio do Cloudflare.
# ZenRows tem plano gratuito de 5.000 créditos/mês, sem cartão — configure a
# chave na variável de ambiente ZENROWS_API_KEY (no painel do Render/Railway,
# em "Environment").
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY")
ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"

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

# Bairros de Salvador identificados durante a validação do protótipo (Fases 1 e 4).
# Usado para extrair o bairro real de cada anúncio a partir do texto do card —
# sem isso, a comparação de valor de mercado (Fase 4) fica sem sentido, porque
# agruparia imóveis de bairros muito diferentes numa única "média geral".
# Lista não exaustiva — bairros fora dela caem no valor padrão informado na busca.
BAIRROS_SALVADOR = [
    "Caminho das Árvores", "Horto Florestal", "Costa Azul", "Jaguaribe",
    "Patamares", "Itaigara", "Cabula", "Candeal", "Pituba", "Barra", "Graça",
    "Armação", "Imbuí", "Piatã", "Stella Maris", "Boca do Rio", "Rio Vermelho",
    "Ondina", "Federação", "Brotas", "Cajazeiras", "Liberdade", "Pernambués",
    "Vila Laura", "Nazaré", "Comércio", "Pituaçu", "Paralela", "Imbuí",
    "São Marcos", "Sussuarana", "Alphaville", "Amaralina", "Canela",
]


def extract_neighborhood(text: str, fallback: str) -> str:
    """
    Procura por um nome de bairro conhecido dentro do texto do anúncio.
    Usa o fallback (bairro genérico passado na busca) se nenhum bater —
    evita que o campo fique vazio, mas prioriza sempre o valor real extraído.
    """
    if not text:
        return fallback
    for bairro in BAIRROS_SALVADOR:
        if bairro.lower() in text.lower():
            return bairro
    return fallback


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
    """
    Busca uma página. Tenta requisição direta primeiro; se vier bloqueio do
    Cloudflare (403 com página de desafio) e houver uma chave do ZenRows
    configurada, refaz a busca através da API de bypass.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429) and ZENROWS_API_KEY:
                logger.info(f"Bloqueio detectado (status {resp.status_code}) — tentando via ZenRows: {url}")
                return fetch_via_zenrows(url)
            logger.warning(f"Status {resp.status_code} em {url} (tentativa {attempt})")
        except requests.RequestException as e:
            logger.warning(f"Erro de rede em {url}: {e} (tentativa {attempt})")
        time.sleep(delay_seconds * attempt)  # backoff progressivo
    logger.error(f"Falhou após {retries} tentativas: {url}")
    return None


def fetch_via_zenrows(url: str, js_render: bool = True, retornar_detalhes: bool = False):
    """
    Busca uma página através da API do ZenRows, que lida com o desafio do
    Cloudflare (proxy residencial + execução de JavaScript real).
    Usa proxy_country=br para evitar respostas diferentes por geolocalização
    (sites brasileiros às vezes servem conteúdo distinto para IPs de fora do país).
    Requer a variável de ambiente ZENROWS_API_KEY configurada.

    Se retornar_detalhes=True, retorna um dict com status/erro em vez de só o HTML
    (usado pelo diagnóstico, para saber exatamente por que uma tentativa falhou).
    """
    if not ZENROWS_API_KEY:
        logger.error("ZENROWS_API_KEY não configurada — não é possível contornar o bloqueio.")
        return {"erro": "ZENROWS_API_KEY não configurada"} if retornar_detalhes else None
    try:
        # Monta a URL manualmente em vez de usar params=... — confirmado via
        # diagnóstico em produção que o '+' na URL (usado de propósito pelo Zap
        # Imóveis) ficava sem codificar quando a biblioteca montava a query
        # string sozinha, o que o ZenRows provavelmente decodifica como espaço
        # em branco, quebrando a URL de destino (gerava 404).
        from urllib.parse import quote
        url_codificada = quote(url, safe="")
        query_string = (
            f"apikey={ZENROWS_API_KEY}&url={url_codificada}"
            f"&js_render={'true' if js_render else 'false'}"
            f"&premium_proxy=true&proxy_country=br&wait=6000"
        )
        request_url = f"{ZENROWS_ENDPOINT}?{query_string}"
        logger.info(f"URL exata enviada ao ZenRows: {request_url}")

        ultimo_erro = None
        for tentativa in range(1, 3):  # até 2 tentativas — RESP001 costuma ser falha pontual
            resp = requests.get(request_url, timeout=120)
            if resp.status_code == 200:
                return {"html": resp.text, "status_code": 200, "url_enviada_zenrows": request_url} if retornar_detalhes else resp.text
            logger.warning(f"ZenRows retornou status {resp.status_code} (tentativa {tentativa}) para {url}: {resp.text[:300]}")
            ultimo_erro = {
                "erro": f"ZenRows retornou status {resp.status_code}",
                "corpo_resposta": resp.text[:500],
                "status_code": resp.status_code,
                "url_enviada_zenrows": request_url,
                "tentativas": tentativa,
            }
            if resp.status_code == 404:
                break  # 404 é erro estrutural (URL errada) — não adianta tentar de novo
            time.sleep(3)

        return ultimo_erro if retornar_detalhes else None
    except requests.exceptions.Timeout:
        if retornar_detalhes:
            return {"erro": "timeout ao chamar o ZenRows (mais de 90s)"}
        return None
    except requests.RequestException as e:
        logger.warning(f"Erro ao usar ZenRows para {url}: {e}")
        if retornar_detalhes:
            return {"erro": f"erro de requisição ao ZenRows: {str(e)[:300]}"}
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
    """
    Converte '124 m² tot.' -> 124.0.
    Exige o símbolo de área (m²/m2) em vez de só "m" — a versão anterior
    (`\\d+\\s*m`) também casava com números seguidos de "mil", "metros",
    "manhã" etc. em qualquer lugar do texto do card, gerando áreas absurdas
    (ex: 100.000 m²) que distorciam a média de R$/m² do bairro (Fase 4).
    Descarta também valores fora de uma faixa plausível de apartamento.
    """
    if not text:
        return None
    match = re.search(r"(\d{1,4})\s*m[²2]\b", text)
    if not match:
        return None
    valor = float(match.group(1))
    if valor < 10 or valor > 2000:
        return None
    return valor


def normalize_source_url(url: str) -> str:
    """
    Remove parâmetros de rastreamento da URL (ex: n_search_id do ImovelWeb),
    que mudam a cada busca mesmo para o mesmo anúncio. Sem isso, a checagem
    de "já existe no banco" (por source_url) nunca bate, e o mesmo imóvel é
    salvo como novo a cada scrape, duplicando os resultados.
    """
    if not url:
        return url
    return url.split("?", 1)[0]


def parse_int_field(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def parse_listing_card(card, source_url_base: str = BASE_URL) -> Optional[dict]:
    """
    Extrai os campos de um card de anúncio na página de listagem.
    Seletores confirmados via diagnóstico em produção (ago/2026):
    - Container do card: [data-posting-type]
    - Link do anúncio: a[href*='/propriedades/']
    O restante dos campos (preço, área, quartos, etc.) é extraído via regex
    sobre o texto completo do card, em vez de classes CSS específicas — essas
    classes usam nomes gerados (CSS modules) que tendem a mudar com builds do
    site, então o texto puro é uma extração mais resiliente.
    """
    try:
        link_el = card.select_one("a[href*='/propriedades/']")
        if not link_el:
            return None
        source_url = link_el.get("href", "")
        if source_url.startswith("/"):
            source_url = source_url_base + source_url
        source_url = normalize_source_url(source_url)

        card_text = card.get_text(" ", strip=True)

        price_match = re.search(r"R\$\s*[\d.]+", card_text)
        price = parse_price(price_match.group(0)) if price_match else None

        area = parse_area(card_text)
        rooms_match = re.search(r"(\d+)\s*quarto", card_text)
        bathrooms_match = re.search(r"(\d+)\s*ban", card_text)
        parking_match = re.search(r"(\d+)\s*vaga", card_text)

        address_el = card.select_one("[class*='location-address'], [class*='LocationAddress']")
        address = address_el.get_text(strip=True) if address_el else None
        neighborhood_extraido = extract_neighborhood(f"{address or ''} {card_text}", fallback=None)

        description_el = card.select_one("[class*='posting-description'], [class*='PostingDescription']")
        description = description_el.get_text(" ", strip=True) if description_el else card_text

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
            "neighborhood_extraido": neighborhood_extraido,
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
        cards = soup.select("[data-posting-type]")
        if not cards:
            logger.info(f"Nenhum card encontrado na página {pagina} — fim dos resultados.")
            break

        pagina_resultados = 0
        for card in cards:
            item = parse_listing_card(card)
            if item:
                item["neighborhood"] = item.pop("neighborhood_extraido", None) or neighborhood
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

    # Se veio bloqueio e há chave do ZenRows configurada, testa o caminho alternativo
    if resultado.get("status_code") in (403, 429):
        resultado["zenrows_configurado"] = bool(ZENROWS_API_KEY)
        if ZENROWS_API_KEY:
            detalhes_zenrows = fetch_via_zenrows(url, retornar_detalhes=True)
            html_zenrows = detalhes_zenrows.get("html")
            resultado["zenrows_funcionou"] = html_zenrows is not None
            if not html_zenrows:
                resultado["zenrows_detalhe_erro"] = detalhes_zenrows
            if html_zenrows:
                resultado["zenrows_tamanho_resposta"] = len(html_zenrows)
                soup = BeautifulSoup(html_zenrows, "html.parser")

                resultado["zenrows_candidatos_de_card"] = {
                    "[data-qa='POSTING_CARD']": len(soup.select("[data-qa='POSTING_CARD']")),
                    "article": len(soup.select("article")),
                    "a[href*='/propriedades/']": len(soup.select("a[href*='/propriedades/']")),
                    "a[href*='/imovel']": len(soup.select("a[href*='/imovel']")),
                    "li": len(soup.select("li")),
                    "div[data-posting-type]": len(soup.select("[data-posting-type]")),
                }

                # Confirma se há anúncios reais na página (evidência independente de seletor)
                resultado["ocorrencias_de_R$"] = html_zenrows.count("R$")

                # Amostra do meio do documento (não só o <head>), para inspeção visual real
                meio = len(html_zenrows) // 3
                resultado["amostra_html_zenrows"] = html_zenrows[meio:meio + 1500]

                # Título da página, útil para saber se caiu numa página de erro/redirecionamento
                title_tag = soup.find("title")
                resultado["titulo_pagina_zenrows"] = title_tag.get_text() if title_tag else None

                # Extrai uma amostra de classes CSS reais, filtrando por termos prováveis
                all_classes = set()
                for tag in soup.find_all(class_=True):
                    all_classes.update(tag.get("class", []))
                termos = ["card", "posting", "listing", "aviso", "property", "price", "list-item", "sc-"]
                resultado["classes_css_relevantes"] = sorted(
                    [c for c in all_classes if any(t in c.lower() for t in termos)]
                )[:40]

                # Amostra de todos os atributos data-* únicos (esses sites costumam usar data-qa, data-testid, etc.)
                data_attrs = set()
                for tag in soup.find_all(True):
                    for attr in tag.attrs:
                        if attr.startswith("data-"):
                            data_attrs.add(attr)
                resultado["atributos_data_encontrados"] = sorted(data_attrs)[:30]

                # Amostra de hrefs reais contendo padrões prováveis de anúncio —
                # essencial para ajustar a regex de identificação de cards
                hrefs_amostra = set()
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/imovel" in href or "/propriedades/" in href or re.search(r"\d{5,}", href):
                        hrefs_amostra.add(href)
                resultado["amostra_hrefs_anuncio"] = sorted(hrefs_amostra)[:15]

    return resultado


def find_cards_by_link_pattern(soup, href_regex: str, min_container_chars: int = 60):
    """
    Estratégia genérica para sites onde não temos um seletor de card confirmado
    (OLX, Zap): encontra os links de anúncio pelo padrão da URL, depois sobe
    na árvore HTML até achar um container com texto suficiente (que deve
    incluir preço, área etc.) — evita depender de nomes de classe CSS
    específicos, que tendem a mudar.
    """
    pattern = re.compile(href_regex)
    vistos = set()
    resultados = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not pattern.search(href):
            continue
        if href in vistos:
            continue

        container = link
        for _ in range(6):  # sobe até 6 níveis procurando um container "rico" em texto
            if container.parent is None:
                break
            container = container.parent
            texto = container.get_text(" ", strip=True)
            if len(texto) >= min_container_chars and "R$" in texto:
                break

        vistos.add(href)
        resultados.append((link, container))

    return resultados


def parse_generic_card(link, container, source: str, base_url: str) -> Optional[dict]:
    """
    Extração genérica por regex sobre o texto do container — usada para OLX e
    Zap, cujos seletores CSS exatos ainda não foram confirmados em produção
    (diferente do ImovelWeb, que já passou por esse ajuste fino).
    """
    try:
        source_url = link.get("href", "")
        if source_url.startswith("/"):
            source_url = base_url + source_url
        source_url = normalize_source_url(source_url)

        card_text = container.get_text(" ", strip=True)

        price_match = re.search(r"R\$\s*[\d.]+", card_text)
        price = parse_price(price_match.group(0)) if price_match else None

        area = parse_area(card_text)
        rooms_match = re.search(r"(\d+)\s*quarto", card_text)
        bathrooms_match = re.search(r"(\d+)\s*ban", card_text)
        parking_match = re.search(r"(\d+)\s*vaga", card_text)

        has_structured_tags = bool(re.search(r"(Lazer|Estrutura|Serviços Próximos)\s*:", card_text))
        parking_type, parking_source = extract_parking_type(card_text)
        if has_structured_tags and parking_type:
            parking_source = "tags"

        neighborhood_extraido = extract_neighborhood(card_text, fallback=None)

        return {
            "source": source,
            "source_url": source_url,
            "price": price,
            "area_total": area,
            "rooms": int(rooms_match.group(1)) if rooms_match else None,
            "bathrooms": int(bathrooms_match.group(1)) if bathrooms_match else None,
            "parking_spaces": int(parking_match.group(1)) if parking_match else None,
            "address": None,  # OLX/Zap não expõem endereço estruturado no card de listagem
            "neighborhood_extraido": neighborhood_extraido,
            "raw_description": card_text,
            "parking_type": parking_type,
            "parking_type_source": parking_source,
            "amenities": json.dumps(extract_amenities(card_text), ensure_ascii=False),
        }
    except Exception as e:
        logger.warning(f"Falha ao parsear card genérico ({source}): {e}")
        return None


# --- OLX ---
# Paginação NÃO resolvida (validado no protótipo — ?o=2 devolve a página 1 de novo).
# Por enquanto cobre só os primeiros ~50 resultados por busca.
OLX_BASE = "https://www.olx.com.br"


def build_search_url_olx(rooms: int = 3, uf: str = "ba", regiao: str = "grande-salvador", cidade: str = "salvador") -> str:
    return f"{OLX_BASE}/imoveis/venda/apartamentos/{rooms}-quartos/estado-{uf}/{regiao}/{cidade}"


def scrape_olx(neighborhood: str = "Salvador (geral)", city: str = "Salvador", rooms: int = 3) -> list[dict]:
    url = build_search_url_olx(rooms=rooms)
    logger.info(f"Buscando OLX: {url}")
    html = fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    # Links de anúncio no OLX terminam em uma sequência longa de dígitos (o ID do anúncio)
    cards = find_cards_by_link_pattern(soup, href_regex=r"/imoveis/.*-\d{6,}$")

    resultados = []
    for link, container in cards:
        item = parse_generic_card(link, container, source="olx", base_url=OLX_BASE)
        if item and item.get("price"):
            item["neighborhood"] = item.pop("neighborhood_extraido", None) or neighborhood
            item["city"] = city
            resultados.append(item)

    logger.info(f"OLX: {len(resultados)} anúncios extraídos.")
    return resultados


# --- Zap Imóveis ---
# Confirmado no protótipo: só funciona buscando por BAIRRO, não pela cidade inteira
# (busca ampla leva bloqueio 429 mesmo antes do Cloudflare entrar em ação).
ZAP_BASE = "https://www.zapimoveis.com.br"


def build_search_url_zap(bairro_slug: str, cidade_slug: str = "ba+salvador", rooms: int = 3) -> str:
    return f"{ZAP_BASE}/venda/apartamentos/{cidade_slug}++{bairro_slug}/{rooms}-quartos/"


def scrape_zap(bairros: list[str], city: str = "Salvador", rooms: int = 3) -> list[dict]:
    """
    bairros: lista de slugs de bairro (ex. ["caminho-das-arvores", "candeal", "itaigara"]).
    Itera um bairro por vez — é a única forma validada de acessar o Zap sem bloqueio.
    """
    resultados = []
    for bairro_slug in bairros:
        url = build_search_url_zap(bairro_slug, rooms=rooms)
        logger.info(f"Buscando Zap Imóveis ({bairro_slug}): {url}")
        html = fetch_page(url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        cards = find_cards_by_link_pattern(soup, href_regex=r"/imovel/.*-id-\d+")

        bairro_nome = bairro_slug.replace("-", " ").title()
        count_bairro = 0
        for link, container in cards:
            item = parse_generic_card(link, container, source="zap", base_url=ZAP_BASE)
            if item and item.get("price"):
                item.pop("neighborhood_extraido", None)
                item["neighborhood"] = bairro_nome
                item["city"] = city
                resultados.append(item)
                count_bairro += 1

        logger.info(f"Zap Imóveis ({bairro_slug}): {count_bairro} anúncios extraídos.")
        time.sleep(2)  # pausa entre bairros

    return resultados


def scrape_all_sources(cidade_slug: str, filtros_slug: str, neighborhood: str, city: str = "Salvador",
                        max_paginas: int = 3, zap_bairros: Optional[list[str]] = None) -> dict:
    """
    Roda as 3 fontes e combina os resultados. Cada fonte é isolada em seu
    próprio try/except — se uma falhar, as outras continuam normalmente.
    zap_bairros: lista de bairros para o Zap (obrigatório buscar por bairro).
    Se não informado, usa uma lista ampla cobrindo a maior parte de Salvador —
    os 5 bairros originais (caminho-das-arvores, candeal, itaigara, costa-azul,
    cabula) cobriam só uma fração pequena da cidade, o que fazia a busca parecer
    ter "poucas opções" mesmo com o Zap sendo a maior fonte de anúncios.
    """
    if zap_bairros is None:
        zap_bairros = [
            "caminho-das-arvores", "candeal", "itaigara", "costa-azul", "cabula",
            "pituba", "barra", "graca", "armacao", "imbui", "piata",
            "stella-maris", "boca-do-rio", "rio-vermelho", "ondina", "federacao",
            "brotas", "cajazeiras", "liberdade", "pernambues", "vila-laura",
            "nazare", "comercio", "pituacu", "paralela", "sao-marcos",
            "sussuarana", "alphaville", "amaralina", "canela", "horto-florestal",
            "jaguaribe", "patamares",
        ]

    todos = []
    resumo_por_fonte = {}

    try:
        dados_imovelweb = scrape_search(cidade_slug, filtros_slug, neighborhood, city, max_paginas)
        todos.extend(dados_imovelweb)
        resumo_por_fonte["imovelweb"] = len(dados_imovelweb)
    except Exception as e:
        logger.error(f"Falha no scraping do ImovelWeb: {e}")
        resumo_por_fonte["imovelweb"] = f"erro: {str(e)[:200]}"

    try:
        rooms = int(re.search(r"(\d+)", filtros_slug).group(1)) if re.search(r"(\d+)", filtros_slug) else 3
        dados_olx = scrape_olx(neighborhood, city, rooms=rooms)
        todos.extend(dados_olx)
        resumo_por_fonte["olx"] = len(dados_olx)
    except Exception as e:
        logger.error(f"Falha no scraping do OLX: {e}")
        resumo_por_fonte["olx"] = f"erro: {str(e)[:200]}"

    try:
        rooms = int(re.search(r"(\d+)", filtros_slug).group(1)) if re.search(r"(\d+)", filtros_slug) else 3
        dados_zap = scrape_zap(zap_bairros, city, rooms=rooms)
        todos.extend(dados_zap)
        resumo_por_fonte["zap"] = len(dados_zap)
    except Exception as e:
        logger.error(f"Falha no scraping do Zap Imóveis: {e}")
        resumo_por_fonte["zap"] = f"erro: {str(e)[:200]}"

    return {"itens": todos, "resumo_por_fonte": resumo_por_fonte}


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
