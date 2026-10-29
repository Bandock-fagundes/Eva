"""Executor com a plataforma falsa: cada op, limites e segurança."""
import base64
import io
import sys

import pytest

from eva_agente.executor import ErroAgente, Executor, descrever, na_canto
from eva_agente.plataforma import PlataformaFalsa


@pytest.fixture
def ex(tmp_path):
    casa = tmp_path / "Gabriel"
    casa.mkdir()
    p = PlataformaFalsa(1920, 1080)
    e = Executor(p, casa=casa)
    e.pausa_menu = e.pausa_busca = 0
    yield e
    e.fechar()


def _sem_mover(p):
    return [c for c in p.chamadas if c[0] != "mover"]


async def test_info_e_tela(ex):
    d = await ex.executar("info", {})
    assert d["tela"] == {"largura": 1920, "altura": 1080} and d["janela"] == "Área de trabalho"
    t = await ex.executar("tela", {"largura": 960, "qualidade": 50})
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(t["imagem_b64"])))
    assert img.format == "JPEG" and img.size == (960, 540)
    assert t["tela"] == {"largura": 1920, "altura": 1080} and t["imagem"] == {"largura": 960, "altura": 540}


async def test_tela_protegida(ex):
    ex.p.falhar_captura = True
    with pytest.raises(ErroAgente, match="UAC"):
        await ex.executar("tela", {})


async def test_cliques(ex):
    p = ex.p
    await ex.executar("mouse", {"acao": "clicar", "x": 100, "y": 200})
    assert p.chamadas[0] == ("mover", 100, 200) and p.chamadas[1] == ("botao", "esquerdo", "clicar")
    p.chamadas.clear()
    await ex.executar("mouse", {"acao": "duplo", "x": 5, "y": 500})
    assert _sem_mover(p) == [("botao", "esquerdo", "clicar")] * 2
    p.chamadas.clear()
    await ex.executar("mouse", {"acao": "direito", "x": 10, "y": 10})
    assert _sem_mover(p) == [("botao", "direito", "clicar")]
    p.chamadas.clear()
    d = await ex.executar("mouse", {"acao": "mover", "x": 300, "y": 300})
    assert _sem_mover(p) == [] and d["mouse"] == {"x": 300, "y": 300}


async def test_arrastar_e_rolar(ex):
    p = ex.p
    await ex.executar("mouse", {"acao": "arrastar", "x": 100, "y": 100, "x2": 700, "y2": 400})
    botoes = [c for c in p.chamadas if c[0] == "botao"]
    assert botoes == [("botao", "esquerdo", "descer"), ("botao", "esquerdo", "subir")]
    movs = [c for c in p.chamadas if c[0] == "mover"]
    assert movs[0] == ("mover", 100, 100) and movs[-1] == ("mover", 700, 400) and len(movs) == 13
    p.chamadas.clear()
    await ex.executar("mouse", {"acao": "rolar", "x": 960, "y": 540, "dy": 5})
    await ex.executar("mouse", {"acao": "rolar", "dx": -99})
    assert [c for c in p.chamadas if c[0] == "rolar"] == [("rolar", 5, 0), ("rolar", 0, -30)]
    with pytest.raises(ErroAgente):
        await ex.executar("mouse", {"acao": "rolar"})


async def test_limites_e_canto(ex):
    with pytest.raises(ErroAgente, match="fora da tela"):
        await ex.executar("mouse", {"acao": "clicar", "x": 1920, "y": 10})
    with pytest.raises(ErroAgente, match="faltou"):
        await ex.executar("mouse", {"acao": "clicar"})
    with pytest.raises(ErroAgente, match="inválida"):
        await ex.executar("mouse", {"acao": "voar", "x": 1, "y": 1})
    # o canto exato é o freio do Gabriel: a Eva nunca para lá
    await ex.executar("mouse", {"acao": "clicar", "x": 1919, "y": 0})
    assert ex.p.chamadas[0] == ("mover", 1915, 4)
    assert not ex.mouse_no_canto()            # foi o agente que moveu
    ex.p.mouse = (0, 1079)                    # o Gabriel jogou o mouse no canto
    assert ex.mouse_no_canto()
    assert na_canto(0, 0, 100, 100) and not na_canto(50, 0, 100, 100)


