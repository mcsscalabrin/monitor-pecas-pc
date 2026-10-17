"""Painel web local para editar data/produtos.json (peças monitoradas).

Uso:
    python -m painel              # abre http://127.0.0.1:8765
    python -m painel --porta 9000 --sem-navegador

Só escuta em 127.0.0.1. Os workflows do GitHub Actions leem data/produtos.json
do repositório, então depois de editar use "Enviar para o GitHub" (commit +
pull --rebase + push de data/produtos.json).
"""

import argparse
import json
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bot.processar_comandos import gerar_id, nome_pela_url, normalizar_url
from scraper import rede, storage
from scraper.sites import SITES, detectar_site, eh_encurtado, obter_imagem
from scraper.sites._parse import parse_brl

INDEX = Path(__file__).with_name("index.html")
CATEGORIAS = [
    "Processador", "Placa de vídeo", "Placa-mãe", "Memória RAM", "SSD", "HD",
    "Fonte", "Gabinete", "Cooler", "Water cooler", "Monitor", "Periférico", "Outro",
]
_trava = threading.Lock()  # serializa leitura+escrita de produtos.json


class ErroPainel(Exception):
    pass


def _alvo(valor) -> float | None:
    if valor in (None, ""):
        return None
    alvo = parse_brl(valor)
    if alvo is None or alvo <= 0:
        raise ErroPainel(f"preço-alvo inválido: {valor!r}")
    return round(alvo, 2)


def _texto(valor, limite: int) -> str:
    return str(valor or "").strip()[:limite]


def listar() -> dict:
    return {
        "produtos": storage.ler_produtos(),
        "estado": storage.ler_estado(),
        "categorias": CATEGORIAS,
        "sites": sorted(SITES),
    }


def adicionar(dados: dict) -> dict:
    url = _texto(dados.get("url"), 2000)
    if not url.lower().startswith(("http://", "https://")):
        raise ErroPainel("URL inválida: precisa começar com http:// ou https://")
    alvo = _alvo(dados.get("preco_alvo"))
    if eh_encurtado(url):
        url = rede.resolver_redirect(url)
    site = detectar_site(url)
    if not site:
        raise ErroPainel("site não suportado (use Kabum, Terabyte, Pichau, Amazon ou Mercado Livre)")
    url = normalizar_url(url, site)

    nome, preco_atual, imagem = _texto(dados.get("nome"), 120), None, None
    if dados.get("buscar_preco", True):
        try:
            preco_atual, nome_site = SITES[site].obter_preco(url)
            nome = nome or (nome_site or "")
        except Exception as e:  # best effort, igual ao /add do bot
            print(f"[painel] não consegui ler o preço agora: {type(e).__name__}: {e}")
        try:
            imagem = obter_imagem(url)
        except Exception as e:
            print(f"[painel] não consegui ler a imagem: {type(e).__name__}: {e}")

    with _trava:
        produtos = storage.ler_produtos()
        for p in produtos:
            if p.get("url") == url:
                raise ErroPainel(f"esse produto já está na lista (id {p['id']})")
        produto = {
            "id": gerar_id(url, site, {p.get("id") for p in produtos}),
            "site": site,
            "url": url,
            "nome": (nome or nome_pela_url(url)).strip()[:120],
            "categoria": _texto(dados.get("categoria"), 40) or None,
            "preco_alvo": alvo,
            "imagem": imagem,
        }
        produtos.append(produto)
        storage.gravar_json(storage.PRODUTOS, produtos)
    return {"produto": produto, "preco_atual": preco_atual}


def editar(pid: str, dados: dict) -> dict:
    with _trava:
        produtos = storage.ler_produtos()
        for p in produtos:
            if p.get("id") == pid:
                if "nome" in dados:
                    p["nome"] = _texto(dados["nome"], 120) or p.get("nome")
                if "categoria" in dados:
                    p["categoria"] = _texto(dados["categoria"], 40) or None
                if "preco_alvo" in dados:
                    p["preco_alvo"] = _alvo(dados["preco_alvo"])
                storage.gravar_json(storage.PRODUTOS, produtos)
                return {"produto": p}
    raise ErroPainel(f"id não encontrado: {pid}")


