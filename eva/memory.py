"""Memória da Eva: histórico da conversa + fatos duradouros sobre o Gabriel e o mundo dele.

- Cada memória: conteudo, categoria, importancia (1-5), ts (criada), atualizada, origem, vetor (embedding JSON).
- Busca: embeddings (similaridade de cosseno em Python) + palavras/trigramas; sem embeddings, só palavras.
- Deduplicação: fato muito parecido com um existente atualiza o existente em vez de criar outro.
- Extração automática: depois de cada resposta, uma chamada barata ao LLM tira fatos duradouros em JSON.
"""
from __future__ import annotations

import json
import logging
import math
import re
import unicodedata

from . import audit, tempo
from .db import db

log = logging.getLogger("eva.memoria")

CATEGORIAS = ["perfil", "preferencias", "pessoas", "projetos", "rotina", "saude", "trabalho", "decisoes", "geral"]
SIM_DUPLICADA_VETOR = 0.92
SIM_DUPLICADA_TEXTO = 0.72

_SENSIVEL = re.compile(
    r"(\b(?:\d[ -]?){13,19}\b)|(\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b)|\bsenha\b|\bpassword\b|\bcvv\b|\bcvc\b|token", re.I)

PALAVRAS_VAZIAS = set("""a o e é de da do das dos em no na nos nas um uma uns umas que se por para pra pro com
sem ao aos à às ou mas mais menos como eu tu ele ela nós vós eles elas me te lhe meu minha teu tua seu sua
isso isto aquilo esse essa este esta aquele aquela já não sim muito muita bem tem ter ser foi era são está
estou the and of to in is it you""".split())


# ---------------------------------------------------------------- texto

def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def palavras(t: str) -> set[str]:
    return {w for w in normalizar(t).split() if len(w) > 2 and w not in PALAVRAS_VAZIAS}


def trigramas(t: str) -> set[str]:
    n = f"  {normalizar(t)} "
    return {n[i:i + 3] for i in range(len(n) - 2)}


def sim_texto(a: str, b: str) -> float:
    ta, tb = trigramas(a), trigramas(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cosseno(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    dbb = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * dbb)


def eh_sensivel(t: str) -> bool:
    return bool(_SENSIVEL.search(t or ""))


def _ler_vetor(v) -> list[float] | None:
    if not v:
        return None
    try:
        d = json.loads(v)
        return d.get("v") if isinstance(d, dict) else d
    except (TypeError, ValueError):
        return None


def _cerebro():
    from .llm import cerebro
    return cerebro


def extrair_json(texto: str):
    """Pega o primeiro objeto/lista JSON de uma resposta (tolera ```json ... ``` e texto em volta)."""
    if not texto:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip(), flags=re.M)
    for abre, fecha in (("{", "}"), ("[", "]")):
        i, j = t.find(abre), t.rfind(fecha)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    return None


PROMPT_EXTRATOR = """EXTRATOR_DE_MEMORIA
Você extrai FATOS DURADOUROS sobre {usuario} a partir de uma troca de mensagens com a assistente E.V.A.
Fatos duradouros: preferências, gostos, pessoas da vida dele (nome + relação), projetos, rotina, saúde
que ele contou, trabalho/estudo, decisões tomadas. NÃO extraia: coisas passageiras (o que ele quer agora,
perguntas pontuais), opiniões da Eva, senhas, números de cartão ou documentos.
Escreva cada fato como uma frase curta e completa em português, na terceira pessoa ("O {usuario} ...").
Mantenha o nível de certeza que ele usou ("às vezes", "acha que"). Nunca invente.

Memórias que já existem (id: texto):
{existentes}

Se um fato novo ATUALIZA/CORRIGE uma memória existente (ex.: mudou de time, de cidade), use "substitui": id.
Se o fato já existe igual, NÃO repita.

Responda SÓ com JSON neste formato (lista vazia se não houver nada):
{{"fatos": [{{"texto": "...", "categoria": "{categorias}", "importancia": 1-5, "substitui": null}}]}}"""

PROMPT_RESUMO = """RESUMIDOR_DE_CONVERSA
Resuma a conversa abaixo entre {usuario} e a E.V.A. para servir de memória de curto prazo dela.
Inclua: assuntos tratados, pedidos e tarefas (feitas e pendentes), decisões, combinados, nomes e dados
importantes. Seja fiel, sem inventar, em português, em tópicos curtos. Máximo ~250 palavras.
{anterior}"""


