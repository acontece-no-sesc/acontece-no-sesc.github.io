#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monta a pasta `web/` — o site hospedável, instalável como PWA e que se
atualiza sozinho.

Diferença para o `embutir.py`:
    embutir.py  → um arquivo só, dados embutidos, vira link compartilhável
                  do Claude. Nunca se atualiza.
    publicar.py → app + dados/eventos.json separados + manifest + service
                  worker. O app busca o JSON a cada abertura, então basta o
                  robô regravar o JSON para todo mundo receber.

Gera:
    web/index.html            o app (cópia do prototipo com manifest e SW)
    web/dados/eventos.json    a base servida ao vivo
    web/manifest.webmanifest  nome, cores e ícones da instalação
    web/sw.js                 casca em cache, dados pela rede
    web/icon-192.png, icon-512.png, icon-maskable.png

Uso:
    python publicar.py
    python publicar.py --base /agenda-sesc/     # subpasta do GitHub Pages
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import zlib
from datetime import date, datetime, timedelta, timezone

import coletor
import regras

# `short_name` é o que aparece embaixo do ícone na tela inicial — e é ele que
# ganha do `name` quando os dois existem. Encurtar para "Acontece" cabia melhor
# na grade do lançador, mas o ícone deixava de dizer de que app se trata, que é
# a única coisa que aquele rótulo tem para fazer. Nome inteiro nos dois: onde
# não couber, o sistema corta com reticências, e "Acontece no…" ainda diz mais
# do que "Acontece".
NOME = "Acontece no SESC"
NOME_CURTO = "Acontece no SESC"
DESC = "Agregador não oficial da programação do Sesc São Paulo."
TEMA = "#16130E"
FUNDO = "#FAF8F4"


# ----------------------------------------------------------------- ícones
def png(caminho, tamanho, maskable=False):
    """Escreve um PNG sem dependências, com a marca do app.

    A marca é a própria linha da agenda reduzida a glifo: a goteira de hora à
    esquerda, em três traços, e a foto à direita, em bloco. O app é
    declaradamente não oficial e não pode imitar a identidade do Sesc — a
    marca precisa ser dele. Tudo aqui é retângulo com canto arredondado, o
    que cabe num gerador de PNG escrito à mão.
    """
    fundo = (0x16, 0x13, 0x0E)      # --ink
    tinta = (0xFA, 0xF8, 0xF4)      # --on-ink
    acento = (0xC0, 0x52, 0x1A)     # --cat-shows

    s = tamanho
    # área segura menor quando maskable: o sistema recorta as bordas
    m = s * (0.28 if maskable else 0.16)
    w = s - 2 * m

    def caixa(x0, y0, x1, y1, r):
        """Teste de pertencimento a um retângulo de cantos arredondados."""
        def dentro(x, y):
            if not (x0 <= x < x1 and y0 <= y < y1):
                return False
            for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                           (x0 + r, y1 - r), (x1 - r, y1 - r)):
                if ((x < x0 + r or x > x1 - r) and (y < y0 + r or y > y1 - r)
                        and abs(x - cx) <= r and abs(y - cy) <= r):
                    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
            return True
        return dentro

    # três traços da goteira e o bloco da foto, na mesma proporção do SVG
    traco_r = w * 0.035
    # o grupo de traços é centrado contra o bloco: 0,235 a 0,765 dá o mesmo
    # meio que 0,12 a 0,88, senão a goteira fica pendurada mais alto
    tracos = [caixa(m + w * 0.04, m + w * (0.235 + i * 0.23),
                    m + w * 0.20, m + w * (0.305 + i * 0.23), traco_r)
              for i in range(3)]
    bloco = caixa(m + w * 0.30, m + w * 0.12, m + w * 0.94, m + w * 0.88, w * 0.09)

    linhas = bytearray()
    for y in range(s):
        linhas.append(0)                       # filtro None
        for x in range(s):
            px, py = x + 0.5, y + 0.5
            if bloco(px, py):
                c = acento
            elif any(t(px, py) for t in tracos):
                c = tinta
            else:
                c = fundo
            linhas.extend(c)

    def chunk(tipo, dados):
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    with open(caminho, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(linhas), 9)))
        f.write(chunk(b"IEND", b""))


# ----------------------------------------------------------------- textos
def esc_ics(s):
    return (str(s or "").replace("\\", "\\\\").replace(",", "\\,")
            .replace(";", "\\;").replace("\n", "\\n"))


