"""Cliente do "Computador da Eva": o computador que a Eva controla olhando a tela (print + mouse + teclado).

Dois "transportes" (backends), mesma interface para as ferramentas computador_*:
- "windows": o PC Windows do PRÓPRIO Gabriel, com o programa EVA-Agente (agente_windows/), que se conecta à Eva por
  WebSocket de saída (ver pc_hub.py). Tem prioridade quando está conectado.
- "nuvem": o desktop Linux na nuvem (computador/), via HTTP(S) no agente_pc.py com o token EVA_PC_TOKEN.
- Coordenadas da Eva: grade normalizada 0–1000 em cada eixo (como o computer-use do Gemini),
  convertida aqui para pixels reais da tela (que pode ter qualquer resolução).
- Guarda o último print (para a aba Tela) e o estado (online, quem está no controle, ação atual).
- Monta a mensagem com o print para o modelo e poda os prints antigos da conversa (economiza tokens).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import weakref
from urllib.parse import quote, urlparse

from . import config
from .killswitch import kill_switch
from .pc_hub import ComputadorErro, decodificar_imagem, hub

log = logging.getLogger("eva.computador")

GRADE = 1000
MARCA_TELA = "[tela do computador]"   # começo do texto das mensagens com print (usado na poda)
MARCA_PODADA = "[print antigo do computador removido para economizar; veja o print mais recente]"


__all__ = ["Computador", "ComputadorErro", "GRADE", "MARCA_PODADA", "MARCA_TELA", "eh_mensagem_tela", "mensagem_tela",
           "pc", "podar_telas"]


class Computador:
    def __init__(self):
        self.configurar()

    def configurar(self, url: str | None = None, token: str | None = None, vnc_url: str | None = None,
                   vnc_senha: str | None = None, timeout: float | None = None):
        self.url = (config.PC_URL if url is None else url).strip().rstrip("/")
        self.token = (config.PC_TOKEN if token is None else token).strip()
        self.vnc_base = (config.PC_VNC_URL if vnc_url is None else vnc_url).strip()
        self.vnc_senha = (config.PC_VNC_SENHA if vnc_senha is None else vnc_senha).strip()
        self.timeout = timeout or config.PC_TIMEOUT
        self.largura, self.altura = 1280, 800
        self.tela: bytes | None = None
        self.tela_versao = 0
        self.tela_ts = 0.0
        self.online: bool | None = None
        self._checado = 0.0
        self.ultimo_erro: str | None = None
        self.controle_gabriel = False
        self.acao_atual: str | None = None
        self._clientes: dict[int, tuple] = {}
        self._checando: asyncio.Task | None = None

    # ---------------------------------------------------------------- configuração
    @property
    def http_configurado(self) -> bool:
        """Desktop na nuvem configurado (EVA_PC_URL + EVA_PC_TOKEN)."""
        return bool(self.url and self.token)

    @property
    def configurado(self) -> bool:
        """Existe ALGUM computador: o desktop na nuvem ou um PC Windows pareado."""
        return self.http_configurado or hub.conectado or hub.tem_dispositivo

    def backend(self) -> str | None:
        """'windows' (PC do Gabriel conectado, ou pareado sem nuvem), 'nuvem' ou None."""
        if hub.conectado:
            return "windows"
        if self.http_configurado:
            return "nuvem"
        if hub.tem_dispositivo:
            return "windows"
        return None

    def _windows(self) -> bool:
        return self.backend() == "windows"

    def vnc_url(self) -> str | None:
        """Base da tela ao vivo (noVNC). Deduzida do EVA_PC_URL quando ele termina em /pc."""
        if not self.http_configurado:
            return None
        if self.vnc_base:
            return self.vnc_base.rstrip("/") + "/"
        if self.url.endswith("/pc"):
            return self.url[: -len("/pc")] + "/vnc/"
        return None

    def vnc_link(self, controle: bool = False) -> str | None:
        """Link pronto para o iframe: conecta sozinho, ajusta à tela e (por padrão) só assiste.
        A senha vai no fragmento (#), que o navegador não manda ao servidor."""
        base = self.vnc_url()
        if not base or not self.vnc_senha:
            return None
        prefixo = urlparse(base).path.strip("/")
        caminho = f"{prefixo}/ws/{self.vnc_senha}" if prefixo else f"ws/{self.vnc_senha}"
        consulta = (f"autoconnect=true&reconnect=true&reconnect_delay=2000&resize=scale&show_dot=true"
                    f"&view_only={'false' if controle else 'true'}&_={int(time.time() * 1000)}")
        fragmento = f"password={quote(self.vnc_senha, safe='')}&path={quote(caminho, safe='')}"
        return f"{base}vnc.html?{consulta}#{fragmento}"

    # ---------------------------------------------------------------- coordenadas
    @staticmethod
    def _num(v, nome: str) -> float:
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ComputadorErro(f"ERRO: '{nome}' precisa ser um número de 0 a {GRADE} (veio {v!r}).") from None
        if n != n:  # NaN
            raise ComputadorErro(f"ERRO: '{nome}' inválido.")
        return n

    def _sincronizar_tamanho(self):
        """No PC Windows o tamanho real da tela vem do agente (no 'ola' e em cada resposta)."""
        if hub.conectado and hub.largura and hub.altura:
            self.largura, self.altura = hub.largura, hub.altura

    def para_pixels(self, x, y) -> tuple[int, int]:
        """Grade 0–1000 -> pixel real. 0 = borda esquerda/superior, 1000 = última coluna/linha."""
        self._sincronizar_tamanho()
        gx = min(GRADE, max(0.0, self._num(x, "x")))
        gy = min(GRADE, max(0.0, self._num(y, "y")))
        return (round(gx / GRADE * (self.largura - 1)), round(gy / GRADE * (self.altura - 1)))

    def para_grade(self, px: int, py: int) -> tuple[int, int]:
        self._sincronizar_tamanho()
        return (round(px / max(1, self.largura - 1) * GRADE), round(py / max(1, self.altura - 1) * GRADE))

    def _atualizar_tamanho(self, dados: dict | None):
        tela = (dados or {}).get("tela") or {}
        try:
            w, h = int(tela.get("largura") or 0), int(tela.get("altura") or 0)
        except (TypeError, ValueError):
            return
        if w > 0 and h > 0:
            self.largura, self.altura = w, h

    # ---------------------------------------------------------------- HTTP
    def _cliente(self):
        import httpx
        loop = asyncio.get_running_loop()
        item = self._clientes.get(id(loop))
        if item is None or item[0]() is not loop:
            if len(self._clientes) > 8:
                self._clientes.clear()
            c = httpx.AsyncClient(base_url=self.url, timeout=self.timeout, follow_redirects=False,
                                  headers={"Authorization": f"Bearer {self.token}", "User-Agent": "EVA/1.1"})
            item = (weakref.ref(loop), c)
            self._clientes[id(loop)] = item
        return item[1]

    async def req(self, metodo: str, caminho: str, *, json: dict | None = None, params: dict | None = None,
                  timeout: float | None = None, conteudo: bytes | None = None):
        import httpx
        if not self.http_configurado:
            raise ComputadorErro("ERRO: o computador da Eva não está configurado (conecte o teu PC na aba Tela, "
                                 "ou configure EVA_PC_URL e EVA_PC_TOKEN).")
        if kill_switch.off:
            raise ComputadorErro("ERRO: a Eva está desligada (botão de desligar). Nenhuma ação no computador.")
        try:
            r = await self._cliente().request(metodo, caminho, json=json, params=params, content=conteudo,
                                              timeout=timeout or self.timeout)
        except httpx.TimeoutException:
            self._marcar(False, "tempo esgotado")
            raise ComputadorErro("ERRO: o computador demorou demais para responder. Tente de novo ou veja a tela.") from None
        except httpx.HTTPError as e:
            self._marcar(False, f"{type(e).__name__}")
            raise ComputadorErro("ERRO: não consegui falar com o computador da Eva (ele está ligado? a internet da "
                                 "máquina está ok?).") from None
        self._marcar(True, None)
        if r.status_code == 401:
            self.ultimo_erro = "token recusado"
            raise ComputadorErro("ERRO: o computador recusou o token (confira EVA_PC_TOKEN).")
        if r.status_code == 429:
            raise ComputadorErro("ERRO: ações demais seguidas no computador; espere um pouco e tente de novo.")
        if r.status_code >= 400:
            try:
                det = r.json().get("detail")
            except Exception:  # noqa: BLE001
                det = r.text[:300]
            if isinstance(det, list):  # erro de validação do FastAPI
                det = "; ".join(str(d.get("msg", d)) for d in det)[:300]
            raise ComputadorErro(f"ERRO do computador ({r.status_code}): {det}")
        return r

    def _marcar(self, online: bool, erro: str | None):
        self.online = online
        self._checado = time.monotonic()
        self.ultimo_erro = erro

    async def disponivel(self, forcar: bool = False) -> bool:
        """Está configurado e respondendo? (resultado guardado por 30 s; offline por 15 s)"""
        if self._windows():
            self._marcar(hub.conectado, None if hub.conectado else "PC desconectado")
            if hub.largura and hub.altura:
                self.largura, self.altura = hub.largura, hub.altura
            return hub.conectado
        if not self.http_configurado:
            return False
        validade = 30.0 if self.online else 15.0
        if not forcar and self.online is not None and time.monotonic() - self._checado < validade:
            return bool(self.online)
        import httpx
        try:
            r = await self._cliente().get("/saude", timeout=4.0)
            dados = r.json() if r.status_code == 200 else {}
            self._marcar(bool(dados.get("ok")), None if dados.get("ok") else f"saúde {r.status_code}")
            self._atualizar_tamanho(dados)
        except (httpx.HTTPError, ValueError) as e:
            self._marcar(False, type(e).__name__)
        return bool(self.online)

    def checar_em_fundo(self):
        """Atualiza o status sem travar quem chamou (usado pelo /api/estado)."""
        if not self.http_configurado or self._windows():
            return
        if self._checando and not self._checando.done():
            return
        validade = 30.0 if self.online else 15.0
        if self.online is not None and time.monotonic() - self._checado < validade:
            return
        try:
            self._checando = asyncio.get_running_loop().create_task(self.disponivel(forcar=True))
        except RuntimeError:
            pass

    # ---------------------------------------------------------------- ações
    async def _acao(self, op: str, corpo: dict, timeout: float | None = None) -> dict:
        """POST /op no desktop da nuvem, ou o comando 'op' pelo WebSocket do PC Windows."""
        if self._windows():
            d = await hub.pedir(op, corpo, timeout=timeout or self.timeout)
        else:
            d = (await self.req("POST", f"/{op}", json=corpo, timeout=timeout)).json()
        self._atualizar_tamanho(d)
        return d

    async def info(self, timeout: float | None = None) -> dict:
        if self._windows():
            d = await hub.pedir("info", {}, timeout=timeout or self.timeout)
        else:
            d = (await self.req("GET", "/info", timeout=timeout)).json()
        self._atualizar_tamanho(d)
        return d

    async def janela_ativa(self) -> str:
        try:
            return str((await self.info(timeout=4.0)).get("janela") or "")
        except Exception:  # noqa: BLE001
            return ""

    async def capturar(self, largura: int | None = None, qualidade: int | None = None) -> bytes:
        largura = largura or config.PC_TELA_LARGURA
        qualidade = qualidade or config.PC_TELA_QUALIDADE
        if self._windows():
            d = await hub.pedir("tela", {"largura": largura, "qualidade": qualidade}, timeout=max(self.timeout, 20))
            img = decodificar_imagem(d)
            self._atualizar_tamanho(d)
            self.tela = img
            self.tela_versao += 1
            self.tela_ts = time.time()
            return img
        r = await self.req("GET", "/tela", params={"formato": "jpeg", "largura": largura, "qualidade": qualidade},
                           timeout=max(self.timeout, 20))
        try:
            w, h = int(r.headers.get("x-tela-largura", 0)), int(r.headers.get("x-tela-altura", 0))
            if w > 0 and h > 0:
                self.largura, self.altura = w, h
        except ValueError:
            pass
        self.tela = r.content
        self.tela_versao += 1
        self.tela_ts = time.time()
        return self.tela

    async def mouse(self, acao: str, **kw) -> dict:
        return await self._acao("mouse", {"acao": acao, **{k: v for k, v in kw.items() if v is not None}})

    async def teclado(self, texto: str | None = None, teclas: str | None = None) -> dict:
        corpo = {}
        if texto:
            corpo["texto"] = texto
        if teclas:
            corpo["teclas"] = teclas
        tempo = self.timeout + (len(texto or "") * 0.03)
        return await self._acao("teclado", corpo, timeout=tempo)

    async def comando(self, cmd: str, timeout: int = 60) -> dict:
        return await self._acao("comando", {"cmd": cmd, "timeout": timeout}, timeout=timeout + 15)

    async def abrir(self, url: str | None = None, app: str | None = None, arquivo: str | None = None) -> dict:
        corpo = {k: v for k, v in {"url": url, "app": app, "arquivo": arquivo}.items() if v}
        return await self._acao("abrir", corpo, timeout=max(self.timeout, 30))

    # ---------- extras (janelas, área de transferência, arquivos) ----------
    async def janelas(self) -> dict:
        if not self._windows():
            raise ComputadorErro("ERRO: listar/trocar de janela por nome só funciona no PC Windows. Use alt+tab.")
        return await hub.pedir("janelas", {}, timeout=self.timeout)

    async def focar(self, titulo: str) -> dict:
        if not self._windows():
            raise ComputadorErro("ERRO: trocar de janela por nome só funciona no PC Windows. Use alt+tab.")
        return await self._acao("focar", {"titulo": titulo})

    async def clipboard(self, texto: str | None = None) -> dict:
        """Sem texto: lê a área de transferência. Com texto: escreve."""
        if self._windows():
            if texto is None:
                return await hub.pedir("clipboard_ler", {}, timeout=self.timeout)
            return await hub.pedir("clipboard_escrever", {"texto": texto}, timeout=self.timeout)
        if texto is None:
            return (await self.req("GET", "/clipboard")).json()
        return (await self.req("POST", "/clipboard", json={"texto": texto})).json()

    async def arquivo_ler(self, caminho: str) -> tuple[str, bytes]:
        """Baixa um arquivo do computador (só dentro da pasta do usuário). Devolve (nome, bytes)."""
        if self._windows():
            d = await hub.pedir("arquivo_ler", {"caminho": caminho}, timeout=120)
            try:
                return str(d.get("nome") or "arquivo"), base64.b64decode(d.get("dados_b64") or "")
            except (ValueError, TypeError):
                raise ComputadorErro("ERRO: o arquivo chegou corrompido.") from None
        r = await self.req("GET", "/arquivo", params={"caminho": caminho}, timeout=120)
        nome = caminho.replace("\\", "/").rsplit("/", 1)[-1] or "arquivo"
        return nome, r.content

    async def arquivo_escrever(self, caminho: str, dados: bytes, sobrescrever: bool = False) -> dict:
        if self._windows():
            return await hub.pedir("arquivo_escrever", {"caminho": caminho, "sobrescrever": bool(sobrescrever),
                                                        "dados_b64": base64.b64encode(dados).decode()}, timeout=120)
        return (await self.req("POST", "/arquivo", params={"caminho": caminho}, conteudo=dados, timeout=120)).json()

    async def parar(self) -> bool:
        """Botão de desligar: para comandos em andamento e solta mouse/teclas (ignora o killswitch).
        Avisa os dois computadores (o PC Windows também se pausa até a Eva ser ligada de novo)."""
        ok = False
        if hub.conn is not None:
            ok = await hub.parar("A Eva foi desligada no app.") or ok
        if self.http_configurado:
            try:
                r = await self._cliente().post("/parar", timeout=4.0)
                ok = r.status_code == 200 or ok
            except Exception:  # noqa: BLE001
                pass
        return ok

    # ---------------------------------------------------------------- estado
    def estado(self) -> dict:
        backend = self.backend()
        e = {
            "configurado": self.configurado,
            "backend": backend,
            "online": bool(self.online) if self.configurado else False,
            "verificado": self.online is not None,
            "controle_gabriel": self.controle_gabriel,
            "largura": self.largura,
            "altura": self.altura,
            "acao_atual": self.acao_atual,
            "tela_versao": self.tela_versao,
            "ao_vivo": bool(self.vnc_url() and self.vnc_senha),
            "erro": self.ultimo_erro,
            "windows": hub.estado(),
        }
        if backend == "windows":
            e.update(online=hub.conectado, verificado=True, ao_vivo=hub.conectado,
                     erro=None if hub.conectado else "PC desconectado")
            if hub.largura and hub.altura:
                e.update(largura=hub.largura, altura=hub.altura)
        return e


pc = Computador()


# ---------------------------------------------------------------- prints na conversa com o modelo

def mensagem_tela(img: bytes, largura: int, altura: int, extra: str = "") -> dict:
    """Mensagem de 'usuário' com o print (as mensagens de ferramenta da API compatível com OpenAI não
    carregam imagem, então o print vai logo depois dos resultados das ferramentas)."""
    b64 = base64.b64encode(img).decode()
    texto = (f"{MARCA_TELA} Print atual da tela ({largura}x{altura} px). Coordenadas na grade 0–1000: "
             f"(0,0) = canto superior esquerdo, (1000,1000) = canto inferior direito. {extra}").strip()
    return {"role": "user", "content": [
        {"type": "text", "text": texto},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}


def eh_mensagem_tela(m: dict) -> bool:
    if m.get("role") != "user" or not isinstance(m.get("content"), list):
        return False
    partes = m["content"]
    return any(p.get("type") == "image_url" for p in partes if isinstance(p, dict)) and any(
        isinstance(p, dict) and p.get("type") == "text" and str(p.get("text", "")).startswith(MARCA_TELA)
        for p in partes)


def podar_telas(mensagens: list[dict], manter: int) -> int:
    """Deixa só os `manter` prints mais recentes; os mais antigos viram uma linha de texto.
    Altera a lista no lugar (as mensagens do assistente — e as thought_signatures — não são tocadas)."""
    idx = [i for i, m in enumerate(mensagens) if eh_mensagem_tela(m)]
    remover = idx[: max(0, len(idx) - max(0, manter))]
    for i in remover:
        mensagens[i] = {"role": "user", "content": MARCA_PODADA}
    return len(remover)
