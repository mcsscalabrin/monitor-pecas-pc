"""Lê comandos enviados ao bot (getUpdates) e atualiza data/produtos.json.

Comandos: /add <url> [preco_alvo], /remover <id>, /listar, /ajuda.

Ordem importa para não perder nem duplicar comandos:
1. processa tudo em memória e grava os arquivos;
2. faz commit + push;
3. só então envia as respostas e confirma o offset no Telegram.
Se o push falhar, o last_update_id não avança no repo e os comandos serão
reprocessados na próxima execução (com /add idempotente por URL).
"""

import hashlib
import html
import re
import sys
from urllib.parse import unquote, urlparse, urlunparse

from scraper import git_sync, rede, storage, telegram
from scraper.main import brl
from scraper.sites import PREFIXOS, SITES, detectar_site, eh_encurtado
from scraper.sites import amazon
from scraper.sites._parse import parse_brl

AJUDA = (
    "<b>Comandos</b>\n"
    "/add &lt;url&gt; [preco_alvo] — monitora um produto\n"
    "   ex.: <code>/add https://www.kabum.com.br/produto/123 1500</code>\n"
    "/remover &lt;id&gt; — para de monitorar\n"
    "/listar — produtos monitorados\n"
    "/ajuda — esta mensagem\n\n"
    "Sites: Mercado Livre, Kabum, Terabyte, Pichau, Amazon.\n"
    "Aviso quando o preço cai ou fica ≤ preço-alvo."
)


def normalizar_url(url: str, site: str) -> str:
    if site == "amazon":
        return amazon.url_canonica(url)
    if site == "mercadolivre":
        return url  # o id do anúncio pode estar na query/fragmento
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def gerar_id(url: str, site: str, existentes: set[str]) -> str:
    digest = hashlib.sha1(url.encode()).hexdigest()
    for tamanho in range(6, 41):
        pid = f"{PREFIXOS[site]}-{digest[:tamanho]}"
        if pid not in existentes:
            return pid
    raise RuntimeError("não foi possível gerar id único")


