#!/usr/bin/env bash
# =====================================================================================
#  Instalador do COMPUTADOR DA EVA
#  Ubuntu 22.04 / 24.04 (ARM64 ou x86_64) — feito para a Oracle Cloud "Always Free".
#
#  Uso (numa máquina nova, conectado por SSH):
#     curl -fsSL https://raw.githubusercontent.com/Bandock-fagundes/Eva/main/computador/instalar.sh | sudo bash
#
#  Com a Eva rodando 24h nesta mesma máquina:
#     curl -fsSL https://raw.githubusercontent.com/Bandock-fagundes/Eva/main/computador/instalar.sh | sudo bash -s -- --com-eva
#
#  Rodar de novo é seguro: atualiza o código, mantém as senhas e religa tudo.
#  Opções: --com-eva | --sem-eva | --ip 1.2.3.4 | --mostrar (só mostra as senhas de novo) | --ajuda
# =====================================================================================
set -Eeuo pipefail

REPO_URL="${EVA_REPO:-https://github.com/Bandock-fagundes/Eva}"
RAMO="${EVA_RAMO:-}"                 # vazio = ramo padrão do repositório
PASTA="${EVA_PASTA:-/opt/eva}"
IP_FORCADO="${EVA_IP:-}"
COM_EVA_ARG=""
SO_MOSTRAR=0
LOG_BUILD="/var/log/eva-computador-build.log"

VERDE=$'\e[32m'; AMARELO=$'\e[33m'; VERMELHO=$'\e[31m'; ROXO=$'\e[35m'; NEGRITO=$'\e[1m'; FIM=$'\e[0m'
passo() { echo; echo "${ROXO}${NEGRITO}==> $*${FIM}"; }
ok()    { echo "${VERDE}✔ $*${FIM}"; }
aviso() { echo "${AMARELO}⚠ $*${FIM}"; }
erro()  { echo "${VERMELHO}✖ $*${FIM}" >&2; exit 1; }
trap 'echo "${VERMELHO}✖ Algo deu errado na linha $LINENO. Rode o mesmo comando de novo; se repetir, mande esta tela pra quem está te ajudando.${FIM}" >&2' ERR

while [ $# -gt 0 ]; do
  case "$1" in
    --com-eva) COM_EVA_ARG="sim" ;;
    --sem-eva) COM_EVA_ARG="nao" ;;
    --ip) IP_FORCADO="${2:-}"; shift ;;
    --ramo) RAMO="${2:-}"; shift ;;
    --mostrar) SO_MOSTRAR=1 ;;
    -h|--ajuda|--help) sed -n '2,16p' "$0" 2>/dev/null || echo "Veja o começo do arquivo instalar.sh"; exit 0 ;;
    *) aviso "Opção desconhecida ignorada: $1" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || erro "Rode com sudo:  curl -fsSL ... | sudo bash"

# ------------------------------------------------------------------ onde está o código
DIR_SCRIPT=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  DIR_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -n "$DIR_SCRIPT" ] && [ -f "$DIR_SCRIPT/docker-compose.yml" ] && [ -f "$DIR_SCRIPT/Dockerfile" ]; then
  DIR_PC="$DIR_SCRIPT"            # rodando de dentro de uma cópia do repositório
  USAR_GIT=0
else
  DIR_PC="$PASTA/Eva/computador"  # baixa do GitHub
  USAR_GIT=1
fi
ENV_ARQ="$DIR_PC/.env"
SENHAS_ARQ="$PASTA/LEIA-ME-SENHAS.txt"

env_get() { [ -f "$ENV_ARQ" ] && grep -E "^$1=" "$ENV_ARQ" | tail -n1 | cut -d= -f2- || true; }
env_set() {
  local chave="$1" valor="$2"
  touch "$ENV_ARQ"
  if grep -qE "^$chave=" "$ENV_ARQ"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$chave" -v v="$valor" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' "$ENV_ARQ" > "$tmp"
    cat "$tmp" > "$ENV_ARQ"; rm -f "$tmp"
  else
    echo "$chave=$valor" >> "$ENV_ARQ"
  fi
}
gerar() { openssl rand -hex "$1"; }   # só letras e números (seguro em URL, .env e caminhos)

