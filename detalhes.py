#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Segunda passada: preços, datas de venda/inscrição, sessões exatas e sorteios.

Duas fontes, nessa ordem de preferência:

1. API DE BILHETERIA (JSON, estruturada) — para eventos com `idJava`:
       https://portal.sescsp.org.br/bilheteria/atividade.action?idAtividade=<idJava>
   Entrega por sessão:
       valorComerciario / valorMeia / valorInteira   → as três faixas
       dataInicialSessaoFmt                          → data e hora EXATAS
       dataInicialVendaOnlineFmt                     → abertura da venda on-line
       dataInicialVendaRedeFmt                       → abertura da venda presencial
       statusSessaoSesc, qtdeIngressosWeb/Rede, urlCompra, maxTicketSessao
   E no evento: classificacaoMinina, unidadePrincipal (endereço + lat/lng).

   Isso confirma a regra das 24h: para Rachel Reis, on-line 04/08 17h e
   presencial 05/08 17h. A regra não precisa ser suposta — vem no dado.

2. HTML DA PÁGINA — só para o que a bilheteria não cobre:
       .info_local                → "Inscrições: 7/8 às 14h a 12/8 · Sorteio: 13/8 às 15h"
       "Resultado do Sorteio:"    → lista de códigos contemplados

Uso:
    python detalhes.py                    # bilheteria em todos que têm idJava
    python detalhes.py --html             # + varredura de HTML (sorteios e inscrições)
    python detalhes.py --html-cats "Turismo Social" "Cursos e Oficinas"
    python detalhes.py --max 300
"""

import argparse
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

import coletor          # o carimbo de hora sai daqui, em horário de Brasília

BILHETERIA = "https://portal.sescsp.org.br/bilheteria/atividade.action?idAtividade=%s"
PAUSA = 0.25
TIMEOUT = 30

# Paciência mais curta que a do coletor, de propósito. Lá são ~70 URLs e 77
# segundos de espera por cada uma cabem folgados; aqui são ~3.000 e a passada
# já leva 70 minutos dos 180 do job. Com esta escada, uma falha custa no
# máximo 17 segundos. A política de QUAIS erros vale repetir, e de como ler o
# `Retry-After`, é a mesma e mora em `coletor` — eram 2 tentativas e um
# `except Exception` que engolia bug junto com portal fora.
TENTATIVAS = 4
ESPERAS = (2, 5, 10)

# Teto de espera acumulada na passada inteira. Sem ele a aritmética vira
# contra nós: 300 páginas tropeçando a 17s cada uma são 85 minutos de sono em
# cima dos 70 de trabalho, e o job morre no limite sem publicar nada. Gasto o
# orçamento, `buscar` para de insistir e passa a devolver None na primeira —
# a passada termina, incompleta e dizendo que está.
ORCAMENTO_ESPERA = 900

# O portal que não responde é perda, e aqui ela era calada duas vezes: o
# `None` de "não respondeu" é o mesmo `None` de "respondeu e não tem nada
# aqui", e os contadores do fim só sabiam somar acerto. O evento fica com o
# que já tinha — é o certo, melhor que derrubar a passada —, mas agora a
# perda tem nome, número e trava.
FALHAS = []
GASTO = [0.0]

# Acima disto a passada não é uma conferência, e carimbar `enriquecidoEm`
# viraria o app prometendo o que não fez. O piso absoluto protege as passadas
# curtas (`--max 30`), onde um décimo é um punhado de páginas.
FRACAO_FALHAS = 0.1
PISO_FALHAS = 25

UA = "agenda-sesc-prototipo/1.0 (agregador de programacao publica; uso pessoal)"

RE_TAG = re.compile(r"<[^>]+>")
RE_COMENTARIO = re.compile(r"<!--.*?-->", re.S)
# tags cujo CONTEÚDO precisa sumir: remover só as marcas deixaria o código solto
RE_INVISIVEL = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.S | re.I)
RE_ESPACO = re.compile(r"\s+")

# Trechos de cromo do portal que vazam quando o bloco tem div aninhada.
CORTES = ("Serviço Social do Comércio", "Escolha o evento", "Compartilhe:",
          "Adicionar à agenda", "Sesc São Paulo por aí")

MESES_TXT = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
             "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
             "outubro": 10, "novembro": 11, "dezembro": 12}
RE_DATA_EXT = re.compile(
    r"(\d{1,2})\s*[ºo°]?\s*de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|"
    r"agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+(\d{4}))?"
    r"(?:\s*,)?(?:\s*[àa]s\s*(\d{1,2})(?:h|:)(\d{2})?)?", re.I)
RE_INFO_LOCAL = re.compile(r'class="info_local"[^>]*>(.*?)</div>', re.S)
# O cabeçalho da lista de contemplados não é um só. "Resultado do Sorteio:"
# cobria o Turismo Social clássico; "Minas em Cena" publica sob um
# "CONTEMPLADOS" seco, logo depois de "Compartilhe:" — e o app dizia que a
# lista não tinha saído quando ela estava na tela.
RE_RESULTADO = re.compile(
    r"(?:resultado\s+d[oa]\s+sorteio"
    r"|lista\s+d[eo]s?\s+contemplad[oa]s"
    r"|contemplad[oa]s"
    r"|sortead[oa]s)\s*:?", re.I)
RE_CODIGO = re.compile(r"[A-Z]{6,12}")
# O enfeite entre o cabeçalho e a lista: "(em ordem alfabética):". Limitado a
# 60 caracteres de propósito — um "(" solto no meio da página não pode engolir
# a lista que vem depois.
RE_ENFEITE = re.compile(r"^\s*\([^)]{0,60}\)\s*:?\s*")
RE_DATA_HORA = re.compile(
    r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?:\s*,)?(?:\s*[àa]s\s*(\d{1,2})(?:h|:)(\d{2})?)?")


def limpar(fragmento):
    sem = RE_INVISIVEL.sub(" ", fragmento)
    sem = RE_COMENTARIO.sub(" ", sem)
    return RE_ESPACO.sub(" ", html.unescape(RE_TAG.sub(" ", sem))).strip()


def cortar_cromo(texto):
    """Descarta o rodapé do portal que às vezes vem junto do bloco."""
    for marca in CORTES:
        i = texto.find(marca)
        if i > 0:
            texto = texto[:i]
    return texto.strip(" ·-")


def buscar(url, json_esperado=False, tentativa=1):
    """A página, ou `None` se o portal não respondeu.

    Devolver `None` é contrato: `sorteios.py` conta com ele, e um evento sem
    resposta tem de ficar com o que já tinha em vez de derrubar a passada
    inteira. O que não era contrato era a perda sair sem registro.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json" if json_esperado else "text/html",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            bruto = r.read().decode("utf-8", "replace")
        return json.loads(bruto) if json_esperado else bruto
    except coletor.FALHA_DE_REDE as e:
        acabou = (tentativa >= TENTATIVAS or not coletor.repetivel(e)
                  or GASTO[0] >= ORCAMENTO_ESPERA)
        if acabou:
            FALHAS.append(url)
            return None
        espera = coletor.espera_de(e, tentativa, ESPERAS)
        GASTO[0] += espera
        time.sleep(espera)
        return buscar(url, json_esperado, tentativa + 1)


