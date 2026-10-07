"""
Etapas 2-4: limpeza, EDA e regressão linear (Asa Sul).
Uso: python 2_analise_modelo.py --input data/imoveis_asa_sul.csv
Saídas: figuras/*.png, resultados/*.csv, resultados/resumo.txt

NOTA METODOLÓGICA: dentro da Asa Sul o "bairro" é constante. A variável de
localização usada no lugar dele é a QUADRA (SQS 102..416), extraída do endereço/título/
descrição, e a SÉRIE da quadra (100/200/300/400).
"""
import argparse, re, warnings
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, seaborn as sns
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.linear_model import LinearRegression, RidgeCV, LassoCV
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
from sklearn.model_selection import train_test_split, cross_val_score, KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
warnings.filterwarnings("ignore")
sns.set_theme(style="whitegrid")
Path("figuras").mkdir(exist_ok=True); Path("resultados").mkdir(exist_ok=True)
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)

# ------------------------------------------------------------ conversões
def para_float(x):
    """'R$ 1.200.000' -> 1200000.0 ; '75 m²' -> 75.0 ; '75-90 m²' -> 75.0"""
    if pd.isna(x): return np.nan
    if isinstance(x, (int, float, np.number)): return float(x)
    m = re.search(r"\d[\d\.,]*", str(x))
    if not m: return np.nan
    s = m.group(0).rstrip(".,")
    if "," in s: s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s): s = s.replace(".", "")
    try: return float(s)
    except ValueError: return np.nan

def extrair_quadra(txt):
    m = re.search(r"(?:SQS|CLS|EQS|quadra|\bQ\.?)\s*0?(\d{3})\b|\b(\d{3})\s*Sul\b", str(txt), re.I)
    if not m:
        return None
    n = int(next(g for g in m.groups() if g))
    return n if 102 <= n <= 416 and 2 <= n % 100 <= 16 else None

# ------------------------------------------------------------ Etapa 2
TIPOS_RES = {"apartamento": "Apartamento", "cobertura": "Cobertura", "casa": "Casa", "sobrado": "Casa",
             "kitnet": "Kitnet", "conjugado": "Kitnet", "studio": "Kitnet", "flat": "Flat",
             "loft": "Kitnet", "apartment": "Apartamento", "home": "Casa"}

def tipo_residencial(t):
    """Normaliza o texto de tipo; devolve None se não for moradia (sala, loja, lote...)."""
    t = str(t).lower()
    if re.search(r"sala|loja|ponto|lote|terreno|galp|escrit|fazenda|s[ií]tio|ch[áa]cara|comercial", t):
        return None
    for chave, nome in TIPOS_RES.items():
        if chave in t:
            return nome
    return None

def diagnostico(df, etapa):
    say(f"\n!! Nenhum registro sobrou após: {etapa}.")
    for c in ["bairro", "tipo", "negocio"]:
        if c in df:
            say(f"   valores de '{c}' (top 8):", df[c].astype(str).value_counts().head(8).to_dict())
    raise SystemExit("Interrompido: veja acima o que os filtros descartaram. Se os anúncios "
                     "não são da Asa Sul, refaça a coleta com a URL correta.")

