#!/bin/bash
# Ajusta a aparência padrão do XFCE (tema escuro, ícones, fontes) editando os arquivos que os pacotes
# instalam — sem substituí-los (o xfce4-session.xml, por exemplo, define a sessão de segurança e não
# pode ser trocado).
set -euo pipefail
X=/etc/xdg/xfce4/xfconf/xfce-perchannel-xml
sed -i \
  -e 's|name="ThemeName" type="string" value="[^"]*"|name="ThemeName" type="string" value="Greybird-dark"|' \
  -e 's|name="IconThemeName" type="string" value="[^"]*"|name="IconThemeName" type="string" value="elementary-xfce-dark"|' \
  -e 's|name="FontName" type="string" value="[^"]*"|name="FontName" type="string" value="Noto Sans 10"|' \
  -e 's|name="MonospaceFontName" type="string" value="[^"]*"|name="MonospaceFontName" type="string" value="DejaVu Sans Mono 10"|' \
  -e 's|name="Antialias" type="int" value="[^"]*"|name="Antialias" type="int" value="1"|' \
  -e 's|name="Hinting" type="int" value="[^"]*"|name="Hinting" type="int" value="1"|' \
  -e 's|name="HintStyle" type="string" value="[^"]*"|name="HintStyle" type="string" value="hintslight"|' \
  -e 's|name="RGBA" type="string" value="[^"]*"|name="RGBA" type="string" value="rgb"|' \
  "$X/xsettings.xml"
grep -q 'Greybird-dark' "$X/xsettings.xml"
grep -q 'Failsafe' "$X/xfce4-session.xml"
# painel padrão sem a pergunta "usar configuração padrão?" na primeira vez
cp /etc/xdg/xfce4/panel/default.xml "$X/xfce4-panel.xml"
echo "aparência OK"