# ---------------------------------------------------------------- bilheteria
def faixas(sessao):
    """As três faixas do Sesc, só as que existem."""
    mapa = [("Credencial Plena", "valorComerciario"),
            ("Meia entrada", "valorMeia"),
            ("Inteira", "valorInteira")]
    out = []
    for rotulo, campo in mapa:
        v = sessao.get(campo)
        if isinstance(v, (int, float)) and v > 0:
            out.append({"label": rotulo, "valor": round(float(v), 2)})
    if sessao.get("gratuito") and not out:
        out.append({"label": "Gratuito", "valor": 0.0})
    return out


def da_bilheteria(idjava):
    d = buscar(BILHETERIA % idjava, json_esperado=True)
    if not isinstance(d, dict) or not d.get("sessoes"):
        return None

    sessoes = [s for s in d["sessoes"] if isinstance(s, dict)]
    if not sessoes:
        return None

    out = {"idAtividade": d.get("idAtividade")}

    precos = faixas(sessoes[0])
    if precos:
        out["precos"] = precos

    cls = d.get("classificacaoMinina")
    if cls:
        out["classificacao"] = cls

    uni = d.get("unidadePrincipal") or {}
    if uni.get("endereco"):
        out["endereco"] = uni["endereco"]
    if uni.get("lat") and uni.get("lng"):
        out["geo"] = [uni["lat"], uni["lng"]]

    prim = sessoes[0]
    if prim.get("dataInicialVendaOnlineFmt"):
        out["vendaOnline"] = prim["dataInicialVendaOnlineFmt"]
    if prim.get("dataInicialVendaRedeFmt"):
        out["vendaPresencial"] = prim["dataInicialVendaRedeFmt"]
    if prim.get("dataFinalVendaOnlineFmt"):
        out["vendaOnlineFim"] = prim["dataFinalVendaOnlineFmt"]
    if prim.get("urlCompra"):
        out["urlCompra"] = html.unescape(prim["urlCompra"])
    if prim.get("maxTicketSessao"):
        out["maxPorPessoa"] = prim["maxTicketSessao"]

    # sessões exatas: resolve o buraco da listagem, que só dava o intervalo
    ses = []
    for s in sessoes:
        ini = s.get("dataInicialSessaoFmt")
        if not ini:
            continue
        item = {
            "quando": ini,
            "status": s.get("statusSessaoSesc") or s.get("dscStatusEvento"),
            "web": s.get("qtdeIngressosWeb"),
            "rede": s.get("qtdeIngressosRede"),
        }
        # Hora de término. Na maioria das atividades a bilheteria repete a
        # hora de início em `dataFinalSessaoFmt` — aí não há término
        # publicado e o app aplica a regra de duração. Só guardamos quando o
        # campo diz algo de verdade.
        fim = s.get("dataFinalSessaoFmt")
        if fim and fim > ini:
            item["fim"] = fim
        ses.append(item)
    if ses:
        ses.sort(key=lambda x: x["quando"])
        out["sessoes"] = ses

    return out


