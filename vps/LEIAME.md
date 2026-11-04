# A coleta numa VPS, publicando no Pages por branch

Por que isto existe: em **31/10/2026 às 14:29 UTC** o CloudFront do portal
passou a devolver **403** para as faixas do GitHub/Azure. Não é bloqueio de
datacenter — 14 nós em 12 países recebem 200, e o Sesc nem deve saber que a
regra está barrando um agregador público. É o runner do GitHub que está
barrado, e nenhuma mudança de código atravessa isso (HANDOFF §11 e §12).

Esta pasta move **só a coleta e a publicação** para uma VPS. O repositório, o
Pages, os testes e o código continuam onde estão.

---

## 1. O que a VPS precisa

Qualquer VPS pequena serve — 1 vCPU, 1 GB de RAM, 10 GB de disco. **Uma no
Brasil ganha em latência**, porque são ~2.500 requisições por rodada.

```bash
sudo apt update && sudo apt install -y python3 git curl gzip util-linux
sudo timedatectl set-timezone America/Sao_Paulo
```

Sem dependências de Python: o projeto todo é biblioteca padrão.

### Antes de qualquer coisa, teste se a VPS não está barrada também

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://www.sescsp.org.br/wp-json/wp/v1/unidades-atividades
```

**200** é o que você quer. **403** significa que a regra pegou também esse
provedor — troque de VPS antes de montar o resto.

## 2. Chave de escrita, só para este repositório

Uma *deploy key* é mais estreita que um token de conta: vale para um
repositório e nada mais.

```bash
ssh-keygen -t ed25519 -C "robo-acontece" -f ~/.ssh/acontece -N ""
cat ~/.ssh/acontece.pub
```

Cole em **Settings → Deploy keys → Add deploy key**, marcando
**Allow write access**. Depois, em `~/.ssh/config`:

```
Host github.com
  IdentityFile ~/.ssh/acontece
  IdentitiesOnly yes
```

## 3. O código

```bash
sudo mkdir -p /opt/acontece && sudo chown "$USER" /opt/acontece
git clone git@github.com:acontece-no-sesc/acontece-no-sesc.github.io.git /opt/acontece
chmod +x /opt/acontece/vps/*.sh
```

## 4. O Pages passa a servir do branch

**Settings → Pages → Source: Deploy from a branch → `gh-pages` / `(root)`.**

A primeira rodada cria o branch. `publicar.py` já grava o `.nojekyll`, sem o
qual o Jekyll do Pages reprocessaria a pasta.

## 5. O cron

```cron
CRON_TZ=America/Sao_Paulo
# PING_URL=https://...      # opcional, ver adiante

10  5    * * *   /opt/acontece/vps/rodar.sh
10  17   * * *   /opt/acontece/vps/rodar.sh
*/20 13-20 * * * /opt/acontece/vps/rodar-sorteios.sh
```

Os horários são os mesmos de sempre, agora cumpridos: o agendador do GitHub
atrasava de 2h26 a 6h02 (HANDOFF §11), e era disso que a tela deixou de
prometer cadência no commit `caded49`.

As duas rodadas grandes existem por causa dos sorteios; a leve, de 20 em 20
minutos, lê só as páginas cuja divulgação está vencendo. Elas compartilham
`dados/eventos.json`, então dividem uma trava (`flock`): a grande **espera**
até 5 minutos pela leve, e a leve **cede a vez** para a grande, dizendo no log
que cedeu.

## 6. A primeira rodada

Rode na mão e acompanhe:

```bash
/opt/acontece/vps/rodar.sh
```

Leva de 30 a 45 minutos e vai dizer *"sem base anterior: esta coleta vira
linha de base e não marca novidade"* — está certo. O trilho de novidades
precisa de duas coletas para existir (HANDOFF §4.5).

## 7. O que muda em relação ao GitHub Actions

| | Actions | VPS |
|---|---|---|
| base anterior | cache do runner, com rede de segurança baixando o publicado | arquivo no disco, sobrevive sozinho |
| base de um dia | artefato da execução, 30 dias | `dados/historico/*.json.gz`, 30 dias |
| log | aba Actions | `vps/log/*.log`, 30 dias |
| "não rodou" | execução ausente na aba | **só o ping avisa** |
| histórico no git | nenhum (artefato) | nenhum (commit órfão) |

**O ganho:** a base anterior deixa de depender de cache. No Actions, cache
evaporado significava trilho de novidades reiniciado em silêncio, e por isso
havia todo um passo de rede de segurança no workflow.

**A perda:** o modo de falhar de um cron é **não acontecer**, e isso não
gera log nenhum para alguém ler. Daí o `PING_URL`: um serviço de
interruptor-de-homem-morto (healthchecks.io e parecidos) avisa quando o ping
não vem. Sem ele, a rodada volta a poder sumir calada — que é exatamente o
defeito que este projeto combate em todo lugar.

## 8. A virada de chave

Enquanto a VPS não estiver de pé, **não mexa nos workflows**: eles falham,
mas falham sem estragar nada. Depois que a primeira rodada publicar:

1. Tire o `schedule:` de `atualizar.yml` e de `sorteios.yml`, deixando só o
   `workflow_dispatch` — senão eles seguem tomando 403 duas vezes por dia,
   enchendo a caixa de e-mail de falha que não é notícia.
2. Deixe `testes.yml` como está: ele não toca no portal.
3. Deixe `sonda.yml` como está. Ela é o teste de porta: um clique diz se o
   bloqueio caiu, e aí a volta para o Actions é tirar o cron da VPS.
