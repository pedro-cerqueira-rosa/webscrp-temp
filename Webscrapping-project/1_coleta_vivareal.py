"""
Etapa 1 - Coleta de moradias à venda na ASA SUL (Brasília-DF) no VivaReal.

COMO FUNCIONA (v2): o VivaReal entrega os anúncios já renderizados no HTML. Cada card é um
link <a href=".../imovel/...-id-123/" title="..."> cujo href, title e texto trazem preço, área,
quartos, banheiros, vagas, tipo e endereço. O script abre a página com Playwright (navegador
real), lê esses links e salva o bruto em data/raw/ antes de gerar o CSV.

Boas práticas / prevenção de bloqueio (sem burlar proteções):
  - navegador real e visível (headless=False), perfil persistente (cookies/consentimento)
  - pausas aleatórias entre páginas, pausa longa a cada 5 páginas, rolagem gradual
  - sessão única e sequencial; poucas páginas por execução; consulta ao robots.txt
  - cada página bruta é salva: nunca é preciso pedir a mesma página duas vezes (retoma sozinho)
  - ao detectar CAPTCHA / 403 / 429: PARA, mantém o que coletou e avisa.
    Não resolve CAPTCHA nem usa proxy/rotação de IP (viola os termos do site).

Uso:
  pip install -r requirements.txt && playwright install chromium
  python 1_coleta_vivareal.py --debug --paginas 1     # teste rápido: salva HTML+print em data/debug/
  python 1_coleta_vivareal.py --paginas 10            # coleta de verdade
  python 1_coleta_vivareal.py --reprocessar           # refaz o CSV a partir de data/raw/
"""
import argparse, json, random, re, time
from html import unescape
from pathlib import Path
from urllib import robotparser

import pandas as pd

BASE = "https://www.vivareal.com.br/venda/distrito-federal/brasilia/bairros/asa-sul/"
# Filtros por tipo (só moradias). Padrão de URL igual ao das páginas de bairro do site.
TIPOS = ["apartamento_residencial", "casa_residencial", "cobertura_residencial",
         "kitnet_residencial", "flat_residencial"]
SEL = 'a[href*="/imovel/"]'          # não casa com /imoveis-lancamentos/ (preço "sob consulta")

RAW = Path("data/raw"); RAW.mkdir(parents=True, exist_ok=True)
DEBUG = Path("data/debug"); DEBUG.mkdir(parents=True, exist_ok=True)
OUT_CSV = Path("data/imoveis_asa_sul.csv")
PROFILE = Path("data/perfil_navegador")
BLOCK_MARKERS = ["captcha", "access denied", "unusual traffic", "just a moment",
                 "pardon our interruption", "verifique que você é humano", "não sou um robô",
                 "acesso negado"]


# ------------------------------------------------------------------ parsing
def anchors_do_html(html):
    """Extrai os cards (links /imovel/) de um HTML em texto: página ao vivo ou arquivo salvo."""
    out = []
    for m in re.finditer(r"<a\s([^>]*?)>(.*?)</a>", html, re.S | re.I):
        attrs, inner = m.groups()
        h = re.search(r'href=["\']([^"\']+)["\']', attrs)
        if not h or "/imovel/" not in h.group(1):
            continue
        href = unescape(h.group(1))
        if href.startswith("/"):
            href = "https://www.vivareal.com.br" + href
        t = re.search(r'title=["\']([^"\']*)["\']', attrs)
        texto = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        out.append(dict(href=href, title=unescape(t.group(1)) if t else "", text=texto))
    return out


def importar_html(pasta):
    """Modo manual: lê páginas salvas pelo próprio navegador (Ctrl+S) em `pasta`."""
    arqs = sorted(Path(pasta).glob("*.htm*"))
    if not arqs:
        print(f"Nenhum .html em {pasta}"); return
    for f in arqs:
        anchors = anchors_do_html(f.read_text(encoding="utf-8", errors="ignore"))
        print(f"{f.name}: {len(anchors)} anúncios")
        (RAW / f"manual_{f.stem[:40]}_p001.json").write_text(
            json.dumps(anchors, ensure_ascii=False), encoding="utf-8")
    reprocessar()


def _first(pats, src):
    for p in pats:
        m = re.search(p, src or "", re.I)
        if m:
            return m.group(1)
    return None


