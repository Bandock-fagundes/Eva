# E.V.A. — Entidade Virtual Adaptativa (v0.1)

A IA pessoal do Gabriel: personalidade e opiniões próprias, memória de longo prazo, mãos pra agir
(web, navegador visível, arquivos, terminal) e **controle total do Gabriel**.

## Rodar (Windows)
1. Instale o Python 3.11+ (marque "Add to PATH").
2. Pegue uma chave grátis do Gemini: https://aistudio.google.com/apikey
3. Dê dois cliques em `iniciar.bat`. Na 1ª vez ele instala tudo e abre o `.env`: cole a chave em `EVA_LLM_API_KEY`, salve e rode de novo.
4. Abre sozinho em http://localhost:8000

No VS Code: abra a pasta, `python -m venv .venv`, `pip install -r requirements.txt`, `python -m playwright install chromium`, `python main.py`.

## Como ela é feita
| Parte | Arquivo | O que faz |
|---|---|---|
| Personalidade | `data/persona.md` (criado de `eva/persona_padrao.md`) | Quem ela é, jeito de pensar, regras. Edite à vontade. |
| Cérebro do turno | `eva/brain.py` | Junta personalidade + hora + memórias relevantes |
| Loop do agente | `eva/agent.py` | Pensa → escolhe ferramenta → checa permissão → age → repete |
| Memória | `eva/memory.py` (`data/memoria.db`) | Conversa + fatos duradouros com busca |
| Ferramentas | `eva/tools/` | web, navegador, arquivos, terminal, memória |
| Permissões | `data/permissoes.json` | permitir / perguntar / bloquear por ação |
| Registro | `data/audit.jsonl` | TUDO que ela faz, com hora |
| Desligar | `eva/killswitch.py` | Para tudo na hora, fecha o navegador e continua desligada até você ligar |

## Segurança
- Só roda no seu PC (127.0.0.1), ninguém de fora acessa.
- Arquivos: ela só mexe em `workspace_eva/`.
- Terminal, clicar e digitar no navegador: pedem sua aprovação por padrão.

## Criar uma ferramenta nova
Crie `eva/tools/minha.py` com `@tool("nome", "descrição", {params})` numa função `async`, e importe no fim de `eva/tools/__init__.py`.

## Próximas versões
- v0.2: voz (fala e ouve), resumo automático de conversas longas
- v0.3: tarefas agendadas e proatividade (ela te chama quando algo importa)
- v0.4: Telegram/WhatsApp, Gmail/Agenda, sub-agentes em paralelo
