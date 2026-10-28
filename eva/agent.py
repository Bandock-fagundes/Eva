"""O loop do agente: pensa -> escolhe ferramentas -> checa permissão -> age -> repete até responder."""
import json
from openai import AsyncOpenAI
from . import audit
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MAX_STEPS
from .brain import build_system_prompt
from .killswitch import kill_switch
from .memory import memory
from .permissions import permissions
from .tools import TOOLS, schemas


class Agent:
    def __init__(self):
        self.client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY or "sem-chave")

    async def run(self, user_text: str, emit, ask_approval):
        """emit(evento: dict) manda coisas pra tela. ask_approval(ferramenta, args) -> bool."""
        if kill_switch.off:
            await emit({"tipo": "erro", "texto": "Estou desligada. Ligue no painel pra eu voltar."})
            return
        if not LLM_API_KEY:
            await emit({"tipo": "erro", "texto": "Falta a chave da API no arquivo .env (EVA_LLM_API_KEY)."})
            return

        memory.add_message("user", user_text)
        audit.log("mensagem_usuario", texto=user_text)
        msgs = [{"role": "system", "content": build_system_prompt(user_text)}] + memory.recent_messages(30)

        for passo in range(MAX_STEPS):
            if kill_switch.off:
                await emit({"tipo": "erro", "texto": "Desligada no meio da tarefa. Parei."})
                return
            await emit({"tipo": "pensando", "passo": passo + 1})
            resp = await self.client.chat.completions.create(model=LLM_MODEL, messages=msgs, tools=schemas())
            m = resp.choices[0].message

            if not m.tool_calls:
                texto = m.content or "(sem resposta)"
                memory.add_message("assistant", texto)
                audit.log("resposta_eva", texto=texto)
                await emit({"tipo": "resposta", "texto": texto})
                return

            msgs.append({
                "role": "assistant", "content": m.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in m.tool_calls],
            })
            for tc in m.tool_calls:
                resultado = await self._executar(tc.function.name, tc.function.arguments, emit, ask_approval)
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})

        await emit({"tipo": "resposta", "texto": f"Cheguei no limite de {MAX_STEPS} passos sem terminar. Quer que eu continue?"})

    async def _executar(self, nome, args_json, emit, ask_approval) -> str:
        try:
            args = json.loads(args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        if nome not in TOOLS:
            return f"Ferramenta '{nome}' não existe."

        regra = permissions.get(nome)
        audit.log("acao_solicitada", ferramenta=nome, args=args, regra=regra)
        await emit({"tipo": "acao", "ferramenta": nome, "args": args, "regra": regra})

        if regra == "bloquear":
            audit.log("acao_bloqueada", ferramenta=nome)
            return "BLOQUEADO pelo Gabriel. Não tente contornar; ofereça outra saída."
        if regra == "perguntar":
            ok = await ask_approval(nome, args)
            audit.log("aprovacao", ferramenta=nome, aprovado=ok)
            if not ok:
                return "NEGADO pelo Gabriel. Não tente de novo nem contornar; pergunte o que ele prefere."
        if kill_switch.off:
            return "Desligada. Ação cancelada."

        try:
            resultado = str(await TOOLS[nome]["fn"](**args))
        except Exception as e:
            resultado = f"ERRO: {type(e).__name__}: {e}"
        audit.log("acao_resultado", ferramenta=nome, resultado=resultado[:1000])
        await emit({"tipo": "resultado", "ferramenta": nome, "texto": resultado[:500]})
        if nome.startswith("navegador_"):
            await emit({"tipo": "tela"})
        return resultado[:8000]


agent = Agent()
