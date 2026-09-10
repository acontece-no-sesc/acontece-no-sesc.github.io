#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Releitura rápida das listas de contemplados — só os sorteios do dia.

Por que existe
--------------
O botão "Consultar" do app existe para dar a resposta AGORA, e não conseguia:
o navegador não lê a página da atividade (o portal libera CORS no `/wp-json` e
recusa em todo o HTML — reconferido em 09/09/2026, no Chrome, a partir do
endereço de produção). Então a única notícia que o app tem sobre um sorteio é
o que veio na nossa coleta.

E a coleta era de madrugada. O Sesc publica os contemplados às 14h ou às 16h,
na página da atividade. Com uma passada por dia, a lista NUNCA chegava no dia:
quem tinha o código abria o app na tarde do sorteio e lia que o resultado não
havia saído, com a lista já na tela do portal. Foi o caso de "Jundiaí: os
caminhos das águas" — 13 contemplados publicados às 14h, coleta das 10h.

`detalhes.py --html` levaria 70 minutos e bateria em 3 mil páginas para
resolver isso; a cada 20 minutos seria abuso de um portal que não é nosso.
Este script faz o recorte mínimo: **as páginas dos sorteios cuja divulgação é
de hoje (ou dos últimos dias) e que ainda não têm lista na base**. Hoje são
duas. O teto de `--max` está aí para que um dia estranho não vire varredura.

O que ele escreve
-----------------
1. `sorteados` dentro do próprio `dados/eventos.json`, para o pacote grande
   continuar completo;
2. `dados/sorteados.json` — alguns KB, só `{id: [códigos]}`, que é o arquivo
   que o "Consultar" busca. Baixar 3,5 MB para conferir oito letras seria
   cobrar da pessoa o preço de um problema nosso.

Uso:

    python sorteios.py                 # sorteios de hoje e dos 2 dias anteriores
    python sorteios.py --dias 7        # janela maior, para recuperar atraso
    python sorteios.py --max 40        # teto de páginas
