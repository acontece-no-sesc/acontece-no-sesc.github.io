#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes dos extratores, sem rede.

O HANDOFF lista uma dúzia de armadilhas de extração já resolvidas, cada uma
com uma rodada de depuração atrás. Nenhuma tinha teste: a única verificação
era `teste_detalhes.py`, que busca cinco páginas na rede, imprime o resultado
e termina com sucesso aconteça o que acontecer — diagnóstico, não teste.

Aqui os textos são reais, tirados da coleta, e as asserções conferem o que
qualquer pessoa lendo o trecho concluiria. Roda em milissegundos e não toca
na rede, então serve para a CI.

    python teste_extratores.py
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error

import detalhes

ANO = 2026
RAIZ = os.path.dirname(os.path.abspath(__file__))


def ler_fonte(nome):
    """Alguns defeitos são de fiação, não de lógica: a função certa existe e
    ninguém a chama. Esses só se pegam olhando o código que liga as peças."""
    with open(os.path.join(RAIZ, nome), encoding="utf-8") as f:
        return f.read()


# A coleta aborta quando não vê nenhum Turismo Social (a categoria governa
# três regras e sumir dela em silêncio já custou caro). Todo stub leva esta
# atividade junto, para que a trava não mascare o que o teste quer medir.
SENTINELA_TURISMO = {
    "id": 999, "titulo": "Passeio", "unidade": [{"name": "Pompeia"}],
    "dataPrimeiraSessao": "2026-09-15T09:00",
    "dataUltimaSessao": "2026-09-15T12:00",
}


def so(lista, titulo):
    """O evento de um título, na lista que a coleta devolveu."""
    achados = [e for e in lista if e["tit"] == titulo]
    assert len(achados) == 1, "%r apareceu %d vezes" % (titulo, len(achados))
    return achados[0]


def coletar_com_stub(atividade, espera_saida=None):
    """Roda `coletor.coletar` sobre UMA atividade, sem tocar na rede.

    A varredura de segurança e as travas moram dentro de `coletar`; testá-las
    de fora exigiria rede, e testá-las por leitura de fonte não pega fiação
    trocada. O stub devolve a atividade só na varredura geral — é lá que os
    dois defeitos de janela e de região aconteciam.
    """
    import contextlib
    import coletor
    from datetime import date

    estado = {"geral": 0}

    def falso_buscar(url, tentativa=1):
        if "unidades-atividades" in url:
            return [{"name": "Pompeia", "description": "Capital"}]
        if "data_inicial" in url:
            return {"atividade": [], "total": {"value": 0}}
        estado["geral"] += 1
        if estado["geral"] > 1:
            return {"atividade": [], "total": {"value": 0}}
        import copy
        sentinela = copy.deepcopy(SENTINELA_TURISMO)
        sentinela["tipos_linguagens"] = [{"titulo": coletor.CAT_TURISMO}]
        return {"atividade": [atividade, sentinela], "total": {"value": 2}}

    original, pausa = coletor.buscar, coletor.PAUSA
    coletor.buscar, coletor.PAUSA = falso_buscar, 0
    quieto = contextlib.redirect_stdout(io.StringIO())
    try:
        if espera_saida is not None:
            with espera_saida.assertRaises(SystemExit) as ctx, quieto:
                coletor.coletar(date(2026, 9, 9), date(2026, 11, 7),
                                set(coletor.REGIOES_VALIDAS))
            return str(ctx.exception)
        with quieto:
            lista, _ = coletor.coletar(date(2026, 9, 9), date(2026, 11, 7),
                                       set(coletor.REGIOES_VALIDAS))
        return lista
    finally:
        coletor.buscar, coletor.PAUSA = original, pausa


class DatasDeInscricao(unittest.TestCase):
    """`datas_do_texto` sobre trechos reais do portal."""

    def ler(self, texto, dia_evento="2026-09-02"):
        r = detalhes.datas_do_texto(texto, ANO, dia_evento)
        r.pop("texto", None)
        return r

    def test_inscricao_com_hora_e_sorteio(self):
        r = self.ler("Inscrições: 7/8 às 14h a 12/8 Sorteio: 13/8 às 15h "
                     "1ª chamada pagamento: 13 a 18/8 2ª chamada pagamento: 19 a 20/8")
        self.assertEqual(r["inscricao"], "2026-08-07T14:00")
        self.assertEqual(r["inscricaoFim"], "2026-08-12")
        self.assertEqual(r["sorteio"], "2026-08-13T15:00")
        self.assertTrue(r["temSorteio"])

    def test_intervalo_de_a_nao_inverte(self):
        """"Inscrições de 6 a 10/8" lia o FIM como início.

        O padrão era ancorado e falhava no "de ", então a primeira data que
        casava era a do fim.
        """
        r = self.ler("Inscrições de 2/7 a 7/7.")
        self.assertEqual(r["inscricao"], "2026-07-02", "o início é 2/7, não 7/7")
        self.assertEqual(r["inscricaoFim"], "2026-07-07")

    def test_para_o_sorteio_nao_vira_data_do_sorteio(self):
        """"Inscrição para o sorteio:" fazia o período virar data do sorteio.

        Resolvido com lookbehind. O trecho tem as duas coisas: a inscrição
        abre 6/8 e o resultado sai 14/8 — nunca o contrário.
        """
        r = self.ler("Inscrição para o sorteio: a partir das 14h do dia 6/8 até às "
                     "18h do dia 11/8. Divulgação dos contemplados: a partir das 14h "
                     "do dia 14/8. Confirmação de vagas e pagamento: 14 a 18/8.",
                     dia_evento="2026-09-19")
        self.assertEqual(r["inscricao"], "2026-08-06")
        self.assertEqual(r["inscricaoFim"], "2026-08-11")
        self.assertEqual(r["sorteio"], "2026-08-14")
        self.assertNotEqual(r["sorteio"], r["inscricao"])

    def test_divulgacao_dos_sorteados_sem_dois_pontos(self):
        """Cabeçalhos alternativos do resultado, sem "Sorteio:"."""
        r = self.ler("Inscrições: dia 18/8, a partir das 14h, até 20/8, às 14h "
                     "Divulgação dos sorteados: dia 25/8, às 14h "
                     "Datas para pagamento: dias 27 e 28/8",
                     dia_evento="2026-09-12")
        self.assertEqual(r["sorteio"], "2026-08-25T14:00")
        self.assertEqual(r["inscricao"], "2026-08-18")
        self.assertEqual(r["pagamento"], "2026-08-27")

    def test_inscricao_no_singular_com_til(self):
        """"Inscrição" tem ã; o padrão exigia [õo] e nunca casava.

        Corrigiu 78 eventos quando foi achado.
        """
        r = self.ler("Inscrição: 5/8 às 10h.")
        self.assertEqual(r.get("inscricao"), "2026-08-05T10:00")