def nome_pela_url(url: str) -> str:
    partes = [s for s in urlparse(url).path.split("/") if s]
    candidatos = [s for s in partes if "-" in s and not re.fullmatch(r"[A-Z]{3}-?\d+", s)]
    slug = unquote(max(candidatos, key=len) if candidatos else (partes[-1] if partes else url))
    slug = re.sub(r"^[A-Z]{3}-?\d+-", "", slug, flags=re.I)  # MLB-123456-nome-do-produto
    slug = re.sub(r"-_JM$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()[:80] or url[:80]


def cmd_add(args: list[str], produtos: list[dict]) -> tuple[str, dict | None]:
    if not args:
        return "Uso: <code>/add &lt;url&gt; [preco_alvo]</code>", None
    url = args[0].strip("<>")
    if not re.match(r"https?://", url, re.I):
        return "URL inválida: precisa começar com http:// ou https://", None

    alvo = None
    if len(args) > 1:
        alvo = parse_brl(" ".join(args[1:]))
        if alvo is None:
            return f"Preço-alvo inválido: <code>{html.escape(' '.join(args[1:]))}</code>", None

    if eh_encurtado(url):
        url = rede.resolver_redirect(url)
    site = detectar_site(url)
    if not site:
        return (
            "Site não suportado. Use Mercado Livre, Kabum, Terabyte, Pichau ou Amazon.",
            None,
        )
    url = normalizar_url(url, site)

    for p in produtos:
        if p.get("url") == url:
            return f"Esse produto já está na lista com id <code>{html.escape(p['id'])}</code>.", None

    nome, preco_atual = None, None
    try:
        preco_atual, nome = SITES[site].obter_preco(url)
    except Exception as e:  # best effort: o /add funciona mesmo se o scraping falhar agora
        print(f"  [add] não consegui ler o preço agora: {type(e).__name__}: {e}")

    produto = {
        "id": gerar_id(url, site, {p.get("id") for p in produtos}),
        "site": site,
        "url": url,
        "nome": (nome or nome_pela_url(url)).strip()[:120],
        "preco_alvo": round(alvo, 2) if alvo is not None else None,
    }
    resposta = [
        "✅ Adicionado!",
        f"<b>{html.escape(produto['nome'])}</b>",
        f"id: <code>{produto['id']}</code> · {site}",
        f"Alvo: {brl(produto['preco_alvo'])}",
        f"Preço agora: {brl(preco_atual)}" if preco_atual else "Preço agora: não consegui ler (vou tentar na próxima checagem)",
    ]
    return "\n".join(resposta), produto


def cmd_remover(args: list[str], produtos: list[dict]) -> tuple[str, str | None]:
    if not args:
        return "Uso: <code>/remover &lt;id&gt;</code> (veja os ids com /listar)", None
    pid = args[0].strip()
    for p in produtos:
        if p.get("id") == pid:
            return f"🗑️ Removido: <b>{html.escape(p.get('nome') or pid)}</b> (<code>{html.escape(pid)}</code>)", pid
    return f"Não achei o id <code>{html.escape(pid)}</code>. Veja /listar.", None


def cmd_listar(produtos: list[dict], estado: dict) -> str:
    if not produtos:
        return "Nenhum produto monitorado. Use /add &lt;url&gt; [preco_alvo]."
    linhas = [f"<b>{len(produtos)} produto(s) monitorado(s)</b>", ""]
    for p in produtos:
        e = estado.get(p.get("id"), {})
        quando = (e.get("atualizado_em") or "")[:16].replace("T", " ")
        categoria = f" · {html.escape(p['categoria'])}" if p.get("categoria") else ""
        linhas.append(f"<code>{html.escape(str(p.get('id')))}</code> · {html.escape(str(p.get('site')))}{categoria}")
        linhas.append(f"<b>{html.escape(p.get('nome') or '')}</b>")
        linhas.append(
            f"Último: {brl(e.get('ultimo_preco'))}"
            + (f" ({quando} UTC)" if quando else "")
            + f" · Alvo: {brl(p.get('preco_alvo'))}"
        )
        linhas.append("")
    return "\n".join(linhas).strip()


def aplicar_operacoes(produtos: list[dict], operacoes: list[tuple[str, object]]) -> list[dict]:
    resultado = list(produtos)
    for tipo, valor in operacoes:
        if tipo == "add":
            if not any(p.get("url") == valor["url"] or p.get("id") == valor["id"] for p in resultado):
                resultado.append(valor)
        elif tipo == "remover":
            resultado = [p for p in resultado if p.get("id") != valor]
    return resultado


def main() -> int:
    try:
        meu_chat = telegram.chat_id()
        telegram.api("deleteWebhook", drop_pending_updates=False)
        last_id = storage.ler_last_update_id()
        updates = telegram.api(
            "getUpdates", offset=last_id + 1, timeout=0, allowed_updates=["message"]
        ) or []
    except telegram.TelegramErro as e:
        print(f"[bot] ERRO: {e}")
        return 1

    print(f"[bot] {len(updates)} update(s) novo(s) (last_update_id={last_id})")
    if not updates:
        return 0

    produtos = storage.ler_produtos()
    estado = storage.ler_estado()
    operacoes: list[tuple[str, object]] = []
    respostas: list[str] = []
    max_id = last_id

    for upd in updates:
        uid = upd.get("update_id", 0)
        max_id = max(max_id, uid)
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != meu_chat:
            print(f"[bot] update {uid}: chat não autorizado, ignorado")
            continue
        texto = (msg.get("text") or "").strip()
        if not texto.startswith("/"):
            print(f"[bot] update {uid}: não é comando, ignorado")
            continue

        partes = texto.split()
        comando = partes[0].split("@")[0].lower()
        args = partes[1:]
        print(f"[bot] update {uid}: {comando} {' '.join(args)[:200]}")

        try:
            if comando == "/add":
                resposta, novo = cmd_add(args, produtos)
                if novo:
                    operacoes.append(("add", novo))
                    produtos = aplicar_operacoes(produtos, [("add", novo)])
            elif comando == "/remover":
                resposta, pid = cmd_remover(args, produtos)
                if pid:
                    operacoes.append(("remover", pid))
                    produtos = aplicar_operacoes(produtos, [("remover", pid)])
            elif comando == "/listar":
                resposta = cmd_listar(produtos, estado)
            else:  # /ajuda, /start e desconhecidos
                resposta = AJUDA
        except Exception as e:
            print(f"[bot] update {uid}: erro processando {comando}: {type(e).__name__}: {e}")
            resposta = f"⚠️ Erro ao processar {html.escape(comando)}: {html.escape(type(e).__name__)}"
        respostas.append(resposta)

    if operacoes:
        storage.gravar_json(storage.PRODUTOS, produtos)
    storage.gravar_json(storage.LAST_UPDATE_ID, {"last_update_id": max_id})

    def reaplicar():
        remoto = storage.ler_produtos()
        storage.gravar_json(storage.PRODUTOS, aplicar_operacoes(remoto, operacoes))
        storage.gravar_json(
            storage.LAST_UPDATE_ID,
            {"last_update_id": max(max_id, storage.ler_last_update_id())},
        )

    ok = git_sync.commit_e_push(
        ["data/produtos.json", "data/last_update_id.json"],
        f"chore: processa comandos do bot (até update {max_id})",
        reaplicar,
    )
    if not ok:
        telegram.enviar_mensagem(
            "⚠️ Não consegui salvar as alterações no repositório. Vou tentar de novo na próxima execução."
        )
        return 1

    for resposta in respostas:
        telegram.enviar_mensagem(resposta, meu_chat)

    try:  # confirma os updates no Telegram para não recebê-los de novo
        telegram.api("getUpdates", offset=max_id + 1, limit=1, timeout=0)
    except telegram.TelegramErro as e:
        print(f"[bot] aviso: não confirmei o offset ({e}); o last_update_id salvo já evita repetição")

    print(f"[bot] fim: {len(respostas)} resposta(s), {len(operacoes)} alteração(ões) em produtos.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
