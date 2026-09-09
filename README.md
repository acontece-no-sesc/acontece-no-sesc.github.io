# Acontece no SESC — agregador não oficial

**No ar: https://acontece-no-sesc.github.io/**

Reúne a programação do Sesc São Paulo (capital, interior e litoral) num app de
celular instalável, com destaques, agenda, favoritos e acompanhamento de
sorteios.

**Não é um site oficial do Sesc.** Cada evento leva de volta para a página
oficial, que é sempre a fonte da verdade.

---

## Como funciona

```
coletor.py  ──►  dados/eventos.json  ──►  publicar.py  ──►  web/  ──►  GitHub Pages
detalhes.py ──►         ▲                                    │
reparse.py  ────────────┘                                    └──► o app busca o JSON
sorteios.py ────────────┘                                         a cada abertura
     └──►  dados/sorteados.json  ──────────────────────────►  e a lista de sorteio
                                                              a cada "Consultar"
```

O app é um arquivo HTML só. Ele carrega com um instantâneo embutido (abre
rápido e funciona offline) e, se encontrar um `dados/eventos.json` ao lado,
substitui pelo que houver de mais novo. É por isso que basta o robô regravar o
JSON para todo mundo receber a atualização, sem reinstalar nada.

### Fontes

| O quê | De onde |
|---|---|
| Listagem, unidades, categorias | `sescsp.org.br/wp-json/wp/v1/atividades/filter` |
| Região de cada unidade | `sescsp.org.br/wp-json/wp/v1/unidades-atividades` (campo `description`) |
| Preços, sessões exatas, datas de venda | `portal.sescsp.org.br/bilheteria/atividade.action?idAtividade=` |
| Inscrição, sorteio, códigos contemplados | HTML da página da atividade |

A API de bilheteria é indexada pelo `id_java`, que já vem na listagem. Ela
entrega as três faixas de preço, a data e hora exata de cada sessão e as
aberturas de venda on-line e presencial.

---

## Publicar (GitHub Pages)

1. Crie um repositório e suba estes arquivos.
2. Em **Settings → Pages**, escolha **Source: GitHub Actions**.
3. Rode uma vez à mão: aba **Actions → Atualizar programação → Run workflow**.

Pronto — o endereço sai como `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.
Desse ponto em diante o robô republica sozinho: a coleta completa às 5h10 e às
17h10, e uma rodada leve de 20 em 20 minutos, das 13h às 20h, que lê só as
páginas dos sorteios do dia. Essa segunda existe porque o Sesc publica os
contemplados às 14h ou 16h — com uma coleta só, de madrugada, o resultado
nunca chegava no dia.

O `--base` do `publicar.py` precisa bater com a subpasta do Pages; o workflow
já preenche isso com o nome do repositório.

Aqui o site é a página da organização (`acontece-no-sesc.github.io`), servida
na **raiz** — então `--base /`, sem subpasta.

### Compartilhar

Mande o endereço. Quem abrir pode instalar:

- **Android/Chrome** — aparece sozinho o convite "Instalar app", ou menu ⋮ →
  *Adicionar à tela inicial*.
- **iPhone/Safari** — botão Compartilhar → *Adicionar à Tela de Início*.
  Precisa ser pelo Safari; no iOS os outros navegadores não instalam PWA.

Instalado, abre em tela cheia, com ícone próprio, e continua funcionando sem
internet com os últimos dados baixados.

---

## Rodar na sua máquina

Só precisa de Python 3.9+. Nenhuma dependência externa.

```bash
python coletor.py --dias 30                                  # ~2 min
python detalhes.py --html-cats "Turismo Social" "Cursos e Oficinas"   # ~10 min
python reparse.py                                            # instantâneo
python embutir.py                                            # arquivo único
python publicar.py                                           # pasta web/
python -m http.server 8000 --directory web                   # testar
```

Do celular, na mesma Wi-Fi: `http://SEU-IP:8000`

### Os scripts

| Script | Faz |
|---|---|
| `coletor.py` | Consulta dia a dia (única forma de saber em que dias cada atividade acontece) e grava `dados/eventos.json` |
| `detalhes.py` | Segunda passada: bilheteria (JSON) e página (HTML) para preço, venda, inscrição e códigos sorteados |
| `sorteios.py` | Rodada leve: só as páginas dos sorteios do dia, e grava `dados/sorteados.json` (alguns KB) |
| `reparse.py` | Reaplica o extrator de datas sobre o texto já guardado, sem rede |
| `embutir.py` | Injeta os dados no `prototipo.html` (arquivo único) |
| `publicar.py` | Monta `web/` com manifest, service worker e ícones |

Opções úteis:

```bash
python coletor.py --de 2026-08-11 --ate 2026-09-09
python coletor.py --regioes capital litoral
python embutir.py --sem-projeto            # tira temporadas e programas recorrentes
python embutir.py --max 800                # versão mais leve
python publicar.py --base /agenda-sesc/
```

---

## Uso responsável

A coleta faz cerca de 1.200 requisições por rodada, com pausa de 0,25 s — o
volume de um visitante atento. Rodar uma vez por dia é suficiente; não
transforme isso em minuto a minuto.

O app não coleta nada de quem usa. Favoritos, unidades escolhidas e códigos de
sorteio ficam apenas no aparelho (`localStorage`), e a conferência do sorteio
compara o código com a lista pública dentro do próprio celular — nada é
enviado para servidor nenhum.
