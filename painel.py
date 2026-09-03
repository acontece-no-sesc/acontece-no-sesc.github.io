#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta `painel.html` — o que a medição está dizendo, com denominadores.

Contagem sem denominador não se lê. "40 eventos abertos" é muito ou pouco?
Depende de quantas pessoas entraram, e de quantas atividades existiam para
abrir. Este painel põe as duas metades lado a lado: o uso vem do GoatCounter,
a base de comparação vem da própria coleta.

Duas formas de trazer o uso, e nenhuma delas escreve a chave em lugar nenhum
versionado:

    # 1) pela API, com a chave num arquivo local (que o .gitignore protege)
    #    crie em acontecesesc.goatcounter.com -> Settings -> API tokens
    echo SUA_CHAVE > .goatcounter-token
    python painel.py

    # 2) pelo CSV exportado no painel (Settings -> Export)
    python painel.py --csv caminho/do/export.csv

Sem nenhuma das duas ele ainda roda: monta a metade do catálogo e diz, em
cada cartão, qual pergunta está esperando resposta. É de propósito — um
painel que inventa número é pior do que um painel vazio.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.abspath(__file__))
SITE = "https://acontecesesc.goatcounter.com"
ARQ_CHAVE = os.path.join(RAIZ, ".goatcounter-token")


# ------------------------------------------------------------------ coleta
def ler_chave():
    """Da variável de ambiente ou do arquivo local. Nunca de um argumento:
    o que vai na linha de comando fica no histórico do terminal."""
    v = os.environ.get("GOATCOUNTER_TOKEN")
    if v:
        return v.strip()
    if os.path.exists(ARQ_CHAVE):
        with open(ARQ_CHAVE, encoding="utf-8") as f:
            return f.read().strip()
    return None