# ---------------------------------------------------------------------- HTML
def data_iso(m, ano_ref):
    dia, mes, ano, hh, mm = m.groups()
    try:
        dia, mes = int(dia), int(mes)
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            return None
    except (TypeError, ValueError):
        return None
    a = (int(ano) + 2000 if ano and int(ano) < 100 else int(ano)) if ano else ano_ref
    s = "%04d-%02d-%02d" % (a, mes, dia)
    if hh:
        s += "T%02d:%02d" % (int(hh), int(mm or 0))
    return s


def diferenca_dias(a, b):
    from datetime import date as _d
    try:
        return abs((_d.fromisoformat(b[:10]) - _d.fromisoformat(a[:10])).days)
    except ValueError:
        return 0


def primeira_data(trecho, ano_ref):
    """Acha a primeira data do trecho, seja 7/8 ou '7 de agosto'.

    Devolve (iso, posicao_final) — a posição serve para procurar o fim do
    período logo em seguida.
    """
    cands = []
    m1 = RE_DATA_HORA.search(trecho)
    if m1 and m1.group(1) and m1.group(2):
        cands.append((m1.start(), "num", m1))
    m2 = RE_DATA_EXT.search(trecho)
    if m2:
        cands.append((m2.start(), "ext", m2))
    if not cands:
        return None, 0
    cands.sort(key=lambda c: c[0])
    _, tipo, m = cands[0]

    if tipo == "num":
        return data_iso(m, ano_ref), m.end()

    try:
        dia = int(m.group(1))
        mes = MESES_TXT[m.group(2).lower().replace("ç", "c")]
    except (KeyError, ValueError, TypeError):
        return None, 0
    ano = int(m.group(3)) if m.group(3) else ano_ref
    s = "%04d-%02d-%02d" % (ano, mes, dia)
    if m.group(4):
        s += "T%02d:%02d" % (int(m.group(4)), int(m.group(5) or 0))
    return s, m.end()


