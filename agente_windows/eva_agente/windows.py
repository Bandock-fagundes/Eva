"""Plataforma Windows 10/11 de verdade, só com ctypes (sem pywin32/pyautogui: menos coisa para o antivírus
estranhar e para o PyInstaller empacotar).

- DPI: o processo fica "per-monitor DPI aware" (preparar()), então print, tamanho da tela e posição do mouse ficam
  todos em pixels REAIS e batem entre si, mesmo com zoom de 125%/150% nas Configurações de vídeo.
- Mouse: SetCursorPos + SendInput (respeita "trocar botões" de canhoto).
- Teclado: SendInput com KEYEVENTF_UNICODE para texto (ç, ã, é, emoji) e códigos VK para atalhos (inclui win).
- Print: mss (BitBlt) do monitor principal. Janelas de UAC/tela de bloqueio ficam pretas ou falham (o Windows
  protege a "área de trabalho segura"); o agente avisa a Eva em vez de travar.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes

from .plataforma import ErroPlataforma, Plataforma

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x20, 0x40
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x800, 0x1000
WHEEL_DELTA = 120
SM_CXSCREEN, SM_CYSCREEN, SM_SWAPBUTTON = 0, 1, 23
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _UNIAO(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNIAO)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

# nome canônico (teclas.py) -> (código VK, tecla estendida?)
VK = {
    "ctrl": (0xA2, False), "alt": (0xA4, False), "shift": (0xA0, False), "win": (0x5B, True),
    "enter": (0x0D, False), "esc": (0x1B, False), "tab": (0x09, False), "backspace": (0x08, False),
    "delete": (0x2E, True), "space": (0x20, False), "up": (0x26, True), "down": (0x28, True),
    "left": (0x25, True), "right": (0x27, True), "pageup": (0x21, True), "pagedown": (0x22, True),
    "home": (0x24, True), "end": (0x23, True), "insert": (0x2D, True), "printscreen": (0x2C, True),
    "apps": (0x5D, True), "capslock": (0x14, False), "numlock": (0x90, True), "scrolllock": (0x91, False),
    "pause": (0x13, False), "volumeup": (0xAF, True), "volumedown": (0xAE, True), "volumemute": (0xAD, True),
    "playpause": (0xB3, True), "medianext": (0xB0, True), "mediaprev": (0xB1, True),
}
for _i in range(1, 25):
    VK[f"f{_i}"] = (0x6F + _i, False)
MOD_DE_VKSCAN = [(1, "shift"), (2, "ctrl"), (4, "alt")]


def _tecla(vk: int, solta: bool, estendida: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if solta else 0) | (KEYEVENTF_EXTENDEDKEY if estendida else 0)
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(wVk=vk, wScan=user32.MapVirtualKeyW(vk, 0) & 0xFF, dwFlags=flags, time=0, dwExtraInfo=0)
    return i


def _unicode(unidade: int, solta: bool) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(wVk=0, wScan=unidade, dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if solta else 0),
                        time=0, dwExtraInfo=0)
    return i


def _mouse(flags: int, dados: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx=0, dy=0, mouseData=dados & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0)
    return i


def _enviar(entradas: list[INPUT]):
    if not entradas:
        return
    arr = (INPUT * len(entradas))(*entradas)
    n = user32.SendInput(len(entradas), arr, ctypes.sizeof(INPUT))
    if n != len(entradas):
        raise ErroPlataforma("o Windows bloqueou o mouse/teclado (provavelmente uma janela de administrador/UAC "
                             "está na frente; peça para o Gabriel responder no PC)")


def _titulo(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _processo(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        tam = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(tam)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def _escondida(hwnd) -> bool:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        val = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(val), ctypes.sizeof(val))  # DWMWA_CLOAKED
        return val.value != 0
    except OSError:
        return False


def ligar_dpi_aware():
    """Precisa rodar ANTES de criar qualquer janela (Tk, ícone)."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # PER_MONITOR_AWARE_V2 (Win10 1703+)
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except (AttributeError, OSError):
        return "nenhum"


