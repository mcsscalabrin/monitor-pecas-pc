"""Pichau (requests + BeautifulSoup).

A página é Next.js (app router). O preço à vista no PIX vem no payload RSC
como `"pichau_prices":{"avista":5399.99,...}` (com aspas escapadas); o JSON-LD
de Product traz só o preço cheio (`offers.price`). Fallbacks: JSON-LD,
`__NEXT_DATA__` (layout antigo) e meta tags. Usa Cloudflare; o HTML pode mudar
sem aviso.
"""

import re

from scraper import rede
from scraper.sites._parse import (
    buscar_chaves,
    next_data,
    parse_brl,
    preco_jsonld,
    preco_meta,
    primeiro_preco,
    sopa,
    titulo,
)

CHAVES_PRECO = ("price_pix", "final_price", "special_price", "price")
RE_AVISTA = re.compile(r'pichau_prices\\*"\s*:\s*\{\s*\\*"avista\\*"\s*:\s*([\d.]+)')


def obter_preco(url: str) -> tuple[float, str | None]:
    html = rede.get_html(url)
    soup = sopa(html)
    nome = {}

    def via_pix():
        m = RE_AVISTA.search(html)
        return float(m.group(1)) if m else None

    def via_jsonld():
        preco, n = preco_jsonld(soup)
        nome["v"] = n
        return preco

    def via_next_data():
        dados = next_data(soup)
        if not dados:
            return None
        for valor in buscar_chaves(dados, CHAVES_PRECO):
            preco = parse_brl(valor)
            if preco:
                return preco
        return None

    preco = primeiro_preco("pichau", [
        ("pix", via_pix),
        ("json-ld", via_jsonld),
        ("__NEXT_DATA__", via_next_data),
        ("meta", lambda: preco_meta(soup)),
    ])
    return preco, nome.get("v") or titulo(soup)
