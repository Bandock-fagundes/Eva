import pytest

from eva_agente.teclas import TeclaInvalida, normalizar


@pytest.mark.parametrize("entrada,esperado", [
    ("enter", [["enter"]]),
    ("Return", [["enter"]]),
    ("ctrl+l", [["ctrl", "l"]]),
    ("ctrl+a BackSpace", [["ctrl", "a"], ["backspace"]]),
    ("ctrl+a delete", [["ctrl", "a"], ["delete"]]),
    ("win", [["win"]]),
    ("super+e", [["win", "e"]]),
    ("win+r", [["win", "r"]]),
    ("alt+F4", [["alt", "f4"]]),
    ("ctrl+shift+esc", [["ctrl", "shift", "esc"]]),
    ("Prior Next", [["pageup"], ["pagedown"]]),
    ("ctrl++", [["ctrl", "+"]]),
    ("ctrl+-", [["ctrl", "-"]]),
    ("apagar espaco cima", [["backspace"], ["space"], ["up"]]),
    ("ctrl+L", [["ctrl", "l"]]),
    (["alt+tab", "enter"], [["alt", "tab"], ["enter"]]),
])
def test_normalizar(entrada, esperado):
    assert normalizar(entrada) == esperado


@pytest.mark.parametrize("ruim", ["", "   ", "ctrl+tecla_que_nao_existe", "f25", " ".join(["a"] * 31)])
def test_invalidas(ruim):
    with pytest.raises(TeclaInvalida):
        normalizar(ruim)