class PlataformaWindows(Plataforma):
    nome = "windows"

    def __init__(self):
        self._local = threading.local()
        self.dpi = "?"

    def preparar(self):
        self.dpi = ligar_dpi_aware()

    # ------------------------------------------------------------ tela
    def tamanho_tela(self):
        return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)

    def capturar(self):
        import mss
        from PIL import Image
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
            self._local.sct = sct
        w, h = self.tamanho_tela()
        try:
            shot = sct.grab({"left": 0, "top": 0, "width": w, "height": h})  # monitor principal começa em (0,0)
        except Exception as e:  # noqa: BLE001
            self._local.sct = None
            raise ErroPlataforma("não consegui tirar o print (tela bloqueada ou janela de administrador/UAC na "
                                 f"frente): {type(e).__name__}") from None
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    # ------------------------------------------------------------ mouse
    def posicao_mouse(self):
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def mover(self, x, y):
        if not user32.SetCursorPos(int(x), int(y)):
            raise ErroPlataforma("o Windows não deixou mover o mouse (janela de administrador/UAC na frente?)")

    def botao(self, botao, acao):
        trocados = bool(user32.GetSystemMetrics(SM_SWAPBUTTON))
        if botao in ("esquerdo", "direito") and trocados:
            botao = "direito" if botao == "esquerdo" else "esquerdo"
        desce, sobe = {"esquerdo": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                       "direito": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                       "meio": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}[botao]
        if acao == "descer":
            _enviar([_mouse(desce)])
        elif acao == "subir":
            _enviar([_mouse(sobe)])
        else:
            _enviar([_mouse(desce)])
            time.sleep(0.02)
            _enviar([_mouse(sobe)])

    def rolar(self, dy=0, dx=0):
        for _ in range(abs(int(dy))):
            _enviar([_mouse(MOUSEEVENTF_WHEEL, -WHEEL_DELTA if dy > 0 else WHEEL_DELTA)])
            time.sleep(0.015)
        for _ in range(abs(int(dx))):
            _enviar([_mouse(MOUSEEVENTF_HWHEEL, WHEEL_DELTA if dx > 0 else -WHEEL_DELTA)])
            time.sleep(0.015)

    # ------------------------------------------------------------ teclado
    def digitar(self, texto):
        lote: list[INPUT] = []

        def descarregar():
            _enviar(lote)
            lote.clear()
            time.sleep(0.008)

        for ch in texto:
            if ch == "\r":
                continue
            if ch in ("\n", "\t"):
                vk = 0x0D if ch == "\n" else 0x09
                lote += [_tecla(vk, False), _tecla(vk, True)]
            else:
                b = ch.encode("utf-16-le")
                for k in range(0, len(b), 2):   # emoji = par de "surrogates": manda as duas metades
                    unidade = int.from_bytes(b[k:k + 2], "little")
                    lote += [_unicode(unidade, False), _unicode(unidade, True)]
            if len(lote) >= 60:
                descarregar()
        descarregar()

    def _vk_do_caractere(self, ch: str) -> tuple[int, list[str]]:
        if "a" <= ch <= "z":
            return ord(ch.upper()), []
        if "0" <= ch <= "9":
            return ord(ch), []
        r = user32.VkKeyScanW(ch)
        if r == -1:
            raise ErroPlataforma(f"não sei apertar a tecla {ch!r} neste teclado")
        vk, estado = r & 0xFF, (r >> 8) & 0xFF
        return vk, [nome for bit, nome in MOD_DE_VKSCAN if estado & bit]

    def combo(self, teclas):
        mods = [t for t in teclas if t in ("ctrl", "alt", "shift", "win")]
        resto = [t for t in teclas if t not in ("ctrl", "alt", "shift", "win")]
        principais: list[tuple[int, bool]] = []
        for t in resto:
            if t in VK:
                principais.append(VK[t])
            elif len(t) == 1:
                vk, extras = self._vk_do_caractere(t)
                mods += [m for m in extras if m not in mods]
                principais.append((vk, False))
            else:
                raise ErroPlataforma(f"tecla desconhecida: {t}")
        entradas = [_tecla(VK[m][0], False, VK[m][1]) for m in mods]
        for vk, ext in principais:
            entradas += [_tecla(vk, False, ext), _tecla(vk, True, ext)]
        entradas += [_tecla(VK[m][0], True, VK[m][1]) for m in reversed(mods)]
        _enviar(entradas)

    def soltar_tudo(self):
        soltar = [_tecla(vk, True, vk in (0x5B, 0x5C)) for vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
                  if user32.GetAsyncKeyState(vk) & 0x8000]
        for vk, flag in ((0x01, MOUSEEVENTF_LEFTUP), (0x02, MOUSEEVENTF_RIGHTUP), (0x04, MOUSEEVENTF_MIDDLEUP)):
            if user32.GetAsyncKeyState(vk) & 0x8000:
                soltar.append(_mouse(flag))
        try:
            _enviar(soltar)
        except ErroPlataforma:
            pass

    # ------------------------------------------------------------ janelas
    def janela_ativa(self):
        h = user32.GetForegroundWindow()
        return _titulo(h) if h else ""

    def _janelas(self) -> list[tuple[int, str]]:
        achadas: list[tuple[int, str]] = []

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x80:   # WS_EX_TOOLWINDOW
                return True
            t = _titulo(hwnd)
            if t and t != "Program Manager" and not _escondida(hwnd):
                achadas.append((hwnd, t))
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return achadas

    def listar_janelas(self):
        ativa = user32.GetForegroundWindow()
        return [{"titulo": t, "ativa": h == ativa, "processo": _processo(h)} for h, t in self._janelas()]

    def focar_janela(self, titulo):
        alvo = titulo.casefold()
        lista = self._janelas()
        h = next((h for h, t in lista if t.casefold() == alvo), None) or \
            next((h for h, t in lista if alvo in t.casefold()), None)
        if not h:
            return None
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)   # SW_RESTORE
        frente = user32.GetForegroundWindow()
        meu, dele = kernel32.GetCurrentThreadId(), user32.GetWindowThreadProcessId(frente, None) if frente else 0
        ligou = bool(dele and dele != meu and user32.AttachThreadInput(meu, dele, True))
        try:
            user32.BringWindowToTop(h)
            user32.SetForegroundWindow(h)
        finally:
            if ligou:
                user32.AttachThreadInput(meu, dele, False)
        if user32.GetForegroundWindow() != h:   # truque do Alt: o Windows libera a troca de foco
            _enviar([_tecla(0xA4, False), _tecla(0xA4, True)])
            user32.SetForegroundWindow(h)
        return _titulo(h)

    # ------------------------------------------------------------ área de transferência
    def _abrir_clip(self):
        for _ in range(20):
            if user32.OpenClipboard(None):
                return
            time.sleep(0.05)
        raise ErroPlataforma("a área de transferência está ocupada por outro programa")

    def clipboard_ler(self):
        self._abrir_clip()
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()

    def clipboard_escrever(self, texto):
        dados = texto.encode("utf-16-le") + b"\x00\x00"
        self._abrir_clip()
        try:
            user32.EmptyClipboard()
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dados))
            if not h:
                raise ErroPlataforma("sem memória para a área de transferência")
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, dados, len(dados))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                kernel32.GlobalFree(h)
                raise ErroPlataforma("não consegui escrever na área de transferência")
        finally:
            user32.CloseClipboard()

    # ------------------------------------------------------------ abrir coisas
    def abrir_url(self, url):
        os.startfile(url)  # navegador padrão do Gabriel (já logado nas contas dele)

    def abrir_arquivo(self, caminho):
        os.startfile(caminho)

    def abrir_programa(self, programa):
        try:
            os.startfile(programa)   # acha pelo PATH e pelo registro "App Paths" (winword, excel, chrome…)
            return True
        except OSError:
            return False

    def abrir_terminal(self, pasta):
        subprocess.Popen(["powershell.exe", "-NoExit"], cwd=pasta, creationflags=0x00000010)  # CREATE_NEW_CONSOLE

    def comando_shell(self, cmd, tipo="powershell"):
        if tipo == "cmd":
            return ["cmd.exe", "/d", "/s", "/c", "chcp 65001>nul & " + cmd]
        prefixo = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefixo + cmd]
