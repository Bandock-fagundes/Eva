"""Executa os comandos que a Eva manda (op + args) usando a Plataforma. Não sabe nada de rede.

Cada op devolve um dicionário no mesmo formato do agente da nuvem (computador/agente/agente_pc.py), para a Eva
tratar os dois do mesmo jeito: {"tela": {"largura", "altura"}, "mouse": {"x", "y"}, "janela": "..."}.

Limites de segurança daqui (os de permissão ficam na Eva): texto até 5000 caracteres por vez, comando até 300 s com
saída cortada, arquivos só dentro da pasta do usuário (C:\\Users\\<nome>) e até 8 MB, nada de abrir executáveis
pelo "abrir" (instalar/rodar programa passa pelo comando, que pede aprovação na Eva).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import imagem
from .plataforma import ErroPlataforma, Plataforma
from .teclas import TeclaInvalida, normalizar

TEXTO_MAX = 5000
SAIDA_MAX = 10000
ARQUIVO_MAX = 8 * 1024 * 1024
CLIP_MAX = 200_000
EXECUTAVEIS = {".exe", ".msi", ".msix", ".appx", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
               ".jse", ".wsf", ".wsh", ".scr", ".pif", ".lnk", ".url", ".reg", ".hta", ".cpl", ".msc", ".jar", ".dll",
               ".sys", ".inf"}

# app -> (tipo, alvo, nome no menu Iniciar)
APPS = {
    "navegador": ("url", "https://www.google.com/", None),
    "chrome": ("prog", "chrome", "Google Chrome"),
    "google chrome": ("prog", "chrome", "Google Chrome"),
    "edge": ("prog", "msedge", "Microsoft Edge"),
    "firefox": ("prog", "firefox", "Firefox"),
    "terminal": ("terminal", None, None),
    "powershell": ("terminal", None, None),
    "arquivos": ("prog", "explorer", "Explorador de Arquivos"),
    "explorador": ("prog", "explorer", "Explorador de Arquivos"),
    "editor": ("prog", "notepad", "Bloco de Notas"),
    "bloco de notas": ("prog", "notepad", "Bloco de Notas"),
    "notepad": ("prog", "notepad", "Bloco de Notas"),
    "calculadora": ("prog", "calc", "Calculadora"),
    "paint": ("prog", "mspaint", "Paint"),
    "configuracoes": ("url", "ms-settings:", None),
    "configurações": ("url", "ms-settings:", None),
    "texto": ("prog", "winword", "Word"),
    "word": ("prog", "winword", "Word"),
    "planilha": ("prog", "excel", "Excel"),
    "excel": ("prog", "excel", "Excel"),
    "apresentacao": ("prog", "powerpnt", "PowerPoint"),
    "apresentação": ("prog", "powerpnt", "PowerPoint"),
    "powerpoint": ("prog", "powerpnt", "PowerPoint"),
    "outlook": ("prog", "outlook", "Outlook"),
    "spotify": ("busca", None, "Spotify"),
    "whatsapp": ("busca", None, "WhatsApp"),
}
ESQUEMAS_OK = ("http://", "https://", "ms-settings:", "mailto:")


class ErroAgente(Exception):
    def __init__(self, msg: str, codigo: str = "falhou"):
        super().__init__(msg)
        self.codigo = codigo


def truncar(texto: str, limite: int = SAIDA_MAX) -> str:
    if len(texto) <= limite:
        return texto
    cab = int(limite * 0.4)
    cauda = limite - cab
    return texto[:cab] + f"\n… ({len(texto) - limite} caracteres omitidos) …\n" + texto[-cauda:]


def na_canto(x: int, y: int, w: int, h: int, margem: int = 1) -> bool:
    return (x <= margem or x >= w - 1 - margem) and (y <= margem or y >= h - 1 - margem)


class Executor:
    def __init__(self, plataforma: Plataforma, casa: str | Path | None = None, registro: logging.Logger | None = None):
        self.p = plataforma
        self.casa = Path(casa or Path.home()).resolve()
        self.log = registro or logging.getLogger("eva_agente")
        self._t_entrada = ThreadPoolExecutor(1, thread_name_prefix="eva-entrada")
        self._t_captura = ThreadPoolExecutor(1, thread_name_prefix="eva-captura")
        self.processos: set = set()
        self.pos_agente: tuple[int, int] | None = None   # onde o próprio agente deixou o mouse
        self.ts_agente = 0.0
        self.pausa_menu = 0.9      # menu Iniciar: tempo para abrir / para achar o programa
        self.pausa_busca = 1.3

    def fechar(self):
        self._t_entrada.shutdown(wait=False, cancel_futures=True)
        self._t_captura.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ utilidades
    async def _em(self, fn, *a):
        try:
            return await asyncio.get_running_loop().run_in_executor(self._t_entrada, fn, *a)
        except ErroPlataforma as e:
            raise ErroAgente(str(e)) from None
        except OSError as e:
            raise ErroAgente(f"o Windows recusou: {e.strerror or e}") from None

    async def _cap(self, fn, *a):
        try:
            return await asyncio.get_running_loop().run_in_executor(self._t_captura, fn, *a)
        except ErroPlataforma as e:
            raise ErroAgente(str(e)) from None
        except Exception as e:  # noqa: BLE001 — mss/PIL: tela protegida (UAC/bloqueio) costuma cair aqui
            raise ErroAgente(f"não consegui tirar o print (a tela pode estar bloqueada ou numa janela de "
                             f"administrador/UAC): {type(e).__name__}") from None

    async def info(self) -> dict:
        (w, h), (mx, my), janela = await asyncio.gather(
            self._em(self.p.tamanho_tela), self._em(self.p.posicao_mouse), self._em(self.p.janela_ativa))
        return {"tela": {"largura": w, "altura": h}, "mouse": {"x": mx, "y": my}, "janela": (janela or "")[:300]}

    def mouse_no_canto(self) -> bool:
        """Failsafe: o GABRIEL jogou o mouse num canto da tela (o agente nunca para exatamente no canto)."""
        try:
            w, h = self.p.tamanho_tela()
            x, y = self.p.posicao_mouse()
        except Exception:  # noqa: BLE001
            return False
        return na_canto(x, y, w, h) and (x, y) != self.pos_agente

    async def _ponto(self, x, y, nome="x/y") -> tuple[int, int]:
        if x is None or y is None:
            raise ErroAgente(f"faltou {nome}", "invalido")
        try:
            x, y = int(round(float(x))), int(round(float(y)))
        except (TypeError, ValueError):
            raise ErroAgente(f"{nome} precisa ser número", "invalido") from None
        w, h = await self._em(self.p.tamanho_tela)
        if not (0 <= x < w and 0 <= y < h):
            raise ErroAgente(f"{nome} fora da tela ({x},{y}); a tela tem {w}x{h}", "invalido")
        if na_canto(x, y, w, h, margem=2):  # nunca para no canto exato (o canto é o freio de emergência do Gabriel)
            x = min(max(x, 4), w - 5)
            y = min(max(y, 4), h - 5)
        return x, y

    async def _mover(self, x: int, y: int):
        await self._em(self.p.mover, x, y)
        self.pos_agente = (x, y)
        self.ts_agente = time.monotonic()

    def caminho_seguro(self, rel: str, deve_existir: bool = False) -> Path:
        """Caminho relativo à pasta do usuário (ou absoluto dentro dela). Nada de sair dela."""
        rel = str(rel or ".").strip().strip('"')
        if "\x00" in rel or len(rel) > 1000:
            raise ErroAgente("caminho inválido", "invalido")
        rel = os.path.expandvars(rel.replace("~", str(self.casa), 1) if rel.startswith("~") else rel)
        p = Path(rel)
        p = (p if p.is_absolute() else self.casa / p).resolve()
        a, b = os.path.normcase(str(p)), os.path.normcase(str(self.casa))
        if a != b and not a.startswith(b.rstrip("\\/") + os.sep):
            raise ErroAgente(f"só posso mexer em arquivos dentro da pasta do usuário ({self.casa}).", "proibido")
        if deve_existir and not p.exists():
            raise ErroAgente("arquivo ou pasta não encontrado", "nao_encontrado")
        return p

    # ------------------------------------------------------------ despacho
    async def executar(self, op: str, args: dict) -> dict:
        fn = getattr(self, f"op_{op}", None)
        if fn is None or not isinstance(args, dict):
            raise ErroAgente(f"comando desconhecido: {op!r}", "invalido")
        return await fn(**{k: v for k, v in args.items() if k != "origem"})

    # ------------------------------------------------------------ ops
    async def op_info(self) -> dict:
        return {"ok": True, **(await self.info())}

    async def op_tela(self, largura: int | None = 1280, qualidade: int = 60, cursor: bool = True) -> dict:
        largura = max(100, min(4000, int(largura or 1280)))
        qualidade = max(10, min(95, int(qualidade or 60)))
        img = await self._cap(self.p.capturar)
        mx, my = await self._em(self.p.posicao_mouse)
        dados, w0, h0, w1, h1 = await self._cap(imagem.processar, img, (mx, my) if cursor else None, largura, qualidade)
        return {"ok": True, "imagem_b64": base64.b64encode(dados).decode(), "tela": {"largura": w0, "altura": h0},
                "imagem": {"largura": w1, "altura": h1}, "mouse": {"x": mx, "y": my}}

    async def op_mouse(self, acao: str, x=None, y=None, x2=None, y2=None, dy: int = 0, dx: int = 0) -> dict:
        if acao == "rolar":
            if x is not None and y is not None:
                await self._mover(*(await self._ponto(x, y)))
            dy, dx = max(-30, min(30, int(dy or 0))), max(-30, min(30, int(dx or 0)))
            if not dy and not dx:
                raise ErroAgente("rolar precisa de dy ou dx", "invalido")
            await self._em(self.p.rolar, dy, dx)
        elif acao in ("mover", "clicar", "duplo", "direito", "meio", "arrastar"):
            px, py = await self._ponto(x, y)
            await self._mover(px, py)
            await asyncio.sleep(0.03)
            if acao == "clicar":
                await self._em(self.p.botao, "esquerdo", "clicar")
            elif acao == "duplo":
                await self._em(self.p.botao, "esquerdo", "clicar")
                await asyncio.sleep(0.08)
                await self._em(self.p.botao, "esquerdo", "clicar")
            elif acao == "direito":
                await self._em(self.p.botao, "direito", "clicar")
            elif acao == "meio":
                await self._em(self.p.botao, "meio", "clicar")
            elif acao == "arrastar":
                qx, qy = await self._ponto(x2, y2, "x2/y2")
                await self._em(self.p.botao, "esquerdo", "descer")
                try:
                    passos = 12
                    for i in range(1, passos + 1):
                        await self._mover(round(px + (qx - px) * i / passos), round(py + (qy - py) * i / passos))
                        await asyncio.sleep(0.02)
                finally:
                    await self._em(self.p.botao, "esquerdo", "subir")
        else:
            raise ErroAgente(f"ação de mouse inválida: {acao!r}", "invalido")
        await asyncio.sleep(0.05)
        return {"ok": True, **(await self.info())}

    async def op_teclado(self, texto: str | None = None, teclas=None, modo: str = "auto") -> dict:
        if not texto and not teclas:
            raise ErroAgente("mande 'texto' ou 'teclas'", "invalido")
        combos = []
        if teclas:
            try:
                combos = normalizar(teclas)
            except TeclaInvalida as e:
                raise ErroAgente(str(e), "invalido") from None
        if texto:
            texto = str(texto).replace("\r\n", "\n")
            if len(texto) > TEXTO_MAX:
                raise ErroAgente(f"texto grande demais (máx. {TEXTO_MAX} caracteres por vez)", "invalido")
            for i in range(0, len(texto), 200):
                await self._em(self.p.digitar, texto[i:i + 200])
        for c in combos:
            await self._em(self.p.combo, c)
            await asyncio.sleep(0.06)
        await asyncio.sleep(0.05)
        return {"ok": True, **(await self.info())}

    async def op_comando(self, cmd: str, timeout: int = 60, shell: str = "powershell") -> dict:
        cmd = str(cmd or "")
        if not cmd.strip() or len(cmd) > 8000:
            raise ErroAgente("comando vazio ou grande demais", "invalido")
        timeout = max(1, min(300, int(timeout or 60)))
        args = self.p.comando_shell(cmd, "cmd" if shell == "cmd" else "powershell")
        inicio = time.monotonic()
        kw: dict = {"cwd": str(self.casa), "stdin": asyncio.subprocess.DEVNULL, "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.STDOUT}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(*args, **kw)
        except OSError as e:
            raise ErroAgente(f"não consegui rodar o comando: {e}") from None
        self.processos.add(proc)
        estourou = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            estourou = True
            self._matar(proc)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:  # noqa: BLE001
                out = b""
        except asyncio.CancelledError:
            self._matar(proc)
            raise
        finally:
            self.processos.discard(proc)
        return {"ok": not estourou and proc.returncode == 0, "codigo": proc.returncode, "tempo_esgotado": estourou,
                "saida": truncar(self._decodificar(out or b"")), "duracao_s": round(time.monotonic() - inicio, 2)}

    @staticmethod
    def _decodificar(b: bytes) -> str:
        for cod in ("utf-8", "cp850", "cp1252"):
            try:
                return b.decode(cod)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", errors="replace")

    @staticmethod
    def _matar(proc):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10,
                               creationflags=0x08000000)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    async def op_abrir(self, url: str | None = None, app: str | None = None, arquivo: str | None = None) -> dict:
        if url:
            alvo = str(url).strip()
            if not alvo.lower().startswith(ESQUEMAS_OK):
                if re.match(r"^[\w.-]+\.[a-z]{2,}(:\d+)?(/.*)?$", alvo, re.I):
                    alvo = "https://" + alvo
                else:
                    raise ErroAgente("URL precisa começar com http:// ou https://", "invalido")
            await self._em(self.p.abrir_url, alvo)
            aberto = alvo
        elif app:
            aberto = await self._abrir_app(str(app).strip())
        elif arquivo:
            p = self.caminho_seguro(arquivo, deve_existir=True)
            if p.suffix.lower() in EXECUTAVEIS:
                raise ErroAgente("não abro programas/instaladores pelo 'abrir'. Para rodar ou instalar algo, use o "
                                 "comando (que pede aprovação ao Gabriel).", "proibido")
            await self._em(self.p.abrir_arquivo, str(p))
            aberto = str(p)
        else:
            raise ErroAgente("mande url, app ou arquivo", "invalido")
        await asyncio.sleep(0.3)
        return {"ok": True, "aberto": aberto, **(await self.info())}

    async def _abrir_app(self, nome: str) -> str:
        if not nome or len(nome) > 60 or not re.fullmatch(r"[\w .+&()'-]+", nome):
            raise ErroAgente("nome de programa inválido", "invalido")
        tipo, alvo, busca = APPS.get(nome.casefold(), ("?", None, None))
        if tipo == "url":
            await self._em(self.p.abrir_url, alvo)
            return nome
        if tipo == "terminal":
            await self._em(self.p.abrir_terminal, str(self.casa))
            return "PowerShell"
        if tipo == "prog" and await self._em(self.p.abrir_programa, alvo):
            return busca or nome
        if tipo == "?" and re.fullmatch(r"[\w.-]+", nome) and await self._em(self.p.abrir_programa, nome):
            return nome
        await self.menu_iniciar(busca or nome)
        return f"{busca or nome} (procurei no menu Iniciar)"

    async def menu_iniciar(self, texto: str):
        await self._em(self.p.combo, ["win"])
        await asyncio.sleep(self.pausa_menu)
        await self._em(self.p.digitar, texto)
        await asyncio.sleep(self.pausa_busca)
        await self._em(self.p.combo, ["enter"])

    async def op_janelas(self) -> dict:
        lista = await self._em(self.p.listar_janelas)
        return {"ok": True, "janelas": lista[:60], "janela": await self._em(self.p.janela_ativa)}

    async def op_focar(self, titulo: str) -> dict:
        titulo = str(titulo or "").strip()
        if not titulo:
            raise ErroAgente("informe parte do título da janela", "invalido")
        achou = await self._em(self.p.focar_janela, titulo[:200])
        if not achou:
            raise ErroAgente(f"não achei janela com '{titulo[:80]}' no título", "nao_encontrado")
        await asyncio.sleep(0.2)
        return {"ok": True, "focada": achou, **(await self.info())}

    async def op_clipboard_ler(self) -> dict:
        return {"ok": True, "texto": truncar(await self._em(self.p.clipboard_ler) or "", 50000)}

    async def op_clipboard_escrever(self, texto: str) -> dict:
        texto = str(texto or "")
        if len(texto) > CLIP_MAX:
            raise ErroAgente("texto grande demais para a área de transferência", "invalido")
        await self._em(self.p.clipboard_escrever, texto)
        return {"ok": True}

    async def op_arquivos_listar(self, caminho: str = ".") -> dict:
        p = self.caminho_seguro(caminho, deve_existir=True)
        if not p.is_dir():
            raise ErroAgente("não é uma pasta", "invalido")
        itens = []
        for f in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
            try:
                st = f.stat()
            except OSError:
                continue
            itens.append({"nome": f.name, "pasta": f.is_dir(), "tamanho": st.st_size, "modificado": int(st.st_mtime)})
        return {"ok": True, "caminho": str(p), "itens": itens}

    async def op_arquivo_ler(self, caminho: str) -> dict:
        p = self.caminho_seguro(caminho, deve_existir=True)
        if not p.is_file():
            raise ErroAgente("não é um arquivo", "invalido")
        tam = p.stat().st_size
        if tam > ARQUIVO_MAX:
            raise ErroAgente(f"arquivo maior que {ARQUIVO_MAX // (1024 * 1024)} MB", "invalido")
        dados = await asyncio.to_thread(p.read_bytes)
        return {"ok": True, "nome": p.name, "tamanho": tam, "dados_b64": base64.b64encode(dados).decode()}

    async def op_arquivo_escrever(self, caminho: str, dados_b64: str, sobrescrever: bool = False) -> dict:
        p = self.caminho_seguro(caminho)
        if p == self.casa or p.is_dir():
            raise ErroAgente("informe o caminho do arquivo (pasta\\nome.ext)", "invalido")
        if p.exists() and not sobrescrever:
            raise ErroAgente("já existe um arquivo com esse nome (mande sobrescrever=true se for para trocar)",
                             "existe")
        try:
            dados = base64.b64decode(dados_b64 or "", validate=True)
        except (ValueError, TypeError):
            raise ErroAgente("arquivo corrompido (base64 inválido)", "invalido") from None
        if len(dados) > ARQUIVO_MAX:
            raise ErroAgente("arquivo grande demais", "invalido")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.parcial")
        await asyncio.to_thread(tmp.write_bytes, dados)
        os.replace(tmp, p)
        return {"ok": True, "caminho": str(p), "tamanho": len(dados)}

    async def parar(self) -> int:
        """Parada de emergência: mata comandos em andamento e solta teclas/botões."""
        n = 0
        for proc in list(self.processos):
            self._matar(proc)
            n += 1
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.p.soltar_tudo)
        except Exception:  # noqa: BLE001
            pass
        return n


def descrever(op: str, args: dict) -> str:
    """Linha curta para o registro local (sem guardar o texto digitado: pode ter dado pessoal)."""
    a = args or {}
    if op == "mouse":
        extra = f" -> ({a.get('x2')},{a.get('y2')})" if a.get("acao") == "arrastar" else ""
        rolar = f" dy={a.get('dy')} dx={a.get('dx')}" if a.get("acao") == "rolar" else ""
        return f"mouse {a.get('acao')} ({a.get('x')},{a.get('y')}){extra}{rolar}"
    if op == "teclado":
        partes = []
        if a.get("texto"):
            partes.append(f"digitou {len(str(a['texto']))} caractere(s)")
        if a.get("teclas"):
            partes.append(f"teclas {str(a['teclas'])[:80]}")
        return "teclado " + ", ".join(partes)
    if op == "comando":
        return f"comando ({a.get('timeout', 60)}s): {str(a.get('cmd'))[:500]}"
    if op == "abrir":
        return f"abrir {a.get('url') or a.get('app') or a.get('arquivo')}"
    if op in ("arquivo_ler", "arquivo_escrever", "arquivos_listar"):
        return f"{op} {a.get('caminho')}"
    if op == "clipboard_escrever":
        return f"área de transferência: escreveu {len(str(a.get('texto') or ''))} caractere(s)"
    if op == "focar":
        return f"focar janela '{str(a.get('titulo'))[:80]}'"
    if op == "tela":
        return "print da tela"
    return op
