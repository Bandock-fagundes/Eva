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

## Conectar meu PC (a Eva usando o teu computador Windows)

A Eva pode usar o **teu próprio PC** (Windows 10 ou 11): ver a tela, mexer o mouse, digitar, abrir programas e sites
— sempre com você podendo assistir, pausar e desconectar. Nenhuma porta é aberta no PC: é o próprio PC que se liga
na Eva (conexão segura de saída, como um navegador).

### Instalar (uma vez só)
1. No PC, abra o app da Eva, vá na aba **Tela** e clique em **⬇️ Baixar o EVA-Agente**
   (ou baixe direto: https://github.com/Bandock-fagundes/Eva/releases/latest/download/EVA-Agente.exe).
2. Dê dois cliques no `EVA-Agente.exe`. Se aparecer a tela azul **“O Windows protegeu o computador”**, clique em
   **Mais informações** e depois em **Executar assim mesmo**. Isso é normal: o programa é novo e não tem uma
   assinatura paga da Microsoft. (Se o antivírus reclamar, veja “Problemas comuns”.)
3. Abre uma janelinha pedindo **Endereço da Eva** (já vem preenchido com `https://eva-hldw.onrender.com`) e um
   **código**. No app da Eva (pode ser no celular), aba **Tela** → **🔗 Conectar meu PC**: aparece um código tipo
   `K7PX-4MQ2`. Digite no EVA-Agente e clique **Conectar**. O código vale 10 minutos e só funciona uma vez.
4. Pronto: a tela do PC aparece AO VIVO na aba Tela. Peça no chat, por exemplo: *“abre o Excel no meu PC e cria
   uma planilha de gastos”*.

### O ícone perto do relógio
Um ícone roxo com “E” fica perto do relógio (talvez escondido na setinha **^**). A bolinha mostra o estado:
🟢 conectado · 🟡 a Eva está usando o PC agora · 🔴 Eva pausada · ⚪ sem conexão (tentando de novo sozinho).
Clique com o botão direito para: **Pausar Eva**, **Retomar Eva**, **Ver registro** (tudo que ela fez no PC, com hora),
**Iniciar com o Windows** (liga/desliga), **Desconectar este PC da Eva** e **Sair**.
Enquanto a Eva mexe, aparece embaixo da tela a faixa **“Eva está usando o computador”** (ela não atrapalha teus
cliques e não aparece nos prints da Eva).

### Parar na hora (emergência)
- Aperte **Ctrl + Alt + Shift + P** → para TUDO na hora (o que ela estava fazendo é interrompido) e a Eva fica
  pausada até você clicar em **Retomar Eva** no ícone.
- Ou jogue o mouse com força para **um canto da tela** enquanto ela mexe → mesma coisa.
- O botão **Desligar** do app também pausa o PC.

### Assumir o controle pelo app
Na aba Tela, **🖐️ Assumir controle**: a Eva para de mexer e você pode clicar na imagem do PC (botão direito =
menu, rodinha = rolar) e digitar pela caixinha “Digitar no PC…”. Use isso para fazer login em sites (a Eva nunca
digita senhas). Depois, **🤖 Devolver pra Eva**.

### O que a Eva faz sozinha e o que ela pergunta
Ela segue as **Permissões** (Ajustes): rodar comandos no PowerShell, copiar arquivos e ler a área de transferência
**pedem tua aprovação**; telas de login/pagamento e números de cartão/CPF também. E ela foi instruída a **perguntar
antes** de apagar arquivos, instalar programas, enviar mensagens ou comprar, e a nunca fechar trabalho não salvo.

### Desconectar
- No app: **Ajustes → Meu PC → Desconectar** (corta o acesso na hora, mesmo se o PC estiver longe de você).
- Ou no PC: ícone → **Desconectar este PC da Eva**.
Para voltar, é só gerar um código novo. (O token do PC fica guardado cifrado pela tua conta do Windows, e na Eva só
fica uma “impressão digital” dele.)

### Problemas comuns
- **Antivírus apagou/bloqueou o EVA-Agente**: programas que controlam mouse e teclado às vezes são confundidos com
  vírus. Baixe de novo pelo link oficial acima e, se precisar, marque como permitido no antivírus.
- **Janelas de “Controle de Conta de Usuário” (UAC)** — aquela tela escurecida perguntando “Deseja permitir…?” — e a
  tela de bloqueio: o Windows não deixa nenhum programa ver nem clicar nelas. Responda você mesmo no PC.
- **A Eva estava dormindo** (Render grátis): o EVA-Agente espera e conecta sozinho quando ela acordar (pode levar
  ~1 minuto). Enquanto o PC está conectado, ele dá um “oi” leve na Eva a cada 10 min para ela não dormir.
- **Zoom do Windows (125%, 150%)**: tudo bem, o agente usa os pixels reais da tela.
- **Mais de um monitor**: a Eva vê e usa só o monitor principal.

Para desenvolvedores: o código do agente fica em `agente_windows/` (testes em `agente_windows/testes`, rodam no
Linux com uma tela falsa). O `.exe` é gerado pelo GitHub Actions (`.github/workflows/agente-windows.yml`): para
publicar uma versão nova, mude `VERSAO` em `agente_windows/eva_agente/__init__.py` e crie a tag `agente-vX.Y.Z`
(ex.: `git tag agente-v1.0.0 && git push origin agente-v1.0.0`). O link “latest” do app passa a baixar essa versão.
Variável opcional na Eva: `EVA_PC_AGENTE_URL` (outro link de download).

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
| Ferramentas | `eva/tools/` | web, navegador, computador (`computador_*`), arquivos, terminal, memória, lembretes, perguntar |
| Computador | `eva/computador.py` + `eva/pc_hub.py` | Transporte das ferramentas `computador_*`: teu PC Windows (WebSocket) ou desktop na nuvem (HTTP) |
| EVA-Agente | `agente_windows/` | Programa do PC Windows: tela, mouse, teclado, ícone, atalho de emergência |
| Permissões | `eva/permissions.py` | permitir / perguntar / bloquear por ferramenta |
| Registro | `eva/audit.py` | Tudo que ela faz, com hora (aba Registro) |
| Servidor | `eva/server.py` | Login, API, WebSocket |
| Tela | `static/` | App (PWA) para PC e celular |

## Criar uma ferramenta nova
Crie `eva/tools/minha.py` com uma função `async` decorada com
`@tool("nome", "descrição", {"param": {"type": "string"}}, rotulo="Fazendo tal coisa")`,
importe o módulo no fim de `eva/tools/__init__.py` e defina a permissão padrão em `eva/permissions.py`.
