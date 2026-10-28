"""Liga a E.V.A.  ->  python main.py   e abra http://localhost:8000"""
import asyncio
import sys
import webbrowser

import uvicorn

from eva.config import HOST, PORT, VERSAO

if sys.platform == "win32":
    # necessário no Windows pro navegador (Playwright) e pro terminal funcionarem
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


async def main():
    from eva.server import app
    server = uvicorn.Server(uvicorn.Config(
        app, host=HOST, port=PORT, loop="none", log_level="warning",
        proxy_headers=True, forwarded_allow_ips="*",       # atrás do proxy do Render
        ws_ping_interval=20, ws_ping_timeout=30, timeout_keep_alive=30,
    ))
    print(f"\n  E.V.A. v{VERSAO} ligada ->  http://localhost:{PORT}\n  (Ctrl+C aqui no terminal desliga tudo)\n", flush=True)
    if "--sem-abrir" not in sys.argv and HOST == "127.0.0.1":
        webbrowser.open(f"http://localhost:{PORT}")
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