mostrar_resumo() {
  local dominio token vnc com_eva senha_eva
  dominio="$(env_get DOMINIO)"; token="$(env_get EVA_PC_TOKEN)"; vnc="$(env_get EVA_PC_VNC_SENHA)"
  com_eva="$(env_get COM_EVA)"; senha_eva="$(env_get EVA_SENHA)"
  cat <<EOF

${VERDE}${NEGRITO}══════════════════════════════════════════════════════════════════════
   ✅  O COMPUTADOR DA EVA ESTÁ LIGADO!
══════════════════════════════════════════════════════════════════════${FIM}

  🌐 Endereço desta máquina:   https://${dominio}
  🖥️  Ver a tela (navegador):   https://${dominio}/vnc/
       senha da tela:          ${vnc:0:8}   (só os 8 primeiros caracteres)

${NEGRITO}  👉 Cole estas 3 variáveis na Eva (Render → teu serviço → Environment):${FIM}

     EVA_PC_URL=https://${dominio}/pc
     EVA_PC_TOKEN=${token}
     EVA_PC_VNC_SENHA=${vnc}

  Depois clique em "Save, rebuild and deploy". Quando a Eva voltar,
  a aba "Tela" mostra este computador AO VIVO.
EOF
  if [ "$com_eva" = "sim" ]; then
    cat <<EOF

${NEGRITO}  🤖 A Eva também roda AQUI, 24 horas:${FIM}
     Abra:   https://${dominio}/
     Senha:  ${senha_eva}
     (a Eva daqui já está ligada no computador; não precisa colar nada)
EOF
  fi
  cat <<EOF

  🔒 Guarde isso com cuidado (é como a chave da tua casa).
     Para ver de novo:   sudo cat ${SENHAS_ARQ}
     Ver o que está rodando:   cd ${DIR_PC} && sudo docker compose ps
     Atualizar/religar:        rode o mesmo comando de instalação de novo
EOF
}

if [ "$SO_MOSTRAR" = "1" ]; then
  [ -f "$ENV_ARQ" ] || erro "Ainda não instalado (não achei $ENV_ARQ)."
  mostrar_resumo; exit 0
fi

echo "${ROXO}${NEGRITO}"
cat <<'EOF'
   ███████╗  ██╗   ██╗   █████╗
   ██╔════╝  ██║   ██║  ██╔══██╗     O Computador da Eva
   █████╗    ██║   ██║  ███████║     instalador
   ██╔══╝    ╚██╗ ██╔╝  ██╔══██║
   ███████╗██╗╚████╔╝██╗██║  ██║██╗
   ╚══════╝╚═╝ ╚═══╝ ╚═╝╚═╝  ╚═╝╚═╝
EOF
echo "${FIM}"

# ------------------------------------------------------------------ 1. conferir a máquina
passo "1/8  Conferindo a máquina"
. /etc/os-release 2>/dev/null || true
ARQ="$(uname -m)"
echo "   Sistema: ${PRETTY_NAME:-desconhecido} · processador: $ARQ"
[ "${ID:-}" = "ubuntu" ] || aviso "Feito para Ubuntu 22.04/24.04. Vou tentar mesmo assim."
case "$ARQ" in aarch64|arm64|x86_64|amd64) ;; *) erro "Processador $ARQ não suportado (precisa ARM64 ou x86_64)." ;; esac
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
DISCO_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "   Memória: ${MEM_MB} MB · espaço livre: ${DISCO_GB} GB"
DISCO_MIN="${EVA_DISCO_MIN:-10}"
[ "${DISCO_GB:-0}" -ge "$DISCO_MIN" ] || erro "Pouco espaço em disco (${DISCO_GB} GB). Precisa de pelo menos ${DISCO_MIN} GB livres."
ok "Máquina compatível"

# ------------------------------------------------------------------ 2. pacotes básicos
passo "2/8  Instalando o básico (git, curl, openssl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git openssl iptables >/dev/null
ok "Pronto"

if [ "$MEM_MB" -lt 3500 ] && ! swapon --show | grep -q .; then
  passo "Memória pequena: criando 2 GB de swap"
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "Swap ligado"
fi

# ------------------------------------------------------------------ 3. Docker
passo "3/8  Instalando o Docker"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "Docker já instalado ($(docker --version | cut -d, -f1))"
else
  if ! curl -fsSL https://get.docker.com | sh >/tmp/eva-docker.log 2>&1; then
    aviso "Instalador oficial falhou; tentando pelos pacotes do Ubuntu"
    apt-get install -y -qq docker.io docker-compose-v2 docker-buildx >/dev/null 2>&1 \
      || apt-get install -y -qq docker.io docker-compose-v2 >/dev/null \
      || erro "Não consegui instalar o Docker (veja /tmp/eva-docker.log)."
  fi
  ok "Docker instalado"
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || erro "O 'docker compose' não está funcionando."