class Memory:
    # ============================================================ conversa
    def add_message(self, papel: str, conteudo: str) -> int:
        return db.insert("INSERT INTO conversa(ts,papel,conteudo) VALUES(?,?,?)", (tempo.ts(), papel, conteudo))

    def history(self, n: int = 80) -> list[dict]:
        rows = db.all("SELECT id, ts, papel, conteudo FROM conversa ORDER BY id DESC LIMIT ?", (int(n),))
        return rows[::-1]

    def mensagens_apos(self, ate_id: int, limite: int = 400) -> list[dict]:
        rows = db.all("SELECT id, ts, papel, conteudo FROM conversa WHERE id > ? ORDER BY id DESC LIMIT ?",
                      (int(ate_id), int(limite)))
        return rows[::-1]

    def recent_messages(self, n: int = 30) -> list[dict]:
        return [{"role": r["papel"], "content": r["conteudo"]} for r in self.history(n)]

    # ============================================================ resumos
    def ultimo_resumo(self) -> dict | None:
        return db.one("SELECT id, ts, ate_id, texto FROM resumos ORDER BY id DESC LIMIT 1")

    def salvar_resumo(self, texto: str, ate_id: int) -> int:
        return db.insert("INSERT INTO resumos(ts,ate_id,texto) VALUES(?,?,?)", (tempo.ts(), int(ate_id), texto))

    # ============================================================ memórias
    _COLS = "id, ts, categoria, conteudo, importancia, atualizada, origem"

    def all(self, categoria: str | None = None) -> list[dict]:
        if categoria:
            return db.all(f"SELECT {self._COLS} FROM memorias WHERE categoria=? ORDER BY id DESC", (categoria,))
        return db.all(f"SELECT {self._COLS} FROM memorias ORDER BY id DESC")

    def obter(self, mid: int) -> dict | None:
        return db.one(f"SELECT {self._COLS} FROM memorias WHERE id=?", (int(mid),))

    def by_category(self, categoria: str, limite: int = 50) -> list[dict]:
        return self.all(categoria)[:limite]

    def importantes(self, limite: int = 25) -> list[dict]:
        rows = db.all(f"SELECT {self._COLS} FROM memorias WHERE importancia >= 4 OR categoria IN ('perfil','preferencias') "
                      "ORDER BY importancia DESC, id DESC LIMIT ?", (int(limite),))
        return rows

    _cache: list[dict] | None = None

    def _invalidar(self):
        Memory._cache = None

    def _todas_com_vetor(self) -> list[dict]:
        """Todas as memórias (com vetor). Fica em cache no processo; toda escrita passa por aqui e invalida."""
        if Memory._cache is None:
            linhas = db.all(f"SELECT {self._COLS}, vetor FROM memorias ORDER BY id DESC LIMIT 3000")
            for m in linhas:
                m["_vet"] = _ler_vetor(m.get("vetor"))
            Memory._cache = linhas
        return Memory._cache

    async def _vetor(self, texto: str) -> str | None:
        vs = await _cerebro().embed([texto])
        if not vs:
            return None
        return json.dumps({"m": getattr(_cerebro(), "embed_modelo_em_uso", None), "v": vs[0]})

    async def lembrar(self, conteudo: str, categoria: str = "geral", importancia: int = 3,
                      origem: str = "eva") -> tuple[int | None, str]:
        """Salva (ou atualiza, se já existir parecido). Devolve (id, 'nova'|'atualizada'|'igual'|'recusada')."""
        conteudo = (conteudo or "").strip()
        if not conteudo:
            return None, "recusada"
        if eh_sensivel(conteudo):
            audit.log("memoria_recusada", motivo="parece dado sensível")
            return None, "recusada"
        categoria = categoria if categoria in CATEGORIAS else "geral"
        importancia = max(1, min(5, int(importancia or 3)))
        vetor_json = await self._vetor(conteudo)
        vetor = _ler_vetor(vetor_json)
        dup = self._achar_duplicada(conteudo, vetor)
        agora = tempo.ts()
        if dup:
            if normalizar(dup["conteudo"]) == normalizar(conteudo):
                db.run("UPDATE memorias SET atualizada=?, importancia=? WHERE id=?",
                       (agora, max(importancia, int(dup.get("importancia") or 3)), dup["id"]))
                self._invalidar()
                return dup["id"], "igual"
            self._atualizar_linha(dup["id"], conteudo, categoria, max(importancia, int(dup.get("importancia") or 3)),
                                  vetor_json, origem)
            audit.log("memoria_atualizada", id=dup["id"], conteudo=conteudo, antes=dup["conteudo"], origem=origem)
            return dup["id"], "atualizada"
        mid = db.insert("INSERT INTO memorias(ts,categoria,conteudo,importancia,atualizada,origem,vetor) "
                        "VALUES(?,?,?,?,?,?,?)", (agora, categoria, conteudo, importancia, agora, origem, vetor_json))
        self._invalidar()
        audit.log("memoria_nova", id=mid, conteudo=conteudo, categoria=categoria, origem=origem)
        return mid, "nova"

    def remember(self, conteudo: str, categoria: str = "geral") -> int:
        """Versão síncrona simples (sem embedding/dedupe por vetor) — compatibilidade."""
        agora = tempo.ts()
        self._invalidar()
        return db.insert("INSERT INTO memorias(ts,categoria,conteudo,importancia,atualizada,origem) VALUES(?,?,?,?,?,?)",
                         (agora, categoria, conteudo, 3, agora, "manual"))

    def _achar_duplicada(self, conteudo: str, vetor: list[float] | None) -> dict | None:
        melhor, melhor_s = None, 0.0
        for m in self._todas_com_vetor():
            s_txt = sim_texto(conteudo, m["conteudo"])
            if normalizar(conteudo) == normalizar(m["conteudo"]):
                return m
            s_vet = cosseno(vetor, m.get("_vet") or []) if vetor else 0.0
            if s_vet >= SIM_DUPLICADA_VETOR or s_txt >= SIM_DUPLICADA_TEXTO:
                s = max(s_vet, s_txt)
                if s > melhor_s:
                    melhor, melhor_s = m, s
        return melhor

    def _atualizar_linha(self, mid, conteudo, categoria, importancia, vetor_json, origem):
        db.run("UPDATE memorias SET conteudo=?, categoria=?, importancia=?, atualizada=?, origem=?, vetor=? WHERE id=?",
               (conteudo, categoria, importancia, tempo.ts(), origem, vetor_json, int(mid)))
        self._invalidar()

    async def editar(self, mid: int, conteudo: str | None = None, categoria: str | None = None,
                     importancia: int | None = None) -> bool:
        atual = self.obter(mid)
        if not atual:
            return False
        novo = (conteudo or atual["conteudo"]).strip()
        cat = categoria if categoria in CATEGORIAS else atual["categoria"]
        imp = max(1, min(5, int(importancia if importancia is not None else (atual.get("importancia") or 3))))
        vetor_json = await self._vetor(novo) if novo != atual["conteudo"] else None
        if vetor_json is None and novo == atual["conteudo"]:
            db.run("UPDATE memorias SET categoria=?, importancia=?, atualizada=? WHERE id=?",
                   (cat, imp, tempo.ts(), int(mid)))
            self._invalidar()
        else:
            self._atualizar_linha(mid, novo, cat, imp, vetor_json, "gabriel")
        audit.log("memoria_editada", id=int(mid), conteudo=novo, categoria=cat, importancia=imp)
        return True

    def forget(self, mid: int) -> bool:
        if not self.obter(mid):
            return False
        db.run("DELETE FROM memorias WHERE id=?", (int(mid),))
        self._invalidar()
        return True

    async def search(self, consulta: str, limite: int = 8) -> list[dict]:
        """Busca híbrida: embeddings (se houver) + palavras/trigramas."""
        consulta = (consulta or "").strip()
        if not consulta:
            return []
        todas = self._todas_com_vetor()
        if not todas:
            return []
        vq = None
        if any(m.get("vetor") for m in todas):
            vs = await _cerebro().embed([consulta])
            vq = vs[0] if vs else None
        pq = palavras(consulta)
        res = []
        for m in todas:
            pm = palavras(m["conteudo"])
            kw = (len(pq & pm) / (len(pq) or 1)) if pq else 0.0
            # casamento parcial (ex.: "time" ~ "times"): trigramas
            tri = sim_texto(consulta, m["conteudo"])
            s_kw = max(kw, tri * 0.8)
            vm = m.get("_vet")
            if vq is not None and vm:
                s = 0.7 * max(0.0, cosseno(vq, vm)) + 0.3 * s_kw
                limiar = 0.35
            else:
                s = s_kw
                limiar = 0.12
            if s >= limiar:
                item = {k: v for k, v in m.items() if k not in ("vetor", "_vet")}
                item["score"] = round(s, 3)
                res.append(item)
        res.sort(key=lambda r: (-r["score"], -(r.get("importancia") or 3)))
        return res[:limite]

    # ============================================================ extração automática
    async def extrair_fatos(self, texto_usuario: str, resposta_eva: str, usuario: str = "Gabriel") -> list[dict]:
        """Chamada barata ao LLM que extrai fatos duradouros e salva com deduplicação."""
        from .llm import LLMErro
        texto_usuario = (texto_usuario or "").strip()
        if not texto_usuario or not self._vale_extrair(texto_usuario):
            return []
        relacionadas = await self.search(texto_usuario, 10)
        vistos = {m["id"] for m in relacionadas}
        relacionadas += [m for m in self.importantes(15) if m["id"] not in vistos]
        existentes = "\n".join(f"{m['id']}: {m['conteudo']}" for m in relacionadas) or "(nenhuma)"
        sistema = PROMPT_EXTRATOR.format(usuario=usuario, existentes=existentes, categorias="|".join(CATEGORIAS))
        conversa = f"{usuario}: {texto_usuario[:3000]}\n\nE.V.A.: {(resposta_eva or '')[:1500]}"
        try:
            r = await _cerebro().chat([{"role": "system", "content": sistema}, {"role": "user", "content": conversa}],
                                      leve=True)
        except LLMErro as e:
            audit.log("memoria_extracao_falhou", tipo_erro=e.tipo, erro=e.tecnico[:300])
            return []
        dados = extrair_json(r.texto)
        fatos = dados.get("fatos", []) if isinstance(dados, dict) else (dados if isinstance(dados, list) else [])
        salvos = []
        ids_validos = {m["id"] for m in relacionadas}
        for f in fatos[:8]:
            if not isinstance(f, dict) or not str(f.get("texto", "")).strip():
                continue
            texto = str(f["texto"]).strip()[:500]
            cat = str(f.get("categoria") or "geral").strip().lower()
            try:
                imp = int(f.get("importancia") or 3)
            except (TypeError, ValueError):
                imp = 3
            subst = f.get("substitui")
            try:
                subst = int(subst) if subst not in (None, "", "null") else None
            except (TypeError, ValueError):
                subst = None
            if subst is not None and subst in ids_validos and not eh_sensivel(texto):
                await self.editar(subst, texto, cat if cat in CATEGORIAS else None, imp)
                salvos.append({"id": subst, "texto": texto, "acao": "substituida"})
                continue
            mid, acao = await self.lembrar(texto, cat, imp, origem="automatica")
            if mid:
                salvos.append({"id": mid, "texto": texto, "acao": acao})
        if salvos:
            audit.log("memoria_extraida", fatos=salvos, modelo=r.modelo)
        return salvos

    @staticmethod
    def _vale_extrair(t: str) -> bool:
        n = normalizar(t)
        if len(n) >= 25:
            return True
        return bool(re.search(r"\b(eu|meu|minha|meus|minhas|lembr\w*|gosto|odeio|prefiro|sou|moro|trabalho|estudo)\b", n))

    # ============================================================ resumo de contexto
    async def resumir(self, mensagens: list[dict], anterior: str | None, usuario: str = "Gabriel") -> str | None:
        from .llm import LLMErro
        bloco = "\n".join(f"{'Gabriel' if m['papel'] == 'user' else 'E.V.A.'}: {m['conteudo'][:2000]}" for m in mensagens)
        ant = f"\nResumo anterior (incorpore e atualize):\n{anterior}" if anterior else ""
        try:
            r = await _cerebro().chat([
                {"role": "system", "content": PROMPT_RESUMO.format(usuario=usuario, anterior=ant)},
                {"role": "user", "content": bloco[:40000]},
            ], leve=True)
        except LLMErro as e:
            audit.log("resumo_falhou", tipo_erro=e.tipo, erro=e.tecnico[:300])
            return None
        return (r.texto or "").strip() or None


memory = Memory()
