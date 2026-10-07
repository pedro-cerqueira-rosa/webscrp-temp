# Previsão de preços de imóveis na Asa Sul (Brasília-DF)

## Passo a passo
1. `pip install -r requirements.txt && playwright install chromium`
2. **Teste rápido (1 página):** `python 1_coleta_vivareal.py --paginas 1` (veja "CSV vazio?" abaixo se der 0 anúncios).
   Abre o Chromium, carrega a 1ª página de apartamentos da Asa Sul e salva `data/debug/pagina1.html` e `.png`. O terminal mostra quantos anúncios foram lidos. Se for > 0, está tudo certo.
3. **Coleta:** `python 1_coleta_vivareal.py --paginas 10` (por tipo de imóvel: apartamento, casa, cobertura, kitnet, flat; retoma de onde parou). Meta: 100+ anúncios, ideal 300+.
4. `python 2_analise_modelo.py` -> gera `figuras/`, `resultados/` e `resultados/resumo.txt`.
5. Monte o relatório (2-4 páginas) com o roteiro abaixo.

`gerar_dados_demo.py` cria dados FICTÍCIOS só para testar o pipeline (`--input data/imoveis_demo.csv`). Não use no relatório.

## Como chegar a 100+ registros sem ser bloqueado
Cada página traz ~30 anúncios, então 100 moradias = ~4 a 6 páginas. O segredo é pedir POUCAS páginas, devagar, em sessões curtas.
1. **Automático e devagar (padrão):** `python 1_coleta_vivareal.py --meta 120`
   - para sozinho ao atingir a meta e após 8 requisições por sessão (`--max-requests`);
   - pausas de 15-35 s entre páginas e 60-120 s a cada 3 páginas; entra pela home e usa referer;
   - se for bloqueado ou atingir o limite: espere 30-60 min e rode o MESMO comando (retoma de onde parou).
2. **Assistido (recomendado se bloquear):** `python 1_coleta_vivareal.py --assistido --meta 120`
   Você navega na janela (rola, clica em "próxima página", como uma pessoa); a cada página aperte ENTER no terminal para o script capturar. Nenhuma navegação automática = nada de padrão de robô.
3. **Manual por arquivos:** salve as páginas (Ctrl+S > "Página da Web, completa") em `data/html/` e rode `--html data/html`.
Misture os modos à vontade: tudo vai para `data/raw/`, sem duplicar anúncios.
Dica: para somar volume sem muitas páginas, repita em dias/horários diferentes e inclua casas e coberturas além de apartamentos.

## CSV vazio? (diagnóstico)
1. Rode `python 1_coleta_vivareal.py --paginas 1`. O script imprime um **diagnóstico** (status HTTP, URL final, título da página, nº de anúncios lidos) e salva `data/debug/pagina1.png` e `pagina1.html`. Abra o `.png` para ver o que o navegador realmente carregou.
2. Se a URL final/ título indicar página não encontrada, copie no seu navegador a URL certa (Comprar > Brasília > Asa Sul > Apartamentos) e use: `python 1_coleta_vivareal.py --url "COLE_A_URL" --paginas 10`
3. Se aparecer CAPTCHA, "acesso negado" ou 0 anúncios com a página normal, use o **modo manual** abaixo.

## Modo manual (não depende de automação, não pode ser bloqueado)
1. No SEU navegador, abra a busca da Asa Sul (apartamentos, depois casas etc.).
2. Role a página até o fim para carregar tudo e salve com Ctrl+S > "Página da Web, completa" na pasta `data/html/` (um arquivo por página de resultados; use os botões 2, 3, 4... da paginação, com calma).
3. `python 1_coleta_vivareal.py --html data/html` gera `data/imoveis_asa_sul.csv`.
Registre no relatório que a coleta foi semiautomática (navegação manual + extração por script).

## Adaptação importante: "bairro" vs. "quadra"
Todos os imóveis são da Asa Sul, então `bairro` é constante e não serve como preditor nem para o boxplot. Usamos a **quadra** (SQS 102–416, extraída do endereço/título/descrição) e a **série** (100/200/300/400) como variáveis de localização. Registre no relatório o % de anúncios em que a quadra foi identificada.

## Se for bloqueado
Sinais: CAPTCHA, HTTP 403/429, página "Access Denied". O script para sozinho e salva o progresso. Então:
- espere algumas horas e rode de novo (retoma automaticamente);
- aumente `--pausa-min/--pausa-max`, reduza `--paginas` por sessão (ex.: 5–8);
- se persistir, troque de portal (Zap, ImovelWeb) ajustando a URL/parser, ou colete manualmente um CSV no mesmo formato de colunas.
Não use rotação de proxy nem resolvedores de CAPTCHA: violam os termos de uso e a ética da atividade.

## Roteiro do relatório
1. **Fonte e método:** portal, URL, data da coleta, interceptação do JSON via Playwright, pausas/limites, consulta ao robots.txt.
2. **Estatísticas descritivas:** `resultados/estatisticas_descritivas.csv`.
3. **EDA:** `figuras/01..04` + resposta à pergunta (quais variáveis mais correlacionam; relação linear? compare r nível vs log-log).
4. **Modelo:** OLS, Ridge, Lasso, OLS com log(preço); métricas em `resultados/resultados_modelos.csv`; VIF em `resultados/vif.csv`.
5. **Coeficientes:** `resultados/coeficientes.csv` ("cada m² adicional eleva o preço em R$ X"; no modelo log, % por unidade).
6. **Limitações:** amostra pequena; preço anunciado ≠ preço de fechamento; anúncios duplicados/desatualizados; quadra ausente em parte dos anúncios; só Asa Sul; variáveis omitidas (andar, idade, reforma, vista, bloco); heterocedasticidade.
7. **Ética:** robots.txt e termos de uso, carga no servidor, uso acadêmico, sem dados pessoais (não coletar nome/telefone de anunciantes), LGPD, não redistribuir a base.
