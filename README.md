# Bot de Telegram para monitorar preços

Bot pessoal, 100% gratuito, que roda **só** com GitHub Actions (sem servidor).
Ele monitora preços no **Mercado Livre, Kabum, Terabyte, Pichau e Amazon** e
te avisa no Telegram quando:

- o preço **cai** em relação à última checagem, ou
- o preço **cruza** o preço-alvo que você definiu (avisa uma vez ao cruzar; se
  continuar abaixo do alvo sem mudar, não repete — se cair mais, avisa de novo).

Você controla a lista de produtos mandando comandos pro próprio bot.

## Como funciona

| Workflow | Frequência | O que faz |
|---|---|---|
| `checar-precos.yml` | a cada 1h | Mercado Livre, Kabum, Terabyte, Pichau |
| `checar-precos-amazon.yml` | a cada 3h | Amazon (separado por risco de bloqueio) |
| `processar-comandos.yml` | a cada 5min | Lê comandos do Telegram (`getUpdates`) |

O estado fica em arquivos JSON dentro do repositório (`data/`), atualizados
por commits automáticos feitos pelos workflows:

- `data/produtos.json` — produtos monitorados
- `data/estado.json` — último preço conhecido de cada produto
- `data/last_update_id.json` — último update do Telegram já processado

## Configuração

### 1. Criar o bot no BotFather

