"""Partes da interface que dá para testar sem tela: ícone gerado e a fila entre o núcleo e o Tk."""
import queue

from eva_agente import app


def test_icones_por_estado():
    for estado, cor in app.CORES.items():
        img = app.icone(cor)
        assert img.size == (64, 64) and img.mode == "RGBA"
        assert img.getpixel((56, 56))[:3] == cor, estado     # bolinha de status no canto


def test_interface_fila():
    f = queue.Queue()
    i = app.InterfaceFila(f)
    i.estado("agindo", "Eva está usando o computador")
    i.banner(True, "x")
    i.aviso("t", "m")
    i.token_invalido("revogado")
    assert [f.get_nowait()[0] for _ in range(4)] == ["estado", "banner", "aviso", "token_invalido"]


def test_caminho_do_icone_existe():
    import os
    assert os.path.isfile(app._caminho_ico())
