#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coletor da programação do Sesc São Paulo.

Fonte: API REST pública do portal (WordPress).
  GET /wp-json/wp/v1/unidades-atividades
      -> lista de unidades, com a região em `description` (capital|interior|litoral)
  GET /wp-json/wp/v1/atividades/filter?data_inicial=&data_final=&ppp=&page=
      -> atividades no intervalo; envelope {editorial, atividade[], total{value}}

Estratégia: uma consulta por DIA. É o único jeito de saber em quais dias cada
atividade acontece — o endpoint devolve `dataPrimeiraSessao`/`dataUltimaSessao`
do evento inteiro, não da sessão daquele dia.

Limitações conhecidas da fonte (documentadas, não contornadas aqui):
  - não há nota da crítica nem avaliação de público;
  - não há data de abertura/encerramento de inscrição nem dados de sorteio
    (isso vive na página de cada atividade, exigiria uma requisição por evento);
  - para temporadas longas, o horário de cada sessão específica não vem na
    listagem; usamos o horário da primeira sessão como representativo.

Uso:
    python coletor.py                      # 30 dias a partir de hoje
    python coletor.py --dias 14
    python coletor.py --de 2026-08-11 --ate 2026-09-10
    python coletor.py --regioes capital litoral
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# O robô roda em UTC (GitHub Actions) e quem lê está em São Paulo. Sem fixar o
# fuso, a hora da coleta aparecia três horas adiantada no app e uma execução
# de madrugada carimbava o dia seguinte. O Brasil não usa mais horário de
# verão desde 2019, então o desvio é constante.
FUSO_BR = timezone(timedelta(hours=-3))


def agora_br():
    return datetime.now(FUSO_BR)

BASE = "https://www.sescsp.org.br"
API_UNIDADES = BASE + "/wp-json/wp/v1/unidades-atividades"
API_ATIVIDADES = BASE + "/wp-json/wp/v1/atividades/filter"

PPP = 300
PAUSA = 0.35          # segundos entre requisições — o portal não é nosso
TIMEOUT = 30

# Paciência com o portal. Eram 3 tentativas esperando 2s e 4s: a rodada
# inteira desistia em 6 segundos. Foi assim que as duas coletas de 31/10/2026
# morreram — a PRIMEIRA requisição, a das unidades, falhou as três vezes e o
# passo terminou antes de coletar um único dia, com a fonte íntegra na manhã
# seguinte. O portal cai e volta; a coleta roda duas vezes por dia e tem 180
# minutos de folga no job. Esperar um minuto é barato, perder o dia não é.
TENTATIVAS = 6
ESPERAS = (2, 5, 10, 20, 40)   # ~77s de paciência somada, por URL

# Erro de servidor e aperto de banda passam sozinhos; 404 e 400 não. Repetir
# os segundos gasta 77 segundos para chegar na mesma resposta e ainda embaça o
# diagnóstico: "falhou 6 vezes" se lê como instabilidade quando a URL mudou.
# 403 fica na lista porque é com ele que o WAF do portal barra tráfego que
# achou suspeito, e isso passa.
REPETIVEIS = (403, 408, 425, 429, 500, 502, 503, 504)
ESPERA_MAXIMA = 120           # teto para um Retry-After que o portal mande

# O que "o portal não respondeu" significa em exceções. `URLError` é a mãe de
# `HTTPError`, então cobre as duas; `JSONDecodeError` entra porque a página de
# manutenção do WAF chega com HTTP 200 e HTML dentro.
FALHA_DE_REDE = (urllib.error.URLError, TimeoutError, json.JSONDecodeError)

# Quantos dias da janela a rodada pode perder e ainda ser uma rodada. Um dia
# é ruído do portal, e a varredura de segurança adiante recupera o evento
# pelas datas do próprio registro. Um décimo da janela é outra coleta, e
# publicá-la por boa troca a base de ontem por um recorte pior.
FRACAO_DIAS_FALHOS = 0.1

