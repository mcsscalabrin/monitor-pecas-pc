"""Checa os preços dos produtos em data/produtos.json e avisa no Telegram.

Uso:
    python -m scraper.main                    # todos os sites
    python -m scraper.main --excluir amazon   # workflow de 1h
    python -m scraper.main --apenas amazon    # workflow de 3h
    python -m scraper.main --sem-telegram     # teste local, só loga
"""

import argparse
import html
import sys
from datetime import datetime, timezone

from scraper import git_sync, storage, telegram
from scraper.sites import SITES


def brl(valor: float | None) -> str:
    if valor is None:
        return "—"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def motivos_alerta(antigo: float | None, novo: float, alvo: float | None) -> list[str]:
    """Regra: avisa se caiu, ou se cruzou o alvo agora (não repete enquanto segue abaixo)."""
    motivos = []
    if antigo is not None and novo < antigo - 0.005:
        motivos.append("caiu")
    if alvo is not None and novo <= alvo and (antigo is None or antigo > alvo):
        motivos.append("alvo")
    return motivos


def montar_mensagem(produto: dict, antigo: float | None, novo: float, motivos: list[str]) -> str:
    nome = html.escape(produto.get("nome") or produto["id"])
    linhas = []
    if "alvo" in motivos:
        linhas.append("🎯 <b>Preço-alvo atingido!</b>")
    else:
        linhas.append("📉 <b>Preço caiu!</b>")
    categoria = f"{html.escape(produto['categoria'])} · " if produto.get("categoria") else ""
    linhas.append(f"<b>{nome}</b> ({categoria}{produto['site']})")
    linhas.append(f"Antes: {brl(antigo)}")
    variacao = ""
    if antigo:
        variacao = f" ({(novo - antigo) / antigo * 100:+.1f}%)"
    linhas.append(f"Agora: <b>{brl(novo)}</b>{variacao}")
    if produto.get("preco_alvo") is not None:
        linhas.append(f"Alvo: {brl(produto['preco_alvo'])}")
    linhas.append(f'<a href="{html.escape(produto["url"], quote=True)}">Abrir produto</a>')
    linhas.append(f"<code>{html.escape(produto['id'])}</code>")
    return "\n".join(linhas)


def _aplicar(estado: dict, atualizacoes: dict, ids_validos: set[str]) -> dict:
    novo = {k: v for k, v in estado.items() if k in ids_validos}
    for pid, valor in atualizacoes.items():
        if pid in ids_validos:
            novo[pid] = valor
    return novo


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Checa preços e avisa no Telegram")
    p.add_argument("--apenas", default="", help="sites a checar, separados por vírgula")
    p.add_argument("--excluir", default="", help="sites a ignorar, separados por vírgula")
    p.add_argument("--sem-telegram", action="store_true", help="não envia mensagens (teste local)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    apenas = {s.strip() for s in args.apenas.split(",") if s.strip()}
    excluir = {s.strip() for s in args.excluir.split(",") if s.strip()}

    produtos = storage.ler_produtos()
    estado = storage.ler_estado()
    selecionados = [
        p for p in produtos
        if isinstance(p, dict) and (not apenas or p.get("site") in apenas) and p.get("site") not in excluir
    ]
    print(f"[main] {len(selecionados)} produto(s) para checar (de {len(produtos)} no total)")

    atualizacoes: dict[str, dict] = {}
    falhas, alertas = [], 0

    for produto in selecionados:
        pid, site, url = produto.get("id"), produto.get("site"), produto.get("url")
        print(f"[{pid}] {site}: {url}")
        modulo = SITES.get(site)
        if not pid or not url or modulo is None:
            print(f"[{pid}] ignorado: produto inválido ou site desconhecido ({site!r})")
            falhas.append(pid)
            continue

        try:
            novo, _ = modulo.obter_preco(url)
        except Exception as e:  # um produto falhar não pode derrubar os outros
            print(f"[{pid}] ERRO: {type(e).__name__}: {e}")
            falhas.append(pid)
            continue

        antigo = (estado.get(pid) or {}).get("ultimo_preco")
        alvo = produto.get("preco_alvo")
        motivos = motivos_alerta(antigo, novo, alvo)
        print(f"[{pid}] antigo={brl(antigo)} novo={brl(novo)} alvo={brl(alvo)} alerta={motivos or 'não'}")

        if motivos:
            alertas += 1
            texto = montar_mensagem(produto, antigo, novo, motivos)
            if args.sem_telegram:
                print(f"[{pid}] (sem-telegram) mensagem:\n{texto}")
            else:
                telegram.enviar_mensagem(texto)

        atualizacoes[pid] = {
            "ultimo_preco": round(novo, 2),
            "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    ids_validos = {p.get("id") for p in produtos if isinstance(p, dict)}
    storage.gravar_json(storage.ESTADO, _aplicar(estado, atualizacoes, ids_validos))

    def reaplicar():
        # Após alinhar com origin: relê produtos/estado remotos e reaplica nossas atualizações.
        ids = {p.get("id") for p in storage.ler_produtos() if isinstance(p, dict)}
        storage.gravar_json(storage.ESTADO, _aplicar(storage.ler_estado(), atualizacoes, ids))

    rotulo = ",".join(sorted(apenas)) if apenas else "todos" + (f" exceto {','.join(sorted(excluir))}" if excluir else "")
    ok = git_sync.commit_e_push(
        ["data/estado.json"],
        f"chore: atualiza preços ({rotulo})",
        reaplicar,
    )

    print(
        f"[main] resumo: {len(atualizacoes)} ok, {len(falhas)} falha(s)"
        f"{' (' + ', '.join(map(str, falhas)) + ')' if falhas else ''}, {alertas} alerta(s)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
