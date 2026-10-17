"""Cliente mínimo da Bot API do Telegram.

O token NUNCA deve aparecer em logs. Como a URL da API contém o token,
exceções do `requests` (que incluem a URL na mensagem) são capturadas aqui
e relançadas como `TelegramErro` com uma mensagem sanitizada.
"""

import os

import requests

API_BASE = "https://api.telegram.org"
LIMITE_MENSAGEM = 4096
LIMITE_LEGENDA = 1024


class TelegramErro(Exception):
    pass


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise TelegramErro("variável de ambiente TELEGRAM_BOT_TOKEN não definida")
    return token


def chat_id() -> str:
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not cid:
        raise TelegramErro("variável de ambiente TELEGRAM_CHAT_ID não definida")
    return cid


def _sanitizar(texto: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    return texto.replace(token, "***") if token else texto


def api(metodo: str, http_timeout: float = 30, **params):
    """Chama um método da Bot API e devolve o campo `result`."""
    url = f"{API_BASE}/bot{_token()}/{metodo}"
    try:
        resp = requests.post(url, json=params, timeout=http_timeout)
    except requests.RequestException as e:
        # `from None` evita que o traceback original (com a URL) seja impresso.
        raise TelegramErro(f"{metodo}: erro de rede ({type(e).__name__}: {_sanitizar(str(e))})") from None

    try:
        dados = resp.json()
    except ValueError:
        raise TelegramErro(f"{metodo}: resposta não-JSON (HTTP {resp.status_code})") from None

    if not dados.get("ok"):
        descricao = _sanitizar(str(dados.get("description", "sem descrição")))
        raise TelegramErro(f"{metodo}: HTTP {resp.status_code} - {descricao}")
    return dados.get("result")


def _dividir(texto: str) -> list[str]:
    """Quebra mensagens longas em blocos de linhas inteiras (não corta tags HTML)."""
    partes, atual = [], ""
    for linha in texto.split("\n"):
        while len(linha) > LIMITE_MENSAGEM:  # linha gigante: corta no seco
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(linha[:LIMITE_MENSAGEM])
            linha = linha[LIMITE_MENSAGEM:]
        candidato = f"{atual}\n{linha}" if atual else linha
        if len(candidato) > LIMITE_MENSAGEM:
            partes.append(atual)
            atual = linha
        else:
            atual = candidato
    if atual or not partes:
        partes.append(atual)
    return partes


def enviar_mensagem(texto: str, destino: str | None = None) -> bool:
    """Envia uma mensagem em HTML para o chat configurado. Devolve True se enviou."""
    destino = destino or chat_id()
    partes = _dividir(texto)
    try:
        for parte in partes:
            api(
                "sendMessage",
                chat_id=destino,
                text=parte,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return True
    except TelegramErro as e:
        print(f"[telegram] falha ao enviar mensagem: {e}")
        return False


def enviar_foto(foto_url: str, legenda: str, destino: str | None = None) -> bool:
    """Envia a foto com a legenda em HTML; se o Telegram recusar a imagem, cai para texto."""
    destino = destino or chat_id()
    if len(legenda) <= LIMITE_LEGENDA:
        try:
            api("sendPhoto", chat_id=destino, photo=foto_url, caption=legenda, parse_mode="HTML")
            return True
        except TelegramErro as e:
            print(f"[telegram] falha ao enviar foto ({e}); enviando só o texto")
    return enviar_mensagem(legenda, destino)
