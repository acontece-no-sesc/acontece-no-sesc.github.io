// Portão de saída para o www.sescsp.org.br.
//
// Por que existe: em 31/10/2026 o CloudFront do portal passou a devolver 403
// para as faixas da Microsoft, e os três tipos de runner do GitHub saem de IP
// Microsoft (HANDOFF §12). O host portal.sescsp.org.br, da bilheteria,
// continua respondendo — só o `www` está barrado. Então o pipeline inteiro
// fica onde está e apenas as requisições ao `www` saem por aqui.
//
// Do lado do projeto muda uma constante: `BASE`.
//
// NÃO é um proxy aberto, e isso é de propósito:
//   - o destino é fixo no código, não vem da requisição;
//   - só GET passa;
//   - com TOKEN configurado, sem a chave não passa nada.
// Um GET aberto para qualquer host na internet seria convite para lavar
// tráfego de terceiros com a nossa conta.
//
// O User-Agent do cliente é repassado inteiro. O projeto se identifica como
// `agenda-sesc-prototipo/1.0 (coletor de programacao publica; uso pessoal)`
// desde o primeiro dia, e o portão não é lugar de abrir mão disso: o bloqueio
// é de faixa de IP e não olha User-Agent, então não há nada a disfarçar.

const ALVO = "https://www.sescsp.org.br";

export default {
  async fetch(req, env) {
    if (req.method !== "GET") {
      return new Response("somente GET\n", { status: 405 });
    }

    const url = new URL(req.url);

    // A chave é nossa, não do portal: confere e tira antes de repassar.
    const chave = req.headers.get("x-chave") || url.searchParams.get("k");
    if (env.TOKEN && chave !== env.TOKEN) {
      return new Response("sem chave\n", { status: 401 });
    }
    url.searchParams.delete("k");

    const destino = ALVO + url.pathname + url.search;

    let resposta;
    try {
      resposta = await fetch(destino, {
        method: "GET",
        headers: {
          "User-Agent": req.headers.get("user-agent") ||
            "agenda-sesc-prototipo/1.0 (coletor de programacao publica; uso pessoal)",
          "Accept": req.headers.get("accept") || "*/*",
          "Accept-Language": "pt-BR,pt;q=0.9",
        },
        redirect: "follow",
      });
    } catch (e) {
      // 502 e não 500: o que falhou foi o salto daqui para o portal, e quem
      // lê o log precisa saber a diferença entre "o portão quebrou" e "o
      // portal não respondeu".
      return new Response("falha ao falar com o portal: " + e + "\n", { status: 502 });
    }

    // Só os cabeçalhos que o cliente usa. Repassar o resto arrastaria
    // cabeçalhos de cache e de cookie que não valem nada fora do portal.
    const cab = new Headers();
    for (const nome of ["content-type", "last-modified", "etag"]) {
      const v = resposta.headers.get(nome);
      if (v) cab.set(nome, v);
    }
    // Serve de prova, no log do coletor, de que a resposta passou pelo portão
    // e não direto — um 403 sem este cabeçalho é o portal falando com o
    // runner, com o portão fora do caminho.
    cab.set("x-portao", "sesc");

    return new Response(resposta.body, { status: resposta.status, headers: cab });
  },
};