class RegrasRecorrentes(unittest.TestCase):
    """`regras_de_inscricao`: a extração mais delicada do projeto.

    São duas datas diferentes para duas pessoas diferentes, e nenhuma existe
    como data em lugar nenhum da fonte. Errar aqui manda alguém ao Sesc no
    dia em que não pode se inscrever.
    """

    CANONICO = ("As vagas disponíveis são liberadas prioritariamente para pessoas "
                "portadoras de Credencial Plena na 1ª e na 3ª quinta-feira de cada mês "
                "a partir das 18h. Caso estas vagas não sejam preenchidas, serão "
                "disponibilizadas para o público em geral na 2ª e na 4ª quarta-feira "
                "a partir das 14h.")

    def test_extrai_as_duas_janelas(self):
        """O caso do HANDOFF, que na prática só entregava uma.

        A segunda oração não repete "de cada mês", e o padrão exigia o sufixo
        em toda ocorrência — então a janela do público geral não existia.
        Medido na base: os 5 eventos com regra tinham 1 janela cada, todas
        "plena". Quem não tem credencial via a data que não era dele.
        """
        rs = detalhes.regras_de_inscricao(self.CANONICO)
        self.assertEqual(len(rs), 2, "faltou a janela do público geral")
        plena = [r for r in rs if r["quem"] == "plena"][0]
        geral = [r for r in rs if r["quem"] == "geral"][0]
        self.assertEqual(plena, {"quem": "plena", "semanas": [1, 3], "dow": 4, "hora": "18:00"})
        self.assertEqual(geral, {"quem": "geral", "semanas": [2, 4], "dow": 3, "hora": "14:00"})

    def test_publico_declarado_depois_da_regra(self):
        """"…a partir das 18h, para quem possui credencial plena".

        Ler só para trás fazia esta forma virar "todos", e com isso quem não
        tem credencial via uma data que não era dele.
        """
        rs = detalhes.regras_de_inscricao(
            "As vagas são liberadas na 1ª e 3ª quinta-feira de cada mês, a partir "
            "das 18h, para quem possui credencial plena.")
        self.assertEqual([r["quem"] for r in rs], ["plena"])

    def test_ordinal_por_extenso_nao_vira_data(self):
        """"primeira e segunda quinta-feira": "segunda" é ordinal e dia.

        Ler por extenso daria data errada, que é pior do que não dar data.
        """
        self.assertIsNone(detalhes.regras_de_inscricao(
            "Vagas liberadas na primeira e segunda quinta-feira de cada mês "
            "a partir das 18h."))

    def test_texto_solto_nao_vira_regra(self):
        """A forma sem "de cada mês" só vale acompanhando uma que o tenha.

        Sem esta trava, "a 1ª sexta-feira do festival, a partir das 20h"
        viraria regra de inscrição.
        """
        self.assertIsNone(detalhes.regras_de_inscricao(
            "O festival abre na 1ª sexta-feira, a partir das 20h, com show."))

    def test_sem_hora_nao_vira_regra(self):
        """Sem hora não há janela: metade da informação é pior que nenhuma."""
        self.assertIsNone(detalhes.regras_de_inscricao(
            "As vagas são liberadas na 1ª quinta-feira de cada mês."))


class ListaDeContemplados(unittest.TestCase):
    """`sorteados_do_corpo`: quatro cabeçalhos, e prosa que os imita."""

    def test_prosa_nao_vira_lista(self):
        """"os sorteados poderão efetivar o pagamento" não traz código.

        A trava é exigir código nos dois primeiros tokens depois do
        cabeçalho, o que em prosa nunca acontece.
        """
        self.assertFalse(detalhes.sorteados_do_corpo(
            "Os sorteados poderão efetivar o pagamento na Central de "
            "Atendimento a partir do dia 14/8, mediante apresentação de "
            "documento com foto."))

    def test_cabecalho_seco_com_codigos(self):
        """"CONTEMPLADOS" sozinho, logo depois de "Compartilhe:"."""
        achado = detalhes.sorteados_do_corpo(
            "Compartilhe: CONTEMPLADOS YDYNALVB WDSOMKPL QRTZAABC MNOPQRST")
        self.assertTrue(achado, "cabeçalho seco não foi reconhecido")
        self.assertIn("YDYNALVB", achado)


