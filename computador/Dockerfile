# syntax=docker/dockerfile:1
# O Computador da Eva: área de trabalho Linux (XFCE) + VNC + noVNC + agente do PC.
# Funciona em ARM64 (Oracle Ampere A1) e AMD64.
#
# Por que Debian (e não Ubuntu) aqui dentro: no Ubuntu o Chromium só existe como "snap", que não funciona
# dentro de container. O Debian tem Chromium, LibreOffice, XFCE e TigerVNC como pacotes normais (.deb)
# para arm64 e amd64. A máquina (host) continua sendo Ubuntu — só o container usa Debian.
FROM debian:trixie-slim

ARG NOVNC_VERSAO=1.5.0
ARG NOVNC_SHA256=6a73e41f98388a5348b7902f54b02d177cb73b7e5eb0a7a0dcf688cc2c79b42a

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Sao_Paulo \
    LANG=pt_BR.UTF-8 \
    LANGUAGE=pt_BR:pt:en \
    LC_ALL=pt_BR.UTF-8 \
    DISPLAY=:1 \
    EVA_PC_RESOLUCAO=1280x800 \
    EVA_PC_TECLADO=br \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 1) sistema, área de trabalho, VNC, programas e fontes
#    (a imagem "slim" apaga as traduções; liberamos as de português para o XFCE/LibreOffice ficarem em PT-BR)
RUN printf 'path-include /usr/share/locale/pt_BR/*\npath-include /usr/share/locale/pt/*\n' > /etc/dpkg/dpkg.cfg.d/eva-locale \
 && apt-get update && apt-get install -y --no-install-recommends \
      locales tzdata ca-certificates curl sudo procps tini supervisor dbus-x11 at-spi2-core \
      tigervnc-standalone-server tigervnc-tools websockify \
      xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfconf xfce4-whiskermenu-plugin \
      thunar thunar-archive-plugin xarchiver xfce4-terminal mousepad ristretto \
      greybird-gtk-theme elementary-xfce-icon-theme adwaita-icon-theme \
      x11-utils x11-xserver-utils x11-xkb-utils xdotool xclip scrot imagemagick xdg-utils \
      chromium chromium-l10n \
      libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-gtk3 \
      libreoffice-l10n-pt-br hunspell-pt-br \
      fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-color-emoji \
      python3 python3-venv \
 && sed -i 's/^# *\(pt_BR.UTF-8\)/\1/' /etc/locale.gen && locale-gen \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/*

# 2) noVNC (versão fixa, conferida por hash)
RUN curl -fsSL -o /tmp/novnc.tgz "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSAO}.tar.gz" \
 && echo "${NOVNC_SHA256}  /tmp/novnc.tgz" | sha256sum -c - \
 && mkdir -p /opt/novnc && tar xzf /tmp/novnc.tgz -C /opt/novnc --strip-components=1 \
 && rm -rf /tmp/novnc.tgz /opt/novnc/tests /opt/novnc/docs /opt/novnc/snap /opt/novnc/utils
COPY config/novnc-index.html /opt/novnc/index.html

# 3) agente do PC (Python num ambiente próprio)
COPY agente/requirements.txt /opt/agente/requirements.txt
RUN python3 -m venv /opt/agente/venv \
 && /opt/agente/venv/bin/pip install --no-cache-dir -r /opt/agente/requirements.txt
COPY agente/agente_pc.py /opt/agente/agente_pc.py

# 4) usuário da área de trabalho + aparência (tema escuro, PT-BR, papel de parede)
RUN useradd -m -s /bin/bash -u 1000 eva \
 && mkdir -p /etc/xdg/xfce4/xfconf/xfce-perchannel-xml /usr/share/backgrounds /etc/chromium/policies/managed \
 && convert -size 1920x1200 radial-gradient:'#1e2436'-'#0b0d12' \
      -font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf -gravity center \
      -fill '#8b7cff' -pointsize 72 -annotate +0-30 'E.V.A.' \
      -font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf -fill '#8b94a8' -pointsize 24 -annotate +0+40 'o computador da Eva' \
      /usr/share/backgrounds/eva.png
COPY config/xfce/*.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/
COPY --chmod=755 config/aparencia.sh /tmp/aparencia.sh
RUN /tmp/aparencia.sh && rm /tmp/aparencia.sh
COPY config/helpers.rc /etc/xdg/xfce4/helpers.rc
COPY config/mimeapps.list /etc/xdg/mimeapps.list
COPY config/chromium-flags /etc/chromium.d/eva
COPY config/chromium-politicas.json /etc/chromium/policies/managed/eva.json
COPY config/supervisord.conf /etc/supervisor/supervisord.conf
COPY --chmod=755 config/entrypoint.sh /usr/local/bin/eva-entrypoint
COPY --chmod=755 config/sessao.sh /usr/local/bin/eva-sessao
RUN cp -r /home/eva/. /etc/skel/ 2>/dev/null || true \
 && python3 -c "import ast,sys; ast.parse(open('/opt/agente/agente_pc.py').read())" \
 && /opt/agente/venv/bin/python -c "import fastapi, uvicorn, PIL; print('agente OK')"

EXPOSE 6080 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/saude || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/eva-entrypoint"]
