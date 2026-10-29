#!/bin/bash
# Sessão XFCE do usuário "eva" (chamada pelo supervisor depois que a tela virtual sobe).
unset EVA_PC_TOKEN EVA_PC_VNC_SENHA   # segredos não ficam visíveis para os programas da área de trabalho
export DISPLAY=:1 HOME=/home/eva USER=eva LOGNAME=eva SHELL=/bin/bash
export XDG_RUNTIME_DIR=/tmp/runtime-eva XDG_CURRENT_DESKTOP=XFCE XDG_SESSION_DESKTOP=xfce
export NO_AT_BRIDGE=1
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

for _ in $(seq 1 100); do xdpyinfo -display :1 >/dev/null 2>&1 && break; sleep 0.2; done

setxkbmap -layout "${EVA_PC_TECLADO:-br}" ${EVA_PC_TECLADO_VARIANTE:+-variant "$EVA_PC_TECLADO_VARIANTE"} 2>/dev/null || true
xset s off -dpms s noblank 2>/dev/null || true

# papel de parede em todos os monitores virtuais (o nome do monitor muda conforme o servidor X)
(
  sleep 6
  for m in $(xrandr --listmonitors 2>/dev/null | awk 'NR>1{print $NF}') VNC-0 0 screen; do
    p="/backdrop/screen0/monitor${m}/workspace0"
    xfconf-query -c xfce4-desktop -p "$p/last-image" -n -t string -s /usr/share/backgrounds/eva.png 2>/dev/null
    xfconf-query -c xfce4-desktop -p "$p/image-style" -n -t int -s 5 2>/dev/null
  done
) &

exec dbus-launch --exit-with-session startxfce4