# ------------------------------------------------------------------ 4. firewall
passo "4/8  Liberando as portas 80 e 443 no firewall da máquina"
abrir_porta() {  # $1 = tcp|udp  $2 = porta
  local regra=(-p "$1" -m state --state NEW -m "$1" --dport "$2" -j ACCEPT)
  iptables -C INPUT "${regra[@]}" 2>/dev/null || iptables -I INPUT 1 "${regra[@]}"
  # as imagens da Oracle guardam as regras em /etc/iptables/rules.v4 com um REJECT no fim:
  # a liberação precisa entrar ANTES dele para valer depois de reiniciar
  if [ -f /etc/iptables/rules.v4 ] && ! grep -qE -- "-p $1 .*--dport $2 -j ACCEPT" /etc/iptables/rules.v4; then
    local linha="-A INPUT -p $1 -m state --state NEW -m $1 --dport $2 -j ACCEPT"
    if grep -qE '^-A INPUT .*-j REJECT' /etc/iptables/rules.v4; then
      awk -v l="$linha" '!f && /^-A INPUT .*-j REJECT/{print l; f=1} {print}' /etc/iptables/rules.v4 > /tmp/rules.v4.eva
    else
      awk -v l="$linha" '{print} /^:INPUT /&&!f{print l; f=1}' /etc/iptables/rules.v4 > /tmp/rules.v4.eva
    fi
    cat /tmp/rules.v4.eva > /etc/iptables/rules.v4 && rm -f /tmp/rules.v4.eva
  fi
}
abrir_porta tcp 80
abrir_porta tcp 443
abrir_porta udp 443
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
  ok "Regras do ufw adicionadas"
fi
ok "Portas 80/443 liberadas na máquina (e salvas para depois de reiniciar)"
echo "   Lembrete: na Oracle também é preciso liberar 80 e 443 na 'Security List' da rede (veja o README)."

# ------------------------------------------------------------------ 5. código
passo "5/8  Baixando o código do computador da Eva"
if [ "$USAR_GIT" = "1" ]; then
  mkdir -p "$PASTA"
  if [ -d "$PASTA/Eva/.git" ]; then
    git -C "$PASTA/Eva" fetch --depth 1 origin ${RAMO:+"$RAMO"} -q
    git -C "$PASTA/Eva" reset --hard -q FETCH_HEAD
    ok "Código atualizado"
  else
    rm -rf "$PASTA/Eva"
    git clone -q --depth 1 ${RAMO:+-b "$RAMO"} "$REPO_URL" "$PASTA/Eva" \
      || erro "Não consegui baixar $REPO_URL (o repositório é público?)."
    ok "Código baixado em $PASTA/Eva"
  fi
  [ -f "$DIR_PC/docker-compose.yml" ] || erro "O repositório não tem a pasta computador/ (confira se o código novo foi enviado pro GitHub)."
else
  ok "Usando o código desta pasta: $DIR_PC"
fi
mkdir -p "$PASTA"

# ------------------------------------------------------------------ 6. endereço e senhas
passo "6/8  Descobrindo o IP público e preparando as senhas"
eh_ipv4() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
IP="$IP_FORCADO"
if [ -z "$IP" ]; then
  for url in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com https://ipv4.icanhazip.com; do
    IP="$(curl -4 -fsS --max-time 6 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    eh_ipv4 "$IP" && break
    IP=""
  done
fi
eh_ipv4 "$IP" || erro "Não descobri o IP público. Rode de novo com:  ... | sudo bash -s -- --ip SEU.IP.PUBLICO"
DOMINIO="${IP//./-}.sslip.io"
echo "   IP público: $IP  →  endereço: https://$DOMINIO"

touch "$ENV_ARQ"; chmod 600 "$ENV_ARQ"
ANTIGO_DOMINIO="$(env_get DOMINIO)"
[ -n "$ANTIGO_DOMINIO" ] && [ "$ANTIGO_DOMINIO" != "$DOMINIO" ] && aviso "O IP mudou ($ANTIGO_DOMINIO → $DOMINIO). Atualize EVA_PC_URL na Eva!"
env_set DOMINIO "$DOMINIO"
[ -n "$(env_get EVA_PC_TOKEN)" ]       || env_set EVA_PC_TOKEN "$(gerar 32)"
[ -n "$(env_get EVA_PC_VNC_SENHA)" ]   || env_set EVA_PC_VNC_SENHA "$(gerar 16)"
[ -n "$(env_get EVA_PC_RESOLUCAO)" ]   || env_set EVA_PC_RESOLUCAO "1280x800"
[ -n "$(env_get EVA_PC_SUDO)" ]        || env_set EVA_PC_SUDO "nao"
if [ -z "$(env_get EVA_PC_MEMORIA)" ]; then
  # deixa pelo menos ~1,5 GB para o sistema (e para a Eva, se ela rodar aqui)
  LIM=$(( (MEM_MB - 1536) / 1024 )); [ "$LIM" -lt 2 ] && LIM=2; [ "$LIM" -gt 8 ] && LIM=8
  env_set EVA_PC_MEMORIA "${LIM}g"
fi
[ -n "$(env_get PERMITIR_IFRAME_DE)" ] || env_set PERMITIR_IFRAME_DE "https://*.onrender.com http://localhost:8000 http://127.0.0.1:8000"