def _ics(linhas):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0",
                        "PRODID:-//Acontece no SESC (nao oficial)//PT-BR",
                        "CALSCALE:GREGORIAN"] + linhas + ["END:VCALENDAR"])


# ---------------------------------------------------------------------- .ics
# As regras são as mesmas do app (ver o bloco "Calendário" no prototipo.html),
# e precisam continuar iguais: o arquivo servido e o montado no aparelho são
# o mesmo compromisso, só que por caminhos diferentes.
#
#   fim         término publicado > início + duração > início + 30 minutos
#   endereço    sempre o da unidade (as 43 têm), não o que vinha na bilheteria
#   alertas     1 hora, 30 minutos e 5 minutos antes
#   fuso        gravado em UTC a partir de Brasília (−03:00, fixo desde 2019),
#               porque hora flutuante seria lida no fuso do aparelho
DUR_PADRAO = 30            # minutos, quando nada é publicado
ALARMES = [("-PT1H", "1 hora"), ("-PT30M", "30 minutos"), ("-PT5M", "5 minutos")]
FUSO_SP = timezone(timedelta(hours=-3))

ENDERECOS = {}             # preenchido em main(), a partir de unidades[]


def _quando(s, hora_padrao="09:00"):
    """Texto ISO local -> datetime com fuso de Brasília."""
    p = str(s).split("T")
    hm = (p[1][:5] if len(p) > 1 and p[1] else hora_padrao)
    a, m, d = (int(x) for x in p[0].split("-"))
    h, mi = (int(x) for x in hm.split(":"))
    return datetime(a, m, d, h, mi, tzinfo=FUSO_SP)


def _stamp(quando, hora_padrao="09:00"):
    """Instante de Brasília gravado em UTC, como o iCalendar pede."""
    return _quando(quando, hora_padrao).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _agora():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _local(ev):
    end = ENDERECOS.get(ev.get("uni")) or ev.get("endereco") or ""
    uni = ev.get("uni") or ""
    # As duas implementações do .ics têm de dizer a mesma coisa, e a do
    # aparelho passou a chamar o projeto quando não há unidade. "Sesc —" no
    # LOCATION do compromisso não leva ninguém a lugar nenhum.
    nome = ("Sesc " + uni) if uni and uni != "—" else (ev.get("projeto") or "Sesc São Paulo")
    return esc_ics(nome + (" — " + end if end else ""))


def _fim(ev, inicio):
    """Término, na ordem da regra."""
    for s in ev.get("sessoes") or []:
        if s.get("quando") == inicio and s.get("fim") and s["fim"] > inicio:
            return _stamp(s["fim"])
    dur = ev.get("duracaoMin") or 0
    return _stamp((_quando(inicio) + timedelta(minutes=dur or DUR_PADRAO)).strftime("%Y-%m-%dT%H:%M"))


def _alarmes(titulo):
    saida = []
    for gatilho, quanto in ALARMES:
        saida += ["BEGIN:VALARM", "TRIGGER:" + gatilho, "ACTION:DISPLAY",
                  "DESCRIPTION:" + esc_ics("Em %s: %s" % (quanto, titulo)), "END:VALARM"]
    return saida


def _vevent(ev, inicio, uid, titulo=None):
    tit = titulo or (ev.get("tit") or "")
    corpo = ["BEGIN:VEVENT", "UID:" + uid, "DTSTAMP:" + _agora(),
             "DTSTART:" + _stamp(inicio), "DTEND:" + _fim(ev, inicio),
             "SUMMARY:" + esc_ics(tit), "LOCATION:" + _local(ev),
             "DESCRIPTION:" + esc_ics(((ev.get("sub") + " — ") if ev.get("sub") else "") +
                                      (ev.get("link") or ""))]
    if ev.get("geo"):
        corpo.append("GEO:%s;%s" % (ev["geo"][0], ev["geo"][1]))
    if ev.get("link"):
        corpo.append("URL:" + esc_ics(ev["link"]))
    return corpo + _alarmes(tit) + ["END:VEVENT"]