def datas_do_texto(texto, ano_ref, dia_evento):
    """Extrai inscrição / sorteio / pagamento de um trecho em prosa.

    Separado de `da_pagina` de propósito: o texto bruto fica guardado no
    eventos.json, então dá para reprocessar as datas sem baixar nada de novo
    (é o que `reparse.py` faz).
    """
    out = {}
    texto = cortar_cromo(texto or "")
    if texto:
        out["texto"] = texto[:400]

        # "de 2 a 9/6/2026" — o primeiro dia vem sem mês, herda do segundo
        # "de 2 a 9/6/2026" e também "dias 27 e 28/8"
        RE_RANGE_CURTO = re.compile(
            r"(\d{1,2})\s*(?:e|a|at[ée]|-)\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")

        # onde um rótulo termina: no começo do próximo
        RE_PROX_ROTULO = re.compile(
            r"\b(Sorteio|Resultado|Divulga[çc][ãa]o|Pagamento|Confirma[çc][ãa]o|"
            r"Hospedagem|Pens[ãa]o|Observa[çc]|Cronograma)\b", re.I)

        def achar(rot, chave, janela=260):
            if chave in out:      # o rótulo mais específico já resolveu
                return
            m = re.search(rot, texto, re.I)
            if not m:
                return
            trecho = texto[m.end():m.end() + janela]
            # A data pode vir bem depois do rótulo ("Inscrições pelo Portal
            # … de 19/06, às 14h, até 24/06"), então a janela é larga. Para
            # não pegar a data do rótulo seguinte, ela é cortada nele.
            corte = RE_PROX_ROTULO.search(trecho)
            if corte and corte.start() > 0:
                trecho = trecho[:corte.start()]

            # "de 6 a 10/8", "a partir das 14h do dia 08 até 13/07": o
            # intervalo pode vir depois de qualquer preâmbulo, então é busca
            # — mas só vale se aparecer ANTES da primeira data solta, senão
            # estaríamos capturando um intervalo de outro rótulo.
            curto = RE_RANGE_CURTO.search(trecho[:80])
            solta, _ = primeira_data(trecho, ano_ref)
            if curto and solta:
                pos_solta = min(
                    [m2.start() for m2 in [RE_DATA_HORA.search(trecho), RE_DATA_EXT.search(trecho)]
                     if m2] or [10 ** 6])
                if curto.start() > pos_solta:
                    curto = None
            if curto:
                d1, d2, mes, ano = curto.groups()
                a = (int(ano) + 2000 if ano and int(ano) < 100 else int(ano)) if ano else ano_ref
                try:
                    out[chave] = "%04d-%02d-%02d" % (a, int(mes), int(d1))
                    out[chave + "Fim"] = "%04d-%02d-%02d" % (a, int(mes), int(d2))
                    return
                except ValueError:
                    pass

            iso, fim = primeira_data(trecho, ano_ref)
            if not iso:
                return
            out[chave] = iso
            # "… até o dia 09/08", "… a 12/8", "… até 9 de agosto"
            # "de 19/06, às 14h, até 24/06": a vírgula depois da hora fica
            # entre a data e o conector, então ela também precisa ser pulada
            depois = trecho[fim:fim + 44];
            m2 = re.match(r"[\s,]*(?:e|a|at[ée]|-)\s*(?:o\s+dia\s+)?", depois)
            if m2:
                iso2, _ = primeira_data(depois[m2.end():], ano_ref)
                if iso2 and iso2[:10] < iso[:10]:
                    # a página às vezes digita o ano errado no fim do período
                    # ("de 29/07 às 14h até 14/08/2025"); alinhar ao início
                    tentativa = iso[:4] + iso2[4:]
                    if tentativa[:10] >= iso[:10]:
                        iso2 = tentativa
                if iso2 and iso2[:10] >= iso[:10]:
                    out[chave + "Fim"] = iso2

        # do rótulo mais específico para o mais genérico: "Inscrição para
        # sorteio de 2 a 9/6" não pode ser lido como a data do resultado
        achar(r"Divulga[çc][ãa]o\s+dos?\s+(?:sortead[oa]s|contemplad[oa]s)\s*:?(?:\s*a\s+partir\s+d[eo]s?)?(?:\s*dia)?", "sorteio")
        achar(r"Resultado[^.]{0,40}?no\s+dia", "sorteio")
        achar(r"Resultado\s*(?:do\s+sorteio)?\s*:", "sorteio")
        # "Inscrição para o sorteio:" contém "sorteio:" — sem o guarda, o
        # período de INSCRIÇÃO era lido como data do sorteio
        achar(r"(?<!para )(?<!para o )Sorteio\s*:", "sorteio")
        # "Sorteio e divulgação do resultado em 25/06" — sem dois-pontos
        achar(r"(?<!para )(?<!para o )Sorteio[^.]{0,40}?\bem\b", "sorteio")
        achar(r"Divulga[çc][ãa]o[^.]{0,40}?\bem\b", "sorteio")
        # "Inscrição" (ã) e "Inscrições" (õ) precisam dos dois acentos: com
        # [õo] apenas, o singular nunca casava e a data se perdia
        INSCR = r"Inscri[çc](?:[ãa]o|[õo]es)"
        # "Inscrição para sorteio" e "Inscrição para o sorteio"
        achar(INSCR + r"\s+para\s+(?:o\s+)?sorteio\s*:?\s*(?:de|a\s+partir\s+d[eo]s?)?", "inscricao")
        achar(r"Pr[ée]-?" + INSCR + r"[^.]{0,30}?(?:a partir das?\s*\d{1,2}h\s*)?(?:de)?", "inscricao")
        achar(INSCR + r"\s*:?", "inscricao")
        achar(r"1[ªa]?\s*chamada\s*(?:de\s*)?pagamento\s*:?", "pagamento")
        achar(r"Pagamentos?\s*:?\s*(?:De)?", "pagamento")

        # Inscrição não acontece depois do evento: quando dá, é virada de ano
        # (a página escreve só "19/06" e o evento é em janeiro). Exijo folga
        # grande — perto da data é inconsistência da página, não outro ano, e
        # recuar um ano ali criaria um erro maior do que o que corrige.
        for k in ("inscricao", "sorteio"):
            if k in out and dia_evento and out[k][:10] > dia_evento:
                if diferenca_dias(dia_evento, out[k][:10]) > 60:
                    out[k] = str(int(out[k][:4]) - 1) + out[k][4:]

        out["temSorteio"] = bool(re.search(r"sorteio|sortead[oa]", texto, re.I))

    return out


RE_PAR_PRECO = re.compile(
    r'<span class="valor">(.*?)</span>\s*<span class="label">(.*?)</span>', re.S)


def precos_do_html(pagina):
    """Preços na página, para quem não passa pela bilheteria.

    Turismo Social não tem `idJava` — os passeios não são vendidos como
    ingresso —, então o valor só existe no HTML. Sem isto, 95 passeios
    apareciam sem preço nenhum.
    """
    out, vistos = [], set()
    for valor, label in RE_PAR_PRECO.findall(pagina):
        v, rot = limpar(valor), limpar(label)
        if "R$" not in v or not rot:
            continue
        num = re.sub(r"[^\d,]", "", v).replace(",", ".")
        try:
            n = round(float(num), 2)
        except ValueError:
            continue
        if (rot, n) in vistos:
            continue
        vistos.add((rot, n))
        out.append({"label": rot, "valor": n})
    return out


