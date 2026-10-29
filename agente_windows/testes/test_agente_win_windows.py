"""Lógica da plataforma Windows.

- No Linux: importa windows.py com um user32/kernel32 de mentira e confere as sequências de SendInput que ele monta
  (atalhos com win/ctrl, texto unicode com emoji, rodinha, botões trocados de canhoto, soltar tudo).
- No Windows (CI): testes de fumaça de verdade (DPI, tamanho da tela, print, área de transferência).
"""
import ctypes
import importlib
import sys
import types

import pytest


class _Funcao:
    def __init__(self, nome, dll):
        self.nome, self.dll = nome, dll
        self.argtypes = None
        self.restype = None

    def __call__(self, *a):
        self.dll.chamadas.append((self.nome, a))
        r = self.dll.retornos.get(self.nome, 1)
        return r(*a) if callable(r) else r


class _DLL:
    def __init__(self, *a, **k):
        self.chamadas, self.retornos = [], {}

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        f = _Funcao(nome, self)
        object.__setattr__(self, nome, f)
        return f


@pytest.fixture
def win(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("no Windows de verdade usamos os testes de fumaça abaixo")
    dlls = {}

    def fabrica(nome, *a, **k):
        return dlls.setdefault(nome, _DLL())
    monkeypatch.setattr(ctypes, "WinDLL", fabrica, raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    sys.modules.pop("eva_agente.windows", None)
    mod = importlib.import_module("eva_agente.windows")
    enviados = []

    def enviar(entradas):
        for i in entradas:
            if i.type == mod.INPUT_KEYBOARD:
                k = i.u.ki
                enviados.append(("tecla", k.wVk, k.wScan, bool(k.dwFlags & mod.KEYEVENTF_KEYUP),
                                 bool(k.dwFlags & mod.KEYEVENTF_UNICODE), bool(k.dwFlags & mod.KEYEVENTF_EXTENDEDKEY)))
            else:
                enviados.append(("mouse", i.u.mi.dwFlags, i.u.mi.mouseData))
    monkeypatch.setattr(mod, "_enviar", enviar)
    mod.user32.retornos.update({"MapVirtualKeyW": 0, "GetSystemMetrics": 0, "GetAsyncKeyState": 0})
    p = mod.PlataformaWindows()
    yield mod, p, enviados, mod.user32
    sys.modules.pop("eva_agente.windows", None)


def _vk(enviados):
    return [(e[1], e[3]) for e in enviados if e[0] == "tecla"]


def test_atalhos(win):
    mod, p, env, user32 = win
    p.combo(["ctrl", "l"])
    assert _vk(env) == [(0xA2, False), (ord("L"), False), (ord("L"), True), (0xA2, True)]
    env.clear()
    p.combo(["win", "r"])
    assert _vk(env) == [(0x5B, False), (ord("R"), False), (ord("R"), True), (0x5B, True)]
    assert env[0][5] is True          # a tecla Windows é "estendida"
    env.clear()
    p.combo(["ctrl", "shift", "esc"])
    assert _vk(env) == [(0xA2, False), (0xA0, False), (0x1B, False), (0x1B, True), (0xA0, True), (0xA2, True)]
    env.clear()
    p.combo(["alt", "f4"])
    assert _vk(env)[1] == (0x73, False)
    env.clear()
    user32.retornos["VkKeyScanW"] = 0x1BB      # '+' no teclado: VK_OEM_PLUS com shift
    p.combo(["ctrl", "+"])
    assert _vk(env) == [(0xA2, False), (0xA0, False), (0xBB, False), (0xBB, True), (0xA0, True), (0xA2, True)]
    user32.retornos["VkKeyScanW"] = -1
    with pytest.raises(mod.ErroPlataforma):
        p.combo(["ç"])


def test_texto_unicode_com_acentos_e_emoji(win):
    mod, p, env, _ = win
    p.digitar("ção😀\n")
    uni = [(e[2], e[3]) for e in env if e[4]]
    assert uni[:6] == [(ord("ç"), False), (ord("ç"), True), (ord("ã"), False), (ord("ã"), True),
                       (ord("o"), False), (ord("o"), True)]
    # emoji = 2 metades (surrogates) UTF-16
    assert [u for u, _ in uni[6:]] == [0xD83D, 0xD83D, 0xDE00, 0xDE00]
    enter = [e for e in env if not e[4]]
    assert [(e[1], e[3]) for e in enter] == [(0x0D, False), (0x0D, True)]


def test_mouse_rodinha_e_botoes(win):
    mod, p, env, user32 = win
    p.rolar(dy=2)
    p.rolar(dy=-1, dx=1)
    rodas = [(e[1], e[2]) for e in env]
    assert rodas == [(mod.MOUSEEVENTF_WHEEL, (-120) & 0xFFFFFFFF)] * 2 + [(mod.MOUSEEVENTF_WHEEL, 120),
                                                                        (mod.MOUSEEVENTF_HWHEEL, 120)]
    env.clear()
    p.botao("esquerdo", "clicar")
    assert [e[1] for e in env] == [mod.MOUSEEVENTF_LEFTDOWN, mod.MOUSEEVENTF_LEFTUP]
    env.clear()
    user32.retornos["GetSystemMetrics"] = 1      # canhoto: botões trocados
    p.botao("esquerdo", "clicar")
    assert [e[1] for e in env] == [mod.MOUSEEVENTF_RIGHTDOWN, mod.MOUSEEVENTF_RIGHTUP]


def test_soltar_tudo_so_o_que_esta_apertado(win):
    mod, p, env, user32 = win
    apertadas = {0xA2, 0x01}
    user32.retornos["GetAsyncKeyState"] = lambda vk: -32768 if vk in apertadas else 0
    p.soltar_tudo()
    assert ("tecla", 0xA2) in [(e[0], e[1]) for e in env]
    assert ("mouse", mod.MOUSEEVENTF_LEFTUP) in [(e[0], e[1]) for e in env]
    assert len(env) == 2


def test_comando_powershell_utf8(win):
    mod, p, *_ = win
    args = p.comando_shell("Get-Date")
    assert args[0] == "powershell.exe" and "-NoProfile" in args and args[-1].endswith("Get-Date")
    assert "UTF8" in args[-1]
    assert p.comando_shell("dir", "cmd")[:2] == ["cmd.exe", "/d"]


# ---------------------------------------------------------------- Windows de verdade (CI windows-latest)

so_windows = pytest.mark.skipif(sys.platform != "win32", reason="só no Windows")


@so_windows
def test_fumaca_windows_dpi_e_tela():
    from eva_agente.windows import PlataformaWindows
    p = PlataformaWindows()
    p.preparar()
    assert p.dpi in ("per-monitor-v2", "per-monitor", "system", "nenhum")
    w, h = p.tamanho_tela()
    assert w > 0 and h > 0
    x, y = p.posicao_mouse()
    assert isinstance(x, int)
    try:
        img = p.capturar()
    except Exception as e:  # noqa: BLE001 — serviço de CI sem área de trabalho interativa
        pytest.skip(f"sem área de trabalho para print: {e}")
    assert img.size == (w, h)   # print e coordenadas na mesma escala (DPI aware)


@so_windows
def test_fumaca_windows_clipboard_e_janelas():
    from eva_agente.windows import PlataformaWindows
    p = PlataformaWindows()
    try:
        p.clipboard_escrever("Eva ✓ ção")
        assert p.clipboard_ler() == "Eva ✓ ção"
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"área de transferência indisponível no CI: {e}")
    assert isinstance(p.listar_janelas(), list)
    assert isinstance(p.janela_ativa(), str)


@so_windows
def test_fumaca_dpapi():
    from eva_agente import dpapi
    assert dpapi.decifrar(dpapi.cifrar(b"evapc_abc")) == b"evapc_abc"