def limpar(df):
    n0 = len(df); say(f"Registros brutos: {n0}")
    if n0 == 0:
        raise SystemExit("CSV vazio: rode a coleta (1_coleta_vivareal.py) e confira data/raw/.")
    base = df.copy()
    # 1) Asa Sul: procura em bairro, rua, título e URL (o campo bairro pode vir vazio)
    campos = [c for c in ["bairro", "rua", "titulo", "url"] if c in df]
    alvo = df[campos].fillna("").astype(str).agg(" ".join, axis=1).str.replace("-", " ", regex=False)
    df = df[alvo.str.contains("asa sul", case=False)]
    say(f"  após filtro Asa Sul: {len(df)}")
    if df.empty: diagnostico(base, "filtro Asa Sul")
    # 2) venda
    if "negocio" in df:
        df = df[df["negocio"].fillna("VENDA").astype(str).str.upper().isin(["SALE", "VENDA"])]
        say(f"  após filtro venda: {len(df)}")
        if df.empty: diagnostico(base, "filtro venda")
    # 3) só moradias
    if "tipo" in df:
        df = df.assign(tipo=df["tipo"].map(tipo_residencial)).dropna(subset=["tipo"])
        say(f"  após filtro moradias: {len(df)}")
        if df.empty: diagnostico(base, "filtro moradias")
    if "id" in df: df = df.drop_duplicates("id")
    say(f"Após filtros Asa Sul / venda / moradia / dedupe: {len(df)}")
    df = df.copy()
    for c in ["preco", "area_m2", "quartos", "banheiros", "vagas"]:
        df[c] = df[c].map(para_float) if c in df else np.nan
    cols_txt = [c for c in ["rua", "titulo", "descricao"] if c in df]
    texto = df[cols_txt].fillna("").astype(str).agg(" ".join, axis=1) if cols_txt else pd.Series("", index=df.index)
    df["quadra_num"] = pd.Series([extrair_quadra(t) for t in texto], index=df.index)
    df["quadra"] = df["quadra_num"].map(lambda q: f"SQS {int(q)}" if pd.notna(q) else "NI")
    df["serie"] = df["quadra_num"].map(lambda q: f"{int(q)//100*100}" if pd.notna(q) else "NI")
    say(f"Quadra identificada em {(df['quadra']!='NI').mean():.0%} dos anúncios")
    df["vagas"] = df["vagas"].fillna(0)                     # sem vaga informada = 0
    obrig = ["preco", "area_m2", "quartos", "banheiros"]
    say(f"Ausentes (obrigatórios): {df[obrig].isna().sum().to_dict()}")
    df = df.dropna(subset=obrig)
    df = df[(df.preco > 0) & (df.area_m2 > 10)]
    if len(df) < 5: diagnostico(base, "remoção de ausentes")
    q1, q3 = df.preco.quantile([.25, .75]); lim = q3 + 3 * (q3 - q1)
    say(f"Outliers de preço removidos (> Q3 + 3*IQR = R$ {lim:,.0f}): {(df.preco > lim).sum()}")
    df = df[df.preco <= lim].copy()
    df["preco_m2"] = df.preco / df.area_m2
    if "tipo" in df:   # agrupa tipos raros para não explodir o One-Hot
        df["tipo"] = df["tipo"].where(df["tipo"].map(df["tipo"].value_counts()) >= 8, "Outro")
    if len(df) >= 50:  # corta erros de digitação extremos (só com amostra razoável)
        p1, p99 = df.preco_m2.quantile([.01, .99])
        df = df[df.preco_m2.between(p1, p99)]
    say(f"Amostra final: {len(df)}")
    return df

# ------------------------------------------------------------ Etapa 3
def eda(df):
    num = ["preco", "preco_m2", "area_m2", "quartos", "banheiros", "vagas"]
    desc = df[num].describe().T; desc.to_csv("resultados/estatisticas_descritivas.csv")
    say("\nEstatísticas descritivas:\n", desc.round(1).to_string())
    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    sns.histplot(df.preco, bins=30, kde=True, ax=ax[0]).set(title="Distribuição do preço (R$)")
    sns.histplot(df.preco_m2, bins=30, kde=True, ax=ax[1]).set(title="Distribuição do preço por m²")
    plt.tight_layout(); plt.savefig("figuras/01_histogramas.png", dpi=130); plt.close()
    corr = df[num].corr()
    plt.figure(figsize=(7, 5.5)); sns.heatmap(corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0)
    plt.title("Matriz de correlação"); plt.tight_layout(); plt.savefig("figuras/02_correlacao.png", dpi=130); plt.close()
    plt.figure(figsize=(7, 5)); sns.regplot(data=df, x="area_m2", y="preco",
        scatter_kws=dict(alpha=.5), line_kws=dict(color="red"))
    plt.title("Área × Preço"); plt.tight_layout(); plt.savefig("figuras/03_scatter_area_preco.png", dpi=130); plt.close()
    top = df[df.quadra != "NI"].quadra.value_counts().head(10).index
    if len(top):
        plt.figure(figsize=(11, 5)); sns.boxplot(data=df[df.quadra.isin(top)], x="quadra", y="preco", order=top)
        plt.xticks(rotation=45); plt.title("Preço por quadra (10 mais frequentes)")
        plt.tight_layout(); plt.savefig("figuras/04_boxplot_quadras.png", dpi=130); plt.close()
    say("\nCorrelação com preço:\n", corr["preco"].drop("preco").sort_values(ascending=False).round(2).to_string())
    r_lin = np.corrcoef(df.area_m2, df.preco)[0, 1]; r_log = np.corrcoef(np.log(df.area_m2), np.log(df.preco))[0, 1]
    say(f"Linearidade área×preço: r={r_lin:.2f} (nível) vs r={r_log:.2f} (log-log)")