UA = "agenda-sesc-prototipo/1.0 (coletor de programacao publica; uso pessoal)"

REGIOES_VALIDAS = ("capital", "interior", "litoral")


def repetivel(e):
    """Se vale esperar e pedir de novo, ou se a resposta não vai mudar."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in REPETIVEIS
    # Rede e JSON quebrado não dizem nada sobre a URL: DNS, conexão recusada,
    # tempo esgotado, página de manutenção no lugar do envelope.
    return True


def espera_de(e, tentativa):
    """Quanto dormir antes da próxima tentativa."""
    padrao = ESPERAS[min(tentativa, len(ESPERAS)) - 1]
    if isinstance(e, urllib.error.HTTPError):
        # 429 e 503 costumam vir com Retry-After. Obedecer é mais educado e
        # mais eficaz que o nosso palpite — com teto, porque o cabeçalho pode
        # mandar esperar meia hora e aí a rodada estoura o job.
        try:
            pedido = int(str((e.headers or {}).get("Retry-After")).strip())
        except (AttributeError, TypeError, ValueError):
            pedido = 0
        if 0 < pedido <= ESPERA_MAXIMA:
            return max(padrao, pedido)
    return padrao


def buscar(url, tentativa=1):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except FALHA_DE_REDE as e:
        if tentativa >= TENTATIVAS or not repetivel(e):
            raise
        espera = espera_de(e, tentativa)
        print("    ! falhou (%s), tentativa %d de %d, repetindo em %ds"
              % (e, tentativa, TENTATIVAS, espera), file=sys.stderr)
        time.sleep(espera)
        return buscar(url, tentativa + 1)


# O nome da unidade é a ÚNICA chave entre a atividade e a região, e o portal
# não o digita duas vezes igual: "São Caetano " chega com um espaço sobrando
# na listagem e sem ele em /unidades-atividades. Vinte e sete atividades
# ficaram com região "outra" por causa desse espaço — e "outra" era descarte
# na saída do coletor. Normalizar os dois lados custa uma linha; não
# normalizar apaga uma unidade inteira, em silêncio.
def chave_unidade(nome):
    return re.sub(r"\s+", " ", (nome or "")).strip()


def carregar_unidades():
    """Devolve {nome_da_unidade: regiao}.

    Esta falha continua sendo fatal, de propósito: o nome da unidade é a
    única chave entre a atividade e a região, e sem o mapa toda a coleta sai
    sem região e sem endereço. O que mudou é a paciência — era aqui, na
    primeira requisição da rodada, que 6 segundos de teimosia decidiam o dia.
    """
    dados = buscar(API_UNIDADES)
    mapa = {}
    for u in dados:
        nome = chave_unidade(u.get("name"))
        regiao = (u.get("description") or "").strip().lower()
        if nome:
            mapa[nome] = regiao if regiao in REGIOES_VALIDAS else "outra"
    return mapa


def url_geral(page):
    """Listagem sem filtro de data — a rede de segurança."""
    q = urllib.parse.urlencode({
        "tipo": "atividade", "dinamico": "true", "ppp": PPP, "page": page,
    })
    return API_ATIVIDADES + "?" + q


def url_dia(dia, page):
    q = urllib.parse.urlencode({
        "local": "", "categoria": "", "gratuito": "", "online": "",
        "publico": "", "atividade": "", "linguagem": "",
        "data_inicial": dia, "data_final": dia,
        "tipo": "atividade", "dinamico": "true",
        "ppp": PPP, "page": page,
    })
    return API_ATIVIDADES + "?" + q


def primeiro(lista, campo="name"):
    if isinstance(lista, list) and lista:
        v = lista[0]
        if isinstance(v, dict):
            return v.get(campo) or v.get("titulo") or v.get("name")
    return None


def hora_de(iso):
    if isinstance(iso, str) and "T" in iso:
        return iso.split("T")[1][:5]
    return None


def dia_de(iso):
    if isinstance(iso, str) and "T" in iso:
        return iso.split("T")[0]
    if isinstance(iso, str) and len(iso) == 10:
        return iso
    return None


def melhor_foto(a):
    """URL da foto do evento em três tamanhos, do menor ao maior.

    A listagem traz a original em `imagem` — que chega a 3 MB — e as variantes
    só com o nome do arquivo, na mesma pasta. Uma tela inicial com quarenta
    cartões carregando originais seria inviável no celular, então monta-se a
    URL da variante do tamanho certo para cada uso:

      thumb  linha da agenda, 62px na tela
      img    cartão do trilho, ~240px na tela
      capa   topo da folha de detalhe, largura inteira
    """
    cheia = (a.get("imagem") or "").strip()
    if not cheia:
        return None, None, None
    pasta = cheia.rsplit("/", 1)[0] + "/"
    tam = a.get("imagens") or {}

    def variante(*nomes):
        for n in nomes:
            v = tam.get(n)
            if isinstance(v, dict) and v.get("file"):
                return pasta + v["file"]
        return None

    thumb = variante("medium", "projeto-thumb", "sites-card-img",
                     "homepage-thumb", "thumbnail")
    img = variante("atividade-img", "sites-card-img", "destacada",
                   "carousel-img", "medium_large")
    capa = variante("banner-img", "large", "destacada", "medium_large")
    return thumb or img or cheia, img or capa or cheia, capa or img or cheia


def normalizar(a, unidades):
    """Achata um item da API no formato que o app consome."""
    uni = chave_unidade(primeiro(a.get("unidade"))) or "—"
    ling = a.get("tipos_linguagens") or []
    cat = sub = None
    if ling and isinstance(ling[0], dict):
        cat = ling[0].get("titulo")
        filhos = ling[0].get("children") or []
        if filhos and isinstance(filhos[0], dict):
            sub = filhos[0].get("titulo")

    gratuito = (a.get("gratuito") or "").strip()
    thumb, img, capa = melhor_foto(a)
    publicos = [p.get("titulo") for p in (a.get("publico_tag") or [])
                if isinstance(p, dict) and p.get("titulo")]
    link = a.get("link") or ""
    if link.startswith("/"):
        link = BASE + link

    return {
        "id": a.get("id"),
        # chave da bilheteria: abre portal.sescsp.org.br/bilheteria/atividade.action
        "idJava": a.get("id_java"),
        "tit": (a.get("titulo") or "").strip(),
        "sub": (a.get("complemento") or "").strip(),
        "uni": uni,
        "reg": unidades.get(uni, "outra"),
        "cat": cat or "Outros",
        "subcat": sub,
        "publico": primeiro(a.get("publico_tag"), "titulo"),
        "publicos": publicos,
        # foto do cartão e capa da folha de detalhe
        "thumb": thumb,
        "img": img,
        "capa": capa,
        "projeto": primeiro(a.get("conjunto")),
        "gratis": gratuito == "Atividade gratuita",
        "pago": gratuito == "Atividade paga",
        "online": bool((a.get("online") or "").strip()),
        "esgotado": bool((a.get("esgotado") or "").strip()),
        "cancelado": bool((a.get("cancelado") or "").strip()),
        "ingressosWeb": a.get("qtdeIngressosWeb"),
        "temporada": bool((a.get("quantDatas") or "").strip()),
        "hora": hora_de(a.get("dataPrimeiraSessao")),
        "inicio": dia_de(a.get("dataPrimeiraSessao")),
        "fim": dia_de(a.get("dataUltimaSessao")),
        "link": link,
        "dias": [],
    }


# A categoria mora aqui porque três arquivos dependem dela de formas
# diferentes: `CONTINUOS` abaixo (a viagem acontece em todos os dias do
# intervalo), `regras.SO_COM_INSCRICAO` (passeio sem data de inscrição sai
# da base) e o padrão de `detalhes --html-cats` (é o único jeito de raspar,
# porque passeio não tem id_java). O portal renomear a categoria produziria
# três efeitos diferentes e todos silenciosos: as viagens deixariam de ser
# contínuas, os passeios parariam de ser raspados e parariam de ser
# descartados. Com um nome só, o teste de sanidade abaixo pega os três.
CAT_TURISMO = "Turismo Social"

CONTINUOS = (CAT_TURISMO,)


def expandir_viagens(lista, de, ate):
    """Preenche os dias do meio de um passeio de vários dias.

    Uma viagem de 11 a 16/8 acontece nos seis dias, mas a listagem devolve o
    evento em uns dias e não em outros — resultado: o passeio aparecia só na
    primeira data da agenda.

    Só vale para categorias contínuas por natureza. Uma exposição também tem
    início e fim distantes, mas fecha às segundas: ali os dias coletados é
    que estão certos, e preencher o intervalo seria inventar.
    """
    n = 0
    for e in lista:
        if e["cat"] not in CONTINUOS:
            continue
        ini, fim = e.get("inicio"), e.get("fim")
        if not ini or not fim or ini == fim:
            continue
        d0 = datetime.strptime(ini, "%Y-%m-%d").date()
        d1 = datetime.strptime(fim, "%Y-%m-%d").date()
        if (d1 - d0).days > 60:      # não é viagem, é engano de cadastro
            continue
        todos = []
        d = max(d0, de)
        while d <= min(d1, ate):
            todos.append(d.isoformat())
            d += timedelta(days=1)
        if not todos or len(todos) < 2:
            continue
        # marca como contínuo mesmo quando a listagem já trouxe todos os dias:
        # é a marca que faz o app tratar a viagem como um intervalo só
        if todos != e["dias"]:
            e["dias"] = todos
            n += 1
        e["continuo"] = True
    if n:
        print("Preenchido o intervalo de %d passeios de vários dias" % n)
    return n


def coletar(de, ate, regioes):
    unidades = carregar_unidades()
    print("Unidades: %d (%s)" % (
        len(unidades),
        ", ".join("%s=%d" % (r, sum(1 for v in unidades.values() if v == r))
                  for r in REGIOES_VALIDAS)))

    eventos = {}
    dia = de
    total_dias = (ate - de).days + 1
    n = 0
    falhos = []
    teto_falhos = max(1, int(total_dias * FRACAO_DIAS_FALHOS))

    while dia <= ate:
        n += 1
        iso = dia.isoformat()
        page = 1
        do_dia = 0
        falhou = False
        while True:
            try:
                envelope = buscar(url_dia(iso, page))
            except FALHA_DE_REDE as e:
                # Um dia perdido não vale a rodada inteira: a varredura de
                # segurança adiante ainda recupera o evento pelas datas do
                # próprio registro. O que se perde é a precisão dos dias
                # intermediários, não o evento.
                #
                # Mas perda neste pipeline tem de gritar, então o dia entra no
                # caderno e sai como aviso na aba Actions. E quando o portal
                # está fora — não é tropeço, é fora — insistir nos 60 dias
                # gastaria horas para abortar no fim: o teto corta na hora.
                falhos.append(iso)
                print("::warning::Dia %s não coletado depois de %d tentativas"
                      " (%s). A varredura de segurança ainda pode recuperar"
                      " os eventos, sem os dias intermediários." % (iso, TENTATIVAS, e))
                if len(falhos) > teto_falhos:
                    raise SystemExit(
                        "ABORTADO: %d dias de %d não responderam (teto: %d). "
                        "Isso não é tropeço do portal, é portal fora. Nada foi "
                        "gravado — a base anterior continua no lugar e a próxima "
                        "rodada tenta de novo. Dias: %s"
                        % (len(falhos), total_dias, teto_falhos, ", ".join(falhos)))
                falhou = True
                break
            itens = envelope.get("atividade") or []
            total = (envelope.get("total") or {}).get("value", 0)

            for a in itens:
                # a API devolve buracos (null) no meio da lista
                if not isinstance(a, dict):
                    continue
                eid = a.get("id")
                if eid is None:
                    continue
                if eid not in eventos:
                    eventos[eid] = normalizar(a, unidades)
                if iso not in eventos[eid]["dias"]:
                    eventos[eid]["dias"].append(iso)
                do_dia += 1

            if page * PPP >= total or not itens:
                break
            page += 1
            time.sleep(PAUSA)

        # "0 sessões" e "não respondeu" são coisas diferentes, e o log é o
        # único lugar onde a diferença aparece depois.
        if falhou:
            print("  [%2d/%2d] %s  FALHOU         (acumulado: %d eventos)"
                  % (n, total_dias, iso, len(eventos)))
        else:
            print("  [%2d/%2d] %s  %4d sessões  (acumulado: %d eventos)"
                  % (n, total_dias, iso, do_dia, len(eventos)))
        dia += timedelta(days=1)
        time.sleep(PAUSA)

    # ------------------------------------------------------------------
    # Rede de segurança: o filtro de data da API deixa eventos de fora.
    # "Ecos da Independência" (12/09) não volta na consulta de 12/09, mas
    # aparece na listagem sem filtro. Então varremos tudo e recuperamos o
    # que ficou faltando, usando as datas do próprio registro.
    # ------------------------------------------------------------------
    print("Varredura de segurança (listagem sem filtro de data)…")
    page, recuperados = 1, 0
    varredura_truncada = False
    while True:
        try:
            envelope = buscar(url_geral(page))
        except FALHA_DE_REDE as e:
            # Sem a primeira página não há varredura nenhuma, e é ela que
            # recupera o que o filtro de data omite ("Ecos da Independência"
            # e os 23 programas contínuos). Seguir sem ela publicaria buracos
            # que ninguém veria.
            if page == 1:
                raise SystemExit(
                    "ABORTADO: a varredura de segurança não respondeu nem na "
                    "primeira página depois de %d tentativas (%s). É ela que "
                    "recupera o que o filtro de data deixa de fora; sem ela a "
                    "coleta sai com buracos invisíveis. Nada foi gravado."
                    % (TENTATIVAS, e))
            # Da segunda em diante o que já veio vale; o estrago de parar no
            # meio é medido pelo piso de volume, em `conferir_volume`.
            varredura_truncada = True
            print("::warning::Varredura de segurança interrompida na página %d"
                  " (%s). O que ela já recuperou vale." % (page, e))
            break
        itens = envelope.get("atividade") or []
        total = (envelope.get("total") or {}).get("value", 0)

        for a in itens:
            if not isinstance(a, dict):
                continue
            eid = a.get("id")
            if eid is None or eid in eventos:
                continue
            ini = dia_de(a.get("dataPrimeiraSessao"))
            fim = dia_de(a.get("dataUltimaSessao")) or ini
            if not ini:
                continue
            if fim < de.isoformat() or ini > ate.isoformat():
                continue

            ev = normalizar(a, unidades)
            # não sabemos os dias intermediários de uma temporada; ficam a
            # primeira e a última, e detalhes.py corrige quando há bilheteria.
            #
            # As pontas são GRAMPEADAS na janela antes de entrar. Recortar
            # depois apagava a temporada que atravessa a janela inteira —
            # "Curumim 2026" vai de fevereiro a dezembro, e as duas pontas
            # caem fora dos 60 dias, então a lista ficava vazia e o programa
            # sumia do app. Eram 23 programas contínuos, todos de esporte e
            # de criança, que acontecem toda semana e nunca apareceram.
            # `parcial` já é o campo que diz "esta lista de dias está
            # incompleta"; a tela avisa.
            de_iso, ate_iso = de.isoformat(), ate.isoformat()
            dias = {max(ini, de_iso), min(fim, ate_iso)}
            ev["dias"] = sorted(d for d in dias if de_iso <= d <= ate_iso)
            if not ev["dias"]:
                continue
            ev["parcial"] = ini != fim
            eventos[eid] = ev
            recuperados += 1

        if page * PPP >= total or not itens:
            break
        page += 1
        time.sleep(PAUSA)

    print("  recuperados %d eventos que o filtro de data não devolveu" % recuperados)

    # As duas perdas toleradas acima se somam mal: dia falho conta com a
    # varredura para recuperar o evento, e varredura truncada conta com os
    # dias para já tê-lo pego. Faltando as duas, ninguém responde por esse
    # pedaço da janela — e aí o silêncio é exatamente o que este pipeline não
    # pode ter.
    if falhos and varredura_truncada:
        raise SystemExit(
            "ABORTADO: %d dia(s) não responderam (%s) E a varredura de "
            "segurança parou no meio. Uma coisa cobre a outra; as duas juntas, "
            "não. Nada foi gravado — a base anterior continua no lugar."
            % (len(falhos), ", ".join(falhos)))
    if falhos:
        print("Dias que não responderam: %d de %d (teto: %d) — %s"
              % (len(falhos), total_dias, teto_falhos, ", ".join(falhos)))

    lista = list(eventos.values())

    # O filtro de região serve para PEDIR MENOS ("só a capital"). Quem pede as
    # três está pedindo tudo, e nesse caso ele não pode descartar nada — era o
    # que fazia, porque `reg` também vale "outra", e "outra" não está entre as
    # três. Sumiam 54 atividades que simplesmente não têm unidade na fonte:
    # `unidade[0].name` vem `null` na programação que acontece fora de uma
    # unidade — "Sesc na Bienal do Livro" sozinho eram 39 eventos gratuitos
    # desta semana, além de "Semana Move" e "Festival Sesc 80 Anos".
    if regioes and set(regioes) < set(REGIOES_VALIDAS):
        antes = len(lista)
        lista = [e for e in lista if e["reg"] in regioes]
        print("Filtro de região %s: %d -> %d eventos" % (regioes, antes, len(lista)))

    # A trava continua, mas medindo o que ela sempre quis medir: unidade com
    # NOME que não bate com nenhum `name` de /unidades-atividades. É esse o
    # sintoma de "o portal renomeou uma unidade", e ele é zero quando está
    # tudo bem. Antes a trava olhava o resultado do filtro, e por isso não viu
    # nem o espaço sobrando em "São Caetano " (27 eventos) nem a programação
    # sem unidade (54): ficou abaixo dos 10% e passou calada.
    orfas = sorted({e["uni"] for e in lista
                    if e["uni"] != "—" and e["uni"] not in unidades})
    if orfas:
        raise SystemExit(
            "ABORTADO: %d nome(s) de unidade que não existem em "
            "/unidades-atividades: %s. Provavelmente uma unidade mudou de "
            "nome. Sem correspondência elas ficam sem região e sem endereço. "
            "Nada foi gravado." % (len(orfas), ", ".join(repr(o) for o in orfas)))
    sem_unidade = sum(1 for e in lista if e["uni"] == "—")
    if sem_unidade:
        print("Mantidos %d eventos sem unidade na fonte (região \"outra\")"
              % sem_unidade)
    if not any(e["cat"] == CAT_TURISMO for e in lista):
        raise SystemExit(
            "ABORTADO: nenhum evento de %s na coleta. A categoria é usada "
            "por três regras diferentes (viagem contínua, retenção e "
            "raspagem de HTML); se ela mudou de nome, as três se apagam em "
            "silêncio. Nada foi gravado." % CAT_TURISMO)

    # Descartado agora só o cancelado. "Outros" é o balde do que o portal não
    # categorizou — jogá-lo fora tirava da agenda atividades que existem de
    # verdade, e a única coisa que faltava nelas era o rótulo.
    antes = len(lista)
    lista = [e for e in lista if not e["cancelado"]]
    print("Descartados %d cancelados" % (antes - len(lista)))
    sem_cat = sum(1 for e in lista if e["cat"] == "Outros")
    if sem_cat:
        print("Mantidos %d eventos sem categoria (entram como \"Outros\")" % sem_cat)

    for e in lista:
        e["dias"].sort()

    expandir_viagens(lista, de, ate)

    lista.sort(key=lambda e: (e["dias"][0] if e["dias"] else "9999", e["tit"]))
    return lista, unidades


# Piso de volume. 3.3 mil eventos numa janela de 60 dias e a norma; qualquer
# coisa abaixo de algumas centenas so acontece se a fonte mudou de forma.
MINIMO_ABSOLUTO = 200
FRACAO_MINIMA = 0.6


def conferir_volume(eventos, caminho_anterior):
    """Recusa gravar uma coleta que desabou em relação à anterior.

    Toda a degradação deste pipeline é silenciosa por construção: uma coleta
    vazia atravessa `carimbar_estreia` ("0 novidades"), `regras.aplicar`
    ("0 de 0") e `publicar.py` ("web/ pronto · 0 eventos"), e os três terminam
    com sucesso. O app publicado fica vazio e nada avisa.

    Pior: o arquivo vazio vira a coleta anterior do dia seguinte, e aí os
    3.300 eventos de volta são todos carimbados como novidade — o trilho
    "Novidades na programação" passa a mostrar o catálogo inteiro.

    O envelope da API não distingue "não há nada nesse dia" de "a resposta
    mudou de forma": as duas chegam como lista vazia. Quem distingue é o
    tamanho da coleta anterior. Sem base anterior não há o que comparar, e
    aí só o piso absoluto vale — é o caso da primeira execução.
    """
    if len(eventos) < MINIMO_ABSOLUTO:
        raise SystemExit(
            "ABORTADO: %d eventos coletados, abaixo do piso de %d. A resposta "
            "da API provavelmente mudou de forma. Nada foi gravado — a base "
            "anterior continua no lugar." % (len(eventos), MINIMO_ABSOLUTO))

    try:
        with open(caminho_anterior, encoding="utf-8") as f:
            antes = len(json.load(f).get("eventos") or [])
    except (OSError, ValueError):
        return

    if antes and len(eventos) < antes * FRACAO_MINIMA:
        raise SystemExit(
            "ABORTADO: %d eventos contra %d da coleta anterior (%.0f%%, "
            "mínimo %.0f%%). Nada foi gravado. Se a queda for real, rode com "
            "--sem-piso." % (len(eventos), antes, 100.0 * len(eventos) / antes,
                             100 * FRACAO_MINIMA))


def carimbar_estreia(lista, caminho_anterior, hoje):
    """Marca em `visto` o dia em que cada evento apareceu pela primeira vez.

    Novidade só se sabe comparando com a coleta anterior, e o app não guarda
    histórico — então o carimbo viaja dentro do próprio arquivo: quem já
    estava lá conserva a data antiga, quem não estava recebe a de hoje.

    A primeira coleta com rastreio não carimba ninguém: ela só estabelece a
    linha de base e grava `rastreioDesde`. Sem isso o primeiro dia mentiria
    duas vezes — o catálogo inteiro apareceria como novo, e uma mudança de
    regra nossa (a entrada dos eventos "Outros", que a coleta antiga jogava
    fora) entraria como se o Sesc tivesse acabado de publicar 359 atividades.

    Quem já estava na base e não tem carimbo fica **sem** carimbo: é anterior
    ao rastreio e não dá para inventar uma data.

    Ressalvas conhecidas: um evento que suma da listagem por um dia e volte
    depois é carimbado de novo, e a janela que anda um dia por vez carimba a
    borda (medido: 3 eventos em 5 dias). Preferimos isso a guardar um
    histórico de ids que cresceria para sempre.

    Devolve a data em que o rastreio começou, para gravar no arquivo.
    """
    try:
        with open(caminho_anterior, encoding="utf-8") as f:
            anterior = json.load(f)
    except (OSError, ValueError):
        anterior = None

    if not anterior:
        print("Sem coleta anterior: linha de base, nenhuma novidade marcada")
        return hoje

    desde = anterior.get("rastreioDesde")
    if not desde:
        print("Primeira coleta com rastreio: linha de base com %d eventos, "
              "nenhuma novidade marcada" % len(anterior.get("eventos") or []))
        return hoje

    antigos = {}
    for e in anterior.get("eventos") or []:
        if e.get("id") is not None:
            antigos[e["id"]] = (e.get("visto") or "")[:10]

    novos = 0
    for e in lista:
        if e["id"] not in antigos:
            e["visto"] = hoje
            novos += 1
        elif antigos[e["id"]]:
            e["visto"] = antigos[e["id"]]
        # já estava lá e não tem carimbo: é anterior ao rastreio
    print("Novidades desta coleta: %d eventos que não estavam na anterior" % novos)
    return desde


def main():
    p = argparse.ArgumentParser(description="Coleta a programação do Sesc SP.")
    p.add_argument("--dias", type=int, default=30, help="janela a partir de hoje")
    p.add_argument("--de", help="data inicial YYYY-MM-DD")
    p.add_argument("--ate", help="data final YYYY-MM-DD")
    p.add_argument("--regioes", nargs="*", default=list(REGIOES_VALIDAS),
                   help="capital interior litoral")
    p.add_argument("--saida", default=os.path.join("dados", "eventos.json"))
    p.add_argument("--sem-piso", action="store_true",
                   help="grava mesmo com a coleta muito menor que a anterior")
    args = p.parse_args()

    hoje = agora_br().date()
    de = datetime.strptime(args.de, "%Y-%m-%d").date() if args.de else hoje
    ate = (datetime.strptime(args.ate, "%Y-%m-%d").date() if args.ate
           else de + timedelta(days=args.dias - 1))

    print("Coletando de %s a %s" % (de, ate))
    inicio = time.time()
    eventos, unidades = coletar(de, ate, set(args.regioes))
    if not args.sem_piso:
        conferir_volume(eventos, args.saida)
    rastreio = carimbar_estreia(eventos, args.saida, hoje.isoformat())

    saida = {
        "fonte": "Sesc São Paulo — portal público (wp-json)",
        "geradoEm": agora_br().isoformat(timespec="seconds"),
        # desde quando dá para dizer o que é novidade (ver carimbar_estreia)
        "rastreioDesde": rastreio,
        "janela": {"de": de.isoformat(), "ate": ate.isoformat()},
        "unidades": [{"nome": k, "regiao": v} for k, v in sorted(unidades.items())],
        "categorias": sorted({e["cat"] for e in eventos}),
        "eventos": eventos,
    }

    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, separators=(",", ":"))

    kb = os.path.getsize(args.saida) / 1024
    ocorrencias = sum(len(e["dias"]) for e in eventos)
    print("\n%d eventos · %d ocorrências · %.0f KB · %.0fs"
          % (len(eventos), ocorrencias, kb, time.time() - inicio))
    print("Escrito em %s" % args.saida)

    por_regiao = {}
    for e in eventos:
        por_regiao[e["reg"]] = por_regiao.get(e["reg"], 0) + 1
    print("Por região: " + ", ".join("%s=%d" % kv for kv in sorted(por_regiao.items())))
    por_cat = {}
    for e in eventos:
        por_cat[e["cat"]] = por_cat.get(e["cat"], 0) + 1
    print("Por categoria: " + ", ".join("%s=%d" % kv for kv in
                                        sorted(por_cat.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