RE_DESC = re.compile(
    r'class="(?:evento--descricao|conteudo-evento|texto-evento|entry-content)"[^>]*>(.*?)</div>',
    re.S | re.I)
RE_PARAGRAFO = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


# Onde a descrição começa e onde termina, no texto corrido da página.
# "Compartilhe:" fecha o cabeçalho do evento em todas as páginas conferidas.
RE_ABERTURA = re.compile(r"Compartilhe\s*:?", re.I)
FIM_DESC = ("Ficha técnica", "FICHA TÉCNICA", "INSCRIÇÕES", "Inscrições:",
            "Inscrição para sorteio", "Classificação", "Serviço Social do Comércio",
            "Programação relacionada", "Você também pode gostar",
            "Utilizamos cookies", "Resultado do Sorteio", "Sesc São Paulo por aí")


def extrair_descricao(pagina, limite=700):
    """O texto de apresentação do evento.

    O portal não marca a descrição com uma classe própria, então o recorte é
    pelo texto: começa depois de "Compartilhe:" (que encerra o cabeçalho) e
    termina no primeiro bloco de serviço (ficha técnica, inscrições, rodapé).
    """
    corpo = limpar(pagina)

    m = RE_ABERTURA.search(corpo)
    if m:
        corpo = corpo[m.end():]
    else:
        # sem a âncora, cai nos parágrafos e descarta o que é serviço
        partes = []
        for p in RE_PARAGRAFO.findall(pagina):
            t = limpar(p)
            if len(t) < 60 or any(mk in t for mk in CORTES):
                continue
            partes.append(t)
            if sum(len(x) for x in partes) > limite:
                break
        corpo = " ".join(partes)

    for marca in FIM_DESC:
        i = corpo.find(marca)
        if i > 0:
            corpo = corpo[:i]

    corpo = limpar_servico(corpo)
    if len(corpo) < 60:
        return None
    if len(corpo) > limite:
        corpo = corpo[:limite].rsplit(" ", 1)[0] + "…"
    return corpo


# Frases de serviço que não são descrição: data, hora, local, preço, sorteio.
RE_SERVICO = re.compile(
    r"\d{1,2}/\d{1,2}"                       # 12/09
    r"|\d{1,2}h\d{0,2}\b"                    # 20h, 20h30
    r"|\b\d{1,2}\s*de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|"
    r"agosto|setembro|outubro|novembro|dezembro)"
    r"|R\$"
    r"|\b(Local|Sa[íi]da|Retorno|Embarque|Dura[çc][ãa]o|Hor[áa]rio|Vagas|"
    r"Ingressos?|Inscri[çc][õoã][eo]s?|Sorteio|Sortead[oa]s|Pagamento|"
    r"Credencial|Classifica[çc][ãa]o|Bilheteria|Portal Sesc)\b",
    re.I)


def limpar_servico(texto):
    """Tira as frases de serviço, deixando só a apresentação do evento.

    O pedido é que a descrição não repita data, local, horário e sorteio —
    isso já aparece nos campos próprios do app, e em prosa só atrapalha.
    """
    frases = re.split(r"(?<=[.!?])\s+", texto)
    ficam = [f for f in frases if f.strip() and not RE_SERVICO.search(f)]
    if not ficam:
        return ""
    return " ".join(ficam).strip(" ·-–—")


# ------------------------------------------------- duração e regra de entrada
# "Duração: 50 minutos" fica no cabeçalho da página, não na API. É o segundo
# degrau da regra de término do .ics (fim publicado > início + duração >
# início + 30 min); sem ele, quase toda atividade caía no bloco de 30 minutos.
RE_DURACAO = re.compile(
    r"Dura[çc][ãa]o\s*:?\s*(?:(\d{1,2})\s*h(?:oras?)?\s*(?:e\s*)?(\d{1,2})?\s*(?:min\w*)?"
    r"|(\d{1,3})\s*(?:min\w*|')" r")", re.I)


def duracao_minutos(pagina):
    m = RE_DURACAO.search(limpar(pagina))
    if not m:
        return None
    if m.group(3):
        v = int(m.group(3))
    else:
        v = int(m.group(1)) * 60 + int(m.group(2) or 0)
    # 5 minutos a 12 horas: fora disso é outra coisa que casou por acaso
    return v if 5 <= v <= 720 else None