def ics_evento(ev, hoje):
    """Um compromisso só: a próxima sessão. Viagem contínua vira o intervalo.

    A versão anterior despejava TODAS as sessões num arquivo — uma temporada
    de dois meses entrava na agenda de alguém como sessenta compromissos que
    ele teria de apagar um a um. O app agenda uma sessão de cada vez, e o
    arquivo servido tem de dizer a mesma coisa. Como o robô republica todo
    dia, "a próxima" nunca fica velha por mais de um dia.
    """
    dias = ev.get("dias") or []
    if not dias:
        return None

    if ev.get("continuo") and len(dias) > 1:
        fim = (date.fromisoformat(dias[-1]) + timedelta(days=1)).isoformat()
        tit = ev.get("tit") or ""
        return _ics(["BEGIN:VEVENT", "UID:%s-viagem@agenda-sesc" % ev["id"],
                     "DTSTAMP:" + _agora(),
                     "DTSTART;VALUE=DATE:" + dias[0].replace("-", ""),
                     "DTEND;VALUE=DATE:" + fim.replace("-", ""),
                     "SUMMARY:" + esc_ics(tit), "LOCATION:" + _local(ev),
                     "DESCRIPTION:" + esc_ics(((ev.get("sub") + " — ") if ev.get("sub") else "") +
                                              (ev.get("link") or "")),
                     # o app põe URL: no ramo contínuo e este não punha: as duas
                     # implementações têm de dizer a mesma coisa (HANDOFF §5.3)
                     ] + (["URL:" + esc_ics(ev["link"])] if ev.get("link") else []) + [
                     # num compromisso de dias inteiros, alerta de 5 minutos
                     # não quer dizer nada: a véspera é o que importa
                     "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
                     "DESCRIPTION:" + esc_ics("Amanhã começa: " + tit),
                     "END:VALARM", "END:VEVENT"])

    pontos = [q for q in datas_de(ev) if q[:10] >= hoje] or datas_de(ev)
    if not pontos:
        return None
    q = pontos[0]
    return _ics(_vevent(ev, q, "%s-%s@agenda-sesc" % (ev["id"], q[:10].replace("-", ""))))


TETO_POR_DATA = 8   # acima disso é curso semanal; não vale 20 arquivos


def ics_uma_data(ev, quando):
    """Uma sessão só, a que a pessoa abriu no app."""
    return _ics(_vevent(ev, quando, "%s-%s@agenda-sesc" % (ev["id"], quando[:10].replace("-", ""))))


def datas_de(ev):
    """Instantes das sessões, na mesma ordem que o app mostra."""
    if ev.get("sessoes"):
        return [s["quando"] for s in ev["sessoes"]]
    return [d + "T" + (ev.get("hora") or "09:00") for d in (ev.get("dias") or [])]


def ics_inscricao(ev):
    """Só a abertura da inscrição ou da venda — um compromisso, como pedido.

    Para quem lê é a mesma pergunta ("quando abre?"), então inscrição e venda
    entram pela mesma porta; encerramento e sorteio ficam na descrição, e não
    viram compromissos extras que ninguém pediu.
    """
    i = ev.get("inscricao") or {}
    quando = i.get("inscricao") or ev.get("vendaOnline") or ev.get("vendaPresencial")
    if not quando:
        return None
    titulo = ("Abrem as inscrições: " if i.get("inscricao") else "Abre a venda: ") + (ev.get("tit") or "")
    extra = []
    if i.get("inscricaoFim"):
        extra.append("Encerra em " + i["inscricaoFim"][:10])
    if i.get("sorteio"):
        extra.append("Sorteio em " + i["sorteio"][:10])
    if ev.get("vendaOnline"):
        extra.append("Venda on-line " + ev["vendaOnline"][:16].replace("T", " "))

    inicio = quando if "T" in str(quando) else str(quando) + "T10:00"
    fim = (_quando(inicio) + timedelta(minutes=DUR_PADRAO)).astimezone(timezone.utc)
    corpo = ["BEGIN:VEVENT", "UID:%s-insc@agenda-sesc" % ev["id"],
             "DTSTAMP:" + _agora(),
             "DTSTART:" + _stamp(inicio), "DTEND:" + fim.strftime("%Y%m%dT%H%M%SZ"),
             "SUMMARY:" + esc_ics(titulo), "LOCATION:" + _local(ev),
             "DESCRIPTION:" + esc_ics(". ".join(extra + [ev.get("urlCompra") or ev.get("link") or ""]))]
    if ev.get("link"):
        corpo.append("URL:" + esc_ics(ev["link"]))
    return _ics(corpo + _alarmes(titulo))


