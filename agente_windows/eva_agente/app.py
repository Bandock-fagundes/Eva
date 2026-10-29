"""Interface do EVA-Agente no Windows: janela de pareamento, ícone perto do relógio, aviso "Eva está usando o
computador", atalho de emergência Ctrl+Alt+Shift+P. O trabalho de verdade fica no núcleo (nucleo.py).

Threads: Tk (principal: janelas e aviso) | núcleo asyncio (conexão com a Eva) | ícone (pystray) | atalho (RegisterHotKey).
Tudo que o núcleo quer mostrar passa por uma fila que o Tk lê a cada 100 ms.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import platform
import queue
import sys
import threading
import webbrowser

from . import NOME, SERVIDOR_PADRAO, VERSAO, config
from .nucleo import Agente, Interface
from .pareamento import ErroPareamento, normalizar_servidor, parear
from .plataforma import plataforma_do_sistema

log = logging.getLogger("eva_agente")

CORES = {"conectado": (34, 197, 94), "agindo": (234, 179, 8), "pausado": (239, 68, 68),
         "conectando": (148, 163, 184), "desconectado": (148, 163, 184), "revogado": (239, 68, 68),
         "parado": (100, 116, 139)}
FUNDO = "#0b0d12"
TEXTO = "#e5e7eb"
DESTAQUE = "#7c5cff"


def icone(cor: tuple[int, int, int], tamanho: int = 64):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, tamanho - 3, tamanho - 3), fill=(124, 92, 255, 255))
    r = tamanho // 4
    d.ellipse((tamanho - r - 3, tamanho - r - 3, tamanho - 1, tamanho - 1), fill=cor + (255,),
              outline=(11, 13, 18, 255), width=2)
    try:
        fonte = ImageFont.truetype("segoeuib.ttf", int(tamanho * 0.5))
    except OSError:
        fonte = ImageFont.load_default()
    d.text((tamanho * 0.30, tamanho * 0.16), "E", fill=(255, 255, 255, 255), font=fonte)
    return img


class InterfaceFila(Interface):
    def __init__(self, fila: queue.Queue):
        self.fila = fila

    def estado(self, estado, texto):
        self.fila.put(("estado", estado, texto))

    def banner(self, visivel, texto=""):
        self.fila.put(("banner", visivel, texto))

    def aviso(self, titulo, texto):
        self.fila.put(("aviso", titulo, texto))

    def token_invalido(self, motivo):
        self.fila.put(("token_invalido", motivo))


# ---------------------------------------------------------------- janela de pareamento

def janela_pareamento(root, cfg: config.Config, mensagem: str = "") -> config.Config | None:
    import tkinter as tk
    from tkinter import ttk

    resultado: dict = {}
    win = tk.Toplevel(root)
    win.title(f"{NOME} — Conectar este PC à Eva")
    win.configure(bg=FUNDO, padx=22, pady=18)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    try:
        win.iconbitmap(default=_caminho_ico())
    except Exception:  # noqa: BLE001
        pass

    def rot(texto, **kw):
        return tk.Label(win, text=texto, bg=FUNDO, fg=kw.pop("fg", TEXTO), justify="left", anchor="w",
                        font=kw.pop("font", ("Segoe UI", 10)), **kw)

    rot("Conectar este PC à Eva", font=("Segoe UI Semibold", 15)).grid(row=0, column=0, columnspan=2, sticky="w")
    rot("1. No app da Eva, abra a aba Tela e toque em “Conectar meu PC”.\n"
        "2. Digite aqui o código que aparecer (ele vale 10 minutos).", fg="#9ca3af"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))
    if mensagem:
        rot(mensagem, fg="#fca5a5", wraplength=420).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))

    rot("Endereço da Eva").grid(row=3, column=0, sticky="w")
    v_serv = tk.StringVar(value=cfg.servidor or SERVIDOR_PADRAO)
    e_serv = ttk.Entry(win, textvariable=v_serv, width=42)
    e_serv.grid(row=4, column=0, columnspan=2, sticky="we", pady=(2, 10))

    rot("Código de pareamento").grid(row=5, column=0, sticky="w")
    v_cod = tk.StringVar()
    e_cod = tk.Entry(win, textvariable=v_cod, width=12, font=("Consolas", 20), justify="center",
                     bg="#151924", fg="white", insertbackground="white", relief="flat")
    e_cod.grid(row=6, column=0, columnspan=2, sticky="we", pady=(2, 10), ipady=6)

    rot("Nome deste PC (aparece no app)").grid(row=7, column=0, sticky="w")
    v_nome = tk.StringVar(value=cfg.nome or platform.node() or "PC do Gabriel")
    ttk.Entry(win, textvariable=v_nome, width=42).grid(row=8, column=0, columnspan=2, sticky="we", pady=(2, 12))

    v_status = tk.StringVar()
    lbl = rot("", fg="#fbbf24", wraplength=420, textvariable=v_status)
    lbl.grid(row=9, column=0, columnspan=2, sticky="w")

    botoes = tk.Frame(win, bg=FUNDO)
    botoes.grid(row=10, column=0, columnspan=2, sticky="e", pady=(12, 0))
    b_ok = tk.Button(botoes, text="Conectar", bg=DESTAQUE, fg="white", relief="flat", padx=18, pady=6,
                     font=("Segoe UI Semibold", 10), activebackground="#6a4cf0", activeforeground="white")
    b_cancelar = tk.Button(botoes, text="Cancelar", bg="#1f2433", fg=TEXTO, relief="flat", padx=14, pady=6,
                           font=("Segoe UI", 10), command=win.destroy)
    b_cancelar.pack(side="right", padx=(8, 0))
    b_ok.pack(side="right")

    def terminou(ok, dados):
        b_ok.configure(state="normal")
        if ok:
            resultado.update(dados)
            win.destroy()
        else:
            v_status.set(dados)
            lbl.configure(fg="#fca5a5")

    def conectar(*_):
        try:
            servidor = normalizar_servidor(v_serv.get())
        except ErroPareamento as e:
            terminou(False, str(e))
            return
        b_ok.configure(state="disabled")
        lbl.configure(fg="#fbbf24")
        v_status.set("Conectando… (se a Eva estava dormindo, pode levar até 1 minuto)")

        def trabalho():
            try:
                d = parear(servidor, v_cod.get(), v_nome.get().strip())
                win.after(0, terminou, True, d)
            except ErroPareamento as e:
                win.after(0, terminou, False, str(e))
            except Exception as e:  # noqa: BLE001
                win.after(0, terminou, False, f"Erro inesperado: {type(e).__name__}: {e}")
        threading.Thread(target=trabalho, daemon=True).start()

    b_ok.configure(command=conectar)
    e_cod.bind("<Return>", conectar)
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 3
    win.geometry(f"+{x}+{y}")
    e_cod.focus_force()
    win.grab_set()
    root.wait_window(win)
    if not resultado:
        return None
    novo = config.Config(servidor=resultado["servidor"], dispositivo_id=resultado.get("dispositivo_id"),
                         nome=resultado.get("nome") or v_nome.get(), token=resultado["token"],
                         manter_eva_acordada=cfg.manter_eva_acordada, extras=cfg.extras)
    config.salvar(novo)
    log.info("PC pareado com a Eva em %s como '%s'", novo.servidor, novo.nome)
    return novo


def _caminho_ico() -> str:
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, "recursos", "eva.ico")


# ---------------------------------------------------------------- aviso "Eva está usando o computador"

class Banner:
    """Faixa pequena, translúcida e sempre por cima, que NÃO recebe cliques e NÃO aparece nos prints da Eva."""

    def __init__(self, root):
        import tkinter as tk
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#111827")
        self.var = tk.StringVar(value="")
        tk.Label(self.top, textvariable=self.var, bg="#111827", fg="#fde68a", font=("Segoe UI Semibold", 10),
                 padx=16, pady=7).pack()
        self.top.withdraw()
        self._estilizado = False
        self.visivel = False

    def _estilizar(self):
        if self._estilizado or sys.platform != "win32":
            return
        self._estilizado = True
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            hwnd = user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
            estilo = user32.GetWindowLongW(hwnd, -20)
            # WS_EX_LAYERED | WS_EX_TRANSPARENT (clique atravessa) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            user32.SetWindowLongW(hwnd, -20, estilo | 0x80000 | 0x20 | 0x80 | 0x08000000)
            user32.SetWindowDisplayAffinity(hwnd, 0x11)   # WDA_EXCLUDEFROMCAPTURE (Win10 2004+): fora dos prints
        except Exception as e:  # noqa: BLE001
            log.debug("estilo do aviso falhou: %s", e)
        self.top.attributes("-alpha", 0.88)

    def mostrar(self, texto: str):
        self.var.set("●  " + texto)
        self.top.update_idletasks()
        w = self.top.winfo_reqwidth()
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.top.geometry(f"+{(sw - w) // 2}+{sh - 110}")
        self.top.deiconify()
        self._estilizar()
        self.top.lift()
        self.visivel = True

    def esconder(self):
        if self.visivel:
            self.top.withdraw()
            self.visivel = False


# ---------------------------------------------------------------- app

class App:
    def __init__(self, root, cfg: config.Config, plataforma):
        self.root = root
        self.cfg = cfg
        self.p = plataforma
        self.fila: queue.Queue = queue.Queue()
        self.agente: Agente | None = None
        self.th_nucleo: threading.Thread | None = None
        self.estado, self.texto = "conectando", "Conectando à Eva…"
        self.banner = Banner(root)
        self.tray = None
        self._hk_tid = None
        self._saindo = False

    # ---------- ciclo ----------
    def iniciar(self):
        self._iniciar_nucleo()
        threading.Thread(target=self._rodar_tray, daemon=True, name="eva-icone").start()
        threading.Thread(target=self._rodar_atalho, daemon=True, name="eva-atalho").start()
        self.root.after(100, self._processar_fila)
        self.fila.put(("aviso", "EVA-Agente ligado", "A Eva já pode usar este PC quando você pedir. "
                                                     "Ctrl+Alt+Shift+P para tudo na hora."))

    def _iniciar_nucleo(self):
        self.agente = Agente(self.cfg, self.p, InterfaceFila(self.fila), registro=log)
        ag = self.agente

        def rodar():
            try:
                motivo = asyncio.run(ag.rodar())
            except Exception as e:  # noqa: BLE001
                log.exception("núcleo caiu")
                motivo = f"erro: {e}"
            self.fila.put(("fim", motivo))
        self.th_nucleo = threading.Thread(target=rodar, daemon=True, name="eva-nucleo")
        self.th_nucleo.start()

    def _processar_fila(self):
        try:
            while True:
                ev = self.fila.get_nowait()
                self._tratar(ev)
        except queue.Empty:
            pass
        if not self._saindo:
            self.root.after(100, self._processar_fila)

    def _tratar(self, ev):
        tipo = ev[0]
        if tipo == "estado":
            self.estado, self.texto = ev[1], ev[2]
            self._atualizar_tray()
        elif tipo == "banner":
            if ev[1]:
                self.banner.mostrar(ev[2])
            else:
                self.banner.esconder()
        elif tipo == "aviso":
            self._notificar(ev[1], ev[2])
        elif tipo == "token_invalido":
            pass  # o "fim" com motivo 'revogado' chega logo depois
        elif tipo == "confirmar_desconectar":
            from tkinter import messagebox
            if messagebox.askyesno(NOME, "Desconectar este PC da Eva?\n\nEla não vai mais conseguir usar este "
                                         "computador até você parear de novo com um código novo."):
                self.agente.desconectar()
        elif tipo == "fim":
            self._fim(ev[1])

    def _fim(self, motivo: str):
        self.banner.esconder()
        if self._saindo or motivo == "sair":
            self._encerrar()
            return
        if motivo in ("revogado", "desconectado"):
            config.esquecer_token()
            msg = ("Este PC foi desconectado da Eva." if motivo == "desconectado" else
                   "A Eva não reconheceu mais este PC (foi desconectado no app). Gere um código novo para voltar.")
            novo = janela_pareamento(self.root, config.carregar(), msg)
            if novo is None:
                self._encerrar()
                return
            self.cfg = novo
            self._iniciar_nucleo()
            return
        log.warning("núcleo terminou (%s); reiniciando", motivo)
        self._iniciar_nucleo()

    def _encerrar(self):
        if getattr(self, "_encerrado", False):
            return
        self._encerrado = True
        self._saindo = True
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._hk_tid and sys.platform == "win32":
            ctypes.windll.user32.PostThreadMessageW(self._hk_tid, 0x0012, 0, 0)   # WM_QUIT
        log.info("EVA-Agente fechado")
        self.root.after(50, self.root.destroy)

    # ---------- ícone ----------
    def _pausado_local(self):
        return bool(self.agente and self.agente.pausa_local)

    def _rodar_tray(self):
        import pystray
        from pystray import Menu, MenuItem as Item
        menu = Menu(
            Item(lambda i: self.texto, None, enabled=False),
            Menu.SEPARATOR,
            Item("Pausar Eva  (Ctrl+Alt+Shift+P)", lambda i, it: self.agente.pausar("pausada pelo ícone"),
                 visible=lambda i: not self._pausado_local()),
            Item("Retomar Eva", lambda i, it: self.agente.retomar(), visible=lambda i: self._pausado_local()),
            Item("Ver registro", lambda i, it: self._ver_registro()),
            Item("Abrir a Eva no navegador", lambda i, it: webbrowser.open(self.cfg.servidor)),
            Item("Iniciar com o Windows", lambda i, it: self._alternar_inicio(),
                 checked=lambda i: config.inicia_com_windows()),
            Menu.SEPARATOR,
            Item("Desconectar este PC da Eva…", lambda i, it: self.fila.put(("confirmar_desconectar",))),
            Item("Sair", lambda i, it: self._sair()),
        )
        self.tray = pystray.Icon(NOME, icone(CORES["conectando"]), f"{NOME} v{VERSAO}", menu)
        self.tray.run()

    def _atualizar_tray(self):
        if self.tray is None:
            return
        try:
            self.tray.icon = icone(CORES.get(self.estado, (148, 163, 184)))
            self.tray.title = f"{NOME}: {self.texto}"[:120]
            self.tray.update_menu()
        except Exception as e:  # noqa: BLE001
            log.debug("ícone: %s", e)

    def _notificar(self, titulo, texto):
        try:
            if self.tray is not None and getattr(self.tray, "HAS_NOTIFICATION", True):
                self.tray.notify(texto, titulo)
        except Exception:  # noqa: BLE001
            pass

    def _ver_registro(self):
        arq = config.arquivo_registro()
        if sys.platform == "win32":
            os.startfile(str(arq))
        else:
            webbrowser.open(arq.as_uri())

    def _alternar_inicio(self):
        try:
            ligado = config.definir_inicio_com_windows(not config.inicia_com_windows())
            self.fila.put(("aviso", NOME, "Vai abrir sozinho quando o Windows ligar." if ligado
                           else "Não abre mais sozinho com o Windows."))
        except OSError as e:
            self.fila.put(("aviso", NOME, f"Não consegui mudar: {e}"))

    def _sair(self):
        self._saindo = True
        if self.agente:
            self.agente.sair()
        self.fila.put(("fim", "sair"))

    # ---------- atalho de emergência ----------
    def _rodar_atalho(self):
        if sys.platform != "win32":
            return
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._hk_tid = kernel32.GetCurrentThreadId()
        # MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, tecla P
        if not user32.RegisterHotKey(None, 1, 0x0001 | 0x0002 | 0x0004 | 0x4000, 0x50):
            self.fila.put(("aviso", "Atalho indisponível", "Outro programa já usa Ctrl+Alt+Shift+P. Use o ícone "
                                                           "da Eva → Pausar, ou jogue o mouse num canto da tela."))
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and self.agente:   # WM_HOTKEY
                    log.info("ATALHO DE EMERGÊNCIA apertado")
                    self.agente.emergencia("atalho de emergência (Ctrl+Alt+Shift+P)")
        finally:
            user32.UnregisterHotKey(None, 1)


def _instancia_unica():
    if sys.platform != "win32":
        return True
    k = ctypes.windll.kernel32
    globals()["_MUTEX"] = k.CreateMutexW(None, False, "Local\\EVA-Agente-unico")
    return k.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def main():
    plataforma = plataforma_do_sistema()
    plataforma.preparar()                 # DPI aware ANTES de qualquer janela
    os.chdir(config.pasta())              # nada de achar arquivos por acaso na pasta Downloads
    config.configurar_registro()
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    if not _instancia_unica():
        messagebox.showinfo(NOME, "O EVA-Agente já está aberto. Procure o ícone roxo perto do relógio "
                                  "(talvez escondido na setinha ^).")
        return
    log.info("EVA-Agente v%s iniciado (DPI: %s)", VERSAO, getattr(plataforma, "dpi", "?"))
    cfg = config.carregar()
    if not cfg.pareado:
        if "--inicio" in sys.argv:   # ligou com o Windows mas ainda não foi pareado: não incomoda
            return
        cfg = janela_pareamento(root, cfg)
        if cfg is None:
            return
    App(root, cfg, plataforma).iniciar()
    root.mainloop()
