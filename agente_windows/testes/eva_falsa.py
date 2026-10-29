"""Uma "Eva" de mentira (servidor WebSocket local) para testar o núcleo do agente sem o servidor de verdade."""
import asyncio
import json
import uuid

from websockets.asyncio.server import serve


class EvaFalsa:
    def __init__(self, token="evapc_token-de-teste"):
        self.token = token
        self.auths, self.olas, self.msgs, self.quadros = [], [], [], []
        self.ack = True
        self.recusar: int | None = None
        self.ws = None
        self.conexoes = 0
        self._resp: dict[str, asyncio.Future] = {}
        self.conectou = asyncio.Event()

    async def iniciar(self):
        self.server = await serve(self._handler, "127.0.0.1", 0, max_size=64 * 2 ** 20)
        self.porta = self.server.sockets[0].getsockname()[1]
        return self

    @property
    def endereco(self):
        return f"http://127.0.0.1:{self.porta}"

    async def fechar(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handler(self, ws):
        auth = ws.request.headers.get("Authorization")
        self.auths.append(auth)
        if self.recusar:
            await ws.close(self.recusar, "recusado")
            return
        if auth != f"Bearer {self.token}":
            await ws.close(4401, "token")
            return
        self.olas.append(json.loads(await ws.recv()))
        self.ws = ws
        self.conexoes += 1
        await ws.send(json.dumps({"tipo": "bem_vindo"}))
        self.conectou.set()
        try:
            async for m in ws:
                if isinstance(m, bytes):
                    self.quadros.append(m)
                    if self.ack:
                        await ws.send(json.dumps({"tipo": "frame_ok"}))
                    continue
                d = json.loads(m)
                if d.get("tipo") == "resp":
                    f = self._resp.pop(d["id"], None)
                    if f and not f.done():
                        f.set_result(d)
                elif d.get("tipo") == "ping":
                    await ws.send(json.dumps({"tipo": "pong"}))
                else:
                    self.msgs.append(d)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if self.ws is ws:
                self.ws = None
                self.conectou.clear()

    async def mandar(self, msg: dict):
        await self.ws.send(json.dumps(msg))

    async def pedir(self, op, args=None, timeout=10):
        pid = uuid.uuid4().hex[:8]
        f = asyncio.get_running_loop().create_future()
        self._resp[pid] = f
        await self.mandar({"tipo": "cmd", "id": pid, "op": op, "args": args or {}})
        return await asyncio.wait_for(f, timeout)


async def esperar(cond, timeout=5.0, passo=0.02):
    for _ in range(int(timeout / passo)):
        if cond():
            return True
        await asyncio.sleep(passo)
    return cond()