async def test_teclado(ex):
    p = ex.p
    d = await ex.executar("teclado", {"texto": "Olá, ação ç ã é 😀", "teclas": "Return"})
    assert p.chamadas == [("digitar", "Olá, ação ç ã é 😀"), ("combo", ("enter",))]
    assert "janela" in d
    p.chamadas.clear()
    await ex.executar("teclado", {"teclas": "ctrl+a BackSpace"})
    assert p.chamadas == [("combo", ("ctrl", "a")), ("combo", ("backspace",))]
    p.chamadas.clear()
    await ex.executar("teclado", {"texto": "x" * 450})
    assert [len(c[1]) for c in p.chamadas] == [200, 200, 50]   # em pedaços
    with pytest.raises(ErroAgente, match="grande demais"):
        await ex.executar("teclado", {"texto": "x" * 5001})
    with pytest.raises(ErroAgente, match="desconhecida"):
        await ex.executar("teclado", {"teclas": "ctrl+nada"})
    with pytest.raises(ErroAgente):
        await ex.executar("teclado", {})


async def test_comando(ex):
    d = await ex.executar("comando", {"cmd": "echo ola-eva", "timeout": 20})
    assert d["codigo"] == 0 and "ola-eva" in d["saida"] and not d["tempo_esgotado"]
    cmd_lento = "ping -n 30 127.0.0.1" if sys.platform == "win32" else "sleep 30"
    d = await ex.executar("comando", {"cmd": cmd_lento, "timeout": 1})
    assert d["tempo_esgotado"] and not ex.processos
    with pytest.raises(ErroAgente):
        await ex.executar("comando", {"cmd": "  "})


async def test_parar_mata_comando(ex):
    import asyncio
    cmd_lento = "ping -n 30 127.0.0.1" if sys.platform == "win32" else "sleep 30"
    t = asyncio.ensure_future(ex.executar("comando", {"cmd": cmd_lento, "timeout": 60}))
    for _ in range(100):
        if ex.processos:
            break
        await asyncio.sleep(0.02)
    assert await ex.parar() == 1
    d = await asyncio.wait_for(t, 10)
    assert d["codigo"] != 0
    assert ("soltar_tudo",) in ex.p.chamadas


async def test_abrir(ex):
    p = ex.p
    d = await ex.executar("abrir", {"url": "g1.globo.com"})
    assert d["aberto"] == "https://g1.globo.com" and ("abrir_url", "https://g1.globo.com") in p.chamadas
    with pytest.raises(ErroAgente, match="http"):
        await ex.executar("abrir", {"url": "javascript:alert(1)"})
    d = await ex.executar("abrir", {"app": "editor"})
    assert ("abrir_programa", "notepad") in p.chamadas and d["aberto"] == "Bloco de Notas"
    await ex.executar("abrir", {"app": "terminal"})
    assert ("abrir_terminal", str(ex.casa)) in p.chamadas
    await ex.executar("abrir", {"app": "configuracoes"})
    assert ("abrir_url", "ms-settings:") in p.chamadas
    p.chamadas.clear()
    d = await ex.executar("abrir", {"app": "WhatsApp"})   # não abre direto: procura no menu Iniciar
    assert p.chamadas == [("combo", ("win",)), ("digitar", "WhatsApp"), ("combo", ("enter",))]
    assert "menu Iniciar" in d["aberto"]
    p.chamadas.clear()
    await ex.executar("abrir", {"app": "excel"})          # Excel não instalado: menu Iniciar com o nome bonito
    assert ("digitar", "Excel") in p.chamadas
    with pytest.raises(ErroAgente):
        await ex.executar("abrir", {"app": "calc; rm -rf /"})
    with pytest.raises(ErroAgente):
        await ex.executar("abrir", {})