class EnfeiteAntesDaLista(unittest.TestCase):
    """"Resultado do sorteio (em ordem alfabética): DOKFCHWU…"

    O parêntese põe três tokens de enfeite entre o cabeçalho e o primeiro
    código, e a trava contra prosa (que desiste no terceiro token sem código)
    desistia justamente ali. Eram 3 dos 13 sorteios já vencidos em 09/09 —
    28, 14 e 13 códigos publicados na página — que o app dava como pendentes,
    e continuaria dando para sempre, porque a página não muda mais.
    """

    def test_parentese_entre_o_cabecalho_e_a_lista(self):
        texto = ("Resultado do sorteio (em ordem alfabética): DOKFCHWU "
                 "EKEFDFKT ENKSPZXC IBLYRDPZ Havendo necessidade operacional")
        self.assertEqual(detalhes.sorteados_do_corpo(texto),
                         ["DOKFCHWU", "EKEFDFKT", "ENKSPZXC", "IBLYRDPZ"])

    def test_a_trava_contra_prosa_continua_de_pe(self):
        """Alargar a janela de tokens teria trazido o falso positivo de volta.

        Descartar o PARÊNTESE é seguro porque prosa não começa com "(".
        Descartar "os primeiros N tokens" não seria: bastariam duas palavras
        em caixa alta para uma frase virar lista de contemplados.
        """
        self.assertEqual(detalhes.sorteados_do_corpo(
            "Resultado do sorteio: os sorteados poderão efetivar o pagamento"), [])
        self.assertEqual(detalhes.sorteados_do_corpo(
            "Resultado do sorteio será divulgado na CENTRAL DE ATENDIMENTO"), [])

    def test_o_enfeite_descartado_e_curto(self):
        """Um "(" solto no meio da página não pode comer o que vem depois.

        Por isso o parêntese só é descartado até 60 caracteres: sem esse
        limite, um parêntese longo — ou um "(" sem fechamento por perto —
        engoliria a lista inteira junto.
        """
        self.assertIsNone(detalhes.RE_ENFEITE.match("(" + "x" * 200 + ") ABCDEFGH"))
        self.assertIsNotNone(
            detalhes.RE_ENFEITE.match(" (em ordem alfabética): ABCDEFGH"))

    def test_lista_sem_enfeite_continua_igual(self):
        self.assertEqual(detalhes.sorteados_do_corpo(
            "Resultado do sorteio: YVOPXAGP UDESMROF fim"),
            ["YVOPXAGP", "UDESMROF"])


class LimpezaDeTexto(unittest.TestCase):
    def test_script_nao_vaza_para_a_descricao(self):
        """As tags saíam e o código ficava, indo parar na descrição."""
        sujo = ("<p>Uma peça sobre memória.</p>"
                "<script>var x = 1; alert('oi');</script>"
                "<style>.a{color:red}</style>")
        limpo = detalhes.limpar(sujo)
        for vazamento in ("var x", "alert", "color:red"):
            self.assertNotIn(vazamento, limpo)
        self.assertIn("memória", limpo)



class ReprocessamentoSemRede(unittest.TestCase):
    """`reparse.py` relê as datas sobre o texto guardado.

    Ele substituía o bloco `inscricao` inteiro e enumerava o que preservar.
    A enumeração citava `sorteados` (que vive no topo do evento, não ali) e
    esquecia `regras` — as janelas recorrentes de credencial, extraídas do
    HTML por `detalhes.regras_de_inscricao` e destruídas um passo depois, no
    mesmo workflow, sem contador nenhum acusar.

    Ficava invisível por acaso: os eventos com `regras` não têm
    `inscricao.texto`, então o laço os pula antes de chegar lá.
    """

    def reprocessar(self, ins, ano=2026, dia="2026-09-20"):
        """Exercita `reparse.reler`, que é o que `main` chama.

        A primeira versão deste teste reimplementava a lógica aqui dentro, e
        por isso continuava passando com o bug de volta — testava a minha
        cópia, não o código. Uma verificação que não falha quando o defeito
        retorna não é verificação.
        """
        import reparse
        return reparse.reler(ins, ano, dia)

    def test_regras_sobrevivem_a_releitura(self):
        regra = [{"quem": "plena", "semanas": [1, 3], "dow": 4, "hora": "18:00"}]
        saida = self.reprocessar({
            "texto": "Inscrições: 7/8 às 14h a 12/8",
            "regras": regra,
            "temSorteio": False,
        })
        self.assertEqual(saida.get("regras"), regra,
                         "o reprocessamento apagou as janelas de credencial")
        self.assertEqual(saida["inscricao"], "2026-08-07T14:00",
                         "e ainda assim precisa reler a data")

    def test_main_usa_a_funcao_que_preserva(self):
        """A função certa não adianta se `main` não a chamar."""
        fonte = ler_fonte("reparse.py")
        self.assertIn("novo = reler(ins,", fonte,
                      "reparse.main deixou de usar reler(): a preservação das "
                      "chaves que não são data some junto")

    def test_chave_desconhecida_tambem_sobrevive(self):
        """Lista positiva: o que a releitura não produz, ela não apaga."""
        saida = self.reprocessar({
            "texto": "Inscrições de 2/7 a 7/7.",
            "campoFuturo": "algo que ainda não existe",
        })
        self.assertEqual(saida.get("campoFuturo"), "algo que ainda não existe")