# Parte das inscrições não tem data: tem REGRA, em prosa, e ela se repete todo
# mês. O caso que motivou isto:
#
#   "As vagas disponíveis são liberadas prioritariamente para pessoas
#    portadoras de Credencial Plena na 1ª e na 3ª quinta feira de cada mês a
#    partir das 18h. Caso estas vagas não sejam preenchidas, serão
#    disponibilizadas para o público em geral na 2ª e na 4ª quarta feira de
#    cada mês a partir das 14h."
#
# São duas datas diferentes para duas pessoas diferentes, e nenhuma delas
# aparece em lugar nenhum como data. Quem tem Credencial Plena que se
# programar para a quinta; quem não tem, para a quarta seguinte.
DOW = {"domingo": 0, "segunda": 1, "terca": 2, "terça": 2, "quarta": 3,
       "quinta": 4, "sexta": 5, "sabado": 6, "sábado": 6}

# "de cada mês", "do mês", "no mês" — a mesma regra escrita de três jeitos.
# Os ordinais ficam em algarismo de propósito: por extenso, "segunda", "quarta"
# e "quinta" são também dias da semana, e ler "primeira e segunda quinta-feira"
# como ordinal daria uma data errada — pior do que não dar data nenhuma.
RE_DIA_DO_MES = re.compile(
    r"(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)"
    r"[\s\-]*(?:feira)?[\s,]*(?:de|do|no)\s+(?:cada\s+)?m[êe]s", re.I)
# A SEGUNDA janela quase nunca repete o "de cada mês" — o texto padrão diz
# "…na 1ª e na 3ª quinta-feira de cada mês a partir das 18h. Caso estas vagas
# não sejam preenchidas, serão disponibilizadas para o público em geral na 2ª
# e na 4ª quarta-feira a partir das 14h." Exigindo o sufixo em toda ocorrência,
# só a primeira era lida, e a janela do público geral simplesmente não existia:
# medido, os 5 eventos com regra na base tinham 1 janela cada, todas "plena".
#
# Aceitar o sufixo opcional em qualquer lugar do texto abriria a porta para
# "a 1ª sexta-feira do festival, a partir das 20h" virar regra de inscrição.
# Por isso a forma frouxa só vale COMO COMPANHIA: um texto precisa ter ao
# menos uma ocorrência com o sufixo para que as demais contem.
RE_DIA_SEM_MES = re.compile(
    r"(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)"
    r"[\s\-]*(?:feira)?", re.I)
RE_ORDINAL = re.compile(r"(\d)\s*[ªa°ºo]")
RE_HORA_REGRA = re.compile(
    r"a\s+partir\s+d[ao]s?\s+(\d{1,2})\s*(?:h|:)\s*(\d{2})?", re.I)
RE_PLENA = re.compile(r"credencial\s+plena", re.I)
RE_GERAL = re.compile(r"p[úu]blico\s+(?:em\s+)?geral|demais\s+p[úu]blicos", re.I)


def regras_de_inscricao(pagina):
    """Janelas recorrentes de inscrição, uma por público.

    Devolve [{quem, semanas, dow, hora}], onde `dow` segue a convenção do
    JavaScript (0 = domingo) porque quem consome é o app.
    """
    corpo = limpar(pagina)
    saida = []
    # Só quando o texto já se identificou como regra recorrente é que as
    # ocorrências sem "de cada mês" passam a valer (ver RE_DIA_SEM_MES).
    padrao = RE_DIA_SEM_MES if RE_DIA_DO_MES.search(corpo) else RE_DIA_DO_MES
    for m in padrao.finditer(corpo):
        antes = corpo[max(0, m.start() - 110):m.start()]
        semanas = sorted({int(x) for x in RE_ORDINAL.findall(antes) if 1 <= int(x) <= 5})
        if not semanas:
            continue
        h = RE_HORA_REGRA.search(corpo[m.end():m.end() + 90])
        if not h:
            continue
        dia = DOW.get(m.group(1).lower())
        if dia is None:
            continue

        # De quem é esta janela. O público tanto vem antes ("para portadores
        # de Credencial Plena na 1ª e na 3ª quinta...") quanto depois ("...a
        # partir das 18h, para quem possui credencial plena"). Olhar só para
        # trás fazia a segunda forma virar "todos".
        antes_ctx = corpo[max(0, m.start() - 320):m.start()]
        depois_ctx = corpo[m.end():m.end() + 160]
        quem = "todos"
        p, g = RE_PLENA.search(antes_ctx), RE_GERAL.search(antes_ctx)
        if p and (not g or p.start() > g.start()):
            quem = "plena"
        elif g:
            quem = "geral"
        else:
            p2, g2 = RE_PLENA.search(depois_ctx), RE_GERAL.search(depois_ctx)
            if p2 and (not g2 or p2.start() < g2.start()):
                quem = "plena"
            elif g2:
                quem = "geral"

        saida.append({
            "quem": quem,
            "semanas": semanas,
            "dow": dia,
            "hora": "%02d:%02d" % (int(h.group(1)), int(h.group(2) or 0)),
        })
    # sem público distinto, uma regra só basta
    unicas, vistas = [], set()
    for r in saida:
        chave = (r["quem"], tuple(r["semanas"]), r["dow"], r["hora"])
        if chave in vistas:
            continue
        vistas.add(chave)
        unicas.append(r)
    return unicas or None


