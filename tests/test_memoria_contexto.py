"""Memória de longo prazo (extração, dedupe, busca) e resumo de contexto."""
import json

from eva import brain
from eva.llm import cerebro
from eva.memory import extrair_json, memory, sim_texto


async def test_lembrar_e_deduplicar():
    a, acao1 = await memory.lembrar("O Gabriel torce para o Avaí.", "preferencias", 4)
    b, acao2 = await memory.lembrar("O Gabriel torce para o Avaí", "preferencias", 3)
    c, acao3 = await memory.lembrar("O Gabriel torce muito para o Avaí.", "preferencias")
    assert acao1 == "nova" and acao2 == "igual" and acao3 == "atualizada"
    assert a == b == c
    assert len(memory.all()) == 1
    assert memory.obter(a)["importancia"] == 4


async def test_fatos_diferentes_nao_sao_fundidos():
    await memory.lembrar("O Gabriel gosta de pizza de calabresa.", "preferencias")
    await memory.lembrar("O Gabriel estuda na ETEP a distância.", "trabalho")
    await memory.lembrar("A namorada do Gabriel faz aniversário em 16 de outubro.", "pessoas")
    assert len(memory.all()) == 3


async def test_recusa_dado_sensivel():
    mid, acao = await memory.lembrar("A senha do banco do Gabriel é 1234", "geral")
    assert mid is None and acao == "recusada"
    mid, acao = await memory.lembrar("Cartão 4111 1111 1111 1111", "geral")
    assert mid is None


async def test_busca_com_embeddings(fake):
    await memory.lembrar("O time de futebol do Gabriel é o Avaí.", "preferencias")
    await memory.lembrar("O Gabriel joga RPG de mesa com os amigos.", "rotina")
    res = await memory.search("qual é o meu time de futebol?")
    assert res and "Avaí" in res[0]["conteudo"]
    assert fake.embeddings_chamadas >= 3
    assert memory._todas_com_vetor()[0]["vetor"]


async def test_busca_sem_embeddings_cai_para_palavras(fake):
    fake.embeddings_modo = "404"
    cerebro.reset()
    await memory.lembrar("O time de futebol do Gabriel é o Avaí.", "preferencias")
    await memory.lembrar("O Gabriel joga RPG de mesa com os amigos.", "rotina")
    assert memory._todas_com_vetor()[0]["vetor"] is None
    res = await memory.search("time de futebol")
    assert res and "Avaí" in res[0]["conteudo"]
    assert not await memory.search("astronomia quântica")


async def test_extracao_automatica_com_dedupe(fake):
    fake.extrator = lambda req: {"fatos": [
        {"texto": "O time do Gabriel é o Figueirense.", "categoria": "preferencias", "importancia": 4,
         "substitui": None}]}
    salvos = await memory.extrair_fatos("lembra que meu time é o Figueirense", "Anotado!")
    assert len(salvos) == 1 and salvos[0]["acao"] == "nova"
    # segunda vez o mesmo fato: não duplica
    salvos2 = await memory.extrair_fatos("meu time é o Figueirense, viu", "Eu sei!")
    assert len(memory.all()) == 1
    assert salvos2[0]["acao"] in ("igual", "atualizada")
    # o extrator recebeu as memórias existentes para poder deduplicar/substituir
    assert "Figueirense" in fake.requisicoes_fundo[-1]["messages"][0]["content"]
    # e usou modelo leve
    assert "lite" in fake.requisicoes_fundo[-1]["model"]


async def test_extracao_substitui_memoria(fake):
    mid, _ = await memory.lembrar("O time do Gabriel é o Avaí.", "preferencias")
    fake.extrator = lambda req: {"fatos": [{"texto": "O time do Gabriel agora é o Figueirense.",
                                           "categoria": "preferencias", "importancia": 4, "substitui": mid}]}
    await memory.extrair_fatos("mudei de time, agora sou Figueirense", "Traidor! Brincadeira.")
    todas = memory.all()
    assert len(todas) == 1 and "Figueirense" in todas[0]["conteudo"] and todas[0]["id"] == mid


async def test_extracao_ignora_conversa_trivial(fake):
    assert await memory.extrair_fatos("oi", "oi!") == []
    assert not fake.requisicoes_fundo