def parse_anchor(a):
    """a = {'href':..., 'title':..., 'text':...} -> dict de campos (ou None)."""
    href = a.get("href") or ""
    title = (a.get("title") or "").strip()
    text = re.sub(r"\s+", " ", a.get("text") or "")
    m = re.search(r"id-(\d+)", href)
    if not m:
        return None
    slug = href.split("/imovel/")[-1].split("/")[0]
    blob = f"{title} {text}"

    preco = _first([r"RS(\d+)"], slug) or _first([r"R\$\s*([\d\.]+)"], text)
    area = (_first([r"Tamanho do im[óo]vel\s*(\d[\d\.]*)\s*m"], text)
            or _first([r"-(\d+)m2-"], slug) or _first([r"(\d[\d\.]*)\s*m²"], title))
    quartos = (_first([r"Quantidade de quartos\s*(\d+)"], text) or _first([r"-(\d+)-quartos"], slug)
               or _first([r"(\d+)\s*(?:quartos?|dormit[óo]rios?)"], title))
    banheiros = (_first([r"Quantidade de banheiros\s*(\d+)"], text)
                 or _first([r"(\d+)\s*banheiros?"], title))
    vagas = (_first([r"Quantidade de vagas de garagem\s*(\d+)"], text)
             or _first([r"(\d+)\s*vagas?"], title))

    mt = re.match(r"(apartamento|cobertura|casa-de-condominio|casa|sobrado|kitnet|flat|studio|loft|"
                  r"sala|conjunto|ponto-comercial|loja|lote|terreno|galpao|garagem|predio|"
                  r"imovel-comercial|fazenda|sitio|chacara)", slug)
    if mt:
        tipo = mt.group(1).replace("-", " ").capitalize()
    else:
        tipo = _first([r"^(.+?)\s+(?:com \d+ quartos?\s+)?(?:à venda|para comprar)"], text) or slug.split("-")[0]
        tipo = re.sub(r"\+\d+ fotos|Super Destaque|Destaque", "", tipo).strip().capitalize()

    rua = bairro = ""
    i = title.rfind(" em ")
    if i >= 0:
        partes = [p.strip() for p in title[i + 4:].split(",")]
        if len(partes) >= 3:
            rua, bairro = ", ".join(partes[:-2]), partes[-2]
        elif len(partes) == 2:
            bairro = partes[0]
    if not bairro:
        mb = re.search(r"-(asa-sul|asa-norte)-bairros", slug)
        bairro = mb.group(1).replace("-", " ").title() if mb else ""

    return dict(id=m.group(1), preco=preco, negocio="VENDA" if "-venda-" in slug else "ALUGUEL",
                area_m2=area, quartos=quartos, banheiros=banheiros, vagas=vagas,
                bairro=bairro, rua=rua, tipo=tipo, titulo=title, descricao=text[:300], url=href)


def reprocessar():
    linhas = []
    for f in sorted(RAW.glob("*_p[0-9][0-9][0-9].json")):
        for a in json.loads(f.read_text(encoding="utf-8")):
            r = parse_anchor(a)
            if r:
                linhas.append(r)
    df = pd.DataFrame(linhas)
    if df.empty:
        print("\n!! Nenhum anúncio em data/raw/. O CSV NÃO foi sobrescrito.")
        print("   Rode: python 1_coleta_vivareal.py --debug --paginas 1  e veja data/debug/")
        return
    df = df.drop_duplicates("id")
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    asa = df["bairro"].str.contains("asa sul", case=False, na=False).sum()
    print(f"\n{len(df)} anúncios únicos salvos em {OUT_CSV} ({asa} com bairro = Asa Sul)")
    print("  negócio:", df["negocio"].value_counts().to_dict())
    print("  tipos  :", df["tipo"].value_counts().head(8).to_dict())
    res = df[(df["negocio"] == "VENDA") & df["tipo"].str.contains(
        "apartamento|casa|cobertura|kitnet|flat|sobrado|studio|loft", case=False, na=False)]
    print(f"  => moradias À VENDA utilizáveis: {len(res)}")
    if len(res) < 100:
        print("  !! Poucas moradias à venda. Confira se a busca é COMPRAR + Apartamento/Casa (a URL deve")
        print("     conter /venda/ e apartamento_residencial). Se coletou aluguel ou salas, apague data/raw/")
        print("     e data/imoveis_asa_sul.csv e refaça a coleta.")


# ------------------------------------------------------------------ coleta

RES_RE = r"apartamento|casa|cobertura|kitnet|flat|sobrado|studio|loft"


def _utilizavel(r):
    alvo = " ".join(str(r.get(k) or "") for k in ("bairro", "rua", "titulo", "url")).replace("-", " ")
    return (r["negocio"] == "VENDA" and re.search(RES_RE, str(r["tipo"]), re.I)
            and re.search("asa sul", alvo, re.I))


def contar_utilizaveis():
    """Quantas moradias À VENDA da Asa Sul únicas já estão em data/raw/."""
    ids = set()
    for f in RAW.glob("*_p[0-9][0-9][0-9].json"):
        for a in json.loads(f.read_text(encoding="utf-8")):
            r = parse_anchor(a)
            if r and _utilizavel(r):
                ids.add(r["id"])
    return len(ids)