"""

import argparse
import json
import os
import time
from datetime import date, timedelta

import coletor
import detalhes

PAUSA = 1.0          # segundos entre páginas — o portal não é nosso
MAX_PADRAO = 40


def _dia(v):
    return v[:10] if isinstance(v, str) and len(v) >= 10 else None


def pendentes(eventos, hoje, dias):
    """Sorteios cuja divulgação já saiu (ou sai hoje) e cuja lista não temos.

    A janela olha para trás porque a divulgação atrasa: o Sesc marca 14h e
    publica às 16h, e um dia em que a Action falhe não pode custar a lista
    inteira. Quem já tem `sorteados` não volta à fila — a lista publicada não
    muda, e reler seria bater na página do portal à toa.
    """
    # O limite sai do `hoje` RECEBIDO, não do relógio. Lendo o relógio, a
    # função devolvia coisas diferentes conforme a hora em que fosse chamada,
    # ignorando o argumento que existe justamente para fixá-la — e o teste
    # que a cobre passou num dia e falhou no seguinte, sem nada ter mudado.
    limite = (date.fromisoformat(hoje) - timedelta(days=dias)).isoformat()
    fila = []
    for ev in eventos:
        if ev.get("sorteados"):
            continue
        i = ev.get("inscricao") or {}
        quando = _dia(i.get("sorteio"))
        if not quando or not ev.get("link"):
            continue
        if limite <= quando <= hoje:
            fila.append(ev)
    # o de hoje primeiro: é o que alguém está esperando neste minuto
    fila.sort(key=lambda e: _dia((e.get("inscricao") or {}).get("sorteio")),
              reverse=True)
    return fila


def ler_lista(ev):
    """Os códigos contemplados da página, ou None.

    Reusa o extrator de `detalhes.py` de propósito: são quatro cabeçalhos
    diferentes e uma trava contra prosa, e ter duas cópias dessa regra seria
    garantir que uma delas envelhecesse.
    """
    pagina = detalhes.buscar(ev["link"])
    if not pagina:
        return None
    return detalhes.sorteados_do_corpo(detalhes.limpar(pagina)) or None


def semear(eventos, caminho_anterior):
    """Costura na base o que JÁ ESTÁ PUBLICADO, antes de montar a fila.

    Sem isto a rodada leve relia a mesma página a tarde inteira. Ela só
    restaura o cache e nunca o salva (de propósito: salvar deixaria uma base
    pela metade sob a chave que a coleta grande restaura de madrugada), então
    toda execução partia da base da madrugada, sem os achados das execuções
    anteriores da própria tarde. Uma lista encontrada às 14h20 voltava para a
    fila às 14h40, às 15h, às 15h20 — vinte releituras da mesma página, num
    portal que não é nosso, contradizendo o que `pendentes` promete.

    O arquivo publicado é exatamente a memória que faltava, e o workflow já o
    baixa para decidir se republica. Devolve quantos foram costurados.
    """
    if not caminho_anterior or not os.path.exists(caminho_anterior):
        return 0
    try:
        with open(caminho_anterior, encoding="utf-8") as f:
            mapa = json.load(f).get("sorteados") or {}
    except Exception:
        return 0
    n = 0
    for ev in eventos:
        lista = mapa.get(str(ev.get("id")))
        if lista and not ev.get("sorteados"):
            ev["sorteados"] = lista
            n += 1
    return n


def novidades(mapa, caminho_anterior):
    """Quantas listas o SITE ainda não tem — não quantas esta passada leu.

    A rodada leve não guarda estado entre execuções: ela restaura a base da
    coleta da madrugada, que não tem a lista de hoje. Contar o que a passada
    leu faria as 21 rodadas de uma tarde acharem a mesma lista e republicarem
    o site 21 vezes, 20 delas para trocar um carimbo. Comparar com o arquivo
    publicado responde a pergunta certa: falta alguma coisa lá fora?

    Sem arquivo anterior (primeira publicação, site fora do ar) devolve None:
    quem chama publica por precaução. Errar publicando é barato; errar calado
    guarda a lista mais um dia.
    """
    if not caminho_anterior or not os.path.exists(caminho_anterior):
        return None
    try:
        with open(caminho_anterior, encoding="utf-8") as f:
            ja = set((json.load(f).get("sorteados") or {}).keys())
    except Exception:
        return None
    return len(set(mapa) - ja)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dados", default=os.path.join("dados", "eventos.json"))
    p.add_argument("--saida", default=os.path.join("dados", "sorteados.json"))
    p.add_argument("--dias", type=int, default=2,
                   help="quantos dias para trás ainda entram na fila")
    p.add_argument("--max", type=int, default=MAX_PADRAO,
                   help="teto de páginas lidas numa passada")
    p.add_argument("--anterior",
                   help="sorteados.json que já está publicado; a contagem "
                        "final passa a ser o que ele AINDA não tem")
    args = p.parse_args()

    with open(args.dados, encoding="utf-8") as f:
        base = json.load(f)
    eventos = base["eventos"]
    hoje = coletor.agora_br().date().isoformat()

    ja = semear(eventos, args.anterior)
    if ja:
        print("%d lista(s) já publicada(s) reaproveitada(s) — não serão relidas"
              % ja)

    fila = pendentes(eventos, hoje, args.dias)
    print("Sorteios sem lista, divulgação entre %s e %s: %d"
          % ((date.fromisoformat(hoje) - timedelta(days=args.dias)).isoformat(),
             hoje, len(fila)))
    if len(fila) > args.max:
        print("  teto de %d páginas: os %d mais recentes"
              % (args.max, args.max))
        fila = fila[:args.max]

    achados = 0
    for ev in fila:
        codigos = ler_lista(ev)
        quando = (ev.get("inscricao") or {}).get("sorteio")
        if codigos:
            ev["sorteados"] = codigos
            achados += 1
            print("  + %-52s %s · %d códigos"
                  % (ev["tit"][:52], quando, len(codigos)))
        else:
            print("  · %-52s %s · ainda sem lista na página"
                  % (ev["tit"][:52], quando))
        time.sleep(PAUSA)

    # O arquivo pequeno sai SEMPRE, mesmo sem achado novo: é o carimbo de
    # "olhamos agora" que o app mostra a quem aperta Consultar. Sem ele, uma
    # passada sem novidade seria indistinguível de uma passada que não houve.
    magro = {
        "geradoEm": coletor.agora_br().isoformat(timespec="seconds"),
        "sorteados": {str(e["id"]): e["sorteados"]
                      for e in eventos if e.get("sorteados")},
    }
    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(magro, f, ensure_ascii=False, separators=(",", ":"))

    if achados:
        with open(args.dados, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, separators=(",", ":"))

    faltam = novidades(magro["sorteados"], args.anterior)
    novas = achados if faltam is None else faltam
    if faltam is None and args.anterior:
        print("sem %s legível: publico por precaução" % args.anterior)

    print("%d lista(s) nova(s) · %d sorteios com lista no total · %s (%.1f KB)"
          % (novas, len(magro["sorteados"]), args.saida,
             os.path.getsize(args.saida) / 1024))


if __name__ == "__main__":
    main()
