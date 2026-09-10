#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes de regressão do pipeline e da casca do app.

Por que este arquivo existe: o rastreio de novidades foi desligado por uma
mudança que nada tinha a ver com ele — `dados/eventos.json` deixou de ser
versionado para o repositório parar de crescer, e com isso o coletor passou a
não achar a coleta anterior. O recurso sumiu do app e o pipeline continuou
terminando com sucesso, imprimindo uma linha informativa.

A lição não é "aquele bug": é que toda verificação deste projeto era manual.
O que está aqui são as invariantes que, quebradas, não gritam sozinhas.

    python teste_pipeline.py

Sem dependências externas. O que precisa de rede ou de uma coleta no disco é
pulado com a razão dita em voz alta, para "pulou" nunca se confundir com
"passou".
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
PROTOTIPO = os.path.join(RAIZ, "prototipo.html")
WORKFLOW = os.path.join(RAIZ, ".github", "workflows", "atualizar.yml")
BASE = os.path.join(RAIZ, "dados", "eventos.json")


def ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def css_de(html):
    """O bloco <style> do app, onde vivem os tokens."""
    return html[html.index("<style>"):html.index("</style>")]


def js_de(html):
    """O último <script> do app — o IIFE inteiro."""
    ini = html.rindex("<script>") + len("<script>")
    return html[ini:html.rindex("</script>")]


def luminancia(hexa):
    h = hexa.lstrip("#")
    canais = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        canais.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canais[0] + 0.7152 * canais[1] + 0.0722 * canais[2]


def contraste(a, b):
    l1, l2 = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def tokens_do_bloco(css, seletor):
    """Os pares --nome: #RRGGBB do primeiro bloco que casa com `seletor`."""
    i = css.index(seletor)
    corpo = css[i:css.index("}", i)]
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", corpo))


