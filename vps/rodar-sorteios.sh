#!/usr/bin/env bash
# A rodada leve dos sorteios — o que no Actions era "Listas de sorteio".
#
# Ela existe porque o Sesc publica a lista de contemplados na página da
# atividade às 14h ou às 16h: com uma passada só, de madrugada, a lista nunca
# chegava no dia, e quem tinha o código lia no app que o resultado não havia
# saído (HANDOFF §6.5). São poucas páginas — só os sorteios cuja divulgação
# está vencendo —, por isso cabe de 20 em 20 minutos; a passada grande são 3
# mil páginas e isso a cada 20 minutos seria abuso de um portal que não é nosso.
#
# Uso (cron):  /opt/acontece/vps/rodar-sorteios.sh
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PY="${PY:-python3}"
DIAS="${DIAS_SORTEIO:-2}"
SITE="${SITE:-https://acontece-no-sesc.github.io}"
LOGS="$RAIZ/vps/log"
mkdir -p "$LOGS"
exec > >(tee -a "$LOGS/sorteios-$(date +%Y%m%d).log") 2>&1

# Sem espera: se a coleta grande está rodando, esta passada cede a vez. São 21
# rodadas numa tarde e a próxima vem em 20 minutos — mas ceder caladinho é
# como um recurso morre sem ninguém notar, então fica dito no log.
exec 9> "$RAIZ/vps/.trava"
flock -n 9 || { echo "$(date -Is) cedi a vez: a coleta grande está com a trava."; exit 0; }

echo "=== sorteios · $(date -Is)"

[ -s dados/eventos.json ] || { echo "Sem base; nada a conferir nesta rodada."; exit 0; }

# A pergunta é "o que o SITE ainda não tem", não "o que eu já sabia" — então a
# referência é o arquivo publicado, e não o local. Assim uma publicação que
# falhou no meio é refeita na rodada seguinte em vez de ficar para trás.
if ! curl -fsSL --max-time 60 "$SITE/dados/sorteados.json" -o "$RAIZ/vps/.publicado.json"; then
  rm -f "$RAIZ/vps/.publicado.json"
  echo "Sem sorteados.json publicado ainda; esta rodada publica."
fi

saida="$RAIZ/vps/.saida-sorteios.txt"
if [ -f "$RAIZ/vps/.publicado.json" ]; then
  "$PY" sorteios.py --dias "$DIAS" --anterior "$RAIZ/vps/.publicado.json" | tee "$saida"
else
  "$PY" sorteios.py --dias "$DIAS" | tee "$saida"
fi

novas=$(sed -n 's/^\([0-9]\+\) lista(s) nova(s).*/\1/p' "$saida")
novas="${novas:-0}"

# O site só é refeito quando há lista nova: numa tarde de sorteio são 21
# rodadas e, na melhor das hipóteses, uma acha algo. Republicar nas outras 20
# seria trocar um carimbo e nada mais.
if [ "$novas" = "0" ]; then
  echo "Nenhuma lista nova; o site fica como está."
  exit 0
fi

"$PY" embutir.py --com-descricao
"$PY" publicar.py --base "/"
vps/publicar-no-pages.sh "$novas lista(s) de sorteio"