COM_EVA="${COM_EVA_ARG:-$(env_get COM_EVA)}"; COM_EVA="${COM_EVA:-nao}"
env_set COM_EVA "$COM_EVA"
if [ "$COM_EVA" = "sim" ]; then
  [ -n "$(env_get EVA_USER_NAME)" ] || env_set EVA_USER_NAME "Gabriel"
  [ -n "$(env_get EVA_SENHA)" ] || env_set EVA_SENHA "$(gerar 8)"
  grep -qE '^DATABASE_URL=' "$ENV_ARQ" || env_set DATABASE_URL ""
  if [ -z "$(env_get EVA_LLM_API_KEY)" ]; then
    CHAVE=""
    if (: < /dev/tty) 2>/dev/null; then   # só pergunta se existe um terminal de verdade
      echo
      echo "   A Eva precisa da chave do Gemini (https://aistudio.google.com/apikey)."
      printf "   Cole a chave aqui e aperte Enter (ou só Enter para colar depois): "
      read -r CHAVE < /dev/tty 2>/dev/null || CHAVE=""
      CHAVE="$(echo "$CHAVE" | tr -d '[:space:]')"
      [ -n "$CHAVE" ] && env_set EVA_LLM_API_KEY "$CHAVE"
    fi
    [ -n "$(env_get EVA_LLM_API_KEY)" ] || aviso "Sem a chave do Gemini a Eva liga mas não pensa. Depois edite: sudo nano $ENV_ARQ (EVA_LLM_API_KEY=...) e rode o instalador de novo."
  fi
fi
ok "Senhas prontas (guardadas em $ENV_ARQ, só o administrador lê)"

# ------------------------------------------------------------------ 7. construir e ligar
passo "7/8  Montando e ligando o computador (na 1ª vez leva uns 10 a 25 minutos)"
cd "$DIR_PC"
PERFIL=()
[ "$COM_EVA" = "sim" ] && PERFIL=(--profile eva)
echo "   (o andamento completo fica em $LOG_BUILD)"
set +e
docker compose "${PERFIL[@]}" build --progress plain 2>&1 | tee "$LOG_BUILD" \
  | grep -E --line-buffered '^#[0-9]+ \[|^#[0-9]+ ERROR|^ERROR' | sed -u 's/^/   /'
RC_BUILD=${PIPESTATUS[0]}
set -e
if [ "$RC_BUILD" -ne 0 ]; then
  tail -n 30 "$LOG_BUILD"
  erro "A montagem falhou (detalhes acima e em $LOG_BUILD). Rode de novo; se repetir, mande esse trecho."
fi
docker compose pull -q caddy >/dev/null 2>&1 || true
if [ "$COM_EVA" != "sim" ]; then
  docker compose --profile eva rm -sf eva >/dev/null 2>&1 || true
fi
docker compose "${PERFIL[@]}" up -d --remove-orphans
ok "Containers ligados"

# ------------------------------------------------------------------ 8. conferir
passo "8/8  Conferindo se está tudo respondendo"
PRONTO=0
for _ in $(seq 1 60); do
  if docker compose exec -T desktop curl -fsS --max-time 3 http://127.0.0.1:8765/saude >/dev/null 2>&1; then PRONTO=1; break; fi
  sleep 3
done
[ "$PRONTO" = "1" ] && ok "A área de trabalho e o agente do PC estão no ar" \
  || { docker compose logs --tail 40 desktop || true; erro "O computador não respondeu em 3 minutos (logs acima)."; }

HTTPS=0
echo "   Pedindo o certificado HTTPS (pode levar até 2 minutos)…"
for _ in $(seq 1 40); do
  if curl -fsS --max-time 5 "https://$DOMINIO/pc/saude" >/dev/null 2>&1; then HTTPS=1; break; fi
  sleep 3
done
if [ "$HTTPS" = "1" ]; then
  ok "HTTPS funcionando em https://$DOMINIO"
else
  aviso "O HTTPS ainda não respondeu de fora. Quase sempre é a 'Security List' da Oracle bloqueando 80/443."
  cat <<EOF
   Na Oracle Cloud: Menu ☰ → Networking → Virtual Cloud Networks → (tua VCN) → Security Lists
   → Default Security List → Add Ingress Rules:
        Source CIDR 0.0.0.0/0 · IP Protocol TCP · Destination Port Range 80
        Source CIDR 0.0.0.0/0 · IP Protocol TCP · Destination Port Range 443
   Depois de salvar, espere 1 minuto e rode o instalador de novo (as senhas continuam as mesmas).
EOF
fi

mostrar_resumo
mostrar_resumo | sed 's/\x1b\[[0-9;]*m//g' > "$SENHAS_ARQ"
chmod 600 "$SENHAS_ARQ"
trap - ERR
exit 0
