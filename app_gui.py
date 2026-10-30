# -*- coding: utf-8 -*-
"""
Interface gráfica do agente de extratos (Streamlit, roda só neste computador).

    streamlit run app_gui.py        (ou: iniciar.bat / ./iniciar.sh)
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import agente_extratos as A
import gui_core as core
from bancos import NOMES

st.set_page_config(page_title="Extratos", page_icon="📒", layout="wide")

TINTA, ENTRADA, SAIDA = "#1F3864", "#1B7F6B", "#B4412E"
PAGINAS = ["Importar", "Painel", "Regras", "Configurações"]

st.markdown(f"""
<style>
  .block-container {{ padding-top: 2.2rem; max-width: 1280px; }}
  h1 {{ font-weight: 650; letter-spacing: -0.01em; }}
  [data-testid="stSidebar"] h1 {{ font-size: 1.35rem; margin-bottom: 0; }}
  .kpi {{ border-left: 4px solid var(--c); padding: .15rem 0 .15rem .9rem; }}
  .kpi .l {{ display:block; font-size:.85rem; color:#5B6676; }}
  .kpi .v {{ display:block; font-size:1.75rem; font-weight:650; font-variant-numeric: tabular-nums;
             letter-spacing:-0.01em; }}
  .linha-resumo {{ font-size:1.02rem; margin:.2rem 0 1rem; }}
  .linha-resumo b {{ font-variant-numeric: tabular-nums; }}
  .ok-box {{ border:1px solid #CFE6DF; background:#F2FAF7; border-radius:6px; padding:1rem 1.2rem; }}
  [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Estado e utilidades
# --------------------------------------------------------------------------- #
if "cfg" not in st.session_state:
    st.session_state.cfg = core.carregar_config()
    st.session_state.ed_ver = 0
cfg = st.session_state.cfg


def ir_para(pagina: str):
    st.session_state["pagina"] = pagina


@st.cache_data(ttl=30, show_spinner=False)
def _status_ollama(host: str, modelo: str) -> dict:
    return core.status_ollama({"ollama_host": host, "modelo": modelo})


@st.cache_data(show_spinner=False)
def _planilha(caminho: str, mtime: float):
    return core.carregar_planilha(caminho)


def kpi(rotulo: str, valor: float, cor: str):
    st.markdown(f'<div class="kpi" style="--c:{cor}"><span class="l">{rotulo}</span>'
                f'<span class="v">{core.brl(valor)}</span></div>', unsafe_allow_html=True)


def estado_vazio(texto: str, botao: str, destino: str):
    st.info(texto)
    st.button(botao, on_click=ir_para, args=(destino,))


# --------------------------------------------------------------------------- #
# Barra lateral
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.title("Extratos")
    st.caption("Leia extratos bancários e organize tudo em uma planilha. Nada sai deste computador.")
    st.radio("Ir para", PAGINAS, key="pagina", label_visibility="collapsed")
    st.divider()
    stt = _status_ollama(cfg["ollama_host"], cfg["modelo"])
    if not cfg["usar_llm"]:
        st.caption("IA local desligada. Só regras e perfis de banco.")
    elif stt["online"] and stt["modelo_ok"]:
        st.caption(f"🟢 IA local pronta · {cfg['modelo']}")
    elif stt["online"]:
        st.caption(f"🟡 Falta instalar o modelo {cfg['modelo']}")
    else:
        st.caption("🔴 Ollama desligado. PDFs de banco conhecido ainda funcionam.")


# --------------------------------------------------------------------------- #
# Página: Importar
# --------------------------------------------------------------------------- #
def pagina_importar():
    st.title("Importar extratos")

    gravado = st.session_state.get("gravado")
    if gravado:
        with st.container():
            st.markdown('<div class="ok-box">', unsafe_allow_html=True)
            st.markdown(f"**{gravado['novas']} transações gravadas** em `{gravado['planilha']}`"
                        + (f" · {gravado['repetidas']} já estavam lá" if gravado["repetidas"] else "")
                        + (f" · {gravado['invalidas']} linhas incompletas ignoradas" if gravado["invalidas"] else ""))
            c1, c2, c3 = st.columns([1, 1, 1])
            c1.button("Ver no painel", type="primary", on_click=ir_para, args=("Painel",))
            caminho = Path(gravado["planilha"])
            if caminho.exists():
                c2.download_button("Baixar planilha", caminho.read_bytes(), file_name=caminho.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            if c3.button("Importar outro extrato"):
                st.session_state.pop("gravado", None)
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        return

    st.caption("Arraste extratos em PDF, CSV, OFX ou Excel. Você confere tudo antes de gravar.")
    arquivos = st.file_uploader("Extratos", type=["pdf", "csv", "ofx", "qfx", "xlsx", "xls", "txt"],
                                accept_multiple_files=True, label_visibility="collapsed")

    with st.expander("Opções de leitura"):
        c1, c2, c3 = st.columns(3)
        nomes = ["Detectar automaticamente"] + list(NOMES.values())
        escolha = c1.selectbox("Banco", nomes)
        chave_banco = next((k for k, v in NOMES.items() if v == escolha), None)
        ano = c2.number_input("Ano das datas sem ano", min_value=0, max_value=2100, value=0, step=1,
                              help="Santander e Itaú imprimem só dia/mês. 0 = detectar pelo extrato.")
        cartao = c3.checkbox("É fatura de cartão", help="Marque se as compras vêm como valor positivo.")
        forcar = st.checkbox("Ler PDF só com a IA, ignorando o perfil do banco")
        salvar_txt = st.checkbox("Salvar texto extraído (diagnóstico)", value=False,
                                 help="Cria um .texto.txt ao lado de cada PDF para inspecionar como o texto ficou.")

    if st.button("Ler extratos", type="primary", disabled=not arquivos):
        dados = {f.name: f.getvalue() for f in arquivos}
        with st.status("Lendo extratos…", expanded=True) as status:
            ts, avisos, erros = core.processar(
                dados, cfg, banco=chave_banco, ano=int(ano) or None, cartao=cartao, forcar_llm=forcar,
                salvar_texto=salvar_txt,
                progresso=lambda i, n, nome: st.write(f"{i}/{n} · {nome}"))
            status.update(label="Leitura concluída" if ts else "Nada para mostrar",
                          state="complete" if ts else "error")
        st.session_state.ed_ver += 1
        st.session_state.res = {
            "ts": ts, "avisos": avisos, "erros": erros,
            "df": core.para_dataframe(ts, core.ids_na_planilha(cfg["planilha"])) if ts else None,
        }

    res = st.session_state.get("res")
    if not res:
        return
    for aviso in res["avisos"]:
        st.warning(aviso)
    for nome, erro in res["erros"].items():
        st.error(f"**{nome}**: {erro}")
    if res["df"] is None:
        return

    df = res["df"]
    sit = df["Situação"].value_counts()
    partes = [f"<b>{len(df)}</b> lidas", f"<b>{sit.get('Nova', 0)}</b> novas"]
    if sit.get("Revisar", 0):
        partes.append(f"<b>{sit['Revisar']}</b> para revisar")
    if sit.get("Já na planilha", 0):
        partes.append(f"<b>{sit['Já na planilha']}</b> já na planilha")
    st.markdown(f'<div class="linha-resumo">{" · ".join(partes)}</div>', unsafe_allow_html=True)
    if sit.get("Revisar", 0):
        st.warning("Linhas marcadas como **Revisar** começam desmarcadas. A coluna Obs. diz o que não bateu "
                   "(ex.: o saldo do extrato). Corrija se precisar e marque **Incluir**.")

    editado = st.data_editor(
        df, key=f"editor_{st.session_state.ed_ver}", hide_index=True, width="stretch",
        height=min(640, 42 + 35 * len(df)),
        disabled=["Situação", "Obs.", "Banco", "Origem"],
        column_config={
            "Incluir": st.column_config.CheckboxColumn(width="small"),
            "Data": st.column_config.DateColumn(format="DD/MM/YYYY", width="small"),
            "Descrição": st.column_config.TextColumn(width="large"),
            "Valor": st.column_config.NumberColumn(format="%.2f", width="small"),
            "Categoria": st.column_config.SelectboxColumn(options=A.CATEGORIAS, width="medium"),
            "Situação": st.column_config.TextColumn(width="small"),
            "Obs.": st.column_config.TextColumn(width="medium"),
        })

    n = int(editado["Incluir"].sum())
    c1, c2 = st.columns([3, 2])
    destino = c1.text_input("Gravar em", value=cfg["planilha"])
    lembrar = c2.checkbox("Lembrar as categorias que eu mudei", value=True,
                          help="Descrições iguais passam a receber a categoria que você escolheu.")
    sel_all = st.toggle("Selecionar / desmarcar todas as linhas", value=False)
    if sel_all:
        editado["Incluir"] = True
        n = int(editado["Incluir"].sum())
    if st.button(f"Gravar {n} transações na planilha", type="primary", disabled=n == 0):
        cfg["planilha"] = destino
        try:
            resultado = core.gravar(editado, res["ts"], cfg, lembrar)
        except (PermissionError, ValueError) as e:
            st.error(str(e))
        else:
            core.salvar_config(cfg)
            _planilha.clear()
            st.toast(f"{resultado['novas']} transações gravadas com sucesso!", icon="✅")
            st.session_state.gravado = resultado
            st.session_state.pop("res", None)
            st.rerun()


# --------------------------------------------------------------------------- #
# Página: Painel
# --------------------------------------------------------------------------- #
def pagina_painel():
    st.title("Painel")
    caminho = Path(cfg["planilha"])
    base = _planilha(str(caminho), caminho.stat().st_mtime) if caminho.exists() else None
    if base is None or base.empty:
        estado_vazio("Ainda não há transações na planilha. Importe um extrato para ver o painel.",
                     "Importar extrato", "Importar")
        return

    with st.container(border=True):
        f1, f2, f3, f4 = st.columns(4)
        meses = sorted(base["Mês"].unique(), reverse=True)
        sel_meses = f1.multiselect("Mês", meses, default=meses[:3] if len(meses) > 3 else meses)
        sel_bancos = f2.multiselect("Banco", sorted(b for b in base["Banco"].unique() if b))
        sel_cats = f3.multiselect("Categoria", sorted(base["Categoria"].unique()))
        fora = f4.multiselect("Fora dos totais", sorted(base["Categoria"].unique()),
                              default=[c for c in ["Pagamento de fatura"] if c in set(base["Categoria"])],
                              help="Categorias que não contam como entrada nem saída (ex.: pagamento de fatura "
                                   "quando você também importa o cartão).")
    df = core.filtrar(base, sel_meses, sel_bancos, sel_cats, fora)
    if df.empty:
        st.info("Nenhuma transação com esses filtros.")
        return

    pend = int((base["Status"] == "REVISAR").sum())
    if pend:
        st.warning(f"{pend} linhas na planilha estão com Status REVISAR. Filtre essa coluna no Excel.")

    k = core.kpis(df)
    c1, c2, c3 = st.columns(3)
    with c1:
        kpi("Entradas", k["entradas"], ENTRADA)
    with c2:
        kpi("Saídas", k["saidas"], SAIDA)
    with c3:
        kpi("Saldo do período", k["saldo"], TINTA if k["saldo"] >= 0 else SAIDA)
    st.write("")

    g1, g2 = st.columns(2)
    with g1:
        st.subheader("Para onde foi o dinheiro")
        cat = core.por_categoria(df)
        if cat.empty:
            st.caption("Sem saídas no período.")
        else:
            graf = alt.Chart(cat).mark_bar(color=SAIDA, cornerRadiusEnd=2).encode(
                x=alt.X("Total:Q", title=None, axis=alt.Axis(format=",.0f")),
                y=alt.Y("Categoria:N", sort="-x", title=None),
                tooltip=["Categoria", alt.Tooltip("Total:Q", format=",.2f")])
            st.altair_chart(graf.properties(height=max(180, 30 * len(cat))), width="stretch")
    with g2:
        st.subheader("Mês a mês")
        mes = core.por_mes(df).melt("Mês", var_name="Tipo", value_name="Valor")
        graf = alt.Chart(mes).mark_bar(cornerRadiusEnd=2).encode(
            x=alt.X("Mês:N", title=None), xOffset="Tipo:N",
            y=alt.Y("Valor:Q", title=None, axis=alt.Axis(format=",.0f")),
            color=alt.Color("Tipo:N", legend=alt.Legend(title=None, orient="top"),
                            scale=alt.Scale(domain=["Entradas", "Saídas"], range=[ENTRADA, SAIDA])),
            tooltip=["Mês", "Tipo", alt.Tooltip("Valor:Q", format=",.2f")])
        st.altair_chart(graf.properties(height=260), width="stretch")

    st.subheader("Transações")
    busca = st.text_input("Buscar na descrição", placeholder="ex.: ifood, uber, aluguel")
    tabela = df[df["Descrição"].str.contains(busca, case=False, na=False)] if busca else df
    st.dataframe(
        tabela.sort_values("Data", ascending=False)[["Data", "Descrição", "Valor", "Categoria", "Banco", "Status"]],
        hide_index=True, width="stretch", height=420,
        column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
                       "Valor": st.column_config.NumberColumn(format="%.2f")})
    st.caption(f"{len(tabela)} transações · para editar linhas antigas, abra a planilha no Excel.")
    csv_bytes = tabela.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("Exportar filtrado (CSV)", csv_bytes, file_name="extratos_filtrados.csv",
                       mime="text/csv")
    if st.button("Reprocessar todas (recategorizar com regras atuais)"):
        todas = []
        for _, row in tabela.iterrows():
            t = A.Transacao(row["Data"].date(), str(row["Descrição"]), float(row["Valor"]),
                            str(row["Origem"]), banco=str(row["Banco"]))
            t.categoria = str(row["Categoria"])
            t.status = str(row["Status"])
            t.nota = str(row["Obs."])
            todas.append(t)
        A.categorizar(todas, None, Path(caminho).with_name("categorias_cache.json"))
        A.gravar_planilha(todas, caminho)
        st.toast("Todas as transações foram recategorizadas e salvas!", icon="🔄")
        st.rerun()


# --------------------------------------------------------------------------- #
# Página: Regras
# --------------------------------------------------------------------------- #
def pagina_regras():
    st.title("Regras de categoria")
    st.caption("A ordem de decisão é: categorias que você corrigiu → suas regras → regras do programa → IA local.")

    st.subheader("Minhas regras")
    st.write("Se a descrição **contiver** a palavra-chave, a transação recebe a categoria.")
    regras = core.carregar_regras_usuario(cfg)
    df = pd.DataFrame({"Palavra-chave": list(regras), "Categoria": list(regras.values())},
                      columns=["Palavra-chave", "Categoria"])
    editado = st.data_editor(df, num_rows="dynamic", hide_index=True, width="stretch", key="ed_regras",
                             column_config={"Categoria": st.column_config.SelectboxColumn(options=A.CATEGORIAS)})
    if st.button("Salvar regras", type="primary"):
        novas = {str(r["Palavra-chave"]): r["Categoria"] for _, r in editado.iterrows()
                 if str(r["Palavra-chave"]).strip() and r["Categoria"]}
        core.salvar_regras_usuario(cfg, novas)
        st.success(f"{len(novas)} regras salvas. Valem a partir da próxima leitura.")

    st.subheader("Categorias aprendidas")
    cache = core.carregar_cache(cfg)
    if not cache:
        st.caption("Ainda vazio. Quando você muda uma categoria na tela de importação, ela aparece aqui.")
    else:
        st.write("Descrições exatas que o programa já classificou (por você ou pela IA). Apague uma linha para esquecê-la.")
        dfc = pd.DataFrame({"Descrição": list(cache), "Categoria": list(cache.values())})
        edc = st.data_editor(dfc, num_rows="dynamic", hide_index=True, width="stretch", key="ed_cache",
                             column_config={"Categoria": st.column_config.SelectboxColumn(options=A.CATEGORIAS)})
        if st.button("Salvar categorias aprendidas"):
            core.salvar_cache(cfg, {str(r["Descrição"]): r["Categoria"] for _, r in edc.iterrows()
                                    if str(r["Descrição"]).strip() and r["Categoria"]})
            st.success("Salvo.")

    with st.expander("Regras que já vêm no programa"):
        st.dataframe(pd.DataFrame({"Palavra-chave": list(A.REGRAS), "Categoria": list(A.REGRAS.values())}),
                     hide_index=True, width="stretch")


# --------------------------------------------------------------------------- #
# Página: Configurações
# --------------------------------------------------------------------------- #
def pagina_config():
    st.title("Configurações")
    st.subheader("Planilha")
    planilha = st.text_input("Arquivo da planilha (.xlsx)", value=cfg["planilha"],
                             help="Se não existir, é criado na primeira gravação.")

    st.subheader("IA local (Ollama)")
    usar = st.toggle("Usar IA local", value=cfg["usar_llm"],
                     help="Lê PDFs de bancos sem perfil e categoriza descrições desconhecidas.")
    host = st.text_input("Endereço do Ollama", value=cfg["ollama_host"])
    stt = core.status_ollama({"ollama_host": host, "modelo": cfg["modelo"]})
    opcoes = stt["modelos"] or [cfg["modelo"]]
    if cfg["modelo"] not in opcoes:
        opcoes = [cfg["modelo"]] + opcoes
    modelo = st.selectbox("Modelo", opcoes, index=opcoes.index(cfg["modelo"]))
    if not stt["online"]:
        st.error("O Ollama não respondeu. Abra o aplicativo Ollama ou rode `ollama serve` e atualize esta página.")
    elif not stt["modelos"]:
        st.warning("O Ollama está rodando, mas não tem modelos. Rode `ollama pull qwen2.5:7b-instruct`.")
    else:
        st.success(f"Ollama online com {len(stt['modelos'])} modelo(s) instalado(s).")

    if st.button("Salvar configurações", type="primary"):
        cfg.update({"planilha": planilha, "usar_llm": usar, "ollama_host": host, "modelo": modelo})
        core.salvar_config(cfg)
        _planilha.clear()
        st.success("Configurações salvas.")


{"Importar": pagina_importar, "Painel": pagina_painel, "Regras": pagina_regras,
 "Configurações": pagina_config}[st.session_state.get("pagina", "Importar")]()
