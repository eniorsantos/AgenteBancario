# -*- coding: utf-8 -*-
"""
Lógica da interface gráfica, independente do Streamlit (por isso é testável sem navegador).
A interface em si está em app_gui.py.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from openpyxl import load_workbook

import agente_extratos as A
from bancos import normaliza

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("EXTRATOS_CONFIG", APP_DIR / "config_gui.json"))
PADRAO = {
    "planilha": str(APP_DIR / "financas.xlsx"),
    "ollama_host": A.OLLAMA_HOST,
    "modelo": A.MODELO_PADRAO,
    "usar_llm": True,
}
SITUACAO_ORDEM = {"Revisar": 0, "Nova": 1, "Já na planilha": 2}


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def carregar_config() -> dict:
    cfg = dict(PADRAO)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def salvar_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def caminho_cache(cfg: dict) -> Path:
    return Path(cfg["planilha"]).with_name("categorias_cache.json")


def caminho_regras(cfg: dict) -> Path:
    return Path(cfg["planilha"]).with_name("regras_usuario.json")


def _ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def carregar_cache(cfg: dict) -> dict:
    return _ler_json(caminho_cache(cfg))


def salvar_cache(cfg: dict, dados: dict) -> None:
    caminho = caminho_cache(cfg)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar_regras_usuario(cfg: dict) -> dict:
    return _ler_json(caminho_regras(cfg))


def salvar_regras_usuario(cfg: dict, regras: dict) -> None:
    caminho = caminho_regras(cfg)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    limpas = {normaliza(k): v for k, v in regras.items() if normaliza(k) and v}
    caminho.write_text(json.dumps(limpas, ensure_ascii=False, indent=2), encoding="utf-8")


def status_ollama(cfg: dict) -> dict:
    return A.OllamaClient(cfg["modelo"], cfg["ollama_host"]).status()


# --------------------------------------------------------------------------- #
# Leitura dos extratos
# --------------------------------------------------------------------------- #
def processar(arquivos: dict[str, bytes], cfg: dict, *, banco: str | None = None, ano: int | None = None,
              cartao: bool = False, forcar_llm: bool = False, salvar_texto: bool = False, progresso=None):
    """Lê e categoriza os arquivos. Retorna (transações, avisos, erros_por_arquivo)."""
    avisos: list[str] = []
    erros: dict[str, str] = {}
    todas: list = []

    llm = None
    if cfg.get("usar_llm"):
        candidato = A.OllamaClient(cfg["modelo"], cfg["ollama_host"])
        st = candidato.status()
        if st["online"] and st["modelo_ok"]:
            llm = candidato
        elif not st["online"]:
            avisos.append("O Ollama não está rodando. Leitura e categorização usaram só regras "
                          "(PDFs sem perfil e descrições desconhecidas ficam como Outros).")
        else:
            avisos.append(f"O modelo {cfg['modelo']} não está instalado. Rode: ollama pull {cfg['modelo']}")

    args = SimpleNamespace(banco=banco, ano=ano or None, cartao=cartao, forcar_llm=forcar_llm, salvar_texto=salvar_texto)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (nome, dados) in enumerate(arquivos.items(), 1):
            if progresso:
                progresso(i, len(arquivos), nome)
            destino = Path(tmp) / Path(nome).name
            destino.write_bytes(dados)
            try:
                ts = A.processar_arquivo(destino, llm, args)
                if not ts:
                    erros[nome] = "Nenhuma transação encontrada neste arquivo."
                    continue
                A.gerar_ids(ts)
                todas.extend(ts)
            except SystemExit as e:
                erros[nome] = str(e.code) if e.code else "Não foi possível ler o arquivo."
            except Exception as e:  # noqa: BLE001 - mostramos o erro ao usuário em vez de derrubar a tela
                erros[nome] = f"{type(e).__name__}: {e}"

    if todas:
        A.categorizar(todas, llm, caminho_cache(cfg))
    return todas, avisos, erros


def ids_na_planilha(caminho) -> set:
    caminho = Path(caminho)
    if not caminho.exists():
        return set()
    wb = load_workbook(caminho, read_only=True)
    try:
        if A.ABA_TRANS not in wb.sheetnames:
            return set()
        ws = wb[A.ABA_TRANS]
        # Descobre a coluna ID pelo cabeçalho (não assume coluna fixa)
        id_col = None
        for cell in ws[1]:
            if cell.value and str(cell.value).strip().lower() == "id":
                id_col = cell.column
                break
        if id_col is None:
            return set()
        return {r[0] for r in ws.iter_rows(min_row=2, min_col=id_col, max_col=id_col, values_only=True) if r[0]}
    finally:
        wb.close()


# --------------------------------------------------------------------------- #
# Tabela de revisão <-> transações
# --------------------------------------------------------------------------- #
def para_dataframe(ts: list, ids_existentes: set) -> pd.DataFrame:
    linhas = []
    for t in ts:
        existe = t.id in ids_existentes
        situacao = "Já na planilha" if existe else ("Revisar" if t.status == "REVISAR" else "Nova")
        linhas.append({
            "Incluir": situacao == "Nova",               # linhas a revisar começam desmarcadas
            "Data": pd.Timestamp(t.data),
            "Descrição": t.descricao,
            "Valor": float(t.valor),
            "Categoria": t.categoria if t.categoria in A.CATEGORIAS else "Outros",
            "Situação": situacao,
            "Obs.": t.nota,
            "Banco": t.banco,
            "Origem": t.origem,
        })
    df = pd.DataFrame(linhas, index=range(len(ts)))
    ordem = df["Situação"].map(SITUACAO_ORDEM)
    return df.assign(_o=ordem).sort_values(["_o", "Data"], kind="stable").drop(columns="_o")


def dataframe_para_transacoes(df: pd.DataFrame, originais: list):
    """Aplica as edições da tabela. Retorna (transações marcadas, linhas inválidas ignoradas)."""
    resultado, invalidas = [], 0
    for i, row in df.iterrows():
        if not bool(row["Incluir"]):
            continue
        if pd.isna(row["Valor"]) or pd.isna(row["Data"]) or not str(row["Descrição"]).strip():
            invalidas += 1
            continue
        t = copy.copy(originais[i])               # mantém o ID original -> deduplicação continua valendo
        t.data = pd.Timestamp(row["Data"]).date()
        t.descricao = str(row["Descrição"]).strip()
        t.valor = round(float(row["Valor"]), 2)
        t.categoria = str(row["Categoria"])
        if t.status == "REVISAR":
            t.status = "OK"
            t.nota = f"{t.nota}; conferido na interface" if t.nota else "conferido na interface"
        resultado.append(t)
    return resultado, invalidas


def gravar(df: pd.DataFrame, originais: list, cfg: dict, lembrar_categorias: bool = True) -> dict:
    ts, invalidas = dataframe_para_transacoes(df, originais)
    if not ts:
        raise ValueError("Nenhuma linha válida está marcada para gravar.")
    if lembrar_categorias:                        # correções manuais passam a valer nas próximas importações
        cache = carregar_cache(cfg)
        for i, row in df.iterrows():
            if bool(row["Incluir"]) and row["Categoria"] != originais[i].categoria:
                cache[normaliza(row["Descrição"])] = row["Categoria"]
        salvar_cache(cfg, cache)
    planilha = Path(cfg["planilha"])
    planilha.parent.mkdir(parents=True, exist_ok=True)
    try:
        novas, repetidas = A.gravar_planilha(ts, planilha)
    except PermissionError as e:
        raise PermissionError("Não consegui salvar. Feche a planilha no Excel e tente de novo.") from e
    return {"novas": novas, "repetidas": repetidas, "invalidas": invalidas, "planilha": str(planilha)}


# --------------------------------------------------------------------------- #
# Painel
# --------------------------------------------------------------------------- #
def carregar_planilha(caminho) -> pd.DataFrame | None:
    caminho = Path(caminho)
    if not caminho.exists():
        return None
    try:
        df = pd.read_excel(caminho, sheet_name=A.ABA_TRANS, engine="openpyxl")
    except (ValueError, KeyError):
        return None
    df = df.rename(columns={"Valor (R$)": "Valor"})
    for c in ("Categoria", "Banco", "Status", "Origem", "Obs."):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce")
    df = df.dropna(subset=["Data", "Valor"]).copy()
    df["Mês"] = df["Data"].dt.to_period("M").astype(str)           # as colunas com fórmula não têm valor salvo
    df["Tipo"] = df["Valor"].map(lambda v: "Saída" if v < 0 else "Entrada")
    return df.sort_values("Data").reset_index(drop=True)


def filtrar(df: pd.DataFrame, meses=None, bancos=None, categorias=None, fora_dos_totais=None) -> pd.DataFrame:
    if meses:
        df = df[df["Mês"].isin(meses)]
    if bancos:
        df = df[df["Banco"].isin(bancos)]
    if categorias:
        df = df[df["Categoria"].isin(categorias)]
    if fora_dos_totais:
        df = df[~df["Categoria"].isin(fora_dos_totais)]
    return df


def kpis(df: pd.DataFrame) -> dict:
    entradas = float(df.loc[df["Valor"] > 0, "Valor"].sum())
    saidas = float(-df.loc[df["Valor"] < 0, "Valor"].sum())
    return {"entradas": entradas, "saidas": saidas, "saldo": entradas - saidas}


def por_categoria(df: pd.DataFrame) -> pd.DataFrame:
    s = df[df["Valor"] < 0].groupby("Categoria")["Valor"].sum().mul(-1).sort_values(ascending=False)
    return s.reset_index().rename(columns={"Valor": "Total"})


def por_mes(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(Entradas=df["Valor"].clip(lower=0), Saídas=(-df["Valor"]).clip(lower=0)) \
          .groupby("Mês")[["Entradas", "Saídas"]].sum().reset_index()
    return g.sort_values("Mês")


def brl(v: float) -> str:
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if v < 0 else ''}R$ {texto}"