# ---------------------------------------------------------------- rastreio
class RastreioDeNovidades(unittest.TestCase):
    """O caso que motivou o arquivo.

    `carimbar_estreia` é a função inteira do recurso "Novidades", e ela falha
    para o lado silencioso: sem base anterior, devolve linha de base e segue.
    """

    def setUp(self):
        sys.path.insert(0, RAIZ)
        import coletor
        self.coletor = coletor
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gravar(self, nome, dados):
        caminho = os.path.join(self.tmp, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        return caminho

    def test_com_base_anterior_preserva_o_carimbo(self):
        anterior = self._gravar("ant.json", {
            "rastreioDesde": "2026-08-01",
            "eventos": [{"id": "a", "visto": "2026-08-10"}, {"id": "b"}],
        })
        lista = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        desde = self.coletor.carimbar_estreia(lista, anterior, "2026-09-03")

        self.assertEqual(desde, "2026-08-01", "rastreioDesde não pode reiniciar")
        por_id = {e["id"]: e for e in lista}
        self.assertEqual(por_id["a"].get("visto"), "2026-08-10",
                         "quem já estava conserva a data antiga")
        self.assertIsNone(por_id["b"].get("visto"),
                          "quem é anterior ao rastreio fica sem carimbo")
        self.assertEqual(por_id["c"].get("visto"), "2026-09-03",
                         "quem não estava na anterior é novidade de hoje")

    def test_sem_base_anterior_nao_carimba_ninguem(self):
        """Comportamento correto — e exatamente por isso perigoso.

        Não marcar tudo como novo está certo. O problema é isto acontecer
        todo dia sem ninguém perceber, e é o workflow que tem de impedir.
        """
        lista = [{"id": "a"}, {"id": "b"}]
        desde = self.coletor.carimbar_estreia(
            lista, os.path.join(self.tmp, "nao_existe.json"), "2026-09-03")
        self.assertEqual(desde, "2026-09-03")
        self.assertFalse(any(e.get("visto") for e in lista))

    def test_workflow_garante_a_base_anterior(self):
        """O teste que teria pego o erro.

        Não basta a função estar certa: alguém tem de entregar a base de
        ontem antes de o coletor rodar. Se este teste falhar, o recurso
        "Novidades" morre em silêncio na próxima execução.
        """
        wf = ler(WORKFLOW)
        antes_do_coletor = wf[:wf.index("python coletor.py")]
        tem_cache = "actions/cache" in antes_do_coletor
        tem_queda = "dados/eventos.json" in antes_do_coletor and "curl" in antes_do_coletor
        self.assertTrue(
            tem_cache or tem_queda,
            "nada restaura dados/eventos.json antes do coletor: sem a coleta "
            "anterior, carimbar_estreia zera o rastreio a cada execução")

    def test_base_no_disco_tem_rastreio(self):
        if not os.path.exists(BASE):
            self.skipTest("sem dados/eventos.json local (rode o coletor)")
        d = json.loads(ler(BASE))
        self.assertIn("rastreioDesde", d,
                      "a base gravada precisa carregar o próprio marco de rastreio")


# ------------------------------------------------------- fonte x publicado
class FonteEnxuta(unittest.TestCase):
    """O arquivo-fonte não pode voltar a carregar o instantâneo.

    Ele já pesou 4,9 MB e entrava inteiro no histórico do git a cada coleta.
    `embutir.py` grava em previa.html; `publicar.py` gera web/.
    """

    def setUp(self):
        self.html = ler(PROTOTIPO)

    def test_marcadores_de_dados_presentes(self):
        # embutir.py e publicar.py recortam por estes marcadores; sem eles,
        # os dois falham — e publicar.py falha depois de uma hora de coleta
        self.assertIn("/* DADOS:INICIO */", self.html)
        self.assertIn("/* DADOS:FIM */", self.html)

    def test_fonte_sem_instantaneo(self):
        ini = self.html.index("/* DADOS:INICIO */")
        fim = self.html.index("/* DADOS:FIM */")
        embutido = self.html[ini:fim]
        self.assertLess(len(embutido), 4096,
                        "prototipo.html voltou a carregar dados embutidos: "
                        "rode `python embutir.py`, que grava em previa.html")

    def test_fonte_sem_script_de_medicao(self):
        """Medir o teste local sujaria o painel com os nossos próprios acessos."""
        self.assertNotIn("data-goatcounter", self.html,
                         "o contador entra só na saída publicada, em publicar.py")

    def test_gitignore_protege_os_gerados(self):
        ig = ler(os.path.join(RAIZ, ".gitignore"))
        for alvo in ("previa.html", "dados/eventos.json", "web/"):
            self.assertIn(alvo, ig, "%s precisa ficar fora do git" % alvo)


# ------------------------------------------------------------- design v4.1
class TokensDeCor(unittest.TestCase):
    """Contraste é a única parte do desenho que se verifica por conta.

    O cinza secundário já reprovou uma vez (3,51:1) e ninguém viu por meses.
    E a paleta noturna nasceu errada: foi medida contra o papel quando o
    texto vive no cartão, que é mais claro.
    """

    def setUp(self):
        self.css = css_de(ler(PROTOTIPO))

    def _conferir(self, seletor, nome_tema):
        t = tokens_do_bloco(self.css, seletor)
        fundo = t.get("--surface")
        self.assertIsNotNone(fundo, "%s sem --surface" % nome_tema)
        alvos = [k for k in t if k.startswith("--cat-")]
        alvos += ["--ink-3", "--ink-2", "--accent", "--pos", "--warn", "--neg"]
        ruins = []
        for k in alvos:
            if k not in t:
                continue
            r = contraste(t[k], fundo)
            if r < 4.5:
                ruins.append("%s %s = %.2f:1" % (k, t[k], r))
        self.assertEqual(ruins, [],
                         "%s: tinta abaixo de 4,5:1 sobre --surface (%s): %s"
                         % (nome_tema, fundo, ", ".join(ruins)))

    def test_tema_claro_passa_em_aa(self):
        self._conferir(":root {", "tema claro")

    def test_tema_escuro_passa_em_aa(self):
        self._conferir(':root[data-tema="escuro"]', "tema escuro")

    def test_escala_de_tipo_em_rem(self):
        """Em px, a preferência de fonte grande do sistema não tem efeito."""
        sobraram = re.findall(r"font-size:\s*([\d.]+)px", self.css)
        self.assertEqual(sobraram, [],
                         "font-size em px voltou ao CSS: %s" % sobraram)


# ------------------------------------------------------------ casca do app
class CascaDoApp(unittest.TestCase):
    def setUp(self):
        self.html = ler(PROTOTIPO)
        self.js = js_de(self.html)

    def test_js_sem_erro_de_sintaxe(self):
        """Um erro aqui mata o app inteiro, e o HTML continua servindo bem.

        Node não está instalado nesta máquina, mas está nos runners do
        GitHub — então na CI este teste roda de verdade.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("node ausente (roda na CI)")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(self.js)
            temp = f.name
        try:
            p = subprocess.run([node, "--check", temp],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
        finally:
            os.unlink(temp)

    def test_sem_marca_combinante_literal_no_js(self):
        """A classe /[\\u0300-\\u036f]/ do normalizador de acento.

        Escrita com os caracteres combinantes de verdade em vez das escapes,
        ela vira uma faixa inválida e derruba o script inteiro. Já aconteceu
        duas vezes ao editar o arquivo por script.
        """
        achadas = re.findall(r"[̀-ͯ]", self.js)
        self.assertEqual(achadas, [],
                         "marca combinante literal no JS: use \\u0300-\\u036f")

    def test_esconder_item_de_menu_funciona(self):
        """`.ditem` define display:flex, que vence o [hidden] do navegador.

        Sem a regra explícita, esconder pelo atributo não surte efeito e
        "Meus Sorteios" fica na tela com zero sorteios guardados.
        """
        css = css_de(self.html)
        self.assertRegex(
            css, r"\.ditem\[hidden\][^{]*\{[^}]*display:\s*none",
            "falta `.ditem[hidden] { display: none }`: o atributo perde para "
            "o display do próprio componente")

    def test_medicao_nao_derruba_o_app(self):
        i = self.js.index("function medir(")
        corpo = self.js[i:self.js.index("\n  }", i)]
        self.assertIn("catch", corpo, "medir() precisa engolir o próprio erro")


# ------------------------------------------------------------- publicação
class Publicacao(unittest.TestCase):
    """Invariantes da pasta web/, quando ela existe."""

    def setUp(self):
        self.web = os.path.join(RAIZ, "web")
        if not os.path.isdir(self.web):
            self.skipTest("sem web/ (rode `python publicar.py`)")
        self.index = ler(os.path.join(self.web, "index.html"))

    def test_publicado_sem_instantaneo(self):
        tamanho = os.path.getsize(os.path.join(self.web, "index.html"))
        self.assertLess(tamanho, 600 * 1024,
                        "web/index.html com %d KB: o instantâneo voltou, e a "
                        "primeira carga triplica" % (tamanho // 1024))

    def test_publicado_tem_medicao(self):
        self.assertIn("data-goatcounter", self.index)
        self.assertIn("https://gc.zgo.at/count.js", self.index,
                      "endereço relativo a protocolo vira http:// e falha")

    def test_pwa_completo(self):
        for nome in ("manifest.webmanifest", "sw.js", "icon-192.png",
                     "icon-512.png", "dados/eventos.json"):
            self.assertTrue(os.path.exists(os.path.join(self.web, nome)),
                            "web/%s não foi gerado" % nome)

    def test_service_worker_versiona_pela_casca(self):
        """O nome do cache precisa mudar quando o app muda.

        Quando era o carimbo da coleta, mexer no CSS entre duas coletas não
        trocava o nome e quem já tinha instalado nunca via a mudança.
        """
        sw = ler(os.path.join(self.web, "sw.js"))
        versao = re.search(r"VERSAO\s*=\s*'([^']+)'", sw)
        self.assertIsNotNone(versao, "sw.js sem VERSAO")
        self.assertRegex(versao.group(1), r"[0-9a-f]{12}",
                         "VERSAO precisa derivar do sha1 do index.html")



class Acessibilidade(unittest.TestCase):
    """O que a auditoria de teclado e leitor de tela encontrou.

    Não dá para rodar navegador na CI sem dependência nova, então aqui as
    verificações são sobre o código que liga as peças — que é onde estes
    defeitos moravam: a folha declarava `aria-modal` e não prendia nada, e
    a gaveta fechada levava sete controles para dentro de uma subárvore
    `aria-hidden="true"`.
    """

    def setUp(self):
        self.html = ler(PROTOTIPO)
        self.css = css_de(self.html)
        self.js = js_de(self.html)

    def test_folha_modal_prende_o_foco(self):
        """Com a folha aberta, 105 dos 112 focáveis ficavam fora dela.

        Oito toques em Tab saíam para os exemplos de busca; um Shift+Tab
        caía em "Importar backup", na gaveta fechada e fora da tela.
        """
        self.assertIn("function lacoDeFoco", self.js,
                      "sumiu o laço de foco da camada aberta")
        self.assertRegex(self.js, r'lacoDeFoco[^)]*\)?,\s*true\s*\)',
                         "o laço precisa escutar na fase de captura")
        self.assertIn("marcarInerte", self.js,
                      "falta marcar o resto da página como inerte")

    def test_gaveta_fechada_sai_do_percurso(self):
        """`translateX(-101%)` tira da vista e não do Tab.

        `visibility: hidden` tira dos dois — e é o que também a remove do
        leitor de tela, que é o ponto: focável dentro de `aria-hidden` é a
        combinação que a ARIA proíbe.
        """
        i = self.css.index(".drawer {")
        bloco = self.css[i:self.css.index("}", i)]
        self.assertIn("visibility: hidden", bloco,
                      "a gaveta fechada continua no percurso do Tab")
        i2 = self.css.index(".drawer.on {")
        self.assertIn("visibility: visible", self.css[i2:self.css.index("}", i2)])

    def test_favoritar_nao_perde_o_lugar(self):
        """`render()` destrói o botão que tinha o foco.

        Medido antes: `activeElement` virava `document.body`, e quem
        favoritava o trigésimo item voltava para o começo da página.
        """
        self.assertIn("comFocoPreservado(render)", self.js,
                      "toggleFav voltou a chamar render() direto")
        self.assertIn('setAttribute("data-fav"', self.js,
                      "sem o id no botão não há como reencontrá-lo")

    def test_alvos_de_toque_chegam_a_44px(self):
        """O token --tap existia e só era aplicado na gaveta.

        O coração da lista densa media 26x18 — alvo primário de favoritar,
        na tela mais usada, colado a um alvo enorme (a linha abre o
        detalhe). Errar o toque abria a folha em vez de salvar.
        """
        self.assertRegex(
            self.css, r"\.ev \.fav::after[^{]*\{[^}]*min-width:\s*var\(--tap\)",
            "o coração da lista voltou a ter alvo menor que o dedo")

    def test_mudanca_de_resultado_e_anunciada(self):
        """Só existia uma região viva no app inteiro: o toast.

        Buscar e não achar nada trocava a tela em silêncio para quem usa
        leitor de tela.
        """
        self.assertRegex(self.html, r'id="greetSub"[^>]*aria-live="polite"',
                         "a contagem de resultados não é anunciada")
        i = self.js.index("function blank(")
        self.assertIn('"role", "status"', self.js[i:i + 400],
                      "os estados vazios não são anunciados")

    def test_atualizar_eventos_aguenta_base_vazia(self):
        """A tela que serve para consertar a falha era a que quebrava.

        `DADOS.eventos.length` sem guarda lançava TypeError e a folha ficava
        meio montada, sem abrir — justamente quando a coleta falhou.
        """
        i = self.js.index("function abrirAtualizar(")
        bloco = self.js[i:i + 2600]
        self.assertNotIn("DADOS.eventos.length", bloco,
                         "acesso sem guarda a DADOS.eventos")
        self.assertNotIn("DADOS.janela.de", bloco,
                         "acesso sem guarda a DADOS.janela")

    def test_contador_de_favoritos_conta_o_que_existe(self):
        """Dava para ver "Favoritos 2" no topo e "Nada salvo ainda" abaixo.

        A base é reescrita a cada coleta e o que passou é cortado, então id
        guardado não garante evento vivo.
        """
        self.assertIn("function favsVivos", self.js)
        i = self.js.index("function render() {")
        bloco = self.js[i:i + 500]
        self.assertNotIn("$(\"#dFavN\").textContent = state.favs.length", bloco,
                         "o distintivo voltou a contar favoritos fantasma")



class TemporadaParcial(unittest.TestCase):
    """`parcial` marca o evento que veio da varredura de segurança.

    Ela só conhece a primeira e a última data, nunca os dias do meio — e o
    app mostrava essas datas como se fossem a programação inteira. A
    informação existia no coletor e era jogada fora antes de chegar à tela.
    """

    def test_campo_chega_ao_app(self):
        import embutir
        self.assertIn("parcial", embutir.CAMPOS,
                      "`parcial` não entra no pacote e o app nunca o vê")

    def test_app_avisa_que_a_lista_esta_incompleta(self):
        js = js_de(ler(PROTOTIPO))
        self.assertIn("não é a lista completa", js,
                      "o detalhe voltou a mostrar datas parciais como certas")
        # o aviso precisa estar preso ao campo, e não solto em qualquer lugar
        i = js.index("não é a lista completa")
        trecho = js[max(0, i - 700):i + 200]
        self.assertIn("ev.parcial", trecho)
        self.assertIn('fato("Quando"', trecho)


class BuscaLivreDeVariasPalavras(unittest.TestCase):
    """As muletas saem da frase e o que sobra deixa de ser trecho contíguo.

    "fragmentos da vida" virava "fragmentos vida" — que não existe em texto
    nenhum. A busca por título de mais de uma palavra parou de funcionar no
    dia em que a lista de muletas entrou, e só apareceu por acaso, ao
    procurar um evento para testar outra coisa.
    """

    def test_sobra_e_casada_palavra_a_palavra(self):
        js = js_de(ler(PROTOTIPO))
        i = js.index("q: function (ev) {")
        trecho = js[i:i + 900]
        self.assertNotIn("indexOf(r.sobraTxt)", trecho,
                         "voltou a exigir o trecho contíguo: título de mais "
                         "de uma palavra deixa de ser encontrado")
        self.assertIn("r.sobra[j]", trecho,
                      "a sobra precisa ser casada palavra a palavra")


class ResultadoDeSorteio(unittest.TestCase):
    """O app não lê a página do Sesc; lê a coleta.

    A página da atividade não manda CORS, então a única notícia que o app
    tem sobre um sorteio é a idade da própria coleta. Enquanto ele dizia
    "a lista ainda não foi publicada", afirmava o que não sabia — e quem
    abria o app na tarde do sorteio de Jundiaí (resultado às 14h, coleta
    das 10h) ia embora achando que não havia resultado, com a lista já na
    tela do portal.
    """

    def setUp(self):
        self.js = js_de(ler(PROTOTIPO))

    def test_nao_afirma_que_a_lista_nao_saiu(self):
        for frase in ("ainda não foi publicada", "ainda não foi realizado"):
            self.assertNotIn(frase, self.js,
                             "o app não tem como saber isso: ele só sabe o "
                             "que veio na coleta")

    def test_a_frase_nao_promete_cadencia(self):
        """O intervalo do cron é um PEDIDO, não um horário.

        Medido em 10/09/2026 sobre 8 execuções agendadas deste repositório:
        o GitHub começa entre 3h38 e 6h02 depois da hora marcada, média de
        4h36. Dizer "de 20 em 20 minutos" na tela é afirmar o que não se
        cumpre — o mesmo defeito que esta tela existe para não repetir.
        """
        # Comentário pode (e deve) dizer que o cron PEDE 20 minutos, e
        # explicar por que a promessa saiu da tela. O que não pode voltar é a
        # promessa — então o que se mede aqui é o código sem comentário
        # nenhum, das duas formas.
        codigo = re.sub(r"/\*.*?\*/", " ", self.js, flags=re.S)
        codigo = "\n".join(l for l in codigo.splitlines()
                           if not l.strip().startswith("//"))
        for frase in ("de 20 em 20 minutos", "força a conferência"):
            self.assertNotIn(frase, codigo,
                             "a tela promete o que o app não controla")

    def test_a_frase_de_atraso_diz_de_quando_e_a_informacao(self):
        i = self.js.index("function textoAtrasado")
        trecho = self.js[i:i + 2200]
        self.assertIn("olhadaFmt()", trecho,
                      "sem a hora da conferência a pessoa não sabe de quando "
                      "é a informação que está lendo")
        self.assertIn("página oficial", trecho,
                      "quando nem a conferência tem a lista, quem tem é o "
                      "portal — e o caminho para lá é um toque")

    def test_o_toast_de_consultar_nao_contradiz_o_cartao(self):
        i = self.js.index("function consultarSorteio")
        trecho = self.js[i:i + 900]
        self.assertIn("Ainda sem lista na conferência de", trecho,
                      "o toast e o cartão têm de contar a mesma história")
        self.assertIn("olhadaFmt()", trecho)

    def test_robo_roda_duas_vezes_por_dia(self):
        """Uma rodada só, de madrugada, nunca alcança um resultado das 14h."""
        wf = ler(os.path.join(RAIZ, ".github", "workflows", "atualizar.yml"))
        crons = re.findall(r"- cron: \"([^\"]+)\"", wf)
        self.assertGreaterEqual(len(crons), 2,
                                "com uma rodada por dia a lista de "
                                "contemplados chega sempre no dia seguinte")
        horas = sorted(int(c.split()[1]) for c in crons)
        self.assertTrue(any(h >= 17 for h in horas),
                        "falta uma rodada depois das 14h de Brasília "
                        "(17h UTC), que é quando o Sesc publica o sorteio")


class PrazoPresumidoSempreMarcado(unittest.TestCase):
    """Data que o app inventou não pode aparecer com cara de fato.

    Sem prazo publicado o app presume abertura + 3 dias, e a regra da casa é
    que isso vem sempre com "(estimado)". A linha da agenda cumpria a regra
    em "inscrição aberta" e a esquecia em "inscrição encerrada": 350 eventos
    (12% da base) afirmavam um encerramento inventado. Quem lesse desistia de
    um prazo que talvez nem tivesse vencido.
    """

    def test_os_dois_estados_usam_a_mesma_marca(self):
        js = js_de(ler(PROTOTIPO))
        i = js.index("function notaInscricao")
        trecho = js[i:i + 1500]
        self.assertEqual(trecho.count("f.presumido"), 1,
                         "cada ramo montando a sua data é como o "
                         "\"(estimado)\" sumiu de um deles")
        for estado in ('est === "aberta"', 'est === "encerrada"'):
            j = trecho.index(estado)
            self.assertIn("marca", trecho[j:j + 120],
                          "%s monta a data à mão e perde a ressalva" % estado)

    def test_nenhum_ramo_formata_a_data_por_fora(self):
        js = js_de(ler(PROTOTIPO))
        i = js.index("function notaInscricao")
        trecho = js[i:i + 1500]
        self.assertNotIn('" em " + curta(f.dia)', trecho)
        self.assertNotIn('" até " + curta(f.dia)', trecho)


class NomeDeFuncaoUnico(unittest.TestCase):
    """Duas funções com o mesmo nome no app: a segunda apaga a primeira.

    O app é um `<script>` só, e o JS não reclama de redeclarar `function`.
    Um ajudante novo chamado `localDe` sentou em cima do `localDe` que
    montava o LOCATION do .ics — que passou a chamar a si mesmo, estourou a
    pilha e derrubou `aplicarDados`: o app abria com a base embutida e não
    trocava mais por nada. Nenhum teste viu, porque a sintaxe estava
    perfeita; só apareceu abrindo o app no navegador.
    """

    def test_nenhuma_funcao_e_declarada_duas_vezes(self):
        js = js_de(ler(PROTOTIPO))
        # só o escopo de cima: `function pintar()` aparece três vezes, cada
        # uma dentro de uma função diferente, e ali não há colisão nenhuma
        nomes = re.findall(r"^  function ([A-Za-z_$][\w$]*)\s*\(", js, re.M)
        repetidos = sorted({n for n in nomes if nomes.count(n) > 1})
        self.assertEqual(repetidos, [],
                         "redeclarada(s): %s — a segunda vence e a primeira "
                         "some sem erro nenhum" % ", ".join(repetidos))


class AtividadeSemUnidade(unittest.TestCase):
    """Nem toda atividade acontece numa unidade.

    "Sesc na Bienal do Livro", "Semana Move" e "Festival Sesc 80 Anos"
    chegam da fonte com `unidade[0].name` nulo. O coletor grava "—", e a
    tela mostrava "Sesc —", que não é lugar nenhum. Quem localiza a pessoa
    nesses casos é o projeto.
    """

    def test_o_app_tem_uma_funcao_so_para_isso(self):
        js = js_de(ler(PROTOTIPO))
        self.assertIn("function ondeDe(ev, curto)", js)
        i = js.index("function ondeDe(ev, curto)")
        self.assertIn("ev.projeto", js[i:i + 300],
                      "sem unidade, o projeto é o que resta para localizar")

    def test_nenhuma_tela_monta_o_local_a_mao(self):
        js = js_de(ler(PROTOTIPO))
        sobras = [l for l in js.split("\n")
                  if '"Sesc " + ev.uni' in l or '"Sesc " + e.uni' in l]
        self.assertEqual(sobras, [],
                         "concatenar direto ressuscita o \"Sesc —\": use "
                         "localDe(ev)")

    def test_o_ics_do_servidor_diz_a_mesma_coisa(self):
        """As duas implementações do .ics têm de dizer a mesma coisa."""
        import publicar
        fora = {"uni": "—", "projeto": "Sesc na Bienal do Livro"}
        self.assertEqual(publicar._local(fora), "Sesc na Bienal do Livro")
        dentro = {"uni": "Pompeia"}
        self.assertTrue(publicar._local(dentro).startswith("Sesc Pompeia"))


class ConsultaDeSorteioAoVivo(unittest.TestCase):
    """"Consultar" tem de dar a resposta agora, e por um arquivo pequeno.

    Ler a página do Sesc no toque é impossível: o portal libera CORS no
    /wp-json e recusa em todo o HTML — reconferido em 09/09/2026, no Chrome,
    a partir do endereço de produção. Quem lê é o robô, de 20 em 20 minutos
    na janela do sorteio, e o app busca o resultado em `sorteados.json`.
    Baixar 3,5 MB para conferir oito letras cobraria de quem usa o preço de
    um problema nosso.
    """

    def setUp(self):
        self.js = js_de(ler(PROTOTIPO))

    def test_o_app_tem_a_fonte_magra(self):
        self.assertIn('var FONTE_SORTEADOS = "dados/sorteados.json"', self.js)

    def test_consultar_tenta_o_arquivo_magro_primeiro(self):
        i = self.js.index("function consultarSorteio")
        trecho = self.js[i:i + 1400]
        self.assertIn("buscarSorteados()", trecho)
        pos_magro = trecho.index("buscarSorteados()")
        self.assertIn("buscarDados(", trecho,
                      "a base inteira continua sendo o plano B, para o app "
                      "publicado antes de sorteados.json existir")
        self.assertLess(pos_magro, trecho.index("buscarDados("),
                        "a base inteira não pode ser a primeira tentativa")

    def test_a_conferencia_tem_carimbo_proprio(self):
        """A base é de 2x por dia e a conferência, de 20 em 20 minutos."""
        i = self.js.index("function textoAtrasado")
        trecho = self.js[i:i + 900]
        self.assertIn("olhadaFmt()", trecho)
        self.assertNotIn("coletaFmt()", trecho,
                         "dizer a hora da coleta envelheceria a resposta em "
                         "horas, no único assunto em que minutos importam")

    def test_a_publicacao_gera_o_arquivo_magro(self):
        fonte = ler(os.path.join(RAIZ, "publicar.py"))
        self.assertIn('"dados", "sorteados.json"', fonte)
        self.assertIn("sorteadosEm", fonte,
                      "sem o carimbo na base, a primeira tela não sabe de "
                      "quando é a conferência")

    def test_service_worker_nao_serve_a_lista_do_cache(self):
        fonte = ler(os.path.join(RAIZ, "publicar.py"))
        self.assertIn("endsWith('sorteados.json')", fonte,
                      "servir a lista do cache devolveria justamente a "
                      "resposta velha que o botão existe para não dar")

    def test_as_duas_rodadas_dividem_o_mesmo_grupo(self):
        """Duas publicações simultâneas no mesmo Pages é como uma coleta pela
        metade vai ao ar. O grupo só protege enquanto o NOME bate nos dois —
        e conferir cada arquivo por si não veria a dessincronia."""
        grupos = []
        for nome in ("atualizar.yml", "sorteios.yml"):
            wf = ler(os.path.join(RAIZ, ".github", "workflows", nome))
            m = re.search(r"^concurrency:\n(?:.*\n)*?\s*group:\s*(\S+)",
                          wf, re.M)
            self.assertIsNotNone(m, "%s sem grupo de concorrência" % nome)
            grupos.append(m.group(1))
        self.assertEqual(grupos[0], grupos[1],
                         "grupos diferentes (%s): as duas rodadas podem "
                         "publicar ao mesmo tempo" % " / ".join(grupos))

    def test_a_publicacao_sobrevive_a_lista_corrompida(self):
        """Um JSON truncado não pode derrubar o build: a base ao lado tem
        tudo o que é preciso para refazer o arquivo."""
        fonte = ler(os.path.join(RAIZ, "publicar.py"))
        i = fonte.index('fonte_sort = os.path.join(')
        trecho = fonte[i:i + 1200]
        self.assertIn("except Exception", trecho,
                      "json.load sem proteção derruba a publicação inteira")
        self.assertIn("refazendo a partir da base", trecho)

    def test_o_robo_leve_roda_na_janela_do_sorteio(self):
        wf = ler(os.path.join(RAIZ, ".github", "workflows", "sorteios.yml"))
        self.assertIn('cron: "*/20 16-23 * * *"', wf,
                      "13h às 20h de Brasília é a janela em que o Sesc publica")
        self.assertIn("group: atualizar", wf,
                      "as duas rodadas publicam no mesmo Pages e não podem "
                      "se cruzar")
        self.assertIn("set -o pipefail", wf,
                      "o shell do runner é `bash -e` sem pipefail: num cano "
                      "quem decide o exit code é o tee, e o passo terminaria "
                      "verde com o script estourado no meio")
        self.assertIn("--anterior publicado.json", wf,
                      "sem comparar com o que está no ar, as 21 rodadas de "
                      "uma tarde republicam o site 21 vezes")
        self.assertIn("needs.sorteios.outputs.novas != '0'", wf,
                      "publicar sem artefato derruba o site que está no ar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