def remover(pid: str) -> dict:
    with _trava:
        produtos = storage.ler_produtos()
        restantes = [p for p in produtos if p.get("id") != pid]
        if len(restantes) == len(produtos):
            raise ErroPainel(f"id não encontrado: {pid}")
        storage.gravar_json(storage.PRODUTOS, restantes)
    return {"removido": pid}


def checar(pid: str) -> dict:
    produto = next((p for p in storage.ler_produtos() if p.get("id") == pid), None)
    if produto is None:
        raise ErroPainel(f"id não encontrado: {pid}")
    try:
        preco, nome = SITES[produto["site"]].obter_preco(produto["url"])
    except Exception as e:
        raise ErroPainel(f"falha ao ler o preço: {type(e).__name__}: {e}") from e
    return {"preco": preco, "nome": nome}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=storage.RAIZ, capture_output=True, text=True)


def sincronizar() -> dict:
    """Commita data/produtos.json, traz o que os workflows commitaram e faz push."""
    log = []

    def passo(*args: str) -> subprocess.CompletedProcess:
        proc = _git(*args)
        log.append(f"$ git {' '.join(args)}\n{(proc.stdout + proc.stderr).strip()}".strip())
        return proc

    with _trava:
        passo("add", "--", "data/produtos.json")
        if _git("diff", "--cached", "--quiet").returncode != 0:
            if passo("commit", "-m", "chore: atualiza produtos pelo painel").returncode != 0:
                raise ErroPainel("commit falhou\n\n" + "\n\n".join(log))
        if passo("pull", "--rebase", "--autostash").returncode != 0:
            _git("rebase", "--abort")
            raise ErroPainel("pull --rebase falhou (conflito?). Resolva no git.\n\n" + "\n\n".join(log))
        if passo("push").returncode != 0:
            raise ErroPainel("push falhou\n\n" + "\n\n".join(log))
    return {"log": "\n\n".join(log)}


class Handler(BaseHTTPRequestHandler):
    def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, status: int, dados) -> None:
        self._responder(status, json.dumps(dados, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _corpo(self) -> dict:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if not tamanho:
            return {}
        dados = json.loads(self.rfile.read(tamanho))
        if not isinstance(dados, dict):
            raise ErroPainel("corpo da requisição precisa ser um objeto JSON")
        return dados

    def _rotear(self, metodo: str) -> None:
        partes = [p for p in self.path.split("?")[0].split("/") if p]
        try:
            if metodo == "GET" and not partes:
                self._responder(HTTPStatus.OK, INDEX.read_bytes(), "text/html; charset=utf-8")
                return
            if partes[:1] != ["api"]:
                self._json(HTTPStatus.NOT_FOUND, {"erro": "não encontrado"})
                return
            rota = partes[1:]
            if metodo == "GET" and rota == ["produtos"]:
                resultado = listar()
            elif metodo == "POST" and rota == ["produtos"]:
                resultado = adicionar(self._corpo())
            elif metodo == "PATCH" and len(rota) == 2 and rota[0] == "produtos":
                resultado = editar(rota[1], self._corpo())
            elif metodo == "DELETE" and len(rota) == 2 and rota[0] == "produtos":
                resultado = remover(rota[1])
            elif metodo == "POST" and len(rota) == 2 and rota[0] == "checar":
                resultado = checar(rota[1])
            elif metodo == "POST" and rota == ["sincronizar"]:
                resultado = sincronizar()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"erro": "rota não encontrada"})
                return
            self._json(HTTPStatus.OK, resultado)
        except (ErroPainel, json.JSONDecodeError) as e:
            self._json(HTTPStatus.BAD_REQUEST, {"erro": str(e)})
        except Exception as e:
            print(f"[painel] erro inesperado: {type(e).__name__}: {e}")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"erro": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._rotear("GET")

    def do_POST(self):
        self._rotear("POST")

    def do_PATCH(self):
        self._rotear("PATCH")

    def do_DELETE(self):
        self._rotear("DELETE")

    def log_message(self, formato, *args):
        print(f"[painel] {self.address_string()} {formato % args}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Painel local para editar os produtos monitorados")
    p.add_argument("--porta", type=int, default=8765)
    p.add_argument("--sem-navegador", action="store_true", help="não abre o navegador automaticamente")
    args = p.parse_args(argv)

    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Handler)
    endereco = f"http://127.0.0.1:{args.porta}"
    print(f"[painel] rodando em {endereco} (Ctrl+C para sair)")
    if not args.sem_navegador:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n[painel] encerrado")
