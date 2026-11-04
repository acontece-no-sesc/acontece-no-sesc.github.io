#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill de foto, público e endereço da unidade em dados/eventos.json.

Por que um script à parte: `coletor.py` reescreve o arquivo do zero e
`detalhes.py` leva ~70 minutos para reenriquecer. Este aqui só acrescenta
campos ao que já existe.

  img       — URL da foto do evento (listagem WP, campo `imagem`)
  publicos  — todas as tags de público (a listagem traz mais de uma)
  unidades[].endereco — raspado de data-geo-address da página da unidade
"""
import json, re, sys, time, urllib.request, urllib.parse, urllib.error, gzip, io

import coletor       # a política de paciência com o portal mora num lugar só

BASE = "https://www.sescsp.org.br"
API_UNIDADES = BASE + "/wp-json/wp/v1/unidades-atividades"
API_ATIVIDADES = BASE + "/wp-json/wp/v1/atividades/filter"
UA = "agenda-sesc-prototipo/1.0 (coletor de programacao publica; uso pessoal)"
PPP, PAUSA, TIMEOUT = 300, 0.35, 30

# São ~115 URLs nesta passada (a varredura, os dias e as 43 unidades), então
# cabe mais paciência que em `detalhes.py` e menos que no coletor. Eram 3
# tentativas esperando 2s e 4s.
TENTATIVAS, ESPERAS = 5, (2, 5, 10, 20)

# `_abrir` devolve None e segue, de propósito. O que faltava era alguém
# guardar quantas vezes isso aconteceu: sem contar, "a unidade não tem
# endereço na página" e "a página não abriu" chegavam iguais ao fim.
FALHAS = []


def _abrir(url, aceita, tentativa=1):
    destino, extra = coletor.pelo_portao(url)
    cabecalhos = {"User-Agent": UA, "Accept": aceita, "Accept-Encoding": "gzip"}
    cabecalhos.update(extra)
    req = urllib.request.Request(destino, headers=cabecalhos)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            bruto = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                bruto = gzip.decompress(bruto)
            return bruto
    except coletor.FALHA_DE_REDE as e:
        if tentativa >= TENTATIVAS or not coletor.repetivel(e):
            print("    ! desisti de %s (%s)" % (url, e), file=sys.stderr)
            FALHAS.append(url)
            return None
        time.sleep(coletor.espera_de(e, tentativa, ESPERAS))
        return _abrir(url, aceita, tentativa + 1)


def json_de(url):
    b = _abrir(url, "application/json")
    return json.loads(b.decode("utf-8")) if b else None


def html_de(url):
    b = _abrir(url, "text/html")
    return b.decode("utf-8", "replace") if b else ""


def url_geral(page):
    return API_ATIVIDADES + "?" + urllib.parse.urlencode({
        "tipo": "atividade", "dinamico": "true", "ppp": PPP, "page": page})


def url_dia(dia, page):
    return API_ATIVIDADES + "?" + urllib.parse.urlencode({
        "local": "", "categoria": "", "gratuito": "", "online": "",
        "publico": "", "atividade": "", "linguagem": "",
        "data_inicial": dia, "data_final": dia,
        "tipo": "atividade", "dinamico": "true", "ppp": PPP, "page": page})


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


def coletar_extras(dias):
    """id -> {img, capa, publicos}. Varre a listagem geral e o dia a dia."""
    achados = {}

    def engolir(env):
        for a in (env or {}).get("atividade") or []:
            if not a or not a.get("id"):
                continue
            thumb, card, capa = melhor_foto(a)
            pubs = [p.get("titulo") for p in (a.get("publico_tag") or [])
                    if isinstance(p, dict) and p.get("titulo")]
            achados[a["id"]] = {"thumb": thumb, "img": card, "capa": capa,
                                "publicos": pubs}

    print("varredura geral…")
    page = 1
    while True:
        env = json_de(url_geral(page))
        # A página que não respondeu chegava aqui como None, virava n == 0 e
        # o laço lia isso como fim de lista: a varredura parava no primeiro
        # tropeço do portal e o backfill saía cobrindo menos eventos, com cara
        # de completo. É a armadilha da §2.1 de cabeça para baixo — ali o fim
        # é a página vazia, e por isso a página vazia não pode ser falha.
        if env is None:
            raise SystemExit(
                "ABORTADO: a página %d da varredura não respondeu depois de %d "
                "tentativas. Parar aqui confundiria falha com fim de lista e "
                "gravaria meio backfill como se fosse inteiro. Nada foi "
                "gravado — a base anterior continua no lugar." % (page, TENTATIVAS))
        n = len((env or {}).get("atividade") or [])
        engolir(env)
        print("  página %d: %d itens (acumulado %d)" % (page, n, len(achados)))
        # a lista vem com `null` no meio, então uma página cheia devolve 299
        # e não 300 — parar em "menos que PPP" cortava a varredura na
        # primeira página. O fim é a página vazia.
        if n == 0 or page > 30:
            break
        page += 1
        time.sleep(PAUSA)

    print("dia a dia (%d dias)…" % len(dias))
    perdidos = []
    for i, d in enumerate(dias):
        env = json_de(url_dia(d, 1))
        # Aqui o dia perdido é perda pequena — a varredura geral acima já
        # cobriu o catálogo, e estes dias só acrescentam. Mas pequena não é
        # nenhuma, então sai no relatório em vez de sair no silêncio.
        if env is None:
            perdidos.append(d)
        engolir(env)
        if (i + 1) % 10 == 0:
            print("  %s — acumulado %d" % (d, len(achados)))
        time.sleep(PAUSA)
    if perdidos:
        print("::warning::%d de %d dias não responderam no dia a dia (%s). A "
              "varredura geral já cobriu o catálogo; o que estes dias "
              "acrescentariam ficou de fora."
              % (len(perdidos), len(dias), ", ".join(perdidos)))
    return achados


RE_END = re.compile(r'data-geo-address="([^"]+)"')
RE_CHEGAR = re.compile(r'data-como-chegar="([^"]*)"')


def enderecos(unidades_api):
    mapa = {}
    for i, u in enumerate(unidades_api):
        nome, slug = (u.get("name") or "").strip(), (u.get("group_slug") or "").strip()
        if not nome or not slug:
            continue
        h = html_de("%s/unidades/%s/" % (BASE, slug))
        # "a página não abriu" e "a página abriu e não tem endereço" pedem
        # providências diferentes, e chegavam as duas como "SEM ENDEREÇO".
        m = RE_END.search(h) if h else None
        if m:
            mapa[nome] = {"endereco": m.group(1).strip()}
            c = RE_CHEGAR.search(h)
            if c and c.group(1).strip():
                mapa[nome]["mapa"] = c.group(1).strip()
        if m:
            estado = "ok"
        else:
            estado = "SEM ENDEREÇO" if h else "SEM RESPOSTA"
        print("  %2d/%d %-34s %s" % (i + 1, len(unidades_api), nome, estado))
        time.sleep(PAUSA)
    return mapa


def main():
    # --fotos pula a raspagem das unidades: serve para reprocessar só as
    # imagens quando o critério de tamanho muda, sem refazer 43 páginas
    so_fotos = "--fotos" in sys.argv
    caminho = "dados/eventos.json"
    base = json.load(open(caminho, encoding="utf-8"))
    ev = base["eventos"]

    dias = sorted({d for e in ev for d in (e.get("dias") or [])})
    extras = coletar_extras(dias)

    com_foto = 0
    for e in ev:
        x = extras.get(e["id"])
        if not x:
            continue
        if x.get("img"):
            e["thumb"] = x["thumb"]
            e["img"] = x["img"]
            e["capa"] = x["capa"]
            com_foto += 1
        if x.get("publicos"):
            e["publicos"] = x["publicos"]

    print("\nendereços das unidades…")
    api = json_de(API_UNIDADES)
    # Sem a lista de unidades, `esperadas` virava 0 e a trava dos 90% logo
    # abaixo se desligava sozinha — o `if esperadas` deixava passar justamente
    # o pior caso, o de nenhum endereço, e o arquivo era gravado sem nada.
    if not api and not so_fotos:
        raise SystemExit(
            "ABORTADO: /wp-json/wp/v1/unidades-atividades não respondeu depois "
            "de %d tentativas. É a lista que diz quais páginas raspar; sem ela "
            "não há endereço nenhum, e gravar assim deixaria o .ics de 486 "
            "eventos sem local. Nada foi gravado." % TENTATIVAS)
    api = api or []
    ends = enderecos(api)
    # o que já veio da bilheteria vale como reserva
    reserva = {}
    for e in ev:
        if e.get("endereco") and e["uni"] not in reserva:
            reserva[e["uni"]] = e["endereco"]

    for u in base.get("unidades") or []:
        d = ends.get(u["nome"])
        if d:
            u["endereco"] = d["endereco"]
            if d.get("mapa"):
                u["mapa"] = d["mapa"]
        elif reserva.get(u["nome"]):
            u["endereco"] = reserva[u["nome"]]

    # O passo promete endereço e, antes disto, entregava exit 0 sem nenhum:
    # `enderecos()` imprime "SEM ENDEREÇO" e segue, e `main` não conferia.
    # As 11 unidades que não vendem ingresso (Itaquera, Interlagos, Bertioga,
    # Registro…) não têm endereço de reserva vindo da bilheteria — são 486
    # eventos cujo .ics sairia com "Sesc Itaquera" e mais nada. É exatamente
    # a regressão que a §2.2 do HANDOFF diz ter sido corrigida.
    if not so_fotos:
        esperadas = len(api or [])
        if esperadas and len(ends) < esperadas * 0.9:
            raise SystemExit(
                "ABORTADO: só %d das %d unidades devolveram endereço. Nada foi "
                "gravado — a base anterior continua no lugar. Se a queda for "
                "real, confira /wp-json/wp/v1/unidades-atividades."
                % (len(ends), esperadas))

    base["extrasEm"] = time.strftime("%Y-%m-%dT%H:%M:%S-03:00")
    json.dump(base, open(caminho, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    n_end = sum(1 for u in base.get("unidades") or [] if u.get("endereco"))
    print("\n%d/%d eventos com foto · %d/%d unidades com endereço · %d URLs sem resposta"
          % (com_foto, len(ev), n_end, len(base.get("unidades") or []),
             len(FALHAS)))


if __name__ == "__main__":
    main()