def manifest(base):
    return json.dumps({
        "name": NOME,
        "short_name": NOME_CURTO,
        "description": DESC,
        "start_url": base,
        "scope": base,
        "display": "standalone",
        "orientation": "portrait",
        "background_color": FUNDO,
        "theme_color": TEMA,
        "lang": "pt-BR",
        "icons": [
            {"src": base + "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": base + "icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": base + "icon-maskable.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    }, ensure_ascii=False, indent=2)


SW = """/* Service worker do Acontece no SESC.
   Casca: cache primeiro (abre offline e instantâneo).
   Dados: rede primeiro, cache como reserva (sempre o mais novo que houver). */
const VERSAO = 'agenda-sesc-%(versao)s';
const CASCA = [%(casca)s];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== VERSAO).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Um .ics só abre o app de calendario se chegar como text/calendar.
  // Nem todo host declara esse tipo (o http.server do Python manda
  // application/octet-stream), entao o proprio worker corrige o cabecalho.
  if (url.pathname.endsWith('.ics')) {
    e.respondWith(
      fetch(req).then((r) => r.blob().then((b) => new Response(b, {
        status: r.status,
        headers: {
          'Content-Type': 'text/calendar; charset=utf-8',
          'Content-Disposition': 'inline'
        }
      })))
    );
    return;
  }

  // A base e o arquivo magro das listas de contemplados: rede primeiro,
  // cache como rede de segurança. O segundo é refeito de 20 em 20 minutos na
  // tarde do sorteio — servi-lo do cache devolveria justamente a resposta
  // velha que o botão "Consultar" existe para não dar.
  if (url.pathname.endsWith('eventos.json') || url.pathname.endsWith('sorteados.json')) {
    e.respondWith(
      fetch(req)
        .then((r) => {
          const copia = r.clone();
          caches.open(VERSAO).then((c) => c.put(req, copia));
          return r;
        })
        .catch(() => caches.match(req, { ignoreSearch: true }))
    );
    return;
  }

  e.respondWith(
    caches.match(req, { ignoreSearch: true }).then((hit) => hit || fetch(req))
  );
});
"""


# ------------------------------------------------------------------ medicao
# Sem cookie, sem impressao digital do navegador e sem guardar IP - por isso
# nao exige banner de consentimento, e o rodape do app diz o que e medido.
#
# O script entra SO aqui, na saida publicada. Se fosse parar no prototipo.html,
# a previa por link entraria na contagem. Vazio desliga tudo.
#
# Verificado no navegador: o count.js recusa contar em localhost por conta
# propria ("not counting because of: localhost"), entao teste local nao suja
# o painel nem por acidente. O endereco vai com https explicito - relativo a
# protocolo virava http:// numa pagina servida por http, e o Pages e https.
GOATCOUNTER = "https://acontecesesc.goatcounter.com/count"

MEDICAO = """
<script data-goatcounter="%(gc)s" async src="https://gc.zgo.at/count.js"></script>
"""


CABECA = """<link rel="manifest" href="%(base)smanifest.webmanifest">
<meta name="theme-color" content="%(tema)s">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="%(curto)s">
<link rel="apple-touch-icon" href="%(base)sicon-192.png">
<meta name="description" content="%(desc)s">
"""

REGISTRO = """
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('%(base)ssw.js').catch(function () {});
  });
}
</script>
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--html", default="prototipo.html")
    p.add_argument("--dados", default=os.path.join("dados", "eventos.json"))
    p.add_argument("--saida", default="web")
    p.add_argument("--base", default="./",
                   help="caminho onde o app vai morar; use /repo/ no GitHub Pages")
    p.add_argument("--horizonte", type=int, default=60,
                   help="dias à frente para a regra de retenção")
    args = p.parse_args()

    base = args.base if args.base.endswith("/") else args.base + "/"

    os.makedirs(os.path.join(args.saida, "dados"), exist_ok=True)

    with open(args.html, encoding="utf-8") as f:
        html = f.read()

    # o embutido continua ali como reserva offline; o JSON servido é que manda
    # ---- o instantaneo embutido sai da versao publicada ----
    # `embutir.py` grava a base inteira dentro do HTML para que a previa por
    # link funcione sem servidor. No site isso e peso puro: o app busca
    # `dados/eventos.json` na abertura e substitui o embutido segundos depois.
    # Medido: index.html 1,07 MB + eventos.json 0,47 MB comprimidos, dos quais
    # 1,02 MB eram baixados so para serem descartados. Sem o instantaneo a
    # casca sozinha sao 53 KB, e a primeira carga cai a cerca de um terco.
    #
    # A costura ja existia: embutir.py delimita os dados entre marcadores.
    ini = html.find("/* DADOS:INICIO */")
    fim = html.find("/* DADOS:FIM */")
    if ini == -1 or fim == -1 or fim < ini:
        raise SystemExit("Marcadores /* DADOS:INICIO */ ... /* DADOS:FIM */ nao "
                         "encontrados em %s - rode embutir.py antes." % args.html)
    html = (html[:ini]
            + "/* DADOS:INICIO */" + chr(10)
            + "  var DADOS = {};   /* a base vem de dados/eventos.json */" + chr(10)
            + "  " + html[fim:])

    cab = CABECA % {"base": base, "tema": TEMA, "curto": NOME_CURTO, "desc": DESC}
    if GOATCOUNTER:
        cab += MEDICAO % {"gc": GOATCOUNTER}
    html = re.sub(r"(<title>.*?</title>)", lambda m: m.group(1) + "\n" + cab, html, count=1)
    html += REGISTRO % {"base": base}

    with open(os.path.join(args.saida, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    # eventos.json sem descrição; cada descrição vira um arquivo próprio,
    # buscado só quando alguém abre aquele evento
    with open(args.dados, encoding="utf-8") as f:
        bruto = json.load(f)

    hoje = max(coletor.agora_br().date().isoformat(), bruto["janela"]["de"])
    bruto["sorteaveis"] = regras.indice_sorteios(bruto["eventos"])
    bruto["eventos"] = regras.aplicar(bruto["eventos"], hoje, args.horizonte)

    dir_desc = os.path.join(args.saida, "dados", "desc")
    if os.path.isdir(dir_desc):
        shutil.rmtree(dir_desc)
    os.makedirs(dir_desc, exist_ok=True)

    n_desc = 0
    magro = dict(bruto)
    magro["eventos"] = []
    for ev in bruto["eventos"]:
        d = ev.pop("desc", None)
        if d:
            with open(os.path.join(dir_desc, "%s.json" % ev["id"]), "w", encoding="utf-8") as f:
                json.dump({"desc": d}, f, ensure_ascii=False, separators=(",", ":"))
            n_desc += 1
        magro["eventos"].append(ev)

    with open(os.path.join(args.saida, "dados", "eventos.json"), "w", encoding="utf-8") as f:
        json.dump(magro, f, ensure_ascii=False, separators=(",", ":"))
    print("%d descrições em arquivos separados" % n_desc)

    # Arquivos .ics servidos por URL real. É isto que abre o app de
    # calendário no celular: um blob: não tem endereço que o sistema
    # reconheça, então o navegador sempre o trata como download.
    dir_ics = os.path.join(args.saida, "dados", "ics")
    if os.path.isdir(dir_ics):
        shutil.rmtree(dir_ics)
    os.makedirs(dir_ics, exist_ok=True)

    def grava(nome, conteudo):
        with open(os.path.join(dir_ics, nome), "w", encoding="utf-8", newline="") as f:
            f.write(conteudo)

    # endereço por unidade: é o mesmo para toda a casa e cobre as 43,
    # inclusive as que não vendem ingresso e por isso não tinham endereço
    ENDERECOS.clear()
    for u in bruto.get("unidades") or []:
        if u.get("endereco"):
            ENDERECOS[u["nome"]] = u["endereco"]

    n_ics = n_ins = n_dia = 0
    for ev in magro["eventos"]:
        conteudo = ics_evento(ev, hoje)
        if conteudo:
            grava("%s.ics" % ev["id"], conteudo)
            n_ics += 1

        conteudo = ics_inscricao(ev)
        if conteudo:
            grava("%s-insc.ics" % ev["id"], conteudo)
            n_ins += 1

        # um arquivo por sessão, para quem escolhe a data no app
        datas = datas_de(ev)
        if not ev.get("continuo") and 1 < len(datas) <= TETO_POR_DATA:
            for q in datas:
                grava("%s-%s.ics" % (ev["id"], q[:10].replace("-", "")),
                      ics_uma_data(ev, q))
                n_dia += 1
            ev["icsd"] = 1        # o app só linka o que existe

    print("%d .ics de evento · %d por sessão · %d de inscrição" % (n_ics, n_dia, n_ins))

    # regrava o eventos.json com a marca icsd
    with open(os.path.join(args.saida, "dados", "eventos.json"), "w", encoding="utf-8") as f:
        json.dump(magro, f, ensure_ascii=False, separators=(",", ":"))

    # O arquivo magro das listas de contemplados. É o que o botão "Consultar"
    # busca: baixar 3,5 MB para conferir oito letras seria cobrar de quem usa
    # o preço de um problema nosso. `sorteios.py` o refaz de 20 em 20 minutos
    # na janela do sorteio; aqui ele é só copiado, e recriado a partir da base
    # quando ainda não existe (primeira publicação, ou build sem a rodada
    # leve). Sem ele o app cai de volta na base inteira, e não quebra.
    fonte_sort = os.path.join(os.path.dirname(args.dados) or ".",
                              "sorteados.json")
    sorteados = None
    if os.path.exists(fonte_sort):
        # Truncado por uma escrita interrompida, ou sobra de um teste à mão:
        # um `json.load` sem proteção aqui derrubaria a publicação inteira com
        # traceback, quando a base ao lado tem tudo o que é preciso para
        # refazer o arquivo. `sorteios.py` já protege a leitura equivalente.
        try:
            with open(fonte_sort, encoding="utf-8") as f:
                sorteados = json.load(f)
            if not isinstance(sorteados.get("sorteados"), dict):
                raise ValueError("sem o mapa de sorteados")
        except Exception as e:
            print("%s ilegível (%s): refazendo a partir da base" % (fonte_sort, e))
            sorteados = None
    if sorteados is None:
        # os dois lados: a lista sai depois de a inscrição fechar, e é
        # justamente aí que o passeio sai da retenção e só existe no índice
        todos = list(bruto["eventos"]) + list(bruto.get("sorteaveis") or [])
        sorteados = {"geradoEm": bruto.get("geradoEm"),
                     "sorteados": {str(e["id"]): e["sorteados"]
                                   for e in todos if e.get("sorteados")}}
    with open(os.path.join(args.saida, "dados", "sorteados.json"), "w",
              encoding="utf-8") as f:
        json.dump(sorteados, f, ensure_ascii=False, separators=(",", ":"))
    # o carimbo também entra na base, para a primeira tela já dizer de quando
    # é a conferência mesmo antes de o arquivo magro ser buscado (ou offline)
    magro["sorteadosEm"] = sorteados.get("geradoEm")
    with open(os.path.join(args.saida, "dados", "eventos.json"), "w", encoding="utf-8") as f:
        json.dump(magro, f, ensure_ascii=False, separators=(",", ":"))
    print("%d listas de contemplados em dados/sorteados.json"
          % len(sorteados.get("sorteados") or {}))

    with open(os.path.join(args.saida, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        f.write(manifest(base))

    png(os.path.join(args.saida, "icon-192.png"), 192)
    png(os.path.join(args.saida, "icon-512.png"), 512)
    png(os.path.join(args.saida, "icon-maskable.png"), 512, maskable=True)

    # A versão do cache é o resumo do HTML publicado, não a data da coleta.
    # Enquanto era `geradoEm`, qualquer mudança no app entre duas coletas não
    # trocava o nome do cache: o worker seguia servindo a casca antiga, e quem
    # já tinha instalado nunca via a mudança. O sintoma era sempre o mesmo —
    # "mexi no CSS e a tela não mudou".
    with open(os.path.join(args.saida, "index.html"), "rb") as f:
        versao = hashlib.sha1(f.read()).hexdigest()[:12]

    casca = ", ".join("'%s%s'" % (base, n) for n in
                      ["", "index.html", "manifest.webmanifest",
                       "icon-192.png", "icon-512.png"])
    with open(os.path.join(args.saida, "sw.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(SW % {"versao": versao, "casca": casca})

    # o GitHub Pages ignora pastas com _ e roda Jekyll sem isso
    open(os.path.join(args.saida, ".nojekyll"), "w").close()

    tam = sum(os.path.getsize(os.path.join(dp, n))
              for dp, _, ns in os.walk(args.saida) for n in ns)
    print("web/ pronto · %d eventos · %.0f KB no total · base %s"
          % (len(magro["eventos"]), tam / 1024, base))
    print("Teste local:  python -m http.server 8000 --directory web")


if __name__ == "__main__":
    main()