async def test_extracao_tolera_json_com_cercas(fake):
    fake.extrator = lambda req: "nada"
    assert extrair_json('```json\n{"fatos": [{"texto": "x"}]}\n```') == {"fatos": [{"texto": "x"}]}
    assert extrair_json("Claro! {\"fatos\": []} pronto") == {"fatos": []}
    assert extrair_json("sem json") is None


async def test_extracao_roda_em_fundo_apos_resposta(fake, rodar_tarefa):
    from eva.agent import ger
    fake.extrator = lambda req: {"fatos": [{"texto": "O Gabriel mora em Florianópolis.", "categoria": "perfil",
                                           "importancia": 5}]}
    fake.adicionar({"texto": "Floripa é ótima!"})
    await rodar_tarefa("eu moro em Florianópolis, sabia?")
    await ger.aguardar_fundo()
    assert any("Florianópolis" in m["conteudo"] for m in memory.all())
    # e entra no prompt da próxima conversa
    fake.adicionar({"texto": "ok"})
    await rodar_tarefa("onde eu moro?")
    assert "Florianópolis" in fake.requisicoes[-1]["messages"][0]["content"]


def test_similaridade_texto():
    assert sim_texto("O Gabriel gosta de pizza", "O Gabriel gosta de pizza!") > 0.9
    assert sim_texto("O Gabriel gosta de pizza", "A irmã mora em Curitiba") < 0.3


# ------------------------------------------------------------------ contexto

async def test_resumo_de_contexto(fake, monkeypatch):
    monkeypatch.setattr(brain, "HIST_RECENTES", 4)
    monkeypatch.setattr(brain, "CONTEXTO_CHARS", 500)
    for i in range(12):
        memory.add_message("user", f"mensagem do usuário número {i} " + "bla " * 20)
        memory.add_message("assistant", f"resposta da eva número {i}")
    fake.resumidor = lambda req: "RESUMO: falaram de 12 assuntos."
    assert await brain.atualizar_resumo()
    r = memory.ultimo_resumo()
    assert r and r["texto"] == "RESUMO: falaram de 12 assuntos."
    msgs = await brain.montar_mensagens("e agora?")
    assert "RESUMO: falaram de 12 assuntos." in msgs[0]["content"]
    conversa = msgs[1:]
    assert len(conversa) <= 4
    assert "número 11" in conversa[-1]["content"] or "número 11" in conversa[-2]["content"]
    assert "número 0 " not in json.dumps(conversa, ensure_ascii=False)
    # o resumidor recebeu as mensagens antigas
    assert "número 0" in fake.requisicoes_fundo[-1]["messages"][1]["content"]


async def test_resumo_nao_roda_com_pouca_conversa(fake):
    memory.add_message("user", "oi")
    memory.add_message("assistant", "oi!")
    assert not await brain.atualizar_resumo()
    assert not fake.requisicoes_fundo


async def test_resumo_incremental_usa_anterior(fake, monkeypatch):
    monkeypatch.setattr(brain, "HIST_RECENTES", 2)
    monkeypatch.setattr(brain, "CONTEXTO_CHARS", 100)
    for i in range(6):
        memory.add_message("user", f"msg {i} " + "x" * 50)
    fake.resumidor = lambda req: "RESUMO 1"
    assert await brain.atualizar_resumo()
    for i in range(6, 12):
        memory.add_message("user", f"msg {i} " + "x" * 50)
    fake.resumidor = lambda req: "RESUMO 2"
    assert await brain.atualizar_resumo()
    assert "RESUMO 1" in fake.requisicoes_fundo[-1]["messages"][0]["content"]
    assert memory.ultimo_resumo()["texto"] == "RESUMO 2"


async def test_mensagens_seguidas_do_mesmo_papel_sao_juntadas():
    memory.add_message("assistant", "⏰ Lembrete: beber água")
    memory.add_message("user", "oi")
    memory.add_message("user", "tá aí?")
    msgs = await brain.montar_mensagens("tá aí?")
    papeis = [m["role"] for m in msgs]
    assert papeis == ["system", "user"]
    assert "oi" in msgs[1]["content"] and "tá aí?" in msgs[1]["content"]
