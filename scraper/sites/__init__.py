"""Roteamento por site: nome do site -> módulo com `obter_preco(url)`."""

from urllib.parse import urljoin, urlparse

from scraper import rede
from scraper.sites import amazon, kabum, mercadolivre, pichau, terabyte
from scraper.sites._parse import PrecoNaoEncontrado, imagem, sopa

SITES = {
    "mercadolivre": mercadolivre,
    "kabum": kabum,
    "terabyte": terabyte,
    "pichau": pichau,
    "amazon": amazon,
}

PREFIXOS = {
    "mercadolivre": "ml",
    "kabum": "kb",
    "terabyte": "tb",
    "pichau": "pc",
    "amazon": "az",
}

# domínio (sufixo) -> site
DOMINIOS = {
    "mercadolivre.com.br": "mercadolivre",
    "mercadolibre.com": "mercadolivre",
    "meli.la": "mercadolivre",
    "kabum.com.br": "kabum",
    "terabyteshop.com.br": "terabyte",
    "pichau.com.br": "pichau",
    "amazon.com.br": "amazon",
    "amazon.com": "amazon",
    "amzn.to": "amazon",
    "a.co": "amazon",
}

ENCURTADORES = {"amzn.to", "a.co", "meli.la"}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def detectar_site(url: str) -> str | None:
    host = _host(url)
    for dominio, site in DOMINIOS.items():
        if host == dominio or host.endswith("." + dominio):
            return site
    return None


def eh_encurtado(url: str) -> bool:
    return _host(url) in ENCURTADORES


def obter_imagem(url: str) -> str | None:
    """Foto do produto (genérico para todas as lojas). Usado só no cadastro, não a cada checagem."""
    foto = imagem(sopa(rede.get_html(url)))
    if not foto:
        return None
    foto = urljoin(url, foto)
    return foto if foto.startswith(("http://", "https://")) else None


__all__ = ["SITES", "PREFIXOS", "PrecoNaoEncontrado", "detectar_site", "eh_encurtado", "obter_imagem"]
