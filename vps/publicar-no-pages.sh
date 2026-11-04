#!/usr/bin/env bash
# Publica a pasta web/ no branch do Pages como UM único commit.
#
# Por que commit órfão e --force: o site são ~5 MB por rodada, e guardar isso
# em histórico é um erro que este projeto já cometeu — em 8 rodadas o .git
# estava em 49 MB, com projeção de 1 a 2 GB no ano (HANDOFF §5). Aqui cada
# publicação JOGA FORA o histórico: o branch tem sempre exatamente um commit,
# então o repositório não cresce, aconteça o que acontecer. Quem quiser a base
# de um dia específico pega no histórico local da VPS, não no git.
#
# O Pages serve direto do branch:
#   Settings -> Pages -> Deploy from a branch -> gh-pages / (root)
# `publicar.py` já grava o `.nojekyll`, sem o qual o Jekyll do Pages
# reprocessa a pasta e come os arquivos que começam com underscore.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$RAIZ/web"
BRANCH="${BRANCH_PAGES:-gh-pages}"
REMOTO="${REMOTO_PAGES:-git@github.com:acontece-no-sesc/acontece-no-sesc.github.io.git}"
MOTIVO="${1:-rodada}"

# Publicar o vazio derruba o site que está no ar. As duas conferências abaixo
# custam nada e são a diferença entre "deu errado" e "deu errado e eu publiquei".
[ -s "$WEB/index.html" ] || {
  echo "ERRO: $WEB/index.html não existe ou está vazio — publicar.py não rodou." >&2
  exit 1
}
[ -f "$WEB/.nojekyll" ] || {
  echo "ERRO: falta $WEB/.nojekyll — sem ele o Pages passa o site pelo Jekyll." >&2
  exit 1
}

rm -rf "$WEB/.git"
git -C "$WEB" init -q -b "$BRANCH"
git -C "$WEB" config user.name "robo do acontece-no-sesc"
git -C "$WEB" config user.email "robo@acontece-no-sesc.invalid"
git -C "$WEB" add -A
git -C "$WEB" commit -qm "Publicação automática ($MOTIVO) — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git -C "$WEB" push -q --force "$REMOTO" "HEAD:$BRANCH"
rm -rf "$WEB/.git"

echo "Publicado em $BRANCH · $(du -sh "$WEB" | cut -f1) · motivo: $MOTIVO"
