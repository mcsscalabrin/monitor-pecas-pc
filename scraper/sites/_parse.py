"""Utilitários de parsing compartilhados pelos scrapers de HTML.

O HTML das lojas muda sem aviso, então cada extrator é uma estratégia
independente; `primeiro_preco` tenta todas em ordem e registra qual funcionou.
"""

import json
import re
from typing import Any, Callable, Iterable

from bs4 import BeautifulSoup


class PrecoNaoEncontrado(Exception):
    pass


def sopa(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def parse_brl(valor: Any) -> float | None:
    """Converte 'R$ 1.234,56', '1234.56', 1234.56 etc. em float. None se não der."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None

    texto = re.sub(r"[^\d,.]", "", str(valor))
    if not texto or not re.search(r"\d", texto):
        return None
    if "," in texto and "." in texto:
        # o separador que aparece por último é o decimal
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", texto):
        # '1.234' ou '1.234.567' = separador de milhar
        texto = texto.replace(".", "")
    try:
        num = float(texto)
    except ValueError:
        return None
    return num if num > 0 else None


def _iterar_jsonld(soup: BeautifulSoup) -> Iterable[dict]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pilha = [dados]
        while pilha:
            item = pilha.pop()
            if isinstance(item, list):
                pilha.extend(item)
            elif isinstance(item, dict):
                yield item
                if "@graph" in item:
                    pilha.append(item["@graph"])


def preco_jsonld(soup: BeautifulSoup) -> tuple[float | None, str | None]:
    for item in _iterar_jsonld(soup):
        tipo = item.get("@type")
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if "Product" not in tipos:
            continue
        nome = item.get("name")
        ofertas = item.get("offers")
        ofertas = ofertas if isinstance(ofertas, list) else [ofertas]
        for oferta in ofertas:
            if not isinstance(oferta, dict):
                continue
            for chave in ("price", "lowPrice"):
                preco = parse_brl(oferta.get(chave))
                if preco:
                    return preco, nome
            spec = oferta.get("priceSpecification")
            specs = spec if isinstance(spec, list) else [spec]
            for s in specs:
                if isinstance(s, dict) and parse_brl(s.get("price")):
                    return parse_brl(s.get("price")), nome
    return None, None


def _url_imagem(valor: Any) -> str | None:
    """JSON-LD aceita `image` como string, lista ou ImageObject."""
    if isinstance(valor, list):
        for v in valor:
            if url := _url_imagem(v):
                return url
        return None
    if isinstance(valor, dict):
        return _url_imagem(valor.get("url") or valor.get("contentUrl"))
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def imagem(soup: BeautifulSoup) -> str | None:
    """URL da foto do produto: JSON-LD de Product, depois og:image / twitter:image."""
    for item in _iterar_jsonld(soup):
        tipo = item.get("@type")
        if "Product" in (tipo if isinstance(tipo, list) else [tipo]):
            if url := _url_imagem(item.get("image")):
                return url
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def preco_meta(soup: BeautifulSoup) -> float | None:
    seletores = [
        ('meta', {"itemprop": "price"}),
        ('meta', {"property": "product:price:amount"}),
        ('meta', {"property": "og:price:amount"}),
    ]
    for nome_tag, attrs in seletores:
        tag = soup.find(nome_tag, attrs=attrs)
        if tag and parse_brl(tag.get("content")):
            return parse_brl(tag.get("content"))
    tag = soup.find(attrs={"itemprop": "price"})
    if tag:
        return parse_brl(tag.get("content") or tag.get_text())
    return None


def next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return None
    try:
        return json.loads(tag.string or tag.get_text())
    except (json.JSONDecodeError, TypeError):
        return None


def buscar_chaves(dados: Any, chaves: tuple[str, ...], limite: int = 50000) -> list[Any]:
    """Busca em largura por valores cujas chaves estejam em `chaves`, na ordem encontrada."""
    achados, fila, vistos = [], [dados], 0
    while fila and vistos < limite:
        atual = fila.pop(0)
        vistos += 1
        if isinstance(atual, dict):
            for k, v in atual.items():
                if k in chaves and not isinstance(v, (dict, list)):
                    achados.append((chaves.index(k), v))
                if isinstance(v, (dict, list)):
                    fila.append(v)
        elif isinstance(atual, list):
            fila.extend(atual)
    return [v for _, v in sorted(achados, key=lambda x: x[0])]


def titulo(soup: BeautifulSoup) -> str | None:
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def primeiro_preco(
    rotulo: str, estrategias: list[tuple[str, Callable[[], float | None]]]
) -> float:
    """Executa estratégias em ordem; devolve o primeiro preço válido."""
    erros = []
    for nome, func in estrategias:
        try:
            preco = func()
        except Exception as e:  # HTML imprevisível: nenhuma estratégia pode derrubar as outras
            erros.append(f"{nome}: {type(e).__name__}")
            continue
        if preco:
            print(f"  [{rotulo}] preço via {nome}: {preco:.2f}")
            return preco
        erros.append(f"{nome}: vazio")
    raise PrecoNaoEncontrado(f"nenhuma estratégia encontrou preço ({'; '.join(erros)})")
