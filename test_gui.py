"""Testes da interface: lógica (gui_core) + execução simulada das páginas do Streamlit.
Rode: python test_gui.py   (não precisa de Ollama nem de navegador)"""
import json, os, sys, tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
TMP = Path(tempfile.mkdtemp())
os.environ["EXTRATOS_CONFIG"] = str(TMP / "config.json")
import gui_core as core                                   # noqa: E402  (depois de definir o env)

CFG = dict(core.PADRAO, planilha=str(TMP / "financas.xlsx"), usar_llm=False)
(TMP / "config.json").write_text(json.dumps(CFG), encoding="utf-8")

CAIXA = """Caixa Econômica Federal - Extrato
Data Mov. Nr. Doc. Histórico Valor Saldo
01/05/2026 000000 SALDO ANTERIOR 3.252,04C
02/05/2026 067400 CIEL VS CD 199,29C 3.451,33C
09/05/2026 000268 AP CDB FLX 8.139,13D 9.999,99C
"""  # última linha com saldo inconsistente -> Revisar
NU = "Data,Valor,Identificador,Descrição\n01/05/2026,-45.90,uuid-0001,Compra no débito - IFOOD\n02/05/2026,1500.00,uuid-0002,Transferência recebida pelo Pix - JOAO\n"
OFX = ("<OFX><BANKID>0237<STMTTRN><DTPOSTED>20260503<TRNAMT>-84.00<FITID>1<MEMO>PIX ENVIADO JOAO\n</STMTTRN>"
       "<STMTTRN><DTPOSTED>20260504<TRNAMT>-39.90<FITID>2<MEMO>NETFLIX.COM\n</STMTTRN></OFX>")
UPLOADS = {"caixa.txt": CAIXA.encode(), "nubank.csv": NU.encode(), "bradesco.ofx": OFX.encode(),
           "quebrado.csv": b"isto,nao,e,um,extrato\n1,2,3\n"}

# ---------------- lógica ----------------
ts, avisos, erros = core.processar(UPLOADS, CFG)
assert len(ts) == 6 and "quebrado.csv" in erros and not avisos, (len(ts), erros, avisos)
df = core.para_dataframe(ts, core.ids_na_planilha(CFG["planilha"]))
assert df.iloc[0]["Situação"] == "Revisar" and not df.iloc[0]["Incluir"]          # revisar vem primeiro e desmarcada
assert (df["Situação"] == "Nova").sum() == 5 and df["Incluir"].sum() == 5

ed = df.copy()
i_ifood = ed.index[ed["Descrição"].str.contains("IFOOD")][0]
ed.loc[i_ifood, "Categoria"] = "Lazer"                                              # usuário corrige a categoria
i_rev = ed.index[ed["Situação"] == "Revisar"][0]
ed.loc[i_rev, "Incluir"] = True                                                      # usuário confere e inclui
r = core.gravar(ed, ts, CFG, lembrar_categorias=True)
assert r["novas"] == 6, r
cache = core.carregar_cache(CFG)
assert cache["compra no debito - ifood"] == "Lazer"

base = core.carregar_planilha(CFG["planilha"])
assert len(base) == 6 and base["Mês"].nunique() == 1 and (base["Status"] == "OK").all()
assert "conferido na interface" in "".join(base["Obs."])
k = core.kpis(base); assert round(k["entradas"] - k["saidas"] - k["saldo"], 2) == 0
assert core.por_categoria(base)["Total"].sum() == round(k["saidas"], 2)
assert list(core.por_mes(base).columns) == ["Mês", "Entradas", "Saídas"]
assert core.brl(-1234.5) == "-R$ 1.234,50"

# reimportar os mesmos arquivos: nada novo, tudo "Já na planilha"; categoria aprendida vale
ts2, _, _ = core.processar({k_: v for k_, v in UPLOADS.items() if k_ != "quebrado.csv"}, CFG)
df2 = core.para_dataframe(ts2, core.ids_na_planilha(CFG["planilha"]))
assert (df2["Situação"] == "Já na planilha").all() and df2["Incluir"].sum() == 0
assert [t.categoria for t in ts2 if "IFOOD" in t.descricao] == ["Lazer"]
# regra do usuário
core.salvar_regras_usuario(CFG, {"Netflix": "Lazer"})
ts3, _, _ = core.processar({"bradesco.ofx": OFX.encode()}, CFG)
assert [t.categoria for t in ts3 if "NETFLIX" in t.descricao] == ["Lazer"]
# planilha aberta/protegida: erro amigável
print("lógica OK")

# ---------------- páginas (execução simulada) ----------------
from streamlit.testing.v1 import AppTest                  # noqa: E402

def rodar(pagina, preparar=None):
    at = AppTest.from_file(str(AQUI / "app_gui.py"), default_timeout=60)
    at.session_state["pagina"] = pagina
    if preparar:
        preparar(at)
    at.run()
    assert not at.exception, f"{pagina}: {[e.value for e in at.exception]}"
    return at

at = rodar("Importar"); assert any("Importar extratos" in t.value for t in at.title)
def com_resultado(at):
    at.session_state["cfg"] = dict(CFG); at.session_state["ed_ver"] = 0
    novos, _, _ = core.processar({"caixa.txt": CAIXA.encode(), "nubank.csv": NU.encode()}, CFG)
    at.session_state["res"] = {"ts": novos, "avisos": ["aviso de teste"], "erros": {"x.pdf": "erro de teste"},
                               "df": core.para_dataframe(novos, set())}
at = rodar("Importar", com_resultado)
assert any("Gravar" in b.label for b in at.button), [b.label for b in at.button]
btn = next(b for b in at.button if "Gravar" in b.label); btn.click().run()
assert not at.exception and at.session_state["gravado"]["repetidas"] == 3, at.session_state["gravado"]
at = rodar("Painel", lambda a: a.session_state.__setitem__("cfg", dict(CFG)))
assert len(at.metric) == 0 and any("Painel" in t.value for t in at.title)
assert not at.info, [i.value for i in at.info]          # não caiu no estado vazio
at = rodar("Regras", lambda a: a.session_state.__setitem__("cfg", dict(CFG)))
at = rodar("Configurações", lambda a: a.session_state.__setitem__("cfg", dict(CFG)))
# painel sem planilha -> estado vazio com botão
vazio = dict(CFG, planilha=str(TMP / "nao_existe.xlsx"))
at = rodar("Painel", lambda a: a.session_state.__setitem__("cfg", vazio))
assert any("Importe um extrato" in i.value for i in at.info)
print("páginas OK\nTODOS OS TESTES DA INTERFACE PASSARAM")