# ------------------------------------------------------------ Etapa 4
def modelar(df):
    NUM = ["area_m2", "quartos", "banheiros", "vagas"]
    CAT = ["serie", "tipo"] if "tipo" in df else ["serie"]
    X, y = df[NUM + CAT], df["preco"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=.2, random_state=42)
    def prep(escala):
        return ColumnTransformer([("n", StandardScaler() if escala else "passthrough", NUM),
                                  ("c", OneHotEncoder(drop="first", handle_unknown="ignore"), CAT)])
    modelos = {
        "OLS": Pipeline([("p", prep(False)), ("m", LinearRegression())]),
        "Ridge": Pipeline([("p", prep(True)), ("m", RidgeCV(alphas=np.logspace(-3, 3, 30)))]),
        "Lasso": Pipeline([("p", prep(True)), ("m", LassoCV(cv=5, random_state=42, max_iter=20000))]),
        "OLS log(preco)": TransformedTargetRegressor(Pipeline([("p", prep(False)), ("m", LinearRegression())]),
                                                     func=np.log, inverse_func=np.exp),
    }
    linhas = []
    for nome, mdl in modelos.items():
        mdl.fit(Xtr, ytr); pr = mdl.predict(Xte)
        cv = cross_val_score(mdl, X, y, cv=KFold(5, shuffle=True, random_state=42), scoring="r2")
        linhas.append(dict(modelo=nome, R2_teste=r2_score(yte, pr),
                           RMSE=np.sqrt(mean_squared_error(yte, pr)), MAE=mean_absolute_error(yte, pr),
                           R2_cv5_medio=cv.mean(), R2_cv5_dp=cv.std()))
    res = pd.DataFrame(linhas).round(3); res.to_csv("resultados/resultados_modelos.csv", index=False)
    say("\nComparação de modelos (RMSE/MAE em R$):\n", res.to_string(index=False))

    # coeficientes interpretáveis (sem padronizar), amostra completa
    cols = ColumnTransformer([("n", "passthrough", NUM), ("c", OneHotEncoder(drop="first"), CAT)]).fit(X)
    nomes = NUM + list(cols.named_transformers_["c"].get_feature_names_out(CAT))
    Xm = cols.transform(X)
    ols = LinearRegression().fit(Xm, y)
    olsl = LinearRegression().fit(Xm, np.log(y))
    coef = pd.DataFrame(
        {"variavel": nomes, "coef_OLS_reais": ols.coef_, "coef_log": olsl.coef_})
    coef["efeito_pct_log"] = (np.exp(coef.coef_log) - 1) * 100
    coef.round(4).to_csv("resultados/coeficientes.csv", index=False)
    say("\nCoeficientes (OLS em R$; efeito % no modelo log):\n", coef.round(3).to_string(index=False))
    say(f"\nInterpretação: cada m² adicional eleva o preço em ~R$ {ols.coef_[0]:,.0f} (OLS), "
        f"ou ~{coef.efeito_pct_log[0]:.2f}% (modelo log), ceteris paribus.")

    try:  # multicolinearidade
        import statsmodels.api as sm
        from statsmodels.stats.outliers_influence import variance_inflation_factor as vif
        Xc = sm.add_constant(df[NUM].astype(float))
        v = pd.DataFrame({"variavel": NUM, "VIF": [vif(Xc.values, i + 1) for i in range(len(NUM))]}).round(2)
        v.to_csv("resultados/vif.csv", index=False); say("\nVIF (preditores numéricos):\n", v.to_string(index=False))
    except Exception as e:
        say("VIF não calculado:", e)

    # resíduos
    pr = modelos["OLS"].predict(Xte)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].scatter(yte, pr, alpha=.6); ax[0].plot([yte.min(), yte.max()], [yte.min(), yte.max()], "r--")
    ax[0].set(title="Real × Previsto (OLS, teste)", xlabel="Real", ylabel="Previsto")
    sns.histplot(yte - pr, bins=25, kde=True, ax=ax[1]).set(title="Resíduos (R$)")
    plt.tight_layout(); plt.savefig("figuras/05_diagnostico_modelo.png", dpi=130); plt.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--input", default="data/imoveis_asa_sul.csv")
    df = limpar(pd.read_csv(ap.parse_args().input))
    if len(df) < 60: say("AVISO: amostra pequena; resultados pouco confiáveis.")
    eda(df); modelar(df)
    df.to_csv("data/imoveis_asa_sul_limpo.csv", index=False, encoding="utf-8-sig")
    Path("resultados/resumo.txt").write_text("\n".join(log), encoding="utf-8")
