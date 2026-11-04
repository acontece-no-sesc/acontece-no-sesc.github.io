#!/usr/bin/env bash
# A coleta grande — o que no GitHub Actions era o workflow "Atualizar
# programação". Mudou de casa porque o CloudFront do portal passou a devolver
# 403 para as faixas do GitHub/Azure em 31/10/2026 (HANDOFF §11 e §12). Não é
# bloqueio de datacenter: medidos 14 nós em 12 países, todos com 200. É o
# runner do GitHub que está barrado.
#
# Uso (cron):  /opt/acontece/vps/rodar.sh
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PY="${PY:-python3}"
DIAS="${DIAS:-60}"
LOGS="$RAIZ/vps/log"
HIST="$RAIZ/dados/historico"
mkdir -p "$LOGS" "$HIST"
LOG="$LOGS/coleta-$(date +%Y%m%d-%H%M).log"

# Toda a saída vai para o log E para a tela: na mão você quer ver, no cron o
# arquivo é a única testemunha. O cron não precisa redirecionar nada.
exec > >(tee -a "$LOG") 2>&1

# A rodada leve dos sorteios escreve no MESMO dados/eventos.json. Duas
# passadas ao mesmo tempo corrompem a base, e era o `concurrency: atualizar`
# do workflow que impedia isso. Aqui quem impede é o flock — e a coleta
# grande ESPERA a leve terminar (ela leva menos de um minuto), em vez de
# desistir: perder a rodada das 5h por causa de 40 segundos de sorteio seria
# trocar o dia inteiro por nada.
exec 9> "$RAIZ/vps/.trava"
flock -w 300 9 || { echo "ABORTADO: outra passada segurou a trava por 5 minutos."; exit 1; }

echo "=== coleta grande · $(date -Is) · janela de $DIAS dias"

# A base anterior mora aqui e sobrevive sozinha entre as rodadas. No Actions
# ela dependia do cache do runner, com rede de segurança baixando o arquivo
# publicado — e `carimbar_estreia` perde o trilho de novidades sem ela
# (HANDOFF §4.5). Na VPS o problema deixa de existir: o arquivo fica no disco.
if [ -s dados/eventos.json ]; then
  echo "Base anterior: $(du -h dados/eventos.json | cut -f1)"
else
  echo "AVISO: sem base anterior. Esta coleta vira linha de base e não marca novidade."
fi

"$PY" coletor.py --dias "$DIAS"
"$PY" detalhes.py --html
"$PY" reparse.py --mostrar 0
"$PY" extras.py
"$PY" embutir.py --com-descricao
# O repositório se chama <dono>.github.io, então o Pages serve na raiz.
"$PY" publicar.py --base "/"

vps/publicar-no-pages.sh "coleta de $(date +%d/%m)"

# O que se perdeu ao sair do Actions foi o artefato por execução, que é como o
# HANDOFF manda recuperar a base de um dia específico. Aqui ele volta como
# cópia comprimida local, com a mesma retenção de 30 dias.
gzip -c dados/eventos.json > "$HIST/eventos-$(date +%Y%m%d-%H%M).json.gz"
find "$HIST" -name 'eventos-*.json.gz' -mtime +30 -delete
find "$LOGS" -name '*.log' -mtime +30 -delete

# Interruptor de homem morto. O modo de falhar de um cron é não acontecer, e
# isso não gera log nenhum para alguém ler: um serviço de monitoramento que
# espera o ping avisa quando ele não vem. Sem PING_URL no ambiente, o passo
# simplesmente não existe — mas aí a rodada volta a poder sumir calada.
if [ -n "${PING_URL:-}" ]; then
  curl -fsS --max-time 20 "$PING_URL" > /dev/null && echo "ping enviado"
fi

echo "=== fim · $(date -Is)"