class PisosDeVolume(unittest.TestCase):
    """Degradação silenciosa é a assinatura deste pipeline.

    Coleta vazia atravessava tudo — carimbo, retenção, publicação — e saía
    com sucesso, publicando um app vazio. E o arquivo vazio virava a coleta
    anterior do dia seguinte, marcando o catálogo inteiro como novidade.
    """

    def setUp(self):
        import coletor
        self.coletor = coletor
        self.tmp = tempfile.mkdtemp()
        self.anterior = os.path.join(self.tmp, "ant.json")
        with open(self.anterior, "w", encoding="utf-8") as f:
            json.dump({"eventos": [{"id": str(i)} for i in range(3000)]}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_coleta_vazia_aborta(self):
        with self.assertRaises(SystemExit):
            self.coletor.conferir_volume([], self.anterior)

    def test_desabamento_aborta(self):
        with self.assertRaises(SystemExit):
            self.coletor.conferir_volume([{"id": str(i)} for i in range(500)],
                                         self.anterior)

    def test_coleta_normal_passa(self):
        self.coletor.conferir_volume([{"id": str(i)} for i in range(3100)],
                                     self.anterior)

    def test_primeira_execucao_passa(self):
        """Sem base anterior só o piso absoluto vale."""
        self.coletor.conferir_volume([{"id": str(i)} for i in range(3000)],
                                     os.path.join(self.tmp, "nao_existe.json"))

    def test_main_confere_antes_de_gravar(self):
        """O piso só serve se `main` o consultar antes do json.dump.

        A primeira versão deste teste chamava `conferir_volume` direto e
        passava mesmo com a chamada removida de `main` — testava a função,
        não a proteção.
        """
        fonte = ler_fonte("coletor.py")
        i_piso = fonte.find("conferir_volume(eventos, args.saida)")
        i_grava = fonte.find("json.dump(saida, f")
        self.assertGreater(i_piso, 0, "coletor.main não confere o volume")
        self.assertLess(i_piso, i_grava,
                        "o piso precisa vir ANTES de gravar o arquivo")


class _RelogioFalso:
    """Entra no lugar de `time` dentro do coletor: anota em vez de dormir.

    Sem isso cada teste de paciência levaria os 77 segundos de verdade.
    """

    def __init__(self):
        self.dormiu = []

    def sleep(self, s):
        self.dormiu.append(s)

    def time(self):
        return 0.0


class PacienciaComOPortal(unittest.TestCase):
    """O portal cai e volta, e a coleta desistia em 6 segundos.

    Em 31/10/2026 as duas rodadas do dia morreram no mesmo passo, sem
    coletar um único dia: eram 3 tentativas esperando 2s e 4s, e a primeira
    requisição — a das unidades, antes de qualquer data — falhou as três. Na
    manhã seguinte a fonte respondia inteira, com as 43 unidades e os 2.971
    itens no lugar. Não faltou código: faltou paciência.

    O outro lado da moeda é insistir onde não há o que esperar. 404 não vira
    200 na sexta tentativa, e repetir gasta 77 segundos para chegar na mesma
    resposta — além de fazer "falhou 6 vezes" parecer instabilidade quando a
    verdade é que a URL mudou.
    """

    def setUp(self):
        import coletor
        self.coletor = coletor
        self.relogio = _RelogioFalso()
        self.urlopen = coletor.urllib.request.urlopen
        coletor.time = self.relogio

    def tearDown(self):
        import time
        self.coletor.urllib.request.urlopen = self.urlopen
        self.coletor.time = time

    def fila(self, *respostas):
        """Põe uma fila no lugar de `urlopen`.

        Cada item é um erro a levantar ou um corpo a devolver. O que sobrar na
        fila é tentativa que não aconteceu — e isso também é asserção.
        """
        restante = list(respostas)
        self.pedidos = []

        class _Corpo:
            def __init__(self, texto):
                self.texto = texto

            def read(self):
                return self.texto.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def falso_urlopen(req, timeout=None):
            self.pedidos.append(req.full_url)
            item = restante.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Corpo(item)

        self.coletor.urllib.request.urlopen = falso_urlopen
        self.restante = restante

    @staticmethod
    def http(codigo, cabecalhos=None):
        return urllib.error.HTTPError("https://www.sescsp.org.br/x", codigo,
                                      "erro", cabecalhos or {}, None)

    def test_sobrevive_a_cinco_tropecos(self):
        """O caso de 31/10: a fonte está lá, só não atendeu de primeira."""
        caiu = urllib.error.URLError("conexão recusada")
        self.fila(caiu, caiu, caiu, caiu, caiu, '{"ok": true}')
        self.assertEqual(self.coletor.buscar("https://x/y"), {"ok": True})
        self.assertEqual(len(self.pedidos), 6)
        self.assertEqual(self.relogio.dormiu, list(self.coletor.ESPERAS))

    def test_a_paciencia_e_de_pelo_menos_um_minuto(self):
        """6 segundos foi o número que perdeu dois dias de coleta."""
        self.assertGreaterEqual(
            sum(self.coletor.ESPERAS), 60,
            "com menos de um minuto de espera somada a rodada volta a morrer "
            "no primeiro piscar do portal")

    def test_cada_tentativa_tem_a_sua_espera(self):
        """`espera_de` indexa `ESPERAS` pela tentativa.

        Subir `TENTATIVAS` sem esticar `ESPERAS` não quebra nada à vista: a
        última espera passa a ser reusada em silêncio, e a paciência anunciada
        no comentário deixa de ser a real.
        """
        self.assertEqual(self.coletor.TENTATIVAS, len(self.coletor.ESPERAS) + 1)

    def test_desiste_e_levanta_depois_da_ultima(self):
        caiu = urllib.error.URLError("conexão recusada")
        self.fila(*[caiu] * self.coletor.TENTATIVAS)
        with self.assertRaises(urllib.error.URLError):
            self.coletor.buscar("https://x/y")
        self.assertEqual(len(self.pedidos), self.coletor.TENTATIVAS)

    def test_nao_insiste_em_404(self):
        self.fila(self.http(404), '{"nunca": "chamado"}')
        with self.assertRaises(urllib.error.HTTPError):
            self.coletor.buscar("https://x/y")
        self.assertEqual(len(self.pedidos), 1)
        self.assertEqual(self.relogio.dormiu, [])

    def test_insiste_no_403_do_waf(self):
        """O portal barra com 403 o tráfego que achou suspeito, e desbarra."""
        self.fila(self.http(403), '{"ok": true}')
        self.assertEqual(self.coletor.buscar("https://x/y"), {"ok": True})
        self.assertEqual(len(self.pedidos), 2)

    def test_pagina_de_manutencao_conta_como_falha(self):
        """A página do WAF chega com HTTP 200 e HTML dentro.

        Antes, isso levantava `JSONDecodeError` fora do `except` e derrubava a
        rodada na primeira — sem repetir e sem dizer que era a fonte.
        """
        self.fila("<html>em manutenção</html>", '{"ok": true}')
        self.assertEqual(self.coletor.buscar("https://x/y"), {"ok": True})
        self.assertEqual(len(self.pedidos), 2)

    def test_obedece_o_retry_after(self):
        self.fila(self.http(429, {"Retry-After": "7"}), '{"ok": true}')
        self.coletor.buscar("https://x/y")
        self.assertEqual(self.relogio.dormiu, [7])

    def test_retry_after_absurdo_nao_para_a_rodada(self):
        """Meia hora de espera estoura o job; o teto devolve o nosso palpite."""
        self.fila(self.http(503, {"Retry-After": "3600"}), '{"ok": true}')
        self.coletor.buscar("https://x/y")
        self.assertEqual(self.relogio.dormiu, [self.coletor.ESPERAS[0]])


class DiaQueNaoRespondeNaoDerrubaARodada(unittest.TestCase):
    """Um dia perdido custava os outros 59.

    A coleta é uma consulta por dia, e qualquer uma delas levantando matava a
    rodada inteira: nada gravado, nenhuma novidade marcada, app com a base de
    ontem. Agora o dia vai para um caderno e a varredura de segurança ainda
    recupera o evento pelas datas do próprio registro — o que se perde é a
    precisão dos dias intermediários, não o evento.

    O risco do conserto é o avesso do defeito: tolerar demais, calado. As
    travas são a outra metade do teste.
    """

    # Janela de 20 dias: `FRACAO_DIAS_FALHOS` dá teto 2, então o terceiro dia
    # perdido é o que aborta.
    DE = (2026, 9, 9)
    ATE = (2026, 9, 28)

    def rodar(self, falhar_em=(), falhar_varredura=0):
        """Roda `coletor.coletar` na janela curta, sem rede.

        `falhar_em` são os dias (ISO) cuja consulta levanta; `falhar_varredura`
        é a página da varredura geral que levanta. Devolve (lista, log).
        """
        import contextlib
        import copy
        import coletor
        from datetime import date

        caiu = urllib.error.URLError("conexão recusada")
        pagina = {"geral": 0}

        def falso_buscar(url, tentativa=1):
            if "unidades-atividades" in url:
                return [{"name": "Pompeia", "description": "Capital"}]
            if "data_inicial" in url:
                if any("data_inicial=%s" % d in url for d in falhar_em):
                    raise caiu
                return {"atividade": [], "total": {"value": 0}}
            pagina["geral"] += 1
            if pagina["geral"] == falhar_varredura:
                raise caiu
            # `total` alto de propósito: a varredura pede a página 2, que é
            # onde se testa parar no meio.
            if pagina["geral"] > 1:
                return {"atividade": [], "total": {"value": 900}}
            sentinela = copy.deepcopy(SENTINELA_TURISMO)
            sentinela["tipos_linguagens"] = [{"titulo": coletor.CAT_TURISMO}]
            return {"atividade": [sentinela], "total": {"value": 900}}

        original, pausa = coletor.buscar, coletor.PAUSA
        coletor.buscar, coletor.PAUSA = falso_buscar, 0
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                lista, _ = coletor.coletar(date(*self.DE), date(*self.ATE),
                                           set(coletor.REGIOES_VALIDAS))
            return lista, log.getvalue()
        finally:
            coletor.buscar, coletor.PAUSA = original, pausa

    def test_dia_falho_segue_e_a_varredura_recupera(self):
        """O evento do dia perdido volta pela rede de segurança."""
        lista, log = self.rodar(falhar_em=("2026-09-15",))
        self.assertEqual(so(lista, "Passeio")["dias"], ["2026-09-15"])
        self.assertIn("FALHOU", log,
                      "'0 sessões' e 'não respondeu' não podem sair iguais")
        self.assertIn("::warning::", log,
                      "dia perdido sem aviso na aba Actions é perda calada")
        self.assertIn("2026-09-15", log)

    def test_dias_demais_abortam_na_hora(self):
        """Portal fora não é tropeço — e insistir nos 60 dias gastaria horas."""
        with self.assertRaises(SystemExit) as ctx:
            self.rodar(falhar_em=("2026-09-10", "2026-09-11", "2026-09-12"))
        self.assertIn("não responderam", str(ctx.exception))
        self.assertIn("Nada foi gravado", str(ctx.exception))

    def test_varredura_sem_a_primeira_pagina_aborta(self):
        """Sem a varredura não há rede nenhuma, e os buracos ficam invisíveis."""
        with self.assertRaises(SystemExit) as ctx:
            self.rodar(falhar_varredura=1)
        self.assertIn("primeira página", str(ctx.exception))

    def test_varredura_truncada_sozinha_passa(self):
        """Os dias responderam todos; o que já veio da varredura vale."""
        lista, log = self.rodar(falhar_varredura=2)
        self.assertEqual(so(lista, "Passeio")["dias"], ["2026-09-15"])
        self.assertIn("::warning::", log)

    def test_dia_falho_mais_varredura_truncada_abortam(self):
        """Uma cobre a outra; as duas juntas não cobrem ninguém."""
        with self.assertRaises(SystemExit) as ctx:
            self.rodar(falhar_em=("2026-09-15",), falhar_varredura=2)
        self.assertIn("cobre a outra", str(ctx.exception))


class FaltantesNaoCongelaPreco(unittest.TestCase):
    """`--faltantes` é sobre descrição, e só.

    Aplicado à lista inteira, tirava da fila da bilheteria todo evento que já
    tem `desc` — quase todos —, congelando preço, sessões e datas de venda,
    com mensagem final de sucesso.
    """

    def test_fila_da_bilheteria_ignora_faltantes(self):
        fonte = ler_fonte("detalhes.py")
        i_java = fonte.index("com_java = [e for e in eventos")
        i_falt = fonte.index("if args.faltantes:")
        self.assertLess(i_java, i_falt,
                        "com_java precisa ser derivado ANTES do filtro de "
                        "--faltantes, senão a bilheteria congela")



class CategoriaComNomeUnico(unittest.TestCase):
    """"Turismo Social" governava três regras a partir de três arquivos.

    `coletor.CONTINUOS` (a viagem acontece em todos os dias do intervalo),
    `regras.SO_COM_INSCRICAO` (passeio sem data de inscrição sai da base) e
    o padrão de `detalhes --html-cats` (é o único jeito de raspar, porque
    passeio não tem id_java). O portal renomear a categoria produziria três
    efeitos diferentes e todos silenciosos.
    """

    def test_uma_constante_governa_as_tres(self):
        import coletor
        import regras
        self.assertEqual(coletor.CONTINUOS, (coletor.CAT_TURISMO,))
        self.assertEqual(regras.SO_COM_INSCRICAO, (coletor.CAT_TURISMO,))
        self.assertIn("default=[coletor.CAT_TURISMO]", ler_fonte("detalhes.py"))

    def test_literal_nao_reaparece_em_codigo(self):
        """Prosa pode citar a categoria; código, não."""
        for nome in ("regras.py", "publicar.py", "embutir.py", "reparse.py"):
            fonte = ler_fonte(nome)
            for linha in fonte.split("\n"):
                nu = linha.strip()
                if nu.startswith("#") or nu.startswith('"""'):
                    continue
                self.assertNotIn('"Turismo Social"', linha,
                                 "%s tem a categoria em código: use "
                                 "coletor.CAT_TURISMO" % nome)


class NomeDaUnidade(unittest.TestCase):
    """O nome da unidade é a única chave entre a atividade e a região.

    O portal não o escreve igual nos dois endpoints: a listagem manda
    "São Caetano " com um espaço sobrando e /unidades-atividades manda
    "São Caetano". Vinte e sete atividades ficaram sem região por causa
    disso — e sem região o coletor as descartava.
    """

    def test_espaco_sobrando_ainda_casa(self):
        import coletor
        ev = coletor.normalizar(
            {"id": 1, "titulo": "x", "unidade": [{"name": "São Caetano "}]},
            {"São Caetano": "capital"})
        self.assertEqual(ev["uni"], "São Caetano")
        self.assertEqual(ev["reg"], "capital")

    def test_atividade_sem_unidade_nao_inventa_nome(self):
        import coletor
        ev = coletor.normalizar(
            {"id": 1, "titulo": "x", "unidade": [{"name": None}]}, {})
        self.assertEqual(ev["uni"], "—")
        self.assertEqual(ev["reg"], "outra")

    def test_os_dois_lados_usam_a_mesma_normalizacao(self):
        """Normalizar um lado só não resolve: as chaves têm de coincidir."""
        fonte = ler_fonte("coletor.py")
        self.assertIn('nome = chave_unidade(u.get("name"))', fonte)
        self.assertIn('uni = chave_unidade(primeiro(a.get("unidade")))', fonte)


class TravaDeRegiao(unittest.TestCase):
    """O filtro de região servia para pedir menos e virou descarte.

    `reg` também vale "outra" — em unidade renomeada e na programação que
    acontece FORA de uma unidade, que a fonte manda com `name` nulo. Como
    "outra" não está entre as três regiões válidas, pedir "todas" apagava
    81 atividades: 27 pelo espaço sobrando em "São Caetano " e 54 sem
    unidade nenhuma, entre elas os 39 eventos do Sesc na Bienal do Livro.
    A trava de 10% não viu nada: era 2,5% do total.
    """

    def test_pedir_todas_as_regioes_nao_descarta(self):
        fonte = ler_fonte("coletor.py")
        self.assertIn("if regioes and set(regioes) < set(REGIOES_VALIDAS):", fonte,
                      "o filtro só pode valer quando se pede um subconjunto")

    def test_trava_mede_unidade_orfa_e_nao_o_saldo_do_filtro(self):
        fonte = ler_fonte("coletor.py")
        trecho = fonte[fonte.index("orfas = sorted("):][:1600]
        self.assertIn('e["uni"] != "—" and e["uni"] not in unidades', trecho,
                      "a trava tem de medir nome que não existe no mapa, que é "
                      "o sintoma de unidade renomeada")
        self.assertIn('if not any(e["cat"] == CAT_TURISMO for e in lista)', trecho,
                      "sem conferir a categoria, renomeá-la apaga três regras "
                      "de uma vez, todas em silêncio")
        self.assertEqual(trecho.count("ABORTADO"), 2,
                         "as duas travas precisam abortar, não avisar")

    def test_unidade_orfa_aborta_a_coleta(self):
        import coletor
        lista = coletar_com_stub(
            {"id": 7, "titulo": "Show", "unidade": [{"name": "Pompéia Nova"}],
             "dataPrimeiraSessao": "2026-09-10T20:00",
             "dataUltimaSessao": "2026-09-10T21:00",
             "tipos_linguagens": [{"titulo": "Cinema"}]},
            espera_saida=self)
        self.assertIn("Pompéia Nova", lista)

    def test_atividade_sem_unidade_sobrevive_a_coleta(self):
        import coletor
        lista = coletar_com_stub(
            {"id": 7, "titulo": "Slam da Guilhermina", "unidade": [{"name": None}],
             "conjunto": [{"name": "Sesc na Bienal do Livro"}],
             "dataPrimeiraSessao": "2026-09-11T15:00",
             "dataUltimaSessao": "2026-09-11T16:00",
             "tipos_linguagens": [{"titulo": "Cinema"}]})
        slam = so(lista, "Slam da Guilhermina")
        self.assertEqual(slam["reg"], "outra")
        self.assertEqual(slam["uni"], "—")


class TemporadaQueAtravessaAJanela(unittest.TestCase):
    """A varredura de segurança só guarda as duas pontas do intervalo.

    Quando as duas caem fora da janela — "Curumim 2026" vai de fevereiro a
    dezembro — a lista de dias saía vazia e o programa sumia do app. Eram
    23 programas contínuos, de esporte e de criança, que acontecem toda
    semana e nunca apareceram.
    """

    def um(self, ini, fim):
        # categoria comum de propósito: viagem é contínua e teria os dias
        # preenchidos por `expandir_viagens`, o que esconderia o grampo
        return so(coletar_com_stub(
            {"id": 7, "titulo": "Curumim", "unidade": [{"name": "Pompeia"}],
             "dataPrimeiraSessao": ini + "T09:00",
             "dataUltimaSessao": fim + "T18:00",
             "tipos_linguagens": [{"titulo": "Esporte e Atividade Física"}]}),
            "Curumim")

    def test_temporada_de_um_ano_nao_some(self):
        ev = self.um("2026-02-10", "2026-12-18")
        self.assertEqual(ev["dias"], ["2026-09-09", "2026-11-07"])
        self.assertTrue(ev["parcial"],
                        "a lista de dias está incompleta e a tela precisa avisar")

    def test_ponta_de_fora_e_grampeada_na_janela(self):
        self.assertEqual(self.um("2026-08-01", "2026-09-20")["dias"],
                         ["2026-09-09", "2026-09-20"])


class PasseioSemDataDeInscricao(unittest.TestCase):
    """A retenção pedia DATA de inscrição a todo Turismo Social.

    Passeio que não tem inscrição nenhuma — "Atividade aberta, sem retirada
    de ingressos", "Acesso livre", "Senhas na Loja Sesc com 30 minutos de
    antecedência" — não tem o que datar, e sumia. Eram 20, entre eles os
    três "Outras viagens" de Campinas. O que não dá para mostrar é o
    passeio de que não se sabe COMO ENTRAR: nem data, nem texto.
    """

    HOJE = "2026-09-09"
    HOR = "2026-11-08"

    def passeio(self, inscricao):
        import coletor
        return {"cat": coletor.CAT_TURISMO, "dias": ["2026-09-20"],
                "inscricao": inscricao}

    def fica(self, inscricao):
        import regras
        return regras.manter(self.passeio(inscricao), self.HOJE, self.HOR)

    def test_atividade_aberta_fica(self):
        self.assertTrue(self.fica(
            {"texto": "Atividade aberta, sem retirada de ingressos, mediante "
                      "capacidade do local.", "temSorteio": False}))

    def test_senha_no_local_fica(self):
        self.assertTrue(self.fica(
            {"texto": "30 vagas. Senhas na Loja Sesc com 30 minutos de "
                      "antecedência."}))

    def test_regra_recorrente_sem_data_fica(self):
        self.assertTrue(self.fica(
            {"regras": [{"quem": "geral", "semanas": [1], "dow": 3,
                         "hora": "14:00"}]}))

    def test_sem_data_e_sem_texto_sai(self):
        self.assertFalse(self.fica({}))
        self.assertFalse(self.fica({"texto": "  "}))

    def test_a_regra_nao_volta_a_pedir_data(self):
        fonte = ler_fonte("regras.py")
        self.assertNotIn('SO_COM_INSCRICAO and not i.get("inscricao")', fonte,
                         "exigir data apaga o passeio que não tem inscrição")


class RodadaLeveDeSorteio(unittest.TestCase):
    """`sorteios.py` — a fila mínima e a decisão de republicar.

    O botão "Consultar" existe para dar a resposta agora, e o navegador não
    lê a página da atividade (o portal libera CORS no /wp-json e recusa em
    todo o HTML). Quem lê é o robô, de 20 em 20 minutos na tarde do sorteio,
    e o que importa é ele bater no portal o mínimo possível e republicar o
    site só quando há o que publicar.
    """

    HOJE = "2026-09-09"

    def passeio(self, quando, sorteados=None, link="https://x/y"):
        ev = {"id": 1, "tit": "Passeio", "link": link,
              "inscricao": {"sorteio": quando} if quando else {}}
        if sorteados:
            ev["sorteados"] = sorteados
        return ev

    def fila(self, eventos, dias=2):
        import sorteios
        return sorteios.pendentes(eventos, self.HOJE, dias)

    def test_so_entra_quem_esta_na_janela(self):
        dentro = self.passeio("2026-09-09T14:00")
        borda = self.passeio("2026-09-07")
        velho = self.passeio("2026-09-06")
        futuro = self.passeio("2026-09-11")
        fila = self.fila([dentro, borda, velho, futuro])
        self.assertEqual(len(fila), 2)
        self.assertNotIn(velho, fila)
        self.assertNotIn(futuro, fila,
                         "sorteio que ainda não aconteceu não tem lista para ler")

    def test_quem_ja_tem_lista_nao_volta_a_fila(self):
        """A lista publicada não muda; reler é bater no portal à toa."""
        self.assertEqual(
            self.fila([self.passeio("2026-09-09", sorteados=["ABCDEFGH"])]), [])

    def test_sem_data_ou_sem_link_fica_de_fora(self):
        self.assertEqual(self.fila([self.passeio(None)]), [])
        self.assertEqual(self.fila([self.passeio("2026-09-09", link=None)]), [])

    def test_o_de_hoje_vem_primeiro(self):
        """Alguém está esperando ESTE neste minuto; o teto corta o resto."""
        fila = self.fila([self.passeio("2026-09-07"),
                          self.passeio("2026-09-09T14:00")])
        self.assertEqual(fila[0]["inscricao"]["sorteio"], "2026-09-09T14:00")

    def test_republica_so_o_que_o_site_ainda_nao_tem(self):
        import sorteios
        anterior = os.path.join(tempfile.mkdtemp(), "publicado.json")
        with open(anterior, "w", encoding="utf-8") as f:
            json.dump({"sorteados": {"1": ["AAAAAAAA"]}}, f)
        self.assertEqual(sorteios.novidades({"1": ["AAAAAAAA"]}, anterior), 0,
                         "republicar sem lista nova gasta deploy do Pages "
                         "para trocar um carimbo")
        self.assertEqual(sorteios.novidades(
            {"1": ["AAAAAAAA"], "2": ["BBBBBBBB"]}, anterior), 1)

    def test_sem_arquivo_anterior_publica_por_precaucao(self):
        """Errar publicando é barato; errar calado guarda a lista mais um dia."""
        import sorteios
        self.assertIsNone(sorteios.novidades({"1": ["A"]}, None))
        self.assertIsNone(sorteios.novidades({"1": ["A"]}, "nao/existe.json"))

    def test_o_que_ja_esta_publicado_nao_e_relido(self):
        """A rodada leve não guarda estado, e relia a mesma página o dia todo.

        Ela só restaura o cache e nunca o salva — salvar deixaria uma base
        pela metade sob a chave que a coleta grande restaura de madrugada.
        Então toda execução partia da base da madrugada: uma lista achada às
        14h20 voltava para a fila às 14h40, às 15h, às 15h20. Vinte
        releituras da mesma página, num portal que não é nosso.
        """
        import sorteios
        pasta = tempfile.mkdtemp()
        publicado = os.path.join(pasta, "publicado.json")
        with open(publicado, "w", encoding="utf-8") as f:
            json.dump({"sorteados": {"1": ["YVOPXAGP", "UDESMROF"]}}, f)

        ev = self.passeio("2026-09-09T14:00")
        self.assertEqual(sorteios.semear([ev], publicado), 1)
        self.assertEqual(ev["sorteados"], ["YVOPXAGP", "UDESMROF"])
        self.assertEqual(self.fila([ev]), [],
                         "com a lista já publicada não há o que reler")

    def test_semear_sobrevive_a_arquivo_ruim(self):
        import sorteios
        ruim = os.path.join(tempfile.mkdtemp(), "meio.json")
        with open(ruim, "w", encoding="utf-8") as f:
            f.write('{"sorteados": {"1": ["AAAA')
        ev = self.passeio("2026-09-09")
        self.assertEqual(sorteios.semear([ev], ruim), 0)
        self.assertNotIn("sorteados", ev,
                         "arquivo pela metade não pode virar lista pela metade")
        self.assertEqual(sorteios.semear([ev], None), 0)

    def test_semear_nao_sobrescreve_o_que_a_base_ja_tem(self):
        import sorteios
        publicado = os.path.join(tempfile.mkdtemp(), "p.json")
        with open(publicado, "w", encoding="utf-8") as f:
            json.dump({"sorteados": {"1": ["VELHOAAA"]}}, f)
        ev = self.passeio("2026-09-09", sorteados=["NOVOBBBB"])
        sorteios.semear([ev], publicado)
        self.assertEqual(ev["sorteados"], ["NOVOBBBB"],
                         "a base enriquecida é mais nova que o publicado")

    def test_o_extrator_e_o_mesmo_de_detalhes(self):
        """Duas cópias da regra dos quatro cabeçalhos, e uma envelhece."""
        fonte = ler_fonte("sorteios.py")
        self.assertIn("detalhes.sorteados_do_corpo", fonte)
        self.assertNotIn("re.compile", fonte,
                         "a regra de extração mora em detalhes.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