def sorteados_do_corpo(corpo):
    """Códigos contemplados, a partir de qualquer um dos cabeçalhos.

    Percorre TODAS as ocorrências e fica com a maior lista: as palavras
    "sorteados" e "contemplados" também aparecem em prosa ("os sorteados
    poderão efetivar o pagamento"), e ali não vem código nenhum. A trava é
    exigir que o código apareça nos dois primeiros tokens depois do cabeçalho
    -- em prosa isso nunca acontece.

    O parêntese entre o cabeçalho e a lista é descartado antes da contagem:
    "Resultado do sorteio **(em ordem alfabética):** DOKFCHWU EKEFDFKT…" põe
    três tokens de enfeite na frente, a trava desistia no terceiro e a lista
    inteira ficava para trás. Eram 3 dos 13 sorteios já vencidos em 09/09,
    com 28, 13 e 13 códigos publicados na página, que o app dava como
    pendentes — e continuaria dando para sempre, porque a página não muda
    mais.

    Descartar só o PARÊNTESE, e não "os primeiros N tokens", é o que
    preserva a trava: prosa depois de "sorteados" não começa com "(". Alargar
    a janela de tokens traria de volta o falso positivo que a trava existe
    para evitar -- "o resultado será divulgado na CENTRAL DE ATENDIMENTO"
    viraria uma lista de dois contemplados.
    """
    melhor = []
    for m in RE_RESULTADO.finditer(corpo):
        codigos = []
        resto = RE_ENFEITE.sub("", corpo[m.end():m.end() + 4000], count=1)
        for k, tok in enumerate(resto.split()):
            t = tok.strip(".,;:()")
            if RE_CODIGO.fullmatch(t):
                codigos.append(t)
            elif codigos or k >= 2:
                break
        if len(codigos) > len(melhor):
            melhor = codigos
    return melhor


def da_pagina(pagina, ano_ref, dia_evento):
    """Inscrição/sorteio no texto e os códigos contemplados."""
    blocos = [limpar(b) for b in RE_INFO_LOCAL.findall(pagina)]
    texto = " · ".join([b for b in blocos if b])

    # Algumas páginas põem tudo no corpo, não no info_local. Além de
    # "INSCRIÇÕES", o Turismo Social usa "Cronograma:" seguido de
    # "Inscrição: … Sorteio: … Pagamento: …".
    # A captura inclui o rótulo: sem ele o texto começaria em ":" e nenhum
    # padrão casaria depois.
    if not re.search(r"Inscri[çc]|Sorteio|Venda", texto, re.I):
        corpo = limpar(pagina)
        m = re.search(r"((?:Cronograma|INSCRI[ÇC][ÕO]ES|Inscri[çc][ãa]o)\s*:?.{0,600})",
                      corpo, re.I)
        if m:
            texto = (texto + " · " if texto else "") + m.group(1).strip()

    out = datas_do_texto(texto, ano_ref, dia_evento)

    corpo = limpar(pagina)
    codigos = sorteados_do_corpo(corpo)
    if codigos:
        out["sorteados"] = codigos

    # `out` É o dicionário de inscrição do evento (main faz
    # `ev["inscricao"] = info`), então as regras entram aqui direto. A duração
    # não: ela é do evento, e main a busca à parte.
    regras = regras_de_inscricao(pagina)
    if regras:
        out["regras"] = regras

    return out or None


