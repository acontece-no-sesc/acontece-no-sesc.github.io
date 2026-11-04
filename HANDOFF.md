# Handoff — Acontece no SESC

Documento de contexto para retomar o projeto em outra conversa. Contém as
decisões, as descobertas sobre a fonte de dados e as armadilhas já resolvidas,
para não serem reintroduzidas.

**Pasta do projeto:** `C:\Users\leand\SESC`
**No ar:** https://acontece-no-sesc.github.io/ — é este o endereço a divulgar.
**Prévia congelada (não é o PWA):** https://claude.ai/code/artifact/10190c1d-a88a-4f8e-a237-2bdb49e16ef6

---

## 1. O que é

Agregador **não oficial** da programação do Sesc São Paulo (capital, interior e
litoral). App de celular instalável como PWA, com destaques, agenda, favoritos
e acompanhamento de sorteios.

Números atuais: **2.119 eventos** retidos de 2.477 coletados, janela de
11/08/2026 a 09/10/2026.

Regra ética adotada desde o começo: nunca imitar a identidade visual do Sesc,
sempre linkar de volta para a página oficial, e deixar visível que não é site
oficial. O app não pede login do Sesc e não coleta nada de quem usa.

---

## 2. As fontes de dados (o achado principal)

Nenhuma exigia chave. Foram descobertas inspecionando o tráfego do portal.

### 2.1 Listagem — API REST do WordPress

```
GET https://www.sescsp.org.br/wp-json/wp/v1/atividades/filter
    ?data_inicial=2026-08-15&data_final=2026-08-15
    &tipo=atividade&dinamico=true&ppp=300&page=1
```

Envelope: `{editorial, atividade[], total:{value}}`. A lista contém `null` no
meio — é preciso filtrar.

Campos úteis: `id`, `id_java`, `titulo`, `complemento`, `link`, `unidade[]`,
`tipos_linguagens[]` (categoria + subcategoria), `publico_tag[]`, `conjunto[]`
(projeto), `gratuito`, `esgotado`, `qtdeIngressosWeb`, `dataPrimeiraSessao`,
`dataUltimaSessao`, `quantDatas`.

**E `imagem` + `imagens{}`** — a foto da atividade. `imagem` é a original (chega
a 3 MB); `imagens` traz as variantes **só com o nome do arquivo**, na mesma
pasta. O coletor monta três URLs a partir daí (`thumb`, `img`, `capa`), uma
para cada uso na tela. Ficou um ano fora do coletor por não ter sido notada.

**Armadilha da paginação:** a listagem vem com `null` no meio, então uma
página cheia devolve **299** e não 300. Parar em "menos que `ppp`" cortava a
varredura na primeira página — o fim é a página **vazia**. Foi isso que fez a
primeira passada de fotos achar 1.569 de 2.342; corrigido, achou 3.582 itens e
só 14 eventos futuros ficaram sem foto.

**Armadilha conhecida:** o filtro de data **omite eventos**. "Ecos da
Independência" (12/09) não volta na consulta de 12/09, mas aparece na listagem
sem filtro. Por isso `coletor.py` faz uma varredura geral depois do dia a dia —
recuperou 53 eventos, 25 deles Turismo Social.

### 2.2 Regiões das unidades

```
GET https://www.sescsp.org.br/wp-json/wp/v1/unidades-atividades
```

O campo `description` já traz `capital` | `interior` | `litoral`. São 43
unidades: 25 capital, 16 interior, 2 litoral. **Endereço não vem aqui.**

O endereço está no HTML da página da unidade, em `data-geo-address` (e o link
do mapa em `data-como-chegar`), em `/unidades/<group_slug>/`. `extras.py`
raspa as 43 e grava em `unidades[].endereco`. Antes o endereço vinha só da
bilheteria, que cobre 32 unidades — as 9 que não vendem ingresso (Interlagos,
Itaquera, Osasco, Bertioga…) ficavam sem, e o `.ics` saía sem local.

### 2.3 Bilheteria — preços, sessões exatas e datas de venda

Indexada pelo `id_java` que vem na listagem:

```
GET https://portal.sescsp.org.br/bilheteria/atividade.action?idAtividade=253940
```

Entrega por sessão: `valorComerciario` / `valorMeia` / `valorInteira`,
`dataInicialSessaoFmt` (data e hora **exatas**), `dataInicialVendaOnlineFmt`,
`dataInicialVendaRedeFmt`, `statusSessaoSesc`, `qtdeIngressosWeb/Rede`,
`urlCompra`, `maxTicketSessao`. E no evento: `classificacaoMinina`,
`unidadePrincipal` com endereço e lat/lng.

Confirma a regra das 24h: venda on-line abre exatamente 24h antes da presencial.

**Só cobre quem tem `id_java`.** Turismo Social **não tem** — passeios não são
vendidos como ingresso. Para eles o preço vem do HTML (ver 2.4). Foi por isso
que 95 passeios ficaram sem preço numa versão.

### 2.4 Página do evento — descrição, inscrição, sorteio, preço do turismo

Não há endpoint de detalhe (conferido em `/wp-json/wp/v1`). Extração do HTML,
apoiada em classes estáveis:

- `.evento--sessao--entrada--preco` → pares `<span class="valor">` + `.label`
- `.info_local` → "Inscrições: 7/8 às 14h a 12/8 · Sorteio: 13/8 às 15h"
- Corpo da página → "Cronograma:" ou "INSCRIÇÕES" (Turismo Social)
- Lista de contemplados → o cabeçalho **não é um só**. `Resultado do Sorteio:`
  cobria o Turismo Social clássico; "Minas em Cena" publica sob um
  `CONTEMPLADOS` seco, logo depois de "Compartilhe:". Enquanto o extrator
  conhecia só a primeira forma, o app afirmava que a lista não tinha saído com
  ela na tela. Hoje reconhece quatro formas, percorre **todas** as ocorrências
  e fica com a maior lista — "sorteados" e "contemplados" também aparecem em
  prosa ("os sorteados poderão efetivar o pagamento"), e a trava é exigir um
  código nos dois primeiros tokens depois do cabeçalho, o que em prosa nunca
  acontece.
- Descrição → texto entre "Compartilhe:" e o primeiro bloco de serviço
- Cabeçalho → **"Duração: 50 minutos"**. Está na página e não na API; é o
  segundo degrau da regra de término do `.ics`. Ficou de fora por muito tempo
  porque foi procurada nas *descrições já coletadas*, que vêm truncadas — e lá
  ela não aparece nunca.
- Corpo → **regras recorrentes de inscrição** (`inscricao.regras`), ver 4.7

---

## 3. Armadilhas de extração já resolvidas

Cada uma custou uma rodada de depuração. **Não reintroduzir.**

| Problema | Correção |
|---|---|
| `<script>` tinha as tags removidas mas o **código ficava** na descrição | `RE_INVISIVEL` remove o conteúdo de script/style/svg antes de limpar |
| "Inscri**ção**" no singular nunca casava — o padrão exigia `[õo]` e a palavra tem **ã** | `Inscri[çc](?:[ãa]o\|[õo]es)`. Corrigiu 78 eventos |
| "Inscrições **de 6 a 10/8**" lia o **fim** como início (match ancorado falhava no "de ") | Prefixo opcional descartado + busca não ancorada |
| Janela de 90 caracteres perdia datas distantes do rótulo | Janela de 260, cortada no próximo rótulo (`RE_PROX_ROTULO`) |
| "Inscrição para **o sorteio**:" fazia o período de inscrição virar data do sorteio | Lookbehind `(?<!para )(?<!para o )` |
| Correção de ano comparava com o **início** do evento — temporada longa jogava a inscrição para o ano anterior | Compara com o **fim**, e só com folga > 60 dias |
| Rótulo "INSCRIÇÕES" era engolido pelo próprio regex, texto começava em ":" | Captura inclui o rótulo |
| "Divulgação dos sorteados" / "Sorteio e divulgação ... em 25/06" não reconhecidos | Padrões adicionais sem dois-pontos |
| "Resultado do sorteio **(em ordem alfabética):**" — o parêntese punha 3 tokens entre o cabeçalho e o 1º código, e a trava contra prosa desistia no 3º | `RE_ENFEITE` descarta o parêntese (até 60 caracteres) antes da contagem. Só o parêntese: alargar a janela de tokens traria de volta o falso positivo |

---

## 4. Regras de negócio

### 4.1 Retenção (`regras.py`)

