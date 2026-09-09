# -*- coding: utf-8 -*-
"""Regra de retenção — o que entra no app.

O corte deixou de ser pela data do evento e passou a ser pela **data de
entrada**: o que importa para quem usa é conseguir vaga, e a inscrição de um
passeio de novembro abre em agosto. Um recorte por data de evento perderia
exatamente esse caso.

Fica no app quem satisfaz uma destas condições:

  1. a inscrição ou a venda ABRE dentro do horizonte (padrão: 60 dias);
  2. a inscrição ou a venda está ABERTA agora;
  3. o evento ACONTECE dentro do horizonte.

Sai do app o que já aconteceu e o que só acontece (e só abre) muito adiante.

O que NÃO é motivo de exclusão: ingresso esgotado e prazo de inscrição
vencido. Os dois são estado da entrada, não fim da atividade — ela acontece
de todo jeito, e quem olha a agenda de quinta quer ver o que vai ter. A tela
diz o que dá para fazer, com as etiquetas "Esgotado" e "Inscrição encerrada";
o corte não decide isso por ninguém.
"""

from datetime import date, timedelta

import coletor

DIAS_SEM_PRAZO = 3       # mesma presunção do app quando a página não publica


def _dia(v):
    return v[:10] if isinstance(v, str) and len(v) >= 10 else None


def fim_inscricao(ev):
    """Encerramento real ou presumido (abertura + 3 dias)."""
    i = ev.get("inscricao") or {}
    if not i.get("inscricao"):
        return None
    if i.get("inscricaoFim"):
        return _dia(i["inscricaoFim"])
    ini = _dia(i["inscricao"])
    if not ini:
        return None
    return (date.fromisoformat(ini) + timedelta(days=DIAS_SEM_PRAZO)).isoformat()


def barreira_fechada(ev, hoje):
    """Há evidência de que o PRAZO de entrada já passou.

    Só conta como fechada o que tem DATA. Um bloco de inscrição em prosa
    ("inscrições no local", "lista de espera") não diz nada sobre prazo, e
    tratá-lo como fechado apagaria centenas de atividades que estão de pé.

    Esgotado NÃO entra aqui. Ingresso esgotado é estado do ingresso, não fim
    da atividade: ela acontece, pode liberar devolução, e quem procura o
    evento que viu ontem precisa achá-lo — com a etiqueta dizendo que
    esgotou. Enquanto isto contava como barreira, os 41 esgotados com data
    futura sumiam da base inteira, sem explicação nenhuma na tela.
    """
    f = fim_inscricao(ev)
    if f and f < hoje:
        return True
    fim_venda = _dia(ev.get("vendaOnlineFim"))
    if fim_venda and fim_venda < hoje:
        return True
    return False


def aberta_agora(ev, hoje):
    i = ev.get("inscricao") or {}
    ini_insc = _dia(i.get("inscricao"))
    if ini_insc and ini_insc <= hoje:
        f = fim_inscricao(ev)
        if f and f >= hoje:
            return True
    venda = _dia(ev.get("vendaOnline")) or _dia(ev.get("vendaPresencial"))
    if venda and venda <= hoje and not barreira_fechada(ev, hoje):
        return True
    return False


SO_COM_INSCRICAO = (coletor.CAT_TURISMO,)