# --------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dados", default=os.path.join("dados", "eventos.json"))
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--html", action="store_true", help="varrer HTML de todos")
    p.add_argument("--faltantes", action="store_true",
                   help="só os que ainda não têm descrição — enriquecimento incremental")
    p.add_argument("--html-cats", nargs="*", default=[coletor.CAT_TURISMO],
                   help="categorias que também levam varredura de HTML")
    args = p.parse_args()

    with open(args.dados, encoding="utf-8") as f:
        d = json.load(f)
    eventos = d["eventos"]
    if args.max:
        eventos = eventos[:args.max]

    # `--faltantes` é sobre DESCRIÇÃO, e só. Aplicado à lista inteira, ele
    # também tirava da fila da bilheteria todo evento que já tem `desc` — ou
    # seja, quase todos —, congelando preço, sessões e datas de venda na
    # última passada completa, com mensagem final de sucesso. O HANDOFF §7
    # oferece a opção como atalho dos 70 minutos; quem a usasse pagaria caro.
    com_java = [e for e in eventos if e.get("idJava")]
    if args.faltantes:
        eventos = [e for e in eventos if not e.get("desc")]
    quer_html = [e for e in eventos
                 if args.html or e.get("cat") in set(args.html_cats)]

    print("%d eventos · %d com bilheteria · %d para varrer HTML"
          % (len(eventos), len(com_java), len(quer_html)))

    inicio = time.time()
    n_preco = n_sessao = n_venda = n_insc = n_sorteio = n_desc = n_dur = 0

    for i, ev in enumerate(com_java, 1):
        b = da_bilheteria(ev["idJava"])
        if b:
            if b.get("precos"):
                ev["precos"] = b["precos"]; n_preco += 1
            if b.get("sessoes"):
                ev["sessoes"] = b["sessoes"]; n_sessao += 1
                # datas exatas substituem a inferência dia-a-dia
                ev["dias"] = sorted({s["quando"][:10] for s in b["sessoes"]})
            for k in ("vendaOnline", "vendaPresencial", "vendaOnlineFim",
                      "urlCompra", "maxPorPessoa", "classificacao", "endereco", "geo"):
                if b.get(k):
                    ev[k] = b[k]
            if b.get("vendaPresencial") or b.get("vendaOnline"):
                n_venda += 1
        if i % 50 == 0 or i == len(com_java):
            print("  bilheteria %4d/%d · preços=%d sessões=%d vendas=%d"
                  % (i, len(com_java), n_preco, n_sessao, n_venda))
        time.sleep(PAUSA)

    for i, ev in enumerate(quer_html, 1):
        if not ev.get("link"):
            continue
        pagina = buscar(ev["link"])
        if not pagina:
            continue
        desc = extrair_descricao(pagina)
        if desc:
            ev["desc"] = desc
            n_desc += 1

        if not ev.get("precos"):          # bilheteria tem prioridade
            p = precos_do_html(pagina)
            if p:
                ev["precos"] = p
                n_preco += 1

        # o parâmetro é o ÚLTIMO dia do evento: comparar com o início faria a
        # correção de ano disparar em temporada longa, cuja inscrição abre
        # meses depois do primeiro encontro
        dur = duracao_minutos(pagina)
        if dur:
            ev["duracaoMin"] = dur
            n_dur += 1

        info = da_pagina(pagina, int((ev.get("inicio") or "2026")[:4]),
                         ev.get("fim") or ev.get("inicio"))
        if info:
            if info.get("sorteados"):
                ev["sorteados"] = info.pop("sorteados"); n_sorteio += 1
            if info:
                ev["inscricao"] = info; n_insc += 1
        if i % 100 == 0 or i == len(quer_html):
            print("  html %4d/%d · descrições=%d inscrições=%d sorteios=%d durações=%d"
                  % (i, len(quer_html), n_desc, n_insc, n_sorteio, n_dur))
            # Gravar pelo caminho: uma exceção perto do fim já custou meia
            # hora de varredura inteira uma vez.
            d["enriquecidoEm"] = coletor.agora_br().isoformat(timespec="seconds")
            with open(args.dados, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        time.sleep(PAUSA)

    # `enriquecidoEm` é o que o app mostra como "última conferência", e isso é
    # uma afirmação. Metade das páginas sem resposta e o carimbo mesmo assim é
    # o app prometendo o que não fez — e é exatamente assim que um resultado
    # de sorteio "não sai" para quem está esperando com o código na mão.
    tentadas = len(com_java) + sum(1 for e in quer_html if e.get("link"))
    if FALHAS:
        print("::warning::%d de %d páginas não responderam depois de %d "
              "tentativas. Esses eventos ficaram com o preço, a descrição e o "
              "sorteio da passada anterior."
              % (len(FALHAS), tentadas, TENTATIVAS))
        if GASTO[0] >= ORCAMENTO_ESPERA:
            print("::warning::Orçamento de espera (%ds) esgotado no caminho: "
                  "daí em diante a passada deixou de insistir, para não levar "
                  "o job inteiro esperando." % ORCAMENTO_ESPERA)
        teto = max(PISO_FALHAS, int(tentadas * FRACAO_FALHAS))
        if len(FALHAS) > teto:
            raise SystemExit(
                "ABORTADO: %d de %d páginas não responderam (teto: %d). Esta "
                "passada não é uma conferência, e carimbá-la como tal faria o "
                "app afirmar preço e sorteio que ninguém leu hoje. Nada foi "
                "publicado — o site continua com a base anterior."
                % (len(FALHAS), tentadas, teto))

    d["enriquecidoEm"] = coletor.agora_br().isoformat(timespec="seconds")
    with open(args.dados, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))

    print("\nPronto em %.0fs · %.0f KB · preços=%d sessões=%d vendas=%d descrições=%d inscrições=%d sorteios=%d · sem resposta=%d"
          % (time.time() - inicio, os.path.getsize(args.dados) / 1024,
             n_preco, n_sessao, n_venda, n_desc, n_insc, n_sorteio, len(FALHAS)))


if __name__ == "__main__":
    main()