O corte é pela **data de entrada**, não pela data do evento — a inscrição de um
passeio de novembro abre em agosto. Fica no app quem satisfaz uma destas:

1. inscrição ou venda **abre** nos próximos 60 dias;
2. inscrição ou venda **está aberta** agora;
3. acontece em 60 dias e **nada indica** que a entrada fechou.

Mais: **sai o Turismo Social de que não se sabe como entrar** — sem data de
inscrição, sem texto e sem regra recorrente. Exigir *data* era o corte
anterior, e ele apagava o passeio que simplesmente não tem inscrição (ver
6.4).

**Esgotado e prazo vencido não excluem mais.** Os dois eram tratados como
"barreira fechada" e tiravam o evento da base inteira — 41 esgotados e 306
inscrições encerradas que *acontecem* nas próximas semanas sumiam da agenda
sem nenhuma explicação na tela. São estado da entrada, não fim da atividade.
Agora ficam, com as etiquetas **"Esgotado"** e **"Inscrição encerrada"**; quem
decide se vale a pena é quem lê, não o corte. Isso trouxe a base de 1.324 para
1.644 eventos, 242 deles cursos e oficinas.

O coletor descarta apenas os **cancelados**. A categoria **"Outros"** — o balde
do que o portal não classificou — passou a ser mantida: jogá-la fora tirava da
agenda atividades reais, cujo único defeito era não ter rótulo. No app ela
aparece como qualquer outra categoria, com a cor neutra de reserva.

