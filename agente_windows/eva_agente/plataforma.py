"""Interface com o sistema (tela, mouse, teclado, janelas, área de transferência, abrir coisas).

- PlataformaWindows (windows.py) faz isso de verdade no Windows 10/11, com ctypes (sem pywin32/pyautogui).
- PlataformaFalsa (aqui) imita uma tela e grava tudo que pediram: é o que os testes usam no Linux.

Coordenadas sempre em PIXELS REAIS do monitor principal ((0,0) = canto superior esquerdo dele), as mesmas do print.
Ações de alto nível (duplo clique, arrastar, procurar no menu Iniciar) ficam no executor, em cima destas primitivas.
"""
from __future__ import annotations

import threading


class ErroPlataforma(Exception):
    """Falha do sistema com mensagem em PT-BR."""


class Plataforma:
    nome = "base"

    def preparar(self):
        """Chamado uma vez no começo (no Windows: liga o modo 'DPI aware' para as coordenadas baterem)."""

    def tamanho_tela(self) -> tuple[int, int]:
        raise NotImplementedError

    def capturar(self):
        """Print do monitor principal em pixels reais (PIL.Image RGB)."""
        raise NotImplementedError

    def posicao_mouse(self) -> tuple[int, int]:
        raise NotImplementedError

    def mover(self, x: int, y: int):
        raise NotImplementedError

    def botao(self, botao: str, acao: str):
        """botao: esquerdo|direito|meio; acao: descer|subir|clicar."""
        raise NotImplementedError

    def rolar(self, dy: int = 0, dx: int = 0):
        """Cliques da rodinha: dy > 0 = para baixo; dx > 0 = para a direita."""
        raise NotImplementedError

    def digitar(self, texto: str):
        """Digita texto unicode (acentos, emoji) onde está o foco."""
        raise NotImplementedError

    def combo(self, teclas: list[str]):
        """Aperta uma combinação (nomes canônicos de teclas.py), ex.: ['ctrl', 'l'] ou ['win']."""
        raise NotImplementedError

    def soltar_tudo(self):
        """Solta modificadores e botões do mouse (depois de uma parada de emergência)."""

    def janela_ativa(self) -> str:
        return ""

    def listar_janelas(self) -> list[dict]:
        return []

    def focar_janela(self, titulo: str) -> str | None:
        return None

    def clipboard_ler(self) -> str:
        return ""

    def clipboard_escrever(self, texto: str):
        raise NotImplementedError

    def abrir_url(self, url: str):
        raise NotImplementedError

    def abrir_arquivo(self, caminho: str):
        raise NotImplementedError

    def abrir_programa(self, programa: str) -> bool:
        """Tenta abrir direto (ex.: 'notepad', 'winword'). Falso se não achou (aí o executor usa o menu Iniciar)."""
        return False

    def abrir_terminal(self, pasta: str):
        raise NotImplementedError

    def comando_shell(self, cmd: str, tipo: str = "powershell") -> list[str]:
        """Linha de comando para rodar 'cmd' (o executor roda e mede o tempo)."""
        return ["bash", "-lc", cmd]


class PlataformaFalsa(Plataforma):
    """Tela de mentira para testes: grava cada chamada em self.chamadas."""
    nome = "falsa"

    def __init__(self, largura: int = 1920, altura: int = 1080):
        self.largura, self.altura = largura, altura
        self.mouse = (largura // 2, altura // 2)
        self.chamadas: list[tuple] = []
        self.janela = "Área de trabalho"
        self.janelas = [{"titulo": "Documento1 - Word", "ativa": False, "processo": "WINWORD.EXE"},
                        {"titulo": "Área de trabalho", "ativa": True, "processo": "explorer.exe"}]
        self.clip = ""
        self.programas = {"notepad", "calc", "explorer", "winword", "mspaint"}
        self.falhar_captura = False
        self.cor = (30, 60, 90)
        self.capturas = 0
        self.preparado = False
        self._trava = threading.Lock()

    def _gravar(self, *c):
        with self._trava:
            self.chamadas.append(c)

    def preparar(self):
        self.preparado = True

    def tamanho_tela(self):
        return self.largura, self.altura

    def capturar(self):
        from PIL import Image
        if self.falhar_captura:
            raise ErroPlataforma("tela protegida (UAC ou tela de bloqueio)")
        self.capturas += 1
        return Image.new("RGB", (self.largura, self.altura), self.cor)

    def posicao_mouse(self):
        return self.mouse

    def mover(self, x, y):
        self.mouse = (int(x), int(y))
        self._gravar("mover", int(x), int(y))

    def botao(self, botao, acao):
        self._gravar("botao", botao, acao)

    def rolar(self, dy=0, dx=0):
        self._gravar("rolar", dy, dx)

    def digitar(self, texto):
        self._gravar("digitar", texto)

    def combo(self, teclas):
        self._gravar("combo", tuple(teclas))

    def soltar_tudo(self):
        self._gravar("soltar_tudo")

    def janela_ativa(self):
        return self.janela

    def listar_janelas(self):
        return [dict(j) for j in self.janelas]

    def focar_janela(self, titulo):
        for j in self.janelas:
            if titulo.casefold() in j["titulo"].casefold():
                for k in self.janelas:
                    k["ativa"] = k is j
                self.janela = j["titulo"]
                self._gravar("focar", j["titulo"])
                return j["titulo"]
        return None

    def clipboard_ler(self):
        return self.clip

    def clipboard_escrever(self, texto):
        self.clip = texto
        self._gravar("clipboard", len(texto))

    def abrir_url(self, url):
        self._gravar("abrir_url", url)

    def abrir_arquivo(self, caminho):
        self._gravar("abrir_arquivo", caminho)

    def abrir_programa(self, programa):
        if programa in self.programas:
            self._gravar("abrir_programa", programa)
            return True
        return False

    def abrir_terminal(self, pasta):
        self._gravar("abrir_terminal", pasta)

    def comando_shell(self, cmd, tipo="powershell"):
        import sys
        return ["cmd.exe", "/d", "/c", cmd] if sys.platform == "win32" else ["bash", "-c", cmd]


def plataforma_do_sistema() -> Plataforma:
    import sys
    if sys.platform == "win32":
        from .windows import PlataformaWindows
        return PlataformaWindows()
    return PlataformaFalsa()