def manter(ev, hoje, horizonte):
    """`hoje` e `horizonte` são strings YYYY-MM-DD."""
    i = ev.get("inscricao") or {}

    # Nada que já aconteceu. As regras abaixo olham a data de ENTRADA (a
    # inscrição de um passeio abre meses antes), e por isso deixavam passar
    # temporada encerrada com venda ainda aberta no papel. Não há o que fazer
    # com um evento cuja última data ficou para trás.
    if not any(d >= hoje for d in (ev.get("dias") or [])):
        return False

    # Passeio de que não se sabe COMO ENTRAR não é oferta: mostrar só gera
    # expectativa vazia. O que não vale é medir isso pela data.
    #
    # A versão anterior exigia data de inscrição, e com isso apagava o passeio
    # que não tem inscrição nenhuma — "Atividade aberta, sem retirada de
    # ingressos, mediante capacidade do local", "Acesso livre", "30 vagas.
    # Senhas na Loja Sesc com 30 minutos de antecedência". Nesses a página diz
    # exatamente como entrar; o que ela não tem é data, porque não há o que
    # datar. Eram 20 passeios, entre eles os três "Outras viagens" de Campinas
    # e as caminhadas do Vila Mariana.
    #
    # Sai quem não tem NEM data NEM texto: aí a página realmente não conta
    # como se participa, e é o caso dos passeios que só guardam o resultado de
    # um sorteio antigo. Prazo vencido não entra aqui — é etiqueta, como em
    # todas as outras categorias desde que "Inscrição encerrada" existe.
    if ev.get("cat") in SO_COM_INSCRICAO:
        if not (i.get("inscricao") or (i.get("texto") or "").strip()
                or i.get("regras")):
            return False

    aberturas = [d for d in (_dia(i.get("inscricao")),
                             _dia(ev.get("vendaOnline")),
                             _dia(ev.get("vendaPresencial"))) if d]

    # 1. a entrada abre dentro do horizonte
    if any(hoje <= d <= horizonte for d in aberturas):
        return True

    # 2. a entrada já está aberta
    if aberta_agora(ev, hoje):
        return True

    # 3. acontece dentro do horizonte
    #
    # Antes esta regra exigia `not barreira_fechada`: prazo de inscrição
    # vencido tirava o evento da base. Eram 320 atividades — 242 delas cursos
    # e oficinas — que ACONTECEM nas próximas semanas e sumiam da agenda sem
    # explicação, do mesmo jeito que os esgotados sumiam. Quem quer saber o
    # que vai ter no Sesc quinta-feira quer ver o que vai ter, com a etiqueta
    # dizendo se ainda dá para entrar. Quem decide isso é a tela, não o corte.
    for d in ev.get("dias") or []:
        if hoje <= d <= horizonte:
            return True

    return False


def indice_sorteios(eventos):
    """Passeios sorteáveis, mesmo os que a retenção descarta.

    Quem já se inscreveu num sorteio precisa registrar o código depois que a
    inscrição fechou — que é exatamente quando o evento sai da agenda. Sem
    este índice, o formulário não reconhece o link.
    """
    out = []
    for e in eventos:
        i = e.get("inscricao") or {}
        if not (i.get("temSorteio") or e.get("sorteados")):
            continue
        if not e.get("link"):
            continue
        # Cópia com os mesmos campos de um evento: a aba de sorteios mostra
        # foto e abre a folha de detalhe, e o passeio já saiu da base quando o
        # resultado sai. Guardar só título e link deixava a aba cega.
        copia = {c: e.get(c) for c in (
            "id", "tit", "sub", "uni", "reg", "cat", "subcat", "publico",
            "publicos", "projeto", "gratis", "pago", "esgotado", "ingressosWeb",
            "temporada", "continuo", "hora", "dias", "link", "thumb", "img",
            "capa", "precos", "sessoes", "duracaoMin", "classificacao",
            "endereco", "geo", "urlCompra", "sorteados") if e.get(c) is not None}
        copia["inscricao"] = e.get("inscricao")
        # forma plana que o formulário de sorteio consome
        copia["sorteio"] = i.get("sorteio")
        copia["inscricaoAbre"] = i.get("inscricao")
        copia["inscricaoFim"] = i.get("inscricaoFim")
        out.append(copia)
    out.sort(key=lambda x: x["tit"])
    return out


def aplicar(eventos, hoje, dias=60, verboso=True):
    horizonte = (date.fromisoformat(hoje) + timedelta(days=dias)).isoformat()
    fica = [e for e in eventos if manter(e, hoje, horizonte)]
    if verboso:
        print("Retenção (%s a %s): %d de %d eventos"
              % (hoje, horizonte, len(fica), len(eventos)))
    return fica