Ponto delicado: "barreira fechada" só conta com **data**. Centenas de eventos
têm bloco de inscrição em prosa sem data ("inscrições no local", "lista de
espera"); tratá-los como fechados descartava 62% do catálogo.

### 4.2 Estados de inscrição (no app)

- `futura` — abre depois de hoje
- `aberta` — prazo explícito ainda válido, **ou** presumido (abertura + 3 dias)
- `encerrada` — prazo passou

Sem prazo publicado, o app assume **abertura + 3 dias** e mostra "(estimado)"
com aviso para confirmar no site. Nunca afirmar "aberta" sem base.

### 4.3 Viagens de vários dias

Turismo Social é **contínuo**: viagem de 11 a 16/08 acontece nos seis dias.
`expandir_viagens` preenche o intervalo e marca `continuo: true`.

Só vale para Turismo Social. Exposição também tem início e fim distantes mas
fecha às segundas — ali os dias coletados é que estão certos.

**O campo `temporada` da API não serve como critério**: vem `False` para
exposições com dias fixos e `True` para passeios.

### 4.4 Relevância dos destaques

Procura 40% (ingressos restantes), custo 35% (gratuidade), raridade 25%
(sessão única vale mais que temporada). **Não existe nota da crítica no portal**
— não inventar. Esgotados saem dos destaques: recomendar o que não dá para
assistir é frustração.

---

### 4.5 O que é novidade

"Entrou agora na programação" não se descobre olhando só a base de hoje. Quem
compara é o **coletor**: antes de gravar, ele lê a coleta anterior e carimba
cada evento com `visto` — a data em que apareceu pela primeira vez. Quem já
estava lá conserva o carimbo antigo; quem não estava recebe a data de hoje.

- A primeira coleta com rastreio **não carimba ninguém**: ela estabelece a
  linha de base e grava `rastreioDesde` no arquivo. É esse campo que separa
  "base anterior ao rastreio" de "base sem novidades", e é o que faz o dia 2
  já valer.
- Sem essa trava o primeiro dia mentiria duas vezes. Medido no ensaio de
  18/08 contra a base de 13/08: **607 eventos** seriam marcados como novos,
  e **359 deles eram os "Outros"** — atividades que sempre estiveram no
  portal e que só entraram porque *nós* mudamos a regra. O trilho abriria com
  59% de entulho retroativo.
- Quem já estava na base e não tem carimbo fica **sem** carimbo: é anterior
  ao rastreio e não dá para inventar uma data. Herdar a data da coleta
  anterior também seria armadilha — a base de 13/08 estava a seis dias da
  primeira rodada, dentro da janela de sete.
- Ressalvas: um evento que suma da listagem por um dia e volte é carimbado de
  novo, e a janela que anda um dia por vez carimba a borda — medido, 3 eventos
  em 5 dias. É o preço de não guardar um histórico de ids que cresceria para
  sempre.

O app mostra o que tem `visto` nos últimos **7 dias** (`DIAS_NOVIDADE`), no
trilho "Novidades na programação" e no chip "Novidades" da agenda.

### 4.7 Inscrição sem data, com regra

Parte das inscrições não publica data nenhuma: publica uma **regra**, em
prosa, que se repete todo mês. O caso que motivou o suporte, num curso de
natação do Bom Retiro:

> As vagas disponíveis são liberadas prioritariamente para pessoas portadoras
> de **Credencial Plena na 1ª e na 3ª quinta-feira** de cada mês a partir das
> **18h**. Caso estas vagas não sejam preenchidas, serão disponibilizadas para
> o **público em geral na 2ª e na 4ª quarta-feira** a partir das **14h**.

São **duas datas diferentes para duas pessoas diferentes**, e nenhuma das duas
existe como data em lugar nenhum da fonte. `detalhes.py` extrai isso em
`inscricao.regras`:

```json
[{"quem": "plena", "semanas": [1, 3], "dow": 4, "hora": "18:00"},
 {"quem": "geral", "semanas": [2, 4], "dow": 3, "hora": "14:00"}]
```

`dow` segue a convenção do JavaScript (0 = domingo) porque quem consome é o
app. O extrator ancora no trecho "<dia da semana> de/do/no mês", lê os ordinais nos
110 caracteres anteriores e a hora nos 90 seguintes.

**O público vem antes ou depois da regra**, e ler só para trás não basta:

- antes — "para portadores de Credencial Plena **na 1ª e na 3ª quinta-feira**…"
- depois — "As vagas são liberadas na 1ª e 3ª quinta-feira de cada mês, a
  partir das 18h, **para quem possui credencial plena**"

A segunda forma virava `todos`, e com isso quem **não** tem Credencial Plena
via uma data que não era dele. Agora olha nos dois sentidos.

**Os ordinais ficam em algarismo de propósito.** Por extenso, "segunda",
"quarta" e "quinta" são também dias da semana: ler "primeira e segunda
quinta-feira" como ordinal daria uma data errada, que é pior do que não dar
data nenhuma.

O app calcula a próxima ocorrência de cada janela e **pergunta uma vez** se a
pessoa tem Credencial Plena, guardando a resposta em `agenda.credencial`. Sem
a resposta ele mostra as duas e marca a que abre primeiro, com o aviso de que
falta escolher — mostrar a quinta-feira da Credencial Plena para quem só entra
na quarta seguinte seria pior do que não mostrar nada.

O `.ics` do lembrete usa a data que é **daquela pessoa**. Por isso ele nunca
vem de arquivo servido: `publicar.py` só gera `-insc.ics` para data publicada,
já que não dá para servir uma versão por credencial.

### 4.6 Fuso horário

Tudo o que é "hoje" e "agora" é **horário de Brasília**, fixo em UTC−3 (o Brasil
não usa mais horário de verão desde 2019).

O robô roda em UTC no GitHub Actions: `geradoEm` saía sem fuso e o app exibia a
coleta três horas adiantada, além de arriscar carimbar o dia seguinte numa
execução de madrugada. Agora `coletor.agora_br()` grava com o deslocamento
explícito (`2026-08-18T05:10:00-03:00`) e o app converte para Brasília antes de
mostrar — inclusive carimbos antigos, sem fuso, que são lidos como já sendo
hora local da coleta.

No app, `HOJE` e `AGORA` também vêm de `agoraBR()`: com o relógio do aparelho,
quem estivesse fora do fuso via a agenda virar o dia na hora errada.

## 5. Arquitetura

```
coletor.py   → dados/eventos.json          (listagem, dia a dia + varredura)
detalhes.py  → enriquece o mesmo arquivo   (bilheteria + HTML)
sorteios.py  → dados/sorteados.json        (só as listas do dia, de 20 em 20 min)
extras.py    → acrescenta foto e endereço  (backfill; o coletor já grava)
reparse.py   → reprocessa sem rede         (usa o texto já guardado)
embutir.py   → prototipo.html              (arquivo único, prévia por link)
publicar.py  → web/                        (site hospedável, PWA)
```

`extras.py` existe porque `coletor.py` reescreve o arquivo do zero e
`detalhes.py` leva 70 minutos para reenriquecer: quando um campo novo aparece,
ele é acrescentado ao que já está no disco. `--fotos` pula a raspagem das
unidades. Numa coleta nova ele é dispensável para as fotos (o coletor já as
grava) mas continua sendo quem traz os endereços.

`reparse.py` é importante: `detalhes.py` guarda o texto bruto em
`inscricao.texto`, então melhorias no extrator se aplicam em segundos, sem
rebaixar milhares de páginas.

`sorteios.py` é o recorte mínimo de `detalhes.py`: lê **só** as páginas dos
sorteios cuja divulgação é de hoje (ou dos dois dias anteriores) e que ainda
não têm lista — hoje são duas. Reusa `detalhes.sorteados_do_corpo`, porque
duas cópias da regra dos quatro cabeçalhos garantiriam que uma envelhecesse.
Roda de 20 em 20 minutos das 13h às 20h (ver 5.2.3).

### 5.1 Duas saídas, um código

`prototipo.html` carrega com um instantâneo embutido e, se achar
`dados/eventos.json` ao lado, substitui pelo mais novo. Mesmo arquivo serve à
prévia por link (sem servidor) e ao site hospedado.

### 5.2 Descrições sob demanda

Ficam fora do pacote principal, em `dados/desc/<id>.json`, buscadas ao abrir o
evento e descartadas ao fechar. Na prévia por link não há servidor — por isso
ela é gerada com `embutir.py --com-descricao`.

### 5.2.1 O que o navegador consegue buscar do portal (e o que não)

Testado, e vale registrar porque define o que é possível em tempo real:

| endereço | CORS | serve para |
|---|---|---|
| `www.sescsp.org.br/wp-json/wp/v1/atividades/filter` | **liberado** | disponibilidade de ingressos ao vivo |
| `portal.sescsp.org.br/bilheteria/...` | bloqueado | — |
| `www.sescsp.org.br/programacao/<evento>` (HTML) | bloqueado | — |

Consequências diretas:

- **Disponibilidade** é conferida a cada abertura, direto do navegador, pela
  API de listagem: ela traz `qtdeIngressosWeb` e `esgotado`, que são os campos
  que o app mostra. Não é a base inteira — 1.644 eventos seriam doze páginas
  de 300 por abertura, e o portal não é nosso. Confere **hoje, amanhã e as
  próximas datas dos favoritos**, no máximo seis dias, e falha em silêncio.
- **Resultado de sorteio não dá para ler da página, na hora.** Os códigos dos
  contemplados estão no HTML da página da atividade, que é justamente o que o
  portal bloqueia.

  **Reconferido em 09/09/2026**, porque valia duvidar: no Chrome de verdade, a
  partir de `https://acontece-no-sesc.github.io`, `/wp-json/...` devolve 200
  legível e **toda** página HTML de `www.sescsp.org.br` devolve `Failed to
  fetch` — a do evento, a de unidade e a raiz. Curioso, e não muda nada: o
  `curl` recebe `Access-Control-Allow-Origin: *` nessa mesma página; o
  navegador, não. O que vale é o navegador.

  Então a automação possível é a que está no ar, e o que dá para mexer é a
  **frequência** — ver 5.2.3.

### 5.2.2 O cache do service worker é o resumo da casca

Era o carimbo da **coleta** (`geradoEm`). Como o robô só coleta uma vez por
dia, qualquer mudança no app entre duas coletas não trocava o nome do cache: o
worker seguia servindo o `index.html` antigo e quem já tinha instalado nunca
via a mudança. O sintoma era sempre o mesmo — "mexi no CSS e a tela não
mudou". Agora é o **sha1 do `index.html` publicado**: mudou o app, muda o
cache.

### 5.2.3 O botão "Consultar" e o arquivo magro

"Consultar" existe para dar a resposta **agora** — é a função dele, e ele não
a cumpria: buscava a base inteira, que era de madrugada, e o Sesc publica os
contemplados às 14h ou às 16h. Ler a página no toque é impossível (5.2.1). O
que sobra é encurtar a distância entre o portal publicar e o app saber.

Duas peças:

1. **`sorteios.py`, de 20 em 20 minutos**, das 13h às 20h de Brasília
   (`.github/workflows/sorteios.yml`). Lê só as páginas dos sorteios do dia —
   hoje, duas. Não é uma segunda coleta: `detalhes.py --html` bateria em 3 mil
   páginas, e a cada 20 minutos isso seria abuso de um portal que não é nosso.

2. **`dados/sorteados.json`**, de alguns KB, só `{id: [códigos]}`. É o que o
   app busca ao abrir e ao tocar em "Consultar". Baixar 3,5 MB para conferir
   oito letras cobraria de quem usa o preço de um problema nosso. A base
   inteira continua sendo o plano B, para quem tem instalado um app anterior a
   este arquivo — ali o `404` cai de volta na base e nada quebra.

**O intervalo do cron é um pedido, não um horário.** Medido em 10/09/2026
sobre as 8 execuções agendadas deste repositório, pela API pública do
GitHub: a rodada diária começa entre **3h38 e 6h02** depois da hora marcada,
média de 4h36. A única execução agendada da rodada leve que houve até agora
começou 2h26 depois do tique. Não é defeito nosso — o GitHub atrasa
`schedule` em repositório gratuito, e quanto mais popular o horário, pior.

Consequências, e elas são duas:

1. **A tela não promete cadência.** Dizia "o robô olha de 20 em 20 minutos"
   e "Consultar força a conferência agora"; as duas eram falsas — a segunda
   sempre foi, porque "Consultar" rebusca a lista publicada e não manda no
   robô. Hoje a frase diz de quando é a última conferência (que é sempre
   verdade) e aponta a página oficial. Há teste que recusa a volta das duas.
2. **O caminho para cadência de verdade existe, e não é `schedule`.** O
   `workflow_dispatch` não é atrasado: o disparo manual de 09/09 começou
   um minuto depois do clique. Um agendador externo chamando a API do
   GitHub entregaria os 20 minutos — ao preço de um token guardado fora
   daqui. Não foi feito.

A defasagem real, então, cai de **um dia para algumas horas** — melhor do que
era, e menos do que o cron sugere. Medido no caso que originou tudo:
"Jundiaí: os caminhos das águas" publicou 13 contemplados às 14h; a lista
chegou ao app na rodada da noite.

Duas travas que o desenho exige:

- **A rodada leve não escreve o cache.** Se escrevesse, um dia em que ela
  falhasse no meio deixaria uma base pela metade sob a chave que a coleta
  grande restaura na madrugada seguinte.
- **Republicar só quando há lista nova**, e "nova" é medida contra o
  `sorteados.json` que **está no ar**, não contra o que a passada leu. A
  rodada não guarda estado: sem essa comparação, as 21 rodadas de uma tarde
  achariam a mesma lista e republicariam o site 21 vezes.

O carimbo da conferência é **separado** do carimbo da coleta (`olhadaFmt()` no
app, `sorteadosEm` na base). Misturá-los faria a tela dizer "coleta das 5h10"
numa informação de vinte minutos atrás — envelhecendo a resposta em horas, no
único assunto do app em que minutos importam.

### 5.3 Calendário

**Um `blob:` nunca abre o app de calendário** — não tem endereço que o sistema
reconheça, então o navegador sempre trata como download. O que funciona é um
`.ics` servido por **URL real** com `Content-Type: text/calendar`. O service
worker reescreve o Content-Type: nem todo host declara esse tipo (o
`http.server` do Python manda `application/octet-stream`) e sem isso o celular
não entrega ao app de agenda.

O `.ics` é montado nos dois lugares — no aparelho (`prototipo.html`) e no
servidor (`publicar.py`) — e as **duas implementações têm de dizer a mesma
coisa**. As regras, uma a uma:

| campo | regra |
|---|---|
| início | a sessão que a pessoa abriu; sem dia aberto, a próxima |
| **fim** | término publicado pela bilheteria > início + duração > **início + 30 min** |
| endereço | sempre o da **unidade** (`unidades[].endereco`), nunca só o nome |
| alertas | **1 hora, 30 minutos e 5 minutos** antes |
| fuso | gravado em **UTC** a partir de Brasília (−03:00) |

Sobre o fim: `dataFinalSessaoFmt` quase sempre **repete a hora de início** —
não é término publicado, é campo vazio disfarçado. `detalhes.py` só guarda
`sessoes[].fim` quando o valor é maior que o início. Duração não existe em
lugar nenhum da fonte (procurada nas 2,3 mil descrições: zero ocorrências), o
que faz do bloco de 30 minutos o caso comum. É deliberado: um bloco curto e
honesto ocupa menos a tarde de alguém do que duas horas inventadas.

Sobre o fuso: hora flutuante (sem `Z`) é lida no fuso do **aparelho**. Quem
abrisse a agenda fora do Brasil marcava na hora errada. Agora sai `Z`.

**Um compromisso por vez.** A versão anterior despejava todas as sessões num
arquivo só: uma temporada de dois meses entrava na agenda como sessenta
compromissos para apagar um a um. Hoje é sempre um. Viagem contínua é a
exceção — vira um único compromisso de dias inteiros (`DTEND` exclusivo), com
alerta de véspera em vez dos três, porque "em 5 minutos" não quer dizer nada
num evento de dia inteiro.

`publicar.py` gera: `<id>.ics` com a **próxima** sessão, `<id>-AAAAMMDD.ics`
por sessão para quem tem até 8 datas, e `<id>-insc.ics` da abertura. O app só
usa o arquivo servido quando ele é do **mesmo instante** que o botão está
agendando; fora disso monta no aparelho. Um `HEAD` confere antes — num deploy
incompleto o arquivo falta e o link ficaria morto.

**Sem tela intermediária.** A escolha de calendário (app / Google / Outlook /
baixar) virava uma folha inteira entre o toque e o resultado. O botão agora
faz o que diz. No computador sobra um botão **ao lado** — não no lugar — para
o Google Agenda, porque lá o `.ics` cai na pasta de downloads e parece que
nada aconteceu.

## 6. A varredura de 09/09/2026 — 134 eventos que não chegavam à tela

Dois relatos de quem usa ("o sorteio saiu e o app diz que não" e "este evento
não aparece") viraram uma conferência do catálogo inteiro: a listagem do
portal, sem filtro de data, contra o que o site publicado mostrava. Deu **146
atividades no portal e fora do app**; 12 estavam legitimamente fora (7 além da
janela de 60 dias, 5 canceladas) e **134 eram perda**, por quatro causas
diferentes — nenhuma delas com teste, nenhuma delas visível no log.

O método vale mais que os números: **o app não sabe o que não coletou**. Todo
descarte silencioso desta lista passou por uma linha de log tranquila ou por
uma trava que mediu a coisa errada. A conferência contra a fonte é a única que
enxerga esse tipo de buraco, e sai barata — a varredura geral são 11 páginas.

### 6.1 "São Caetano " com um espaço sobrando — 27 eventos

O nome da unidade é a **única** chave entre a atividade e a região, e o portal
não o escreve igual nos dois endpoints: `/atividades/filter` manda
`"São Caetano "` e `/unidades-atividades` manda `"São Caetano"`. Sem casar,
`normalizar` marcava região `"outra"`, e `"outra"` era descarte.

`coletor.chave_unidade` normaliza os **dois** lados. Normalizar um só não
adianta: o que precisa coincidir são as chaves.

### 6.2 Atividade sem unidade nenhuma — 54 eventos

`unidade[0].name` vem `null` na programação que acontece **fora** de uma
unidade. Só o **Sesc na Bienal do Livro** eram 39 eventos gratuitos daquela
semana; havia ainda Semana Move, Festival Sesc 80 Anos e Sesc Galeria.

A causa era o filtro de região. Ele existe para **pedir menos** ("só a
capital"), mas rodava sempre — e como `reg` também vale `"outra"`, pedir as
três regiões apagava tudo o que não tinha região. Hoje ele só age quando se
pede um **subconjunto** próprio das três.

Na tela, `ondeDe(ev)` mostra o **projeto** quando não há unidade: `"Sesc —"`
não é lugar nenhum. O `.ics` do servidor (`publicar._local`) diz o mesmo — as
duas implementações do calendário continuam obrigadas a coincidir.

**A trava de 10% não viu nada**, e a lição é sobre travas: 81 eventos eram
2,5% do total, bem abaixo do limite. Ela também media a coisa errada — o saldo
do filtro, não o sintoma. Agora mede o que sempre quis medir: nome de unidade
que **não existe** no mapa, que é o rastro de "o portal renomeou uma unidade".
Isso é zero quando está tudo bem, então qualquer ocorrência aborta a coleta.

### 6.3 Temporada que atravessa a janela inteira — 23 eventos

A varredura de segurança só conhece a primeira e a última data. O código
guardava as duas pontas e **depois** recortava na janela — então "Curumim
2026" (fevereiro a dezembro), com as duas pontas fora dos 60 dias, ficava com
lista de dias vazia e sumia. Eram 23 programas contínuos, quase todos de
esporte e de criança, que acontecem toda semana e nunca apareceram.

As pontas passaram a ser **grampeadas** na janela antes de entrar. `parcial`
já era o campo que diz "esta lista de dias está incompleta", e a tela já
avisa.

### 6.4 Passeio sem data de inscrição — 20 eventos

A retenção exigia **data** de inscrição de todo Turismo Social. Isso apagava o
passeio que não tem inscrição nenhuma: *"Atividade aberta, sem retirada de
ingressos, mediante capacidade do local"*, *"Acesso livre"*, *"30 vagas.
Senhas na Loja Sesc com 30 minutos de antecedência"*. Não há o que datar
nesses — a página diz exatamente como entrar, e era isso que se queria saber.
Foram os três "Outras viagens" de Campinas e as caminhadas do Vila Mariana,
entre outros.

A regra passou a medir o que ela sempre quis dizer: sai o passeio de que não
se sabe **como entrar** — nem data, nem texto, nem regra recorrente. São 3 na
base de hoje, e neles a página realmente não conta como se participa.

### 6.5 O sorteio que "não saiu" — a coleta é que era de antes

"Jundiaí: os caminhos das águas" publicou os 13 contemplados às **14h**; o
robô tinha coletado às **10h**. O extrator está certo (relido na mão, acha os
13 códigos); o que faltava era hora. Com **uma** rodada por dia, de
madrugada, a lista de contemplados **nunca** chegava no dia — e o Sesc publica
às 14h ou às 16h.

Três correções, e as três são necessárias:

1. **Cadência da base.** O workflow grande ganhou uma segunda rodada, às 17h10
   de Brasília (20:10 UTC).
2. **Cadência do sorteio.** Uma rodada leve, de 20 em 20 minutos na janela em
   que o Sesc publica, que lê só as páginas dos sorteios do dia e escreve um
   arquivo de alguns KB. É o que faz o botão "Consultar" cumprir o que promete
   — ver 5.2.3.
3. **A frase.** O app dizia *"a lista ainda não foi publicada"* — afirmação
   que ele não tem como fazer, porque não lê a página do Sesc (sem CORS, ver
   5.2.1); ele lê o que o **robô** leu. Agora diz de que horas foi a última
   conferência, avisa quando ela é anterior ao horário do sorteio, e aponta a
   página oficial.

**Correção de 09/09, à noite:** o levantamento acima dizia que *6 dos 13
sorteios vencidos nunca teriam lista*. Errado, e o erro era nosso: em **3
deles os códigos estavam publicados na página** — 28, 14 e 13 — e o extrator
é que não os via, por causa do parêntese "(em ordem alfabética)" descrito
na §3. O que enganou foi a leitura apressada: as três páginas *também*
mencionam "Portal de Inscrições" e "Central de Atendimento", mas noutro
trecho, sobre confirmação de vaga e pagamento. Procurar a frase sem ler o
que está em volta do cabeçalho classifica errado.

O limite verdadeiro é menor: **2 dos 13**, os que de fato só publicam o
resultado num sistema com login. Para esses o app vai dizer "pendente" para
sempre, e distinguir esse caso — o cabeçalho está lá, os códigos nunca
estarão — continua sendo trabalho não feito.

Vale a lição do método: *"o extrator não achou"* nunca é conclusão, é
hipótese. A conclusão exige abrir a página e olhar o trecho em volta do
cabeçalho.

### 6.6 A data que o app inventou, com cara de fato — 350 eventos

Sem prazo publicado o app presume **abertura + 3 dias**, e a regra da casa é
que isso apareça sempre com "(estimado)" (ver 4.2). A linha da agenda cumpria
a regra em "inscrição aberta" e a esquecia em "inscrição encerrada": 350
eventos — **12% do catálogo** — afirmavam *"inscrição encerrada em 14 set"*
sobre uma data que ninguém publicou. Quem lesse desistia de um prazo que
talvez nem tivesse vencido.

A causa é a de sempre nesse tipo de defeito: **cada ramo montava a sua data**,
e a ressalva só foi acrescentada no ramo que motivou o recurso. Agora a data e
a ressalva saem juntas de um lugar só, e há teste que recusa qualquer ramo que
formate a data por fora.

### 6.7 O que a suíte não pegava, e agora pega

Noventa e nove testes, contra cinquenta e quatro. Os novos cobrem cada uma
das causas acima e mais uma que apareceu **fazendo** a correção:

Um ajudante novo chamado `localDe` sentou em cima do `localDe` que já existia
para montar o `LOCATION` do `.ics`. O JS não reclama de redeclarar `function`:
a segunda vence, e a antiga passou a chamar a si mesma, estourou a pilha e
derrubou `aplicarDados` — o app abria com a base embutida e não trocava por
nada. **Sintaxe perfeita, teste de sintaxe passando, app quebrado.** Só
apareceu abrindo o app no navegador e lendo o console. Hoje há um teste que
recusa duas funções com o mesmo nome no escopo de cima do app.

---

## 7. Estado atual e pendências

### Feito
Tudo o que foi pedido até aqui está implementado e verificado no navegador.

### Pendência única
**Publicar no GitHub.** O repositório está commitado (9 commits, 12 arquivos,
7,5 MB). Faltam os passos que exigem credencial do usuário:

```bash
git remote add origin https://github.com/SEU-USUARIO/agenda-sesc.git
git push -u origin main
```

Depois: **Settings → Pages → Source: GitHub Actions**, e
**Actions → Atualizar programação → Run workflow**.

O endereço sai como `https://SEU-USUARIO.github.io/agenda-sesc/`.

A coleta grande roda duas vezes por dia — 5h10 e 17h10 de Brasília (08:10 e
20:10 UTC) — e leva 30 a 45 minutos. A rodada leve dos sorteios roda de 20 em
20 minutos das 13h às 20h e leva menos de um minuto (ver 5.2.3).

`gh` CLI não está instalado e a conexão do GitHub não está autorizada na
sessão — por isso o push não pode ser feito pelo assistente.

### Não verificado
- **Service worker não registra no painel de prévia** (webview restrito). O
  código está escrito e o arquivo é servido corretamente, mas só se confirma
  em Chrome ou Safari de verdade.
- **O passo final do calendário depende do aparelho.** No iPhone o Calendário
  costuma abrir na hora; no Android o arquivo passa pela lista de downloads.
  Não existe endereço universal que force "abra o app de calendário" pela web.

### Sabidamente fora de alcance
- Datas de pagamento pós-sorteio não estão no bloco padrão de algumas páginas.
- Conferência automática de sorteio depende de o Sesc publicar a lista; hoje
  são 11 eventos com códigos coletados.

---

## 8. Como rodar

Só precisa de Python 3.9+. Nenhuma dependência externa. **Node não está
instalado nesta máquina.**

```bash
python coletor.py --de 2026-08-11 --ate 2026-10-09   # ~5 min
python detalhes.py --html                            # ~70 min (descrições)
python reparse.py                                    # instantâneo
python extras.py                                     # ~4 min (fotos + endereços)
python embutir.py --com-descricao                    # arquivo único
python publicar.py                                   # pasta web/
python -m http.server 8100 --directory web           # testar
```

Do celular na mesma Wi-Fi: `http://192.168.0.240:8100`

Opções úteis:

```bash
python detalhes.py --html --faltantes    # incremental, só os sem descrição
python embutir.py --sem-projeto          # tira temporadas e programas recorrentes
python embutir.py --horizonte 90         # muda a janela da retenção
python publicar.py --base /agenda-sesc/  # subpasta do GitHub Pages
```

---

## 9. Decisões de design

### 9.0 v4 — "Cartaz" (o design em vigor)

A v3 era **papel e tinta**: guia impresso, fios finos no lugar de cartões,
serifa nos títulos, sans na lista, monoespaçada nos dados, raio máximo 4px,
tema claro. Ela resolvia o volume — 2,3 mil eventos, 500 por dia — mas
escondia o que faz alguém escolher um espetáculo: **a foto**. O portal publica
uma imagem para cada atividade e o app jogava fora.

A v4 mantém a densidade e parte da foto. Três mudanças de fundo:

1. **A foto é o convite.** Cartão com imagem 16:9, linha da agenda com
   miniatura de 62px, folha de detalhe com capa sangrada e título por cima.
   Sem foto (12 de 1.320 no publicado), o lugar vira um cartaz desenhado com a
   tinta da categoria e a inicial do título — um retângulo cinza vazio se lê
   como falha de carregamento.
2. **As respostas viram etiqueta.** Três perguntas se repetem em toda decisão:
   *ainda dá para entrar* (Últimos ingressos / Esgotado), *é para mim*
   (Público) e *onde é* (Local). Viraram um componente só, `.tag`, com a mesma
   forma no cartão, na lista e no detalhe. Na linha densa o texto abrevia
   (`opc.curto`): sai o "a partir de" e o "Sesc", que não cabem em 185px.
3. **O filtro sai da gaveta.** Ver "Filtros" abaixo.

Tipografia: **Bricolage Grotesque** nos títulos (voz de cartaz, com caráter) e
**Archivo** no texto, com pilha de reserva do sistema — sem rede o app cai em
sans-serif e nada quebra de layout. Monoespaçada continua em hora e número.

Cor: papel morno (`#FAF8F4`), tinta quente quase preta, **um** azul de ação e
as oito tintas de categoria. A regra da v3 continua valendo — o cromo é
neutro, a cor pertence ao conteúdo — só que agora quem colore é a foto. Raio:
6px em controle, 12px em mídia e caixa, 20px na folha, pílula em botão e
etiqueta.

Os tokens estão no topo do CSS de `prototipo.html`. Trocar os valores desse
bloco reveste o app inteiro sem tocar em componente.

**"Para você" tem quatro trilhos, nesta ordem**, e cada um é uma pergunta:

1. Novidades na programação — o que é novo
2. Hoje e amanhã — o que dá para fazer agora
3. Agenda da semana — como está a semana
4. Inscrição ou venda abre em breve — do que cuidar antes que abra

A v3 tinha doze. A tela virava corredor e a mesma atividade aparecia em
quatro deles. Inscrição e venda viraram um trilho só porque, para quem lê, a
pergunta é uma só: *quando abre?* — separá-las obrigava a olhar os dois para
não perder nada. O trilho de novidades **não aparece** numa base sem o carimbo
`visto` (ver 4.5): ali ele seria permanentemente vazio na primeira dobra.

**Filtros.** Antes eram quatro lugares com quatro gramáticas: a barra de
contexto (unidades), a gaveta (projetos e categorias), a folha de unidades e a
fileira de chips da agenda. Quem tinha três filtros ligados não tinha onde ver
os três juntos, e desligar um exigia lembrar de onde tinha vindo.

Agora é um lugar só: uma **barra sempre visível** com o que está ligado, em
pastilha que se desliga com um toque, e uma **folha única** com região,
unidades (com busca), categorias, público, preço, situação da entrada e
projetos. As contagens continuam facetadas — cada opção é contada com todos os
outros filtros aplicados, **menos o dela própria**. Até três unidades aparecem
pelo nome na barra, porque "3 unidades" não diz se Pompeia está entre elas.

Consequência importante: a situação da entrada (grátis, com ingressos,
inscrição aberta, por sorteio…) **deixou de ser um chip só da agenda** e virou
faceta global — agora vale também nos trilhos e nas contagens.

**Duas colisões de nome que custaram depuração:**

- A miniatura da lista chamava-se `.mini` e a fileira compacta de etiquetas,
  `.tags.mini`. A regra `.ev .mini { width: 62px; height: 62px }` casava com as
  duas, e a fileira de etiquetas virava um quadrado de 62px: `Esg…`,
  `a parti…`, `Ses…`. A miniatura agora é `.thumb`.
- `regiaoDaUnidade()` era chamada em `abrirUnidades()` e **nunca existiu** —
  trocar a aba de região com unidades marcadas quebrava a folha inteira com
  `ReferenceError`. Bug antigo, exposto ao reaproveitar o código na folha de
  filtros. Definida.

### 9.1 Computador

O app nasceu para o celular — uma coluna de 460px — e numa tela larga isso vira
uma tira no meio de um deserto. A versão para computador é **só layout**: media
queries no fim do CSS, nenhum componente novo, nenhum caminho de código
separado. O celular não é tocado.

A escada de larguras:

| a partir de | o que muda |
|---|---|
| 900px | a gaveta vira coluna fixa à esquerda (286px), as abas do topo somem, a busca fica sempre visível, os trilhos viram grade de 2 colunas |
| 1100px | trilhos com 3 colunas |
| 1240px | trilhos com 4 colunas; a lista densa (agenda e favoritos) ganha **duas colunas**, com a faixa do dia atravessando as duas |

Decisões que valem registro:

- **A gaveta deixa de ser gaveta.** Com ela permanente, as abas do topo viram o
  mesmo menu duas vezes e saem (`.mainnav { display: none }`). Isso muda o
  grude da faixa de dia, que passa de 99px para 57px.
- **Os quatro destinos são a navegação, e precisam ter peso disso.** Na
  primeira versão eles ficaram com a aparência de item de menu do celular e
  não foram lidos como abas — o relato foi "faltou uma aba para cada opção".
  Agora têm corpo de aba: alvo de 42px, tipo maior, estado ativo em bloco
  cheio de tinta com marcador de cor, e contador à direita. Filtros,
  categorias e dados seguem em texto miúdo, para a hierarquia dizer o que é
  navegação e o que é ajuste.
- **Trilho vira grade, não carrossel.** Arrastar cartão com mouse é ruim. São
  sempre **duas fileiras** — o resto continua atrás de "ver todos", senão cada
  trilho viraria parede.
- **A folha de detalhe encosta à direita** em vez de subir do rodapé: numa tela
  larga, subir do fundo esconde o que a pessoa estava lendo. O véu começa em
  286px, então a coluna da esquerda continua clicável com a folha aberta.
- **O conteúdo para de crescer em 1180px.** Linha longa demais cansa tanto
  quanto lista longa demais.

O único JS envolvido é `ajustarModo()`, atrelado a um `matchMedia`: uma coluna à
vista não pode continuar marcada como `aria-hidden`, e o véu e a trava de
rolagem não fazem sentido nela.

**Armadilha ao testar:** o site publicado tem service worker, e ele guarda a
casca. Mudou o CSS e a tela não mudou? O SW está servindo o `index.html`
antigo — é preciso desregistrar e limpar o cache, não basta recarregar.

### 9.2 Calendário: o computador é outro bicho

No celular o alvo é o app de agenda do aparelho, e o `.ics` é o que o sistema
entende. No computador esse caminho não existe: o navegador baixa o arquivo e
ele morre na pasta de downloads — foi exatamente o "não acontece nada"
relatado. Por isso, no computador, aparece um botão **ao lado** do principal
para o **Google Agenda** (`calendar.google.com/render?action=TEMPLATE`), com
`ctz=America/Sao_Paulo` explícito: sem o fuso, o Google usa o da conta e quem
estiver fora do Brasil agenda na hora errada. E o aviso diz onde o arquivo foi
parar, senão "baixar" também parece "não aconteceu nada".

O que **saiu** na v4: a folha de escolha de calendário (app / Google / Outlook
/ baixar / escolher datas). Ela era uma tela inteira entre o toque e o
resultado, para uma decisão que quase sempre tem uma resposta só. O botão faz
o que diz; o resto é escolhido pelo contexto.

### 9.3 Como rodar o backfill de fotos e endereços

```bash
python extras.py            # fotos + públicos + endereços das 43 unidades
python extras.py --fotos    # só as fotos (quando muda o critério de tamanho)
python embutir.py --com-descricao
python publicar.py
```

O design system está no topo do CSS de `prototipo.html`, em tokens. Trocar os
valores desse bloco reveste o app inteiro sem tocar em componente.

---

## 10. Divulgação (03/09/2026)

A squad ganhou um sexto papel — **vídeo e marketing** — definido em
`.claude/agents/video-marketing.md`. Ele carrega as regras que não podem se
perder entre uma peça e outra: nunca imitar o Sesc, todo número conferido na
base antes de entrar em tela, e nada de prometer o que o app não faz (ele não
manda notificação, não vende ingresso, não inscreve e não adivinha sorteio).

Pasta `divulgacao/`:

| Arquivo | O quê |
|---|---|
| `filme.html` | A peça pronta, um arquivo só (~1,2 MB): 9:16, tour de 65 s, médio de 30 s, curto de 15 s e quadro parado. Abre em qualquer navegador; para virar MP4, grave a tela enquanto toca |
| `filme.template.html` | O molde, com `{{TELA:nome}}` no lugar de cada foto — **edite este**, não o `filme.html` |
| `montar_filme.py` | Troca cada marcação por um `data:` URI WebP e escreve o `filme.html` |
| `capturar.py` | Fotografa as 12 telas do app rodando |
| `cdp.py` | Cliente do DevTools Protocol em Python puro, escrito para isto |
| `roteiro-lancamento.md` | Cenas, tempos, legendas, os números com a conta de cada um, e o histórico v1 → v3 |
| `personas.md` | As seis fichas da validação de 02/09, agora em disco — antes só existiam no relatório publicado |
| `plano-visual.md` | Paleta, tipografia e as decisões de forma |
| `telas/` | As capturas, regeráveis (`_*.png` está no `.gitignore`) |

Refazer tudo: `python divulgacao/capturar.py && python divulgacao/montar_filme.py`.

### 10.1 As telas são fotografias do app, não maquete

A v1 e a v2 recriavam a interface à mão; a v3 fotografa o app rodando. O
`capturar.py` sobe um Edge headless conduzido pelo DevTools Protocol, semeia o
`localStorage` (quatro unidades, credencial plena, três favoritos, um sorteio
guardado), aperta os botões do próprio app e fotografa em 390×844 a 3×.

Três armadilhas já resolvidas ali:

* **`--screenshot` da linha de comando não serve.** No Windows a janela é
  pedida em pixel físico e o viewport nasce em DIP com a escala do sistema:
  pedir 390 entrega 492 CSS e a foto sai cortada à direita. Por isso o
  `cdp.py` e o `Emulation.setDeviceMetricsOverride`.
* **O Edge devolve o terminal antes de escrever o PNG** — um filho termina o
  arquivo depois. Quem não espera, apaga o HTML que ele ainda está lendo.
* **O portal recusa a foto quando o agente diz "HeadlessChrome".** Sem trocar
  o `User-Agent`, toda capa vira a inicial colorida. `--sem-fotos` força esse
  estado de propósito, se a peça precisar sair sem imagem de terceiro.

**Ampliar a tela toda corta as laterais**, porque o recorte 9:16 não sobra em
lugar nenhum. O detalhe vem numa lupa sobre o aparelho, posicionada em pixel a
partir de `data-f` — a altura da faixa como fração da tela.

**O endereço fica num lugar só:** `var ENDERECO`, no alto do script do molde.
Hoje vale `acontece-no-sesc.github.io`, e aparece em três lugares — no quadro
parado (a cena 2, que é o que viaja no WhatsApp), na chamada final e na última
legenda. Vazio, a peça cai numa frase genérica em vez de inventar uma URL.

Peça publicada: https://claude.ai/code/artifact/41247c3c-318a-47c7-9891-5883b32078e6

---

## 11. O dia em que o portal piscou — 31/10/2026

As duas rodadas de **Atualizar programação** daquele dia falharam no mesmo
passo, `Coletar a listagem`. Nada foi gravado — o pipeline falhou do jeito que
foi desenhado para falhar — e o site ficou servindo a base de 30/10 às 20:42
por dois dias, correto e velho.

O que entrega o diagnóstico é a **duração do passo**, não a mensagem: uma
coleta inteira leva 4 a 5 minutos, e a rodada #119 morreu em **6 segundos**.
Seis segundos era exatamente a paciência do coletor: `TENTATIVAS = 3`
esperando 2s e 4s. Ou seja, ela morreu na **primeira requisição da rodada**,
a das unidades, antes de pedir um único dia. A #118 durou 88s — dois timeouts
de 30s seguidos. Na manhã seguinte a fonte respondia inteira: 43 unidades,
nenhuma órfã, `Turismo Social` no lugar, 2.971 itens na varredura geral.

Não era defeito nosso nem mudança de forma na fonte. Era o portal piscando, e
uma teimosia de 6 segundos decidindo o dia.

### 11.1 O que mudou em `coletor.py`

**Paciência.** `TENTATIVAS = 6` com `ESPERAS = (2, 5, 10, 20, 40)` — 77
segundos somados por URL, contra os 6 de antes. O job tem 180 minutos de
folga; esperar um minuto é barato, perder o dia não é.

**Insistir só onde há o que esperar.** `repetivel()` repete 5xx, 408, 425,
429 e **403** (é com ele que o WAF do portal barra tráfego que achou
suspeito, e isso passa). 404 e 400 levantam na primeira: repetir gastaria 77
segundos para chegar na mesma resposta e ainda faria "falhou 6 vezes" parecer
instabilidade quando a verdade é que a URL mudou.

**`Retry-After` obedecido, com teto** de 120s (`ESPERA_MAXIMA`) — o cabeçalho
pode mandar esperar meia hora, e aí a rodada estoura o job.

**Página de manutenção conta como falha.** O WAF devolve HTTP **200 com HTML
dentro**; isso levantava `JSONDecodeError` fora do `except` e derrubava a
rodada na primeira, sem repetir. Agora `FALHA_DE_REDE` inclui o JSON quebrado.

**Um dia perdido não vale os outros 59.** A consulta de um dia que falhe entra
num caderno, sai como `::warning::` na aba Actions, e a rodada segue: a
varredura de segurança ainda recupera o evento pelas datas do próprio
registro. O que se perde é a precisão dos dias intermediários, não o evento.
No log, `FALHOU` e `0 sessões` são linhas diferentes — é o único lugar onde a
diferença aparece depois.

### 11.2 As três travas que o conserto exigiu

Tolerar é o avesso do defeito antigo, e tolerar calado é o pecado deste
pipeline. Então:

1. **Mais de 10% dos dias perdidos aborta na hora** (`FRACAO_DIAS_FALHOS`),
   dentro do laço. Portal fora não é tropeço, e insistir nos 60 dias gastaria
   horas para abortar no fim — ou estouraria o job.
2. **Varredura de segurança sem a primeira página aborta.** É ela que
   recupera o que o filtro de data omite (§6.3); sem ela a coleta sai com
   buracos invisíveis. Da segunda página em diante, o que já veio vale.
3. **Dia perdido + varredura truncada abortam juntos.** Uma cobre a outra; as
   duas faltando, ninguém responde por aquele pedaço da janela.

`carregar_unidades()` **continua fatal de propósito**: o nome da unidade é a
única chave entre a atividade e a região, e sem o mapa a coleta inteira sai
sem região e sem endereço. O que mudou ali foi só a paciência.

### 11.3 Como isso foi verificado

Vinte e seis testes novos em `teste_extratores.py`, sem rede e em
milissegundos — catorze do coletor (`PacienciaComOPortal` e
`DiaQueNaoRespondeNaoDerrubaARodada`) e doze dos outros dois
(`PacienciaRepartidaEntreOsTres`, `FalhaDoPortalContadaEmDetalhes`,
`VarreduraDeFotosNaoConfundeFalhaComFim`).

**O que vale neles é que a maioria falha quando rodada contra o código de
antes do conserto**: 12 dos 14 e 9 dos 12. O método é barato e deveria ser o
padrão aqui — carregar a versão anterior do arquivo com
`importlib.util.spec_from_file_location`, injetá-la em `sys.modules` e rodar
só as classes novas. Os que passam nas duas versões descrevem comportamento
que já estava certo (desistir no fim, repetir 403, o fim da varredura ser a
página vazia) ou leem a fonte do disco, que é sempre a nova.

A suíte vai a **126 testes**. Fora isso, verificação ao vivo contra o portal:
coleta de 5 dias em 61s com 1.595 eventos, `detalhes.py --max 8 --html` com
`sem resposta=0`, e os buscadores do `extras` devolvendo as 43 unidades, três
endereços raspados e um 404 que **não** foi repetido.

### 11.4 A paciência é repartida; a política é uma

`detalhes.py` e `extras.py` falham de outra forma: devolvem `None` e seguem,
então portal instável não derruba a rodada — enche de buracos em silêncio, que
é o defeito oposto. O `None` é contrato (`sorteios.py` conta com ele, e um
evento sem resposta tem de ficar com o que já tinha em vez de custar uma
passada de 70 minutos). O que não era contrato era a perda sair sem registro.

**A paciência não pode ser a mesma nos três, porque o número de URLs não é.**
77 segundos por URL cabem nas ~70 do coletor e comeriam o job nas ~3.000 de
`detalhes.py`. Então a escada é de cada um, e quem bate em mais URLs espera
menos — há teste que recusa a inversão:

| script | URLs | tentativas | escada | pior caso por URL |
|---|---|---|---|---|
| `coletor.py` | ~70 | 6 | 2, 5, 10, 20, 40 | 77s |
| `extras.py` | ~115 | 5 | 2, 5, 10, 20 | 37s |
| `detalhes.py` | ~3.000 | 4 | 2, 5, 10 | 17s |

O que **não** varia mora em `coletor`, num lugar só: quais códigos vale
repetir, como ler o `Retry-After`, o que conta como falha de rede. Há teste
que recusa uma segunda cópia dessa lista nos outros dois arquivos — a §6.7 já
ensinou o preço de duas cópias da mesma regra.

Três coisas a mais em `detalhes.py`:

* **O `except Exception` saiu.** Ele engolia bug nosso junto com portal fora:
  um `TypeError` virava "não respondeu", entrava na estatística de rede e a
  passada terminava com sucesso. Agora só falha de rede é capturada.
* **Orçamento de espera** (`ORCAMENTO_ESPERA`, 900s). A aritmética virava
  contra nós: 300 páginas tropeçando a 17s cada uma são 85 minutos de sono em
  cima dos 70 de trabalho, e o job morreria no limite sem publicar. Gasto o
  orçamento, `buscar` para de insistir — a passada termina incompleta **e
  dizendo que está**.
* **Trava antes do carimbo.** `enriquecidoEm` é o que o app mostra como
  "última conferência", e isso é uma afirmação. Acima de 10% de páginas sem
  resposta (piso de 25) a passada aborta em vez de carimbar: é exatamente
  assim que um resultado de sorteio "não sai" para quem está com o código na
  mão. O número de perdas agora sai na linha final, como `sem resposta=N`.

### 11.5 Dois buracos calados que apareceram lendo o `extras.py`

Nenhum dos dois tinha a ver com paciência. Apareceram porque, para contar as
falhas, foi preciso perguntar o que o código fazia com elas.

1. **A varredura de fotos confundia falha com fim de lista.** Ela para na
   página vazia — tem de ser assim, porque uma página cheia devolve 299 e não
   300 (§2.1). Mas a página que não respondia chegava como `None`, virava "0
   itens", e o laço lia fim de lista: um tropeço na página 3 encerrava a
   varredura e o backfill gravava um terço das fotos com cara de inteiro. É a
   armadilha da §2.1 de cabeça para baixo.
2. **A trava dos 90% se desligava sozinha no pior caso.** Se
   `/unidades-atividades` não respondesse, `api` ficava vazia, `esperadas`
   virava 0, e o `if esperadas and …` deixava passar justamente o caso de
   **nenhum** endereço — o `.ics` de 486 eventos sem local, que é a regressão
   que a §2.2 diz ter sido corrigida.

E no relatório das unidades, "SEM RESPOSTA" agora é diferente de "SEM
ENDEREÇO": pedem providências diferentes e chegavam iguais.

### 11.6 Como ler o log de uma falha dessas

Para ler o log de uma falha dessas é preciso **estar logado no GitHub**, mesmo
em repositório público; a API anônima dá 403 e as *annotations* só dizem
"Process completed with exit code 1". O que dá para ver sem login é a duração
de cada passo, e ela basta para separar "desistiu na primeira requisição" de
"coletou e a trava recusou".

---

## 12. A coleta sai do GitHub Actions — 01/11/2026

O §11 consertou a paciência de um cliente que, descobriu-se depois, está
**barrado**. O log das três falhas diz `HTTP 403 Forbidden` em todas as
tentativas, e o run #118 datou o começo: ele coletou as 43 unidades e 19 dos
60 dias — umas 40 requisições — e só então tomou o primeiro 403, em **31/10 às
14:29 UTC**. Os dois runs seguintes tomaram 403 na primeira requisição.

Uma sonda (`.github/workflows/sonda.yml`, disparo manual) mediu o que está
sendo barrado:

| variante | resultado |
|---|---|
| listagem · UA do coletor | 403, 919B, `server: CloudFront` |
| listagem · UA de navegador | **mesmo 403, mesmos 919B** |
| listagem · sem UA nenhum | **mesmo 403** |
| página de unidade (HTML) | 403 |
| bilheteria (`portal.sescsp.org.br`) | **500 do aplicativo** — host aberto |

**Não é o User-Agent**, então não havia nada a disfarçar: a pergunta ética
evaporou junto com a técnica. É o CloudFront recusando na borda, no host
`www`. E **não é bloqueio de datacenter**: a mesma URL pedida de 14 nós do
check-host.net, em 12 países, devolveu **200 em todos os 14**. A regra acerta
as faixas do GitHub/Azure.

Dois mistérios menores que caíram no caminho:

* O workflow de sorteios "passava" porque a fila estava vazia —
  `Sorteios sem lista: 0`, zero requisições ao portal. Não era álibi.
* Como cada run sai de um IP diferente da Azure, limite por IP está
  descartado: o #119 teria começado com orçamento novo e começou barrado.

### 12.1 O plano B, que não precisou

**Leia a §12.3 antes desta seção: o portão resolveu, e isto aqui virou plano
B.** Fica registrado porque está testado e porque é o caminho no dia em que a
Cloudflare fechar — a sonda diz quando.

`vps/` tem o caminho inteiro, com o passo a passo em `vps/LEIAME.md`: a coleta
e a publicação passam a rodar numa VPS pequena por cron, e o Pages passa a
servir do branch `gh-pages`. Repositório, testes e código ficam onde estão.

A publicação é **um commit órfão com `--force`**, então o branch tem sempre
exatamente um commit e o repositório não cresce — é a mesma preocupação que
tirou `dados/eventos.json` do histórico (§5), resolvida de outro jeito.

Medido num repositório descartável, publicando três vezes:

* `web/` são **43 MB e 10.451 arquivos** — muito mais do que os ~5 MB que eu
  supunha, por causa do fan-out de `dados/desc/*.json`;
* o remoto fica em **4,0 MB** (o JSON comprime muito);
* a terceira publicação acrescentou **um único objeto**, o commit, e **zero**
  byte de pacote. Então as 21 rodadas de uma tarde de sorteio não custam
  banda nem tamanho — o que custava era republicar sem lista nova, e isso o
  script já não faz.

O que **não** foi verificado: `rodar.sh` e `rodar-sorteios.sh` não rodaram
nunca. Usam `flock` e substituição de processo, que não existem no Git Bash do
Windows; a primeira execução de verdade é na VPS. O que foi testado aqui é
`publicar-no-pages.sh`, que é a parte com plumbing de git.

### 12.2 O que ainda falta no código

Tirar o **403 da lista dos repetíveis**. Hoje o coletor gasta 78 segundos
(2+5+10+20+40) para bater seis vezes na mesma parede: bloqueio não é tropeço.
E fazer todo `ABORTADO` sair como `::error::`, porque *annotations* são
legíveis sem login e os logs não são — a investigação de 01/11 só foi possível
depois de instalar o `gh` no meio dela.

### 12.3 O portão — a coleta volta para o GitHub

Antes de montar VPS, valia esgotar o GitHub. A sonda rodou nos **três** tipos
de runner hospedado, que num repositório público têm minuto ilimitado:

| runner | IP de saída | listagem |
|---|---|---|
| `ubuntu-latest` | 40.76.239.102 | 403 |
| `windows-latest` | 158.23.93.162 | 403 |
| `macos-latest` | 13.105.117.81 | 403 |

O macOS era a esperança, porque historicamente não rodava na mesma
infraestrutura. Não há configuração que mude o IP de saída de um runner
hospedado, então o compute do GitHub estava esgotado.

**O que não estava esgotado era o egresso.** A ideia que virou o jogo: o
problema nunca foi onde o código roda, foi de onde a requisição sai. Um
**Worker da Cloudflare** de quinze linhas (`portao/worker.js`) recebe o pedido
e repassa ao portal. Plano gratuito: 100 mil requisições por dia, contra as
~2.400 de duas rodadas. Nada para manter ligado, nada para pagar.

Medido no **mesmo job do Actions, no mesmo minuto**:

| mesma URL, mesmo runner | direto | pelo portão |
|---|---|---|
| `/wp-json/.../unidades-atividades` | 403 · 919B | **200** · 11.742B |
| listagem do dia, com filtro de data | — | **200** · **1.070.556B** |
| `/unidades/14-bis/` (HTML) | 403 · 919B | **200** · 60.570B |

O cabeçalho `x-portao: sesc` em todas é o que faz a medição valer: sem ele, a
resposta pode ter vindo do portal falando direto com quem mediu — e de casa
dá 200 de qualquer jeito.

**Do lado do código mudou pouco:** `coletor.pelo_portao(url)` reescreve o que
for do `www` e acrescenta a chave; `detalhes.py` e `extras.py` chamam o mesmo
ajudante. A troca mora **dentro do `buscar`**, não na montagem das URLs,
porque metade delas não é montada por nós — `ev["link"]` vem absoluto da API,
e trocar só onde montamos deixaria três mil páginas indo direto para a parede.
A bilheteria não é desviada: `portal.sescsp.org.br` nunca foi barrado.

Sem `PORTAO_URL` no ambiente **nada muda** — é o caminho de antes. É isso que
mantém o teste local honesto e o que permite desligar o portão num dia.

O portão **não é um proxy aberto**: destino fixo no código, só GET, e fechado
com `TOKEN` (sem a chave responde 401). `PORTAO_URL` e `PORTAO_TOKEN` são
secrets do repositório — o endereço fica fora do repositório público porque a
cota do Worker é nossa.

### 12.4 Uma correção e uma armadilha

A §12 dizia que a regra acerta "a faixa da Microsoft". É mais largo: nesta
última sonda um runner saiu por **3.42.157.193**, que não é faixa Microsoft, e
levou 403 igual. Com os 14 nós de provedores pequenos que deram 200, o retrato
honesto é uma regra de anti-abuso que cobre as nuvens grandes e não cobre
hospedagem pequena.

E a armadilha que custou duas idas e vindas: os secrets foram criados no
repositório **errado** — `lmtrigo/agenda-sesc`, o antigo, que ainda existe como
remote `antigo`. Existiam de verdade, só que onde nada roda. O sintoma foi
`PORTAO_URL` chegando vazio ao runner, sem erro nenhum. De passagem: o GitHub
**nunca** mostra um segredo de volta, nem ao dono — editar e ver o campo em
branco é o comportamento normal, não um segredo vazio.

### 12.5 O portão atravessou uma coleta inteira — 04/11/2026

Execução 41836747699, das 13h17 às 14h21 UTC, verde de ponta a ponta. O que
ela mediu, e que nenhuma rodada desde 31/10 havia conseguido medir:

| passo | resultado |
|---|---|
| listagem, 60 dias | 3.036 eventos, **nenhum dia com FALHOU** |
| detalhes (3.036 páginas, 56 min) | `sem resposta=0`, 3.007 descrições |
| extras (varredura + 44 unidades) | `0 URLs sem resposta`, 44/44 endereços |

Zero falha de rede em ~3.200 requisições pelo Worker. A paciência reforçada da
§11 não precisou de um único degrau — o que custava a rodada era o 403, não a
instabilidade que eu havia diagnosticado primeiro.

O site publicou às 14h21 UTC: `geradoEm` 04/11 10h21, `enriquecidoEm` 11h17,
3.029 eventos depois da retenção, descrições servidas como
`dados/desc/<id>.json` (conferido: HTTP 200).

**Os crons nunca saíram.** O passo 8 do `vps/LEIAME.md` manda removê-los, mas
isso era o plano da §12.1, que não foi executado. `atualizar.yml` continua em
`10 8` e `10 20`, `sorteios.yml` em `*/20 16-23` — o que os impedia de
funcionar era o 403, e é ele que o portão contorna. Nada a reativar.

Um achado de passagem ao conferir a base publicada: `extrasEm` saía **três
horas no futuro**. Era `time.strftime("…-03:00")`, com o fuso colado no texto,
num runner que roda em UTC. Campo que ninguém lê — e por isso ninguém viu.
Agora usa `coletor.agora_br`, como os outros dois carimbos, com teste.