def da_api(chave, dias):
    """{caminho: contagem} e o total de visitas, pela API do GoatCounter."""
    inicio = (date.today() - timedelta(days=dias)).isoformat()
    caminhos, total = {}, 0
    cursor = ""
    while True:
        url = ("%s/api/v0/stats/hits?start=%s&limit=100%s"
               % (SITE, inicio, ("&cursor=" + cursor) if cursor else ""))
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + chave,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        for linha in d.get("hits") or []:
            caminhos[linha.get("path", "").lstrip("/")] = linha.get("count", 0)
        if not d.get("more"):
            break
        cursor = str(d.get("cursor") or "")
        if not cursor:
            break

    req = urllib.request.Request(
        "%s/api/v0/stats/total?start=%s" % (SITE, inicio),
        headers={"Authorization": "Bearer " + chave,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            total = json.load(r).get("total", 0)
    except urllib.error.HTTPError:
        total = sum(caminhos.values())
    return caminhos, total


def do_csv(caminho):
    """O export de 'Individual pageviews': uma linha por acesso."""
    caminhos = Counter()
    total = 0
    with open(caminho, encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f):
            p = (linha.get("Path") or "").strip().lstrip("/")
            if (linha.get("Bot") or "0") not in ("", "0"):
                continue
            caminhos[p] += 1
            total += 1
    return dict(caminhos), total


def do_backup(pasta):
    """O export de backup do GoatCounter: uma tabela por arquivo .jsonl.

    É o que sai de Settings -> Export, e é mais completo que o CSV: traz
    `paths.jsonl` ({id, path, event}) e `hit_stats.jsonl` ({hour, path_id,
    count}), que se juntam pelo id. Também traz navegador, sistema e origem,
    que o painel usa para saber se a amostra é gente ou é teste nosso.
    """
    def jl(nome):
        caminho = os.path.join(pasta, nome)
        if not os.path.exists(caminho):
            return []
        with open(caminho, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    paths = {p["id"]: p for p in jl("paths.jsonl")}
    refs = {r["id"]: (r.get("ref") or "") for r in jl("refs.jsonl")}

    caminhos, total, horas, origens = Counter(), 0, set(), Counter()
    for h in jl("hit_stats.jsonl"):
        p = paths.get(h.get("path_id")) or {}
        alvo = (p.get("path") or "").lstrip("/")
        n = h.get("count") or 0
        if p.get("event"):
            caminhos[alvo] += n
        else:
            total += n                      # visita de página, não evento
        horas.add((h.get("hour") or "")[:13])
        origens[refs.get(h.get("ref_id"), "") or "(direto)"] += n

    sistemas = Counter()
    for s in jl("system_stats.jsonl"):
        sistemas[s.get("name") or "?"] += s.get("count") or 0

    return dict(caminhos), total, {
        "horas": len(horas),
        "primeira": min(horas) if horas else None,
        "ultima": max(horas) if horas else None,
        "origens": origens.most_common(),
        "sistemas": sistemas.most_common(),
    }


# Os eventos que o app dispara. A lista está no próprio código; o painel a
# lê de lá para não sair de sincronia — se alguém instrumentar algo novo e
# esquecer daqui, a conferência abaixo acusaria falta que não existe.
def eventos_do_codigo():
    caminho = os.path.join(RAIZ, "prototipo.html")
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8") as f:
        s = f.read()
    achados = re.findall(r'medir\(\s*"([a-z0-9-]+)"', s)
    return sorted(set(achados))


# ------------------------------------------------------------------ contas
def agrupar(caminhos):
    """{evento: {detalhe: n}} a partir de 'evento/detalhe'."""
    fora = defaultdict(Counter)
    for p, n in caminhos.items():
        if not p or p in ("/", "index.html"):
            continue
        ev, _, det = p.partition("/")
        fora[ev][det or "—"] += n
    return fora


def soma(grupo, evento):
    return sum(grupo.get(evento, {}).values())


def catalogo():
    """A base de comparação. Sem ela nenhum número de uso se lê."""
    caminho = os.path.join(RAIZ, "dados", "eventos.json")
    if not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    ev = d.get("eventos") or []
    hoje = max((d.get("janela") or {}).get("de", ""), date.today().isoformat())
    por_cat = Counter(e.get("cat") for e in ev)
    return {
        "eventos": len(ev),
        "unidades": len(d.get("unidades") or []),
        "gratis": sum(1 for e in ev if e.get("gratis")),
        "hoje": sum(1 for e in ev if hoje in (e.get("dias") or [])),
        "categorias": por_cat.most_common(),
        "coleta": (d.get("geradoEm") or "")[:16].replace("T", " às "),
        "janela": d.get("janela") or {},
        "sorteaveis": sum(1 for e in ev if (e.get("inscricao") or {}).get("temSorteio")),
        "com_regra": sum(1 for e in ev if (e.get("inscricao") or {}).get("regras")),
        "novidades": sum(1 for e in ev if e.get("visto")),
    }


def f_n(n):
    """Número no formato de casa: 3.300, não 3300."""
    if n is None:
        return "—"
    return "{:,}".format(int(n)).replace(",", ".")


# ------------------------------------------------------------------ leitura
def perguntas(g, total, cat):
    """Cada evento foi instrumentado para responder UMA pergunta.

    O painel mostra a pergunta, não o nome do evento — e a leitura junto,
    porque número solto não decide nada. Onde ainda não há dado, o cartão diz
    o que cada faixa de resposta vai significar; é mais útil do que um zero, e
    não finge conclusão que não existe.
    """
    def pct(a, b):
        return (100.0 * a / b) if b else None

    abertos = soma(g, "abre-evento")
    calend = soma(g, "calendario")
    oficial = soma(g, "abre-pagina-oficial")
    favs = g.get("favorita", {}).get("poe", 0)

    zero = soma(g, "busca-zero")
    zero_cego = sum(n for det, n in g.get("busca-zero", {}).items()
                    if det.startswith("nada"))

    instalado = g.get("abre", {}).get("instalado", 0)
    navegador = g.get("abre", {}).get("navegador", 0)
    aberturas = instalado + navegador

    pulou = g.get("onboard-unidades", {}).get("pulou", 0)
    escolheu = g.get("onboard-unidades", {}).get("escolheu", 0)
    onboard = pulou + escolheu

    def maior(ev):
        d = g.get(ev) or {}
        return max(d, key=d.get) if d else None

    return [
        {
            "grupo": "alcance",
            "titulo": "Quantas pessoas chegam",
            "valor": total, "unidade": "visitas",
            "base": ("%s atividades no ar" % f_n(cat["eventos"])) if cat else None,
            "detalhe": [("instalado como app", instalado), ("pelo navegador", navegador)],
            "leitura": (
                "%.0f%% das aberturas vêm do app instalado. Instalar é um ato "
                "deliberado — é a medida de fidelidade mais honesta daqui."
                % pct(instalado, aberturas)) if instalado else None,
            "espera": "A razão entre instalado e navegador diz se o PWA pegou. "
                      "Um quinto já é bom sinal para um app sem divulgação.",
        },
        {
            "grupo": "conversao",
            "titulo": "De cada evento aberto, quantos viram compromisso",
            "valor": calend, "unidade": "foram ao calendário",
            "base": ("de %s eventos abertos" % f_n(abertos)) if abertos else None,
            "detalhe": [("abriram a página oficial", oficial), ("favoritaram", favs)],
            "leitura": (
                "1 em cada %.1f eventos abertos vira compromisso."
                % (float(abertos) / calend)) if (calend and abertos) else None,
            "espera": "É A conversão do projeto. O app não vende nada: existe para "
                      "levar gente ao Sesc, e calendário e página oficial são as "
                      "duas únicas provas de que isso aconteceu.",
        },
        {
            "grupo": "conversao",
            "titulo": "Quantas visitas chegam a abrir um evento",
            "valor": abertos, "unidade": "eventos abertos",
            "base": ("de %s visitas" % f_n(total)) if total else None,
            "detalhe": sorted((g.get("abre-evento") or {}).items(), key=lambda x: -x[1])[:4],
            "leitura": (
                "%.0f%% das visitas abrem ao menos um evento, e a origem mais "
                "comum é a aba %s."
                % (pct(abertos, total), maior("abre-evento"))) if (abertos and total) else None,
            "espera": "O detalhe diz de qual tela a pessoa veio. Se a home entregar "
                      "mais que a agenda, os trilhos estão fazendo o trabalho.",
        },
        {
            "grupo": "busca",
            "titulo": "Buscas que não acharam nada",
            "valor": zero, "unidade": "buscas vazias",
            "base": None,
            "detalhe": sorted((g.get("busca-zero") or {}).items(), key=lambda x: -x[1])[:5],
            "leitura": (
                "%.0f%% das buscas vazias não tinham nenhuma palavra do catálogo."
                % pct(zero_cego, zero)) if zero else None,
            "espera": "O evento mais valioso do conjunto. Se a maioria vier com zero "
                      "termos reconhecidos, o vocabulário fechado não basta e cabe "
                      "discutir um modelo de linguagem. Se vier COM termos "
                      "reconhecidos, o problema é de acervo, não de busca.",
        },
        {
            "grupo": "v41",
            "titulo": "A agenda por semana foi aceita",
            "valor": (g.get("agenda-escopo") or {}).get("tudo", 0),
            "unidade": "trocaram para todas as datas",
            "base": ("%s pediram mais linhas" % f_n(soma(g, "carrega-mais"))) if soma(g, "carrega-mais") else None,
            "detalhe": [("ficaram na semana", (g.get("agenda-escopo") or {}).get("semana", 0)),
                        ("abriram uma série", soma(g, "serie-abre"))],
            "leitura": None,
            "espera": "Se muita gente trocar para todas as datas, o recorte semanal "
                      "que virou padrão foi um erro meu. E “carregar mais” alto "
                      "significa que a semana ainda é longa demais.",
        },
        {
            "grupo": "v41",
            "titulo": "Os filtros novos se pagam",
            "valor": soma(g, "filtro"), "unidade": "filtros aplicados",
            "base": None,
            "detalhe": sorted((g.get("filtro") or {}).items(), key=lambda x: -x[1]),
            "leitura": None,
            "espera": "Período do dia e faixa etária entraram sobre campos que já "
                      "eram coletados. Preço e situação já existiam e servem de "
                      "régua: se os dois novos ficarem muito atrás, saem.",
        },
        {
            "grupo": "v41",
            "titulo": "O tema escuro era necessário",
            "valor": (g.get("tema") or {}).get("escuro", 0), "unidade": "escolheram escuro",
            "base": None,
            "detalhe": sorted((g.get("tema") or {}).items(), key=lambda x: -x[1]),
            "leitura": None,
            "espera": "Só conta quem trocou à mão — quem fica em “sistema” não "
                      "aparece, e deve ser a maioria. Qualquer número acima de zero "
                      "já significa que alguém foi ao menu de propósito.",
        },
        {
            "grupo": "primeiro",
            "titulo": "A folha de boas-vindas convida ou afugenta",
            "valor": onboard, "unidade": "passaram pelo onboarding",
            "base": None,
            "detalhe": [("escolheram unidades", escolheu), ("pularam", pulou)],
            "leitura": (
                "%.0f%% escolheram unidades em vez de pular."
                % pct(escolheu, onboard)) if onboard else None,
            "espera": "Era o achado D5: a saída ficava 1.670 px abaixo da dobra. "
                      "Com o rodapé preso, dá para saber se escolher virou escolha "
                      "de verdade ou continua o caminho de menor resistência.",
        },
        {
            "grupo": "primeiro",
            "titulo": "Quem responde qual é o seu preço",
            "valor": soma(g, "credencial"), "unidade": "responderam",
            "base": None,
            "detalhe": sorted((g.get("credencial") or {}).items(), key=lambda x: -x[1]),
            "leitura": None,
            "espera": "A distribuição real entre Credencial Plena, comerciário e "
                      "público geral — que nenhuma outra fonte deste projeto tem. "
                      "Muitos “nenhuma” significa que a pergunta chegou cedo demais.",
        },
        {
            "grupo": "conversao",
            "titulo": "Como as pessoas compartilham",
            "valor": soma(g, "compartilha"), "unidade": "compartilhamentos",
            "base": None,
            "detalhe": sorted((g.get("compartilha") or {}).items(), key=lambda x: -x[1]),
            "leitura": None,
            "espera": "“sistema” é a folha do celular; “whatsapp” e “copia” só "
                      "aparecem no computador, onde ela não existe. Se “copia” for "
                      "expressivo, vale um botão a mais lá.",
        },
    ]


GRUPOS = [
    ("alcance", "Alcance", "quantas pessoas, e de onde"),
    ("conversao", "O que o app entrega", "a única prova de que alguém foi ao Sesc"),
    ("busca", "A busca", "a pergunta que decide o próximo investimento"),
    ("v41", "As mudanças da v4.1", "o que eu impus como padrão foi aceito?"),
    ("primeiro", "O primeiro minuto", "onde a pessoa decide ficar ou sair"),
]


# ------------------------------------------------------------------ desenho
# A paleta é a do app — os mesmos tokens da v4.1 "Cartaz", inclusive o cinza
# corrigido e as tintas noturnas calculadas contra a superfície do cartão.
# O painel é sobre o app; herdar a voz dele é o certo. O que muda é a
# densidade: aqui se varre, não se navega.
CSS = """
:root {
  color-scheme: light;
  --papel:   #F3F0E8;
  --carta:   #FCFBF8;
  --erguido: #FFFFFF;
  --linha:   #DED8CA;
  --linha-f: #EAE5DA;
  --tinta:   #14120C;
  --tinta-2: #57523F;
  --tinta-3: #6E6857;
  --acento:  #1B4D7A;
  --acento-f:#E3ECF3;
  --bom:     #1D6844;
  --atencao: #8A5806;
  --frio:    #8A8474;
  --sombra:  0 1px 2px rgba(20,18,12,.05), 0 14px 30px -24px rgba(20,18,12,.45);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --papel:   #100E0A;
    --carta:   #1B1811;
    --erguido: #211D16;
    --linha:   #3A362C;
    --linha-f: #2C2820;
    --tinta:   #F3EFE5;
    --tinta-2: #C0BAA6;
    --tinta-3: #8C8677;
    --acento:  #3789D3;
    --acento-f:#1A2A38;
    --bom:     #2A9863;
    --atencao: #BB7708;
    --frio:    #8C8677;
    --sombra:  0 1px 2px rgba(0,0,0,.5), 0 14px 30px -24px rgba(0,0,0,.9);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --papel:   #100E0A;
  --carta:   #1B1811;
  --erguido: #211D16;
  --linha:   #3A362C;
  --linha-f: #2C2820;
  --tinta:   #F3EFE5;
  --tinta-2: #C0BAA6;
  --tinta-3: #8C8677;
  --acento:  #3789D3;
  --acento-f:#1A2A38;
  --bom:     #2A9863;
  --atencao: #BB7708;
  --frio:    #8C8677;
  --sombra:  0 1px 2px rgba(0,0,0,.5), 0 14px 30px -24px rgba(0,0,0,.9);
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--papel); color: var(--tinta);
  font-family: "Archivo", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 16px; line-height: 1.55; -webkit-font-smoothing: antialiased;
}
.folha { max-width: 1120px; margin: 0 auto; padding: 0 24px 88px; }

h1, h2, h3 {
  font-family: "Bricolage Grotesque", "Archivo", ui-sans-serif, system-ui, sans-serif;
  font-weight: 800; letter-spacing: -.025em; line-height: 1.06;
  text-wrap: balance; margin: 0;
}
.rotulo {
  font-size: 10.5px; font-weight: 700; letter-spacing: .14em;
  text-transform: uppercase; color: var(--tinta-3);
}
.dado { font-variant-numeric: tabular-nums;
  font-family: ui-monospace, "SF Mono", "Cascadia Mono", "Segoe UI Mono", Menlo, monospace; }

/* ---- capa ---- */
.capa { padding: 68px 0 32px; border-bottom: 1px solid var(--linha); display: grid; gap: 18px; }
.capa h1 { font-size: clamp(34px, 5.4vw, 56px); }
.capa p { margin: 0; max-width: 62ch; color: var(--tinta-2); font-size: clamp(16px, 1.7vw, 18.5px); }
.meta { display: flex; flex-wrap: wrap; gap: 6px 26px; font-size: 13px; color: var(--tinta-3);
  border-top: 1px solid var(--linha-f); padding-top: 16px; }
.meta b { color: var(--tinta-2); font-weight: 600; }

/* ---- o funil, que é a espinha da página ---- */
.funil { display: grid; gap: 1px; background: var(--linha); border: 1px solid var(--linha);
  border-radius: 14px; overflow: hidden; margin-top: 30px;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.degrau { background: var(--carta); padding: 22px 20px; display: grid; gap: 6px; align-content: start; }
.degrau .rotulo { color: var(--tinta-3); }
.degrau .n { font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
  font-size: clamp(30px, 4.4vw, 42px); line-height: 1; letter-spacing: -.035em;
  font-variant-numeric: tabular-nums; }
.degrau .u { font-size: 13.5px; color: var(--tinta-2); }
.degrau .taxa { font-size: 12.5px; color: var(--acento); font-weight: 600; }
.degrau.vazio .n { color: var(--frio); }

/* ---- seções de perguntas ---- */
section { padding-top: 54px; }
section > header { display: grid; gap: 4px; margin-bottom: 20px; }
section > header h2 { font-size: clamp(22px, 2.8vw, 28px); }
section > header p { margin: 0; color: var(--tinta-2); font-size: 14.5px; max-width: 60ch; }

.cartoes { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); }
.cartao { background: var(--carta); border: 1px solid var(--linha); border-radius: 14px;
  padding: 20px 22px; display: grid; gap: 14px; align-content: start; box-shadow: var(--sombra); }
.cartao h3 { font-size: 17px; letter-spacing: -.015em; }

.numero { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.numero .v { font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
  font-size: 40px; line-height: .95; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
.numero .u { font-size: 13.5px; color: var(--tinta-2); }
.numero .base { font-size: 12.5px; color: var(--tinta-3); }
.cartao.sem-dado .numero .v { color: var(--frio); }

.barras { display: grid; gap: 7px; }
.barra { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; align-items: baseline;
  font-size: 13px; }
.barra .k { color: var(--tinta-2); }
.barra .n { color: var(--tinta); font-weight: 600; font-variant-numeric: tabular-nums;
  font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.barra .trilho { grid-column: 1 / -1; height: 5px; border-radius: 999px; background: var(--linha-f); overflow: hidden; }
.barra .fita { height: 100%; background: var(--acento); border-radius: 999px; }

.leitura { font-size: 14.5px; color: var(--tinta); border-left: 3px solid var(--bom);
  padding: 2px 0 2px 12px; }
.espera { font-size: 13.5px; color: var(--tinta-2); border-left: 3px solid var(--linha);
  padding: 2px 0 2px 12px; }
.espera b { color: var(--tinta); font-weight: 600; }

.aviso { background: var(--acento-f); border: 1px solid var(--acento);
  border-radius: 12px; padding: 18px 22px; margin-top: 26px; display: grid; gap: 8px; }
.aviso h3 { font-size: 16px; color: var(--acento); }
.aviso p { margin: 0; font-size: 14.5px; color: var(--tinta-2); max-width: 68ch; }
.aviso code { font-family: ui-monospace, Menlo, monospace; font-size: .9em;
  background: var(--carta); padding: 1px 6px; border-radius: 4px; color: var(--tinta); }

/* ---- catálogo: os denominadores ---- */
.grade { display: grid; gap: 1px; background: var(--linha); border: 1px solid var(--linha);
  border-radius: 14px; overflow: hidden; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); }
.grade div { background: var(--carta); padding: 18px 16px; }
.grade .n { font-family: "Bricolage Grotesque", sans-serif; font-weight: 800; font-size: 26px;
  line-height: 1; letter-spacing: -.03em; display: block; margin-bottom: 6px;
  font-variant-numeric: tabular-nums; }
.grade .k { font-size: 12.5px; color: var(--tinta-3); line-height: 1.4; display: block; }

/* ---- conferência da instrumentação ---- */
.confere { background: var(--carta); border: 1px solid var(--linha); border-radius: 14px;
  padding: 20px 22px; display: grid; gap: 14px; }
.confere .placar { margin: 0; font-size: 14.5px; color: var(--tinta-2); }
.confere .placar b { font-family: "Bricolage Grotesque", sans-serif; font-weight: 800;
  font-size: 26px; color: var(--tinta); letter-spacing: -.02em; }
.confere ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.confere li { display: flex; align-items: baseline; gap: 9px; font-size: 13.5px;
  font-family: ui-monospace, Menlo, monospace; }
.confere li.viu { color: var(--tinta); }
.confere li.espera { color: var(--tinta-3); }
.confere .marca { font-weight: 700; width: 12px; display: inline-block; }
.confere li.viu .marca { color: var(--bom); }
.confere li.espera .marca { color: var(--frio); }
.aviso em { font-style: normal; font-weight: 600; color: var(--tinta); }
.aviso b { color: var(--tinta); }

rodape, footer { display: block; margin-top: 64px; padding-top: 22px;
  border-top: 1px solid var(--linha); font-size: 13px; color: var(--tinta-3); }
footer p { max-width: 74ch; }
a { color: var(--acento); text-underline-offset: 2px; }
:focus-visible { outline: 2px solid var(--acento); outline-offset: 3px; border-radius: 4px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def barras(itens):
    """Distribuição interna de um evento. Sem dado, some — barra a zero é ruído."""
    itens = [(k, v) for k, v in itens if v]
    if not itens:
        return ""
    teto = max(v for _, v in itens)
    linhas = []
    for k, v in itens:
        larg = 100.0 * v / teto if teto else 0
        linhas.append(
            '<div class="barra"><span class="k">%s</span><span class="n">%s</span>'
            '<span class="trilho"><span class="fita" style="width:%.1f%%"></span></span></div>'
            % (esc(k), f_n(v), larg))
    return '<div class="barras">%s</div>' % "".join(linhas)


def cartao(p, confiavel=True):
    tem = bool(p["valor"])
    partes = ['<article class="cartao%s">' % ("" if tem else " sem-dado")]
    partes.append("<h3>%s</h3>" % esc(p["titulo"]))
    partes.append('<div class="numero"><span class="v">%s</span>'
                  '<span class="u">%s</span>%s</div>'
                  % (f_n(p["valor"]) if tem else "—", esc(p["unidade"]),
                     ('<span class="base">%s</span>' % esc(p["base"])) if p.get("base") else ""))
    partes.append(barras(p.get("detalhe") or []))
    if p.get("leitura") and confiavel:
        partes.append('<p class="leitura">%s</p>' % esc(p["leitura"]))
    if p.get("espera"):
        partes.append('<p class="espera">%s</p>' % esc(p["espera"]))
    partes.append("</article>")
    return "".join(partes)


MINIMO_PARA_CONCLUIR = 30          # visitas, não eventos


def conferencia(g):
    """Quais eventos instrumentados já se viu disparar ao menos uma vez.

    Numa amostra pequena esta é a única leitura legítima: ela não diz nada
    sobre comportamento, mas prova que a medição chegou inteira do outro
    lado — que é exatamente o que se quer saber logo depois de publicar.
    """
    no_codigo = eventos_do_codigo()
    vistos = set(g.keys())
    return [(e, e in vistos) for e in no_codigo]


def bloco_conferencia(g):
    itens = conferencia(g)
    if not itens:
        return ""
    ok = sum(1 for _, v in itens if v)
    linhas = "".join(
        '<li class="%s"><span class="marca">%s</span>%s</li>'
        % ("viu" if v else "espera", "✓" if v else "·", esc(e))
        for e, v in itens)
    return (
        "<section><header><h2>A medição chegou inteira?</h2>"
        "<p>Cada evento instrumentado no app, e se já se viu disparar ao menos "
        "uma vez. Os que faltam pedem uma ação específica — filtrar, buscar sem "
        "resultado, trocar o alcance da agenda — e o onboarding só dispara uma "
        "vez por aparelho.</p></header>"
        '<div class="confere"><p class="placar"><b>%d</b> de %d confirmados</p>'
        "<ul>%s</ul></div></section>" % (ok, len(itens), linhas))


def montar(g, total, cat, origem, ctx=None):
    # Antes de qualquer leitura: a amostra sustenta conclusão? Abaixo do piso,
    # as leituras derivadas somem e o painel muda de assunto.
    confiavel = total >= MINIMO_PARA_CONCLUIR
    ps = perguntas(g, total, cat)
    abertos = soma(g, "abre-evento")
    acoes = soma(g, "calendario") + soma(g, "abre-pagina-oficial") + \
        (g.get("favorita") or {}).get("poe", 0)

    def degrau(rot, n, uni, taxa=None):
        return ('<div class="degrau%s"><span class="rotulo">%s</span>'
                '<span class="n">%s</span><span class="u">%s</span>%s</div>'
                % ("" if n else " vazio", esc(rot), f_n(n) if n else "—", esc(uni),
                   ('<span class="taxa">%s</span>' % esc(taxa)) if taxa else ""))

    # Numa amostra de teste as razões produzem absurdo — "133% de quem abriu",
    # porque um mesmo evento gera calendário E página oficial. Taxa só aparece
    # quando há gente suficiente para que ela queira dizer alguma coisa.
    t_abre = t_acao = None
    if confiavel:
        if abertos and total:
            t_abre = "%.0f%% das visitas" % (100.0 * abertos / total)
        if acoes and abertos:
            t_acao = "%.0f%% de quem abriu" % (100.0 * acoes / abertos)

    funil = ('<div class="funil">%s%s%s</div>'
             % (degrau("Chegou", total, "visitas"),
                degrau("Abriu um evento", abertos, "aberturas", t_abre),
                degrau("Fez alguma coisa", acoes, "calendário, página oficial ou favorito", t_acao)))

    secoes = []
    for chave, titulo, sub in GRUPOS:
        doGrupo = [p for p in ps if p["grupo"] == chave]
        if not doGrupo:
            continue
        secoes.append(
            "<section><header><h2>%s</h2><p>%s</p></header>"
            '<div class="cartoes">%s</div></section>'
            % (esc(titulo), esc(sub),
               "".join(cartao(p, confiavel) for p in doGrupo)))

    if cat:
        base = ('<section><header><h2>Os denominadores</h2>'
                "<p>O que existe do outro lado. Toda taxa acima se lê contra estes "
                "números — e eles vêm da coleta, não da medição.</p></header>"
                '<div class="grade">%s</div></section>'
                % "".join(
                    '<div><span class="n">%s</span><span class="k">%s</span></div>' % (a, b)
                    for a, b in [
                        (f_n(cat["eventos"]), "atividades na janela"),
                        (f_n(cat["hoje"]), "acontecem hoje"),
                        (f_n(cat["unidades"]), "unidades"),
                        ("%.0f%%" % (100.0 * cat["gratis"] / cat["eventos"]), "são gratuitas"),
                        (f_n(cat["novidades"]), "entraram nos últimos dias"),
                        (f_n(cat["sorteaveis"]), "por sorteio"),
                    ]))
    else:
        base = ""

    if total and not confiavel:
        horas = (ctx or {}).get("horas") or 0
        aviso = (
            '<div class="aviso"><h3>Isto é um teste de fumaça, não uso</h3>'
            "<p>São <b>%s visitas</b> e <b>%s eventos</b>%s. Nesse tamanho, "
            "nenhuma porcentagem significa coisa alguma — duas pessoas a mais "
            "mudariam qualquer conclusão —, então as leituras derivadas estão "
            "suprimidas de propósito. Os números crus continuam à vista.</p>"
            "<p>O que este volume <em>prova</em> está na seção abaixo: que a "
            "medição chega inteira do outro lado. Volte quando houver algumas "
            "centenas de visitas ao longo de dias diferentes.</p></div>"
            % (f_n(total), f_n(sum(soma(g, e) for e in g)),
               (" numa única hora" if horas == 1 else " em %d horas" % horas)))
    elif total:
        aviso = ""
    else:
        aviso = (
            '<div class="aviso"><h3>Ainda sem dados de uso</h3>'
            "<p>Os cartões acima mostram a pergunta que cada evento foi feito "
            "para responder, e o que cada faixa de resposta vai significar. "
            "Para preencher, crie uma chave em Settings &rarr; API tokens no "
            "GoatCounter e grave em <code>.goatcounter-token</code> (o "
            "<code>.gitignore</code> já protege o arquivo), depois rode "
            "<code>python painel.py</code>. Ou exporte o CSV e rode "
            "<code>python painel.py --csv arquivo.csv</code>.</p>"
            "<p>Um painel que inventa número é pior do que um painel vazio, "
            "então nada aqui é estimado.</p></div>")

    return """<title>Painel do Acontece no SESC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&display=swap">
<style>%(css)s</style>
<div class="folha">
  <header class="capa">
    <span class="rotulo">Medição de uso &middot; sem cookie, sem identificar ninguém</span>
    <h1>O que a medição está dizendo</h1>
    <p>%(n_eventos)s eventos foram instrumentados para responder a perguntas
       específicas — não para encher gráfico. Cada cartão traz a pergunta, o
       número e o denominador contra o qual ele se lê.</p>
    <div class="meta">
      <span><b>Uso:</b> %(origem)s</span>
      <span><b>Catálogo:</b> %(coleta)s</span>
      <span><b>Painel:</b> acontecesesc.goatcounter.com</span>
    </div>
  </header>
  %(funil)s
  %(aviso)s
  %(confere)s
  %(secoes)s
  %(base)s
  <footer>
    <p>O GoatCounter não usa cookie nem identificador, então visitantes únicos
       são estimativa por período e eventos não se ligam a pessoas: vê-se
       quantas vezes algo aconteceu, nunca quem fez a sequência. Foi decisão de
       projeto — é o que dispensa banner de consentimento. Do campo de busca sai
       apenas o que o app reconheceu do próprio catálogo, e só quando a busca
       não achou nada.</p>
    <p>Gerado por <code>painel.py</code>. Rode de novo para atualizar.</p>
  </footer>
</div>""" % {
        "css": CSS, "funil": funil, "aviso": aviso,
        "confere": bloco_conferencia(g) if total else "",
        "n_eventos": f_n(len(eventos_do_codigo())) or "Os",
        "secoes": "".join(secoes), "base": base,
        "origem": esc(origem),
        "coleta": esc(cat["coleta"] + " · " + f_n(cat["eventos"]) + " atividades") if cat
                  else "sem dados/eventos.json",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", help="pasta do export de backup do GoatCounter (.jsonl)")
    p.add_argument("--csv", help="export de 'Individual pageviews' do GoatCounter")
    p.add_argument("--dias", type=int, default=90, help="janela a pedir na API")
    p.add_argument("--saida", default="painel.html")
    args = p.parse_args()

    caminhos, total, origem, ctx = {}, 0, "sem dados ainda", None
    if args.dir:
        caminhos, total, ctx = do_backup(args.dir)
        janela = ""
        if ctx.get("primeira"):
            janela = " · %s" % ctx["primeira"].replace("T", ", ") + "h UTC"
            if ctx["horas"] > 1:
                janela += " (%d horas)" % ctx["horas"]
        origem = "export de %s%s" % (
            os.path.basename(args.dir.rstrip("/\\")), janela)
    elif args.csv:
        caminhos, total = do_csv(args.csv)
        origem = "CSV de %s · %s acessos" % (os.path.basename(args.csv), f_n(total))
    else:
        chave = ler_chave()
        if chave:
            try:
                caminhos, total = da_api(chave, args.dias)
                origem = "API, últimos %d dias · %s visitas" % (args.dias, f_n(total))
            except Exception as e:
                print("Não consegui falar com a API (%s). Seguindo sem uso." % e)
        else:
            print("Sem chave e sem --csv: monto só a metade do catálogo.")

    cat = catalogo()
    html = montar(agrupar(caminhos), total, cat, origem, ctx)
    with open(os.path.join(RAIZ, args.saida), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("%s pronto · %s eventos de uso · %s caminhos"
          % (args.saida, f_n(total), f_n(len(caminhos))))


if __name__ == "__main__":
    main()