def _ids_raw():
    ids = set()
    for f in RAW.glob("*_p[0-9][0-9][0-9].json"):
        for a in json.loads(f.read_text(encoding="utf-8")):
            m = re.search(r"id-(\d+)", a.get("href", ""))
            if m:
                ids.add(m.group(1))
    return ids


def assistido(url_inicial, meta):
    """VOCÊ navega (como uma pessoa); o script só lê a página aberta quando você manda."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = dict(headless=False, locale="pt-BR", timezone_id="America/Sao_Paulo",
                  viewport={"width": 1366, "height": 850})
        try:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), **kw)
        page = ctx.new_page()
        page.goto(url_inicial or BASE + "apartamento_residencial/", wait_until="domcontentloaded")
        print("\nMODO ASSISTIDO: navegue na janela do navegador (role a página, clique em 'próxima').")
        while True:
            print(f"\nMoradias à venda na Asa Sul coletadas: {contar_utilizaveis()} / meta {meta}")
            cmd = input("ENTER = capturar esta página | s + ENTER = sair: ").strip().lower()
            if cmd == "s":
                break
            try:
                anchors = page.eval_on_selector_all(
                    SEL, "els => els.map(e => ({href: e.href, title: e.getAttribute('title') || '', "
                         "text: e.innerText || ''}))")
            except Exception:
                anchors = []
            if not anchors:
                anchors = anchors_do_html(page.content())
            vistos = _ids_raw()
            novos = [a for a in anchors if (m := re.search(r"id-(\d+)", a["href"])) and m.group(1) not in vistos]
            (RAW / f"assistido_{int(time.time())}_p001.json").write_text(
                json.dumps(novos, ensure_ascii=False), encoding="utf-8")
            print(f"  {len(anchors)} anúncios na página, {len(novos)} novos")
            if contar_utilizaveis() >= meta:
                print(f"  Meta de {meta} atingida! Pode sair com 's'.")
        ctx.close()
    reprocessar()

def checar_robots():
    try:
        rp = robotparser.RobotFileParser("https://www.vivareal.com.br/robots.txt"); rp.read()
        print(f"[robots.txt] coleta de {BASE} permitida? {rp.can_fetch('*', BASE)}")
    except Exception as e:
        print(f"[robots.txt] não foi possível consultar: {e}")


def bloqueado(page, status=None):
    if status in (403, 429):
        return True
    try:
        corpo = (page.inner_text("body", timeout=3000) or "").lower()[:4000]
        titulo = (page.title() or "").lower()
    except Exception:
        return False
    return any(m in corpo or m in titulo for m in BLOCK_MARKERS)


def coletar(max_pag, pmin, pmax, urls, debug, espera_captcha=5, meta=100, max_req=8):
    from playwright.sync_api import sync_playwright
    checar_robots()
    parar = False
    with sync_playwright() as p:
        kw = dict(headless=False, locale="pt-BR", timezone_id="America/Sao_Paulo",
                  viewport={"width": 1366, "height": 850})
        try:                                   # Chrome instalado no PC parece mais "normal"
            ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), **kw)
        page = ctx.new_page()
        n_req = 0
        ultima = "https://www.vivareal.com.br/"
        try:                                   # "aquecimento": entra pela home, como uma pessoa
            page.goto(ultima, wait_until="domcontentloaded", timeout=60000)
            time.sleep(random.uniform(5, 9))
        except Exception:
            pass
        for rotulo, base in urls:
            ids_tipo = set()
            for n in range(1, max_pag + 1):
                if contar_utilizaveis() >= meta:
                    print(f"\nMeta de {meta} moradias atingida. Parando para não sobrecarregar o site.")
                    parar = True; break
                arq = RAW / f"{rotulo}_p{n:03d}.json"
                if arq.exists():
                    antigos = json.loads(arq.read_text(encoding="utf-8"))
                    ids_tipo |= {re.search(r"id-(\d+)", a["href"]).group(1) for a in antigos
                                 if re.search(r"id-(\d+)", a["href"])}
                    if not antigos:
                        break
                    continue
                sep = "&" if "?" in base else "?"
                url = base if n == 1 else f"{base}{sep}pagina={n}"
                print(f"[{rotulo}] página {n}: {url}")
                if n_req >= max_req:
                    print(f"\nLimite de {max_req} requisições nesta sessão atingido. Faça uma pausa de 30-60 min")
                    print("e rode o mesmo comando de novo: ele retoma de onde parou.")
                    parar = True; break
                status, anchors = None, []
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000,
                                     referer=ultima)
                    status = resp.status if resp else None
                    try:
                        page.wait_for_selector(SEL, timeout=20000)
                    except Exception:
                        pass
                    for _ in range(random.randint(4, 7)):          # rolagem gradual
                        page.mouse.wheel(0, random.randint(500, 900))
                        time.sleep(random.uniform(0.8, 2.0))
                    anchors = page.eval_on_selector_all(
                        SEL, "els => els.map(e => ({href: e.href, title: e.getAttribute('title') || '', "
                             "text: e.innerText || ''}))")
                    n_dom = len(anchors)
                    if not anchors:                                  # fallback: regex no HTML
                        anchors = anchors_do_html(page.content())
                except Exception as e:
                    print(f"  erro de navegação: {e}"); n_dom = 0
                n_req += 1; ultima = url
                if n_req == 1 or debug:
                    try:
                        titulo = page.title()
                        trecho = re.sub(r"\s+", " ", page.inner_text("body", timeout=3000))[:200]
                    except Exception:
                        titulo = trecho = "?"
                    print(f"  [diagnóstico] status={status} | url final={page.url}\n"
                          f"  título da página: {titulo!r}\n"
                          f"  anúncios via DOM={n_dom} | total após fallback={len(anchors)}\n"
                          f"  início do texto: {trecho!r}")
                    (DEBUG / "pagina1.html").write_text(page.content(), encoding="utf-8")
                    page.screenshot(path=str(DEBUG / "pagina1.png"))
                    print(f"  salvos: {DEBUG}/pagina1.html e pagina1.png")

                if not anchors and bloqueado(page, status):
                    print("\n!! O site pediu verificação (CAPTCHA/bloqueio).")
                    print(f"   Resolva VOCÊ MESMO na janela do navegador. Aguardando até {espera_captcha} min...")
                    t0 = time.time()
                    while time.time() - t0 < espera_captcha * 60 and not anchors:
                        time.sleep(3)
                        try:
                            anchors = page.eval_on_selector_all(
                                SEL, "els => els.map(e => ({href: e.href, title: e.getAttribute('title') || '', "
                                     "text: e.innerText || ''}))")
                        except Exception:
                            pass
                    if not anchors:
                        print("   Verificação não concluída a tempo. Parando; o progresso está em data/raw/.")
                        print("   Retome depois (ele continua de onde parou) ou use o modo manual --html.")
                        parar = True; break
                    print("   Verificação concluída, seguindo a coleta com calma.")
                    time.sleep(random.uniform(10, 20))
                novos = []
                for a in anchors:
                    m = re.search(r"id-(\d+)", a["href"])
                    if m and m.group(1) not in ids_tipo:
                        ids_tipo.add(m.group(1)); novos.append(a)
                arq.write_text(json.dumps(novos, ensure_ascii=False), encoding="utf-8")
                print(f"  {len(novos)} anúncios novos | moradias utilizáveis: {contar_utilizaveis()}/{meta}")
                if not novos:                                   # fim dos resultados desta URL
                    break
                time.sleep(random.uniform(pmin, pmax))
                if n_req % 3 == 0:
                    longa = random.uniform(60, 120)
                    print(f"  pausa longa de {longa:.0f}s"); time.sleep(longa)
            if parar:
                break
            time.sleep(random.uniform(pmin, pmax))
        ctx.close()
    reprocessar()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paginas", type=int, default=10, help="máx. de páginas POR URL")
    ap.add_argument("--pausa-min", type=float, default=15)
    ap.add_argument("--pausa-max", type=float, default=35)
    ap.add_argument("--url", action="append",
                    help="URL copiada do seu navegador (pode repetir). Sem isso, usa a Asa Sul por tipo.")
    ap.add_argument("--espera-captcha", type=float, default=5,
                    help="minutos que o script espera VOCÊ resolver um CAPTCHA na janela")
    ap.add_argument("--meta", type=int, default=100, help="para quando houver N moradias à venda")
    ap.add_argument("--max-requests", type=int, default=8, help="máx. de páginas por sessão")
    ap.add_argument("--assistido", action="store_true",
                    help="você navega no navegador; o script só lê a página quando você aperta ENTER")
    ap.add_argument("--debug", action="store_true", help="diagnóstico em todas as páginas")
    ap.add_argument("--html", metavar="PASTA", help="MODO MANUAL: lê páginas .html salvas por você")
    ap.add_argument("--reprocessar", action="store_true")
    a = ap.parse_args()
    if a.assistido:
        assistido((a.url or [None])[0], a.meta)
    elif a.html:
        importar_html(a.html)
    elif a.reprocessar:
        reprocessar()
    else:
        for u in (a.url or []):
            if "/venda/" not in u:
                print(f"AVISO: a URL {u} não contém /venda/ (provavelmente é aluguel). Confira.")
        urls = ([(f"url{i+1}", u) for i, u in enumerate(a.url)] if a.url
                else [(t, f"{BASE}{t}/") for t in TIPOS])
        coletar(a.paginas, a.pausa_min, a.pausa_max, urls, a.debug, a.espera_captcha, a.meta, a.max_requests)
