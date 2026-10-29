# EVA-Agente (Windows)

Programa que roda no PC do Gabriel e deixa a E.V.A. usar o computador (tela, mouse, teclado), com segurança.
Guia do usuário: seção **“Conectar meu PC”** do README principal.

## Peças
| Arquivo | O que faz |
|---|---|
| `main.py` | entrada do `.exe` (PyInstaller) |
| `eva_agente/app.py` | janela de pareamento (Tk), ícone perto do relógio (pystray), aviso “Eva está usando o computador”, atalho Ctrl+Alt+Shift+P |
| `eva_agente/nucleo.py` | conexão WebSocket de saída com a Eva, comandos, pausa/emergência, failsafe do canto, tela ao vivo adaptativa, reconexão |
| `eva_agente/executor.py` | cada comando (mouse, teclado, abrir, comando, janelas, área de transferência, arquivos) + limites de segurança |
| `eva_agente/plataforma.py` | interface com o sistema + `PlataformaFalsa` (testes) |
| `eva_agente/windows.py` | Windows de verdade com ctypes: SendInput (unicode), DPI aware, mss, janelas, clipboard |
| `eva_agente/config.py` / `dpapi.py` | `%APPDATA%\EVA-Agente\config.json`, token cifrado com DPAPI, registro local, iniciar com o Windows |
| `eva_agente/pareamento.py` | troca o código de 8 caracteres pelo token do dispositivo |

## Protocolo (wss://EVA/ws/pc, cabeçalho `Authorization: Bearer <token>`)
- agente → Eva: `ola {nome, versao, tela, pausado}`, `resp {id, ok, dados | erro, codigo}`, `ping`,
  `estado {pausado, motivo, emergencia}`, `desconectar`; binário `b"F" + JPEG` (quadro ao vivo).
- Eva → agente: `bem_vindo`, `cmd {id, op, args}`, `pong`, `frame_ok`, `ao_vivo {ligar, fps}`, `parar {motivo}`,
  `liberar`, `controle {gabriel}`, `revogado`.
- Fechamentos: 4401 token inválido/revogado (volta para o pareamento), 4409 outro PC conectou, 4429 tentativas demais.
- ops: `info`, `tela`, `mouse`, `teclado`, `comando`, `abrir`, `janelas`, `focar`, `clipboard_ler`,
  `clipboard_escrever`, `arquivos_listar`, `arquivo_ler`, `arquivo_escrever`. Respostas no mesmo formato do agente
  da nuvem (`computador/agente/agente_pc.py`).

## Testar e empacotar
```bash
cd agente_windows
pip install -r requirements-dev.txt
python -m pytest -q            # roda no Linux também (tela falsa)
# no Windows:
python empacotar/gerar_versao.py
pyinstaller --noconfirm --onefile --windowed --name EVA-Agente --icon recursos/eva.ico ^
  --add-data "recursos/eva.ico;recursos" --version-file empacotar/versao_windows.txt ^
  --hidden-import pystray._win32 --collect-submodules websockets main.py
```
O GitHub Actions (`.github/workflows/agente-windows.yml`) faz isso sozinho e publica o Release na tag `agente-vX.Y.Z`.