1. No Telegram, abra uma conversa com [@BotFather](https://t.me/BotFather).
2. Envie `/newbot`, escolha um nome e um username (precisa terminar em `bot`).
3. O BotFather responde com o **token** (algo como `123456789:AAH...`).
   Guarde-o: ele é o `TELEGRAM_BOT_TOKEN`. **Nunca** coloque o token no código.

Opcional: envie `/setcommands` ao BotFather, escolha seu bot e cole:

```
add - Monitorar produto: /add <url> [preco_alvo]
remover - Parar de monitorar: /remover <id>
listar - Listar produtos monitorados
ajuda - Ajuda
```

### 2. Descobrir o seu chat_id

1. Abra uma conversa com o seu bot e mande qualquer mensagem (ex.: `oi`).
2. No navegador, abra (trocando `<TOKEN>` pelo seu token):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Procure `"chat":{"id": 123456789, ...}` — esse número é o `TELEGRAM_CHAT_ID`.

Alternativa: mande uma mensagem para [@userinfobot](https://t.me/userinfobot),
que responde com o seu id.

> Se o `getUpdates` vier vazio, é porque o bot tem webhook configurado. Abra
> `https://api.telegram.org/bot<TOKEN>/deleteWebhook` e tente de novo.
> (O workflow de comandos também chama `deleteWebhook` a cada execução.)

### 3. Criar o repositório e configurar os secrets

1. Crie um repositório **público** no GitHub (Actions é ilimitado e grátis
   para repositórios públicos) e faça push deste código.
2. Em **Settings → Secrets and variables → Actions → New repository secret**,
   crie:
   - `TELEGRAM_BOT_TOKEN` = token do BotFather
   - `TELEGRAM_CHAT_ID` = seu chat_id
3. Em **Settings → Actions → General → Workflow permissions**, marque
   **Read and write permissions** e salve (os workflows também declaram
   `permissions: contents: write`, mas alguns repositórios/organizações
   bloqueiam isso por padrão).
4. Na aba **Actions**, habilite os workflows se o GitHub pedir. Para testar
   na hora, abra cada workflow e clique em **Run workflow**.

Pronto: mande `/ajuda` pro bot. A resposta chega na próxima execução do
workflow de comandos (até ~5 min, às vezes mais).

## Comandos

| Comando | Exemplo |
|---|---|
| `/add <url> [preco_alvo]` | `/add https://www.kabum.com.br/produto/123456/... 1500` |
| `/remover <id>` | `/remover kb-3f9a1c` |
| `/listar` | mostra nome, site, preço-alvo e último preço |
| `/ajuda` | lista os comandos |

O preço-alvo aceita `1500`, `1500.90`, `1.500,90` ou `R$ 1.500,90`.
Links encurtados (`amzn.to`, `a.co`, `meli.la`) são resolvidos automaticamente.
Só mensagens vindas do seu `TELEGRAM_CHAT_ID` são aceitas.

## Limitações (leia!)

- **Atrasos do cron**: o GitHub não garante o horário dos workflows agendados.
  Atrasos de 5–30 min são comuns e execuções podem ser puladas em horários de pico.
- **Desativação após 60 dias**: em repositórios públicos, workflows agendados
  são desativados após 60 dias sem atividade no repositório. Os commits
  automáticos costumam manter o repo ativo, mas se receber o e-mail do GitHub
  avisando, é só reativar na aba Actions.
- **Repositório público**: qualquer um pode ver `data/produtos.json` (links e
  preços-alvo). O token e o chat_id ficam nos Secrets e não aparecem.
- **Bloqueios**: Kabum, Terabyte e Pichau usam Cloudflare; a Amazon bloqueia
  scraping agressivamente a partir de IPs de datacenter. O código tenta
  `requests` e, se for bloqueado, tenta de novo com `curl_cffi` (imita o
  Chrome). Mesmo assim pode falhar — o erro aparece no log e os outros
  produtos continuam sendo checados.
- **Pichau bloqueada no Actions**: em out/2026 a Pichau devolveu HTTP 403
  para os IPs do GitHub mesmo com `curl_cffi`, então o workflow de 1h usa
  `--excluir amazon,pichau`. Do PC de casa funciona: veja o preço com
  "Testar preço" no painel ou `python -m scraper.main --apenas pichau --sem-telegram`.
- **HTML muda**: os scrapers usam várias estratégias (JSON-LD, meta tags,
  `__NEXT_DATA__`, seletores CSS). Se uma loja mudar o layout, ajuste o
  arquivo correspondente em `scraper/sites/`.
- **Mercado Livre (hoje não funciona)**: o código tenta a API
  `api.mercadolibre.com/items/{id}` e depois a página do produto. Em testes de
  out/2026 a API respondeu 403 sem token, e a página veio sem preço
  ("micro-landing" anti-robô), inclusive com navegador headless. Produtos do ML
  podem ser adicionados, mas a checagem vai registrar erro no log até o ML
  liberar o acesso.

## Painel web (cadastrar peças pelo navegador)

Além dos comandos do Telegram, dá para gerenciar a lista por um painel local:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m painel        # abre http://127.0.0.1:8765
```

No painel você:

- adiciona uma peça colando o link, escolhendo a **categoria** (Processador,
  Placa de vídeo, Memória RAM…) e, se quiser, o **preço-alvo** e um nome;
- edita nome, categoria e alvo direto na tabela (salva ao sair do campo);
- usa **Testar preço** para ver se o scraper consegue ler a página agora;
- remove peças.

As alterações vão para `data/produtos.json` no seu PC. Os workflows rodam no
GitHub, então clique em **Enviar para o GitHub** (faz commit, `pull --rebase`
e push de `data/produtos.json`) para o bot passar a usá-las. O painel só
escuta em `127.0.0.1` e não tem login, por isso não o exponha na rede.

Cada produto em `data/produtos.json` tem este formato (`categoria` é
opcional e aparece nos alertas e no `/listar`):

```json
{
  "id": "kb-3e0576",
  "site": "kabum",
  "url": "https://www.kabum.com.br/produto/101657/...",
  "nome": "Memória RAM Corsair Vengeance LPX 16GB",
  "categoria": "Memória RAM",
  "preco_alvo": 899.9
}
```

## Rodando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# checa preços sem mandar mensagem (fora do Actions não faz commit/push)
python -m scraper.main --sem-telegram

# só um site
python -m scraper.main --apenas kabum --sem-telegram

# processar comandos de verdade (precisa das variáveis)
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python -m bot.processar_comandos
```

Fora do GitHub Actions (`GITHUB_ACTIONS` diferente de `true`) os scripts
gravam os JSON localmente mas **não** fazem commit/push.

## Estrutura

```
.github/workflows/      3 workflows agendados
scraper/main.py         checagem de preços e alertas
scraper/sites/*.py      um módulo por loja (obter_preco(url) -> (preço, nome))
scraper/rede.py         HTTP com User-Agent real, delay 1–3s, fallback curl_cffi
scraper/telegram.py     enviar_mensagem(texto) e chamadas à Bot API
scraper/git_sync.py     commit + pull --rebase + push com retry
scraper/storage.py      leitura/escrita dos JSON
bot/processar_comandos.py  comandos /add, /remover, /listar, /ajuda
painel/                 painel web local (python -m painel)
data/                   estado persistido
```
