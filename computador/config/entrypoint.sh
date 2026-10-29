#!/bin/bash
# Liga o computador da Eva: prepara senha do VNC, pasta pessoal e sobe tudo pelo supervisor.
set -euo pipefail

if [ -z "${EVA_PC_TOKEN:-}" ] || [ "${#EVA_PC_TOKEN}" -lt 24 ]; then
  echo "ERRO: EVA_PC_TOKEN ausente ou curto (mínimo 24 caracteres). Rode o instalar.sh de novo." >&2
  exit 1
fi
if [ -z "${EVA_PC_VNC_SENHA:-}" ] || [ "${#EVA_PC_VNC_SENHA}" -lt 8 ]; then
  echo "ERRO: EVA_PC_VNC_SENHA ausente ou curta (mínimo 8 caracteres)." >&2
  exit 1
fi

RES="${EVA_PC_RESOLUCAO:-1280x800}"
if ! [[ "$RES" =~ ^[0-9]{3,4}x[0-9]{3,4}$ ]]; then
  echo "AVISO: EVA_PC_RESOLUCAO inválida ($RES); usando 1280x800." >&2
  RES="1280x800"
fi
export EVA_PC_RESOLUCAO="$RES"

# pasta pessoal (volume persistente): na 1ª vez vem do /etc/skel
CASA=/home/eva
mkdir -p "$CASA"
if [ ! -f "$CASA/.eva-iniciada" ]; then
  cp -rn /etc/skel/. "$CASA/" 2>/dev/null || true
  touch "$CASA/.eva-iniciada"
fi
mkdir -p "$CASA/.vnc" "$CASA/Downloads" "$CASA/Documentos" "$CASA/.cache"
chown eva:eva "$CASA" "$CASA/.vnc" "$CASA/Downloads" "$CASA/Documentos" "$CASA/.cache" "$CASA/.eva-iniciada"

# senha do VNC (o protocolo VNC só usa os 8 primeiros caracteres; o Caddy protege a conexão com a senha inteira)
printf '%s\n' "$EVA_PC_VNC_SENHA" | vncpasswd -f > "$CASA/.vnc/passwd"
chown eva:eva "$CASA/.vnc/passwd"
chmod 600 "$CASA/.vnc/passwd"

# sudo para o usuário da área de trabalho (opcional; padrão: não)
case "${EVA_PC_SUDO:-nao}" in
  sim|SIM|1|true|yes) echo "eva ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/eva; chmod 440 /etc/sudoers.d/eva ;;
  *) rm -f /etc/sudoers.d/eva ;;
esac

# restos de um desligamento brusco
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
mkdir -p /tmp/.X11-unix /tmp/.ICE-unix && chmod 1777 /tmp/.X11-unix /tmp/.ICE-unix

exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