async def test_abrir_arquivo_seguro(ex):
    (ex.casa / "Documentos").mkdir()
    (ex.casa / "Documentos" / "nota.txt").write_text("oi")
    (ex.casa / "instalar.exe").write_bytes(b"MZ")
    d = await ex.executar("abrir", {"arquivo": "Documentos/nota.txt"})
    assert d["aberto"].endswith("nota.txt")
    with pytest.raises(ErroAgente, match="instaladores"):
        await ex.executar("abrir", {"arquivo": "instalar.exe"})
    with pytest.raises(ErroAgente, match="pasta do usuário"):
        await ex.executar("abrir", {"arquivo": "../../etc/passwd"})
    with pytest.raises(ErroAgente, match="não encontrado"):
        await ex.executar("abrir", {"arquivo": "nao_existe.txt"})


async def test_janelas_e_clipboard(ex):
    d = await ex.executar("janelas", {})
    assert [j["titulo"] for j in d["janelas"]] == ["Documento1 - Word", "Área de trabalho"]
    d = await ex.executar("focar", {"titulo": "word"})
    assert d["janela"] == "Documento1 - Word"
    with pytest.raises(ErroAgente, match="não achei"):
        await ex.executar("focar", {"titulo": "Photoshop"})
    await ex.executar("clipboard_escrever", {"texto": "copiado ✓"})
    assert (await ex.executar("clipboard_ler", {}))["texto"] == "copiado ✓"


async def test_arquivos(ex):
    dados = "relatório ç".encode()
    d = await ex.executar("arquivo_escrever", {"caminho": "Documentos/rel.txt",
                                               "dados_b64": base64.b64encode(dados).decode()})
    assert d["tamanho"] == len(dados) and (ex.casa / "Documentos" / "rel.txt").read_bytes() == dados
    with pytest.raises(ErroAgente, match="já existe"):
        await ex.executar("arquivo_escrever", {"caminho": "Documentos/rel.txt", "dados_b64": "AA=="})
    await ex.executar("arquivo_escrever", {"caminho": "Documentos/rel.txt", "dados_b64": "AA==", "sobrescrever": True})
    d = await ex.executar("arquivo_ler", {"caminho": "Documentos/rel.txt"})
    assert base64.b64decode(d["dados_b64"]) == b"\x00" and d["nome"] == "rel.txt"
    d = await ex.executar("arquivos_listar", {"caminho": "."})
    assert d["itens"][0] == {**d["itens"][0], "nome": "Documentos", "pasta": True}
    fora = ex.casa.parent / "segredo.txt"
    fora.write_text("x")
    for op, args in [("arquivo_ler", {"caminho": str(fora)}), ("arquivo_ler", {"caminho": "../segredo.txt"}),
                     ("arquivo_escrever", {"caminho": "../x.txt", "dados_b64": "AA=="})]:
        with pytest.raises(ErroAgente, match="pasta do usuário"):
            await ex.executar(op, args)
    (ex.casa / "grande.bin").write_bytes(b"0" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ErroAgente, match="maior"):
        await ex.executar("arquivo_ler", {"caminho": "grande.bin"})


async def test_op_desconhecida(ex):
    with pytest.raises(ErroAgente, match="desconhecido"):
        await ex.executar("formatar_disco", {})


def test_registro_nao_guarda_texto_digitado():
    linha = descrever("teclado", {"texto": "minha senha secreta", "teclas": "enter"})
    assert "senha" not in linha and "19 caractere" in linha
    assert "dir" in descrever("comando", {"cmd": "dir"})
    assert "arrastar" in descrever("mouse", {"acao": "arrastar", "x": 1, "y": 2, "x2": 3, "y2": 4})
