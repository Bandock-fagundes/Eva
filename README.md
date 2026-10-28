# E.V.A. — Entidade Virtual Adaptativa (v1.0)

A IA pessoal do Gabriel: conversa em português, tem opinião, lembra das coisas, pesquisa na web,
usa um navegador próprio, agenda lembretes — e o **Gabriel controla tudo** (registro, permissões, botão de desligar).

## O que mudou na v1.0
- **Nunca mais quebra por modelo sumido:** se o modelo configurado não existir (404), ela tenta sozinha o próximo:
  `gemini-3.8-flash → gemini-3.6-flash → gemini-3.5-flash-lite → gemini-3.1-flash-lite`.
- **Aguenta limite do plano grátis e instabilidade:** espera e tenta de novo (429/5xx), e troca para um modelo mais leve se precisar.
- **Erros em português claro** no chat (o detalhe técnico vai para o Registro).
- **Resposta aparecendo enquanto ela escreve** (streaming), botão **Parar**, perguntas da Eva no meio da tarefa.
- **Tarefas continuam se a conexão cair** (fechou o app? quando voltar, está tudo lá).
- **Memória de verdade:** ela tira sozinha os fatos importantes de cada conversa, sem duplicar; você vê, edita e apaga na aba Memória.
- **Conversas longas:** as mensagens antigas viram um resumo automático.
- **Lembretes:** "me lembra de X amanhã às 9h" — aparece no chat (e como notificação) na hora.
- **Navegador mais esperto:** botões numerados, rolar, voltar, reinicia sozinho, fecha quando parado (poupa memória).
  Clique em "Finalizar compra" ou digitar senha/cartão **sempre pede tua aprovação**.

## Variáveis de ambiente (Render → Environment)
| Variável | Obrigatória | Exemplo / padrão |
|---|---|---|
| `EVA_LLM_API_KEY` | sim | chave do Google AI Studio (https://aistudio.google.com/apikey) |
| `EVA_SENHA` | sim (na nuvem) | a senha para entrar no app |
| `DATABASE_URL` | sim (na nuvem) | string de conexão do Neon (`postgresql://...`) |
| `EVA_LLM_BASE_URL` | não | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `EVA_LLM_MODEL` | não | `gemini-3.8-flash` |
| `EVA_USER_NAME` | não | `Gabriel` |
| `EVA_SECRET` | não | texto aleatório extra para assinar o login (trocar = desloga todo mundo) |
| `EVA_MAX_STEPS` | não | `25` (passos de ferramenta por pedido) |

Outras opcionais: `EVA_LLM_FALLBACKS`, `EVA_LLM_TIMEOUT` (90), `EVA_LLM_RACIOCINIO` (low/medium/high),
`EVA_EMBED_MODEL` (gemini-embedding-001), `EVA_BROWSER_OCIOSO_MIN` (5), `EVA_HIST_RECENTES` (16), `EVA_CONTEXTO_CHARS` (24000).

O banco antigo (v0.2) continua funcionando: as tabelas novas e colunas novas são criadas sozinhas, sem apagar nada.

## Rodar no PC (Windows)
1. Instale o Python 3.12 (marque "Add to PATH").
2. Dê dois cliques em `iniciar.bat`. Na 1ª vez ele instala tudo e abre o `.env`: cole a chave em `EVA_LLM_API_KEY`, salve e rode de novo.
3. Abre sozinho em http://localhost:8000

## Nuvem (Render grátis + Neon)
O `Dockerfile` já usa a imagem com Chromium. No Render, crie o serviço a partir do repositório (Docker) e preencha as variáveis acima.
- O Render grátis **dorme depois de ~15 min sem uso**: o primeiro acesso demora ~1 min, e lembretes marcados para
  enquanto ela dormia disparam quando ela acordar (com aviso de atraso). Para mantê-la acordada, um monitor grátis
  (ex.: UptimeRobot) pode chamar `https://SEU-APP.onrender.com/api/saude?banco=0` a cada 10 min.
- `GET /api/saude` é público e não mostra nada sensível (só se está ok, versão, banco e se a chave existe).

## Testar
```bash
pip install -r requirements-dev.txt
pytest                      # usa um "Gemini falso" local; não gasta nada e não precisa de internet
# com Postgres real (opcional):
EVA_TEST_PG_URL=postgresql://usuario@localhost:5432/teste pytest tests/test_postgres.py
# contra o servidor de verdade (usa o Gemini real):
python tests/teste_ao_vivo.py https://SEU-APP.onrender.com SUA_SENHA
```

## Como ela é feita
| Parte | Arquivo | O que faz |
|---|---|---|
| Cérebro (LLM) | `eva/llm.py` | Fala com o Gemini: fallback de modelos, retry, streaming, erros amigáveis |
| Loop do agente | `eva/agent.py` | Fila de tarefas, pensa → ferramenta → permissão → age, parar/desligar |
| Prompt | `eva/brain.py` + `eva/persona_padrao.md` | Personalidade + hora + regras + memórias + resumo |
| Memória | `eva/memory.py` | Conversa, fatos duradouros (busca por significado), resumos |
| Lembretes | `eva/lembretes.py` | Agenda e dispara lembretes |
| Ferramentas | `eva/tools/` | web, navegador, arquivos, terminal, memória, lembretes, perguntar |
| Permissões | `eva/permissions.py` | permitir / perguntar / bloquear por ferramenta |
| Registro | `eva/audit.py` | Tudo que ela faz, com hora (aba Registro) |
| Servidor | `eva/server.py` | Login, API, WebSocket |
| Tela | `static/` | App (PWA) para PC e celular |

## Criar uma ferramenta nova
Crie `eva/tools/minha.py` com uma função `async` decorada com
`@tool("nome", "descrição", {"param": {"type": "string"}}, rotulo="Fazendo tal coisa")`,
importe o módulo no fim de `eva/tools/__init__.py` e defina a permissão padrão em `eva/permissions.py`.
