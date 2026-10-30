#!/usr/bin/env python3
"""
Agente de análise de extratos bancários com LLM local (Ollama).

Bancos com perfil próprio: Bradesco, Nubank, Itaú, Santander, Caixa e Banco do Brasil
(veja bancos.py). O banco é detectado automaticamente; use --banco para forçar.

Fluxo:
  1. Lê o extrato (OFX, CSV, XLS/XLSX, PDF com texto ou TXT)
  2. Extrai as transações
       - OFX/CSV/XLSX: parser determinístico (sem LLM, sem risco de alucinação)
       - PDF/TXT: perfil do banco (regex) + conferência pelo saldo do extrato;
                  se o perfil não encontrar nada, o LLM local extrai em JSON
  3. Categoriza (regras por palavra-chave -> cache -> LLM local)
  4. Grava na planilha .xlsx (sem duplicar) e recria a aba "Resumo" com fórmulas

Uso:
  python agente_extratos.py extrato.pdf --planilha financas.xlsx
  python agente_extratos.py fatura.csv --cartao --modelo llama3.1:8b
  python agente_extratos.py extrato.ofx --banco bradesco
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

import bancos
from bancos import (NOMES, Transacao, ler_texto_arquivo, normaliza, parse_data, parse_valor)

# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
OLLAMA_HOST = "http://localhost:11434"
MODELO_PADRAO = "qwen2.5:7b-instruct"
LINHAS_POR_BLOCO = 150
TAMANHO_LOTE_CATEGORIAS = 60

CATEGORIAS = [
    "Alimentação", "Mercado", "Transporte", "Moradia", "Saúde", "Educação",
    "Lazer", "Assinaturas", "Compras", "Serviços e Tarifas", "Impostos",
    "Salário", "Rendimentos", "Transferências", "Investimentos", "Pagamento de fatura", "Outros",
]

# Regras rápidas (palavra-chave em minúsculas, sem acento -> categoria).
# A PRIMEIRA que casar vence; por isso: tarifas/investimentos/fatura -> estabelecimentos -> transferências.
REGRAS = {
    # 1) prioridade alta (evita "RESGATE MERCADO ABERTO" -> Mercado, "TAR ... SALARIO" -> Salário)
    "tarifa": "Serviços e Tarifas", "tar ": "Serviços e Tarifas", "manut": "Serviços e Tarifas",
    "anuidade": "Serviços e Tarifas", "juros": "Serviços e Tarifas",
    "iof": "Impostos", "darf": "Impostos", "ipva": "Impostos", "iptu": "Impostos",
    "resgate": "Investimentos", "aplicacao": "Investimentos", "apl ": "Investimentos",
    "cdb": "Investimentos", "tesouro": "Investimentos",
    "pagamento da fatura": "Pagamento de fatura", "pagamento de fatura": "Pagamento de fatura",
    "pgto fatura": "Pagamento de fatura", "pagto cartao": "Pagamento de fatura",
    "pagamento cartao": "Pagamento de fatura",
    # 2) estabelecimentos e serviços
    "ifood": "Alimentação", "restaurante": "Alimentação", "lanchonete": "Alimentação",
    "padaria": "Alimentação", "supermercado": "Mercado", "mercado": "Mercado",
    "atacadao": "Mercado", "assai": "Mercado", "uber": "Transporte", "99 pop": "Transporte",
    "99app": "Transporte", "posto": "Transporte", "combustivel": "Transporte",
    "estacionamento": "Transporte", "netflix": "Assinaturas", "spotify": "Assinaturas",
    "amazon prime": "Assinaturas", "youtube": "Assinaturas", "farmacia": "Saúde",
    "drogaria": "Saúde", "droga": "Saúde", "unimed": "Saúde", "hospital": "Saúde",
    "escola": "Educação", "faculdade": "Educação", "curso": "Educação",
    "aluguel": "Moradia", "condominio": "Moradia", "energia": "Moradia",
    "coelba": "Moradia", "embasa": "Moradia", "internet": "Moradia",
    "salario": "Salário", "folha pagto": "Salário", "rendimento": "Rendimentos",
    # 3) transferências genéricas por último
    "pix": "Transferências", "ted": "Transferências", "doc ": "Transferências",
    "transf": "Transferências",
}

# --------------------------------------------------------------------------- #
# Cliente Ollama
# --------------------------------------------------------------------------- #
class OllamaClient:
    def __init__(self, modelo: str, host: str = OLLAMA_HOST):
        self.modelo, self.host = modelo, host.rstrip("/")

    def status(self) -> dict:
        """Consulta o Ollama sem encerrar o programa (usado pela interface gráfica)."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=3)
            r.raise_for_status()
        except requests.RequestException:
            return {"online": False, "modelos": [], "modelo_ok": False}
        nomes = [m["name"] for m in r.json().get("models", [])]
        ok = any(n == self.modelo or n.startswith(self.modelo + ":") for n in nomes)
        return {"online": True, "modelos": nomes, "modelo_ok": ok}

    def verificar(self):
        st = self.status()
        if not st["online"]:
            sys.exit(f"Ollama não respondeu em {self.host}. Inicie com: ollama serve")
        if not st["modelo_ok"]:
            sys.exit(f"Modelo '{self.modelo}' não instalado. Rode: ollama pull {self.modelo}")

    def json_chat(self, sistema: str, usuario: str, tentativas: int = 3) -> dict:
        ultimo_erro = None
        for _ in range(tentativas):
            r = requests.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.modelo,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_ctx": 8192},
                    "messages": [
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": usuario},
                    ],
                },
                timeout=600,
            )
            r.raise_for_status()
            conteudo = r.json()["message"]["content"]
            try:
                return json.loads(conteudo)
            except json.JSONDecodeError as e:
                ultimo_erro = e
                m = re.search(r"\{.*\}", conteudo, flags=re.S)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except json.JSONDecodeError:
                        pass
        raise RuntimeError(f"LLM não devolveu JSON válido: {ultimo_erro}")


# --------------------------------------------------------------------------- #
# Extração com LLM (PDF / TXT)
# --------------------------------------------------------------------------- #
SISTEMA_EXTRACAO = """Você extrai transações de extratos bancários brasileiros.
Responda SOMENTE com JSON no formato:
{"transacoes": [{"data": "DD/MM/AAAA", "descricao": "...", "valor": "1.234,56", "tipo": "debito" ou "credito"}]}
Regras:
- Inclua apenas movimentações reais (compras, pix, transferências, tarifas, depósitos).
- IGNORE saldo anterior, saldo do dia, saldo final, totais, limites e cabeçalhos.
- "valor" deve ser copiado EXATAMENTE como aparece no texto, sem sinal e sem "R$".
- "tipo": "debito" para saídas (valores com '-', 'D', pagamentos, compras, tarifas) e "credito" para entradas.
- Se uma data vier sem ano, use o ano informado pelo usuário.
- Se não houver transações no trecho, responda {"transacoes": []}.
- Não invente nada que não esteja no texto."""


def extrair_texto_pdf(caminho: Path) -> str:
    import pdfplumber
    paginas = []
    with pdfplumber.open(caminho) as pdf:
        for p in pdf.pages:
            paginas.append(p.extract_text(x_tolerance=2) or "")
    texto = "\n".join(paginas).strip()
    if len(texto) < 50:
        sys.exit(f"'{caminho.name}' parece ser PDF escaneado (sem texto). "
                 "Rode antes: ocrmypdf entrada.pdf saida.pdf")
    return texto


def valor_aparece_no_texto(valor: float, bloco_norm: str) -> bool:
    """Anti-alucinação: o número extraído precisa existir no trecho original."""
    alvo = f"{abs(valor):.2f}".replace(".", ",")
    return alvo in bloco_norm


def extrair_com_llm(texto: str, origem: str, llm: OllamaClient, ano: int | None) -> list[Transacao]:
    linhas = [l for l in texto.splitlines() if l.strip()]
    ano_info = ano or datetime.now().year
    resultado = []
    for i in range(0, len(linhas), LINHAS_POR_BLOCO):
        bloco = "\n".join(linhas[i:i + LINHAS_POR_BLOCO])
        bloco_norm = re.sub(r"[.\s]", "", bloco)  # remove separador de milhar e espaços
        print(f"  [LLM] extraindo linhas {i + 1}-{i + LINHAS_POR_BLOCO}...", file=sys.stderr)
        dados = llm.json_chat(SISTEMA_EXTRACAO, f"Ano de referência: {ano_info}\n\nTRECHO:\n{bloco}")
        for item in dados.get("transacoes", []):
            try:
                d = parse_data(item["data"], ano_info)
                v = abs(parse_valor(item["valor"]))
                if str(item.get("tipo", "")).lower().startswith("deb"):
                    v = -v
                t = Transacao(d, str(item["descricao"]).strip(), v, origem)
            except (KeyError, ValueError):
                continue
            if not valor_aparece_no_texto(v, bloco_norm):
                t.status = "REVISAR"
            resultado.append(t)
    return resultado


# --------------------------------------------------------------------------- #
# Categorização
# --------------------------------------------------------------------------- #
SISTEMA_CATEGORIA = f"""Você classifica transações bancárias brasileiras.
Categorias permitidas: {json.dumps(CATEGORIAS, ensure_ascii=False)}
Responda SOMENTE com JSON: {{"categorias": {{"0": "Categoria", "1": "Categoria"}}}}
Use exatamente os nomes permitidos. Valores positivos são entradas, negativos são saídas.
Na dúvida, use "Outros"."""


def categorizar(transacoes: list[Transacao], llm: OllamaClient | None, cache_path: Path):
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    usuario = cache_path.with_name("regras_usuario.json")
    regras_usuario = json.loads(usuario.read_text(encoding="utf-8")) if usuario.exists() else {}
    regras = [(normaliza(k), v) for k, v in regras_usuario.items()] + list(REGRAS.items())
    pendentes = []
    for t in transacoes:
        chave = normaliza(t.descricao)
        if chave in cache:                       # 1) correção manual / aprendido (descrição exata)
            t.categoria = cache[chave]
            continue
        regra = next((cat for kw, cat in regras                       # 2) suas regras  3) regras embutidas
                      if re.search(r"\b" + re.escape(kw), chave)), None)
        if regra:
            t.categoria = regra
        else:
            pendentes.append(t)                  # 4) LLM

    if pendentes and llm:
        for i in range(0, len(pendentes), TAMANHO_LOTE_CATEGORIAS):
            lote = pendentes[i:i + TAMANHO_LOTE_CATEGORIAS]
            print(f"  [LLM] categorizando {len(lote)} transações...", file=sys.stderr)
            lista = "\n".join(f"{n}: {t.descricao} | R$ {t.valor:.2f}" for n, t in enumerate(lote))
            try:
                resp = llm.json_chat(SISTEMA_CATEGORIA, lista).get("categorias", {})
            except RuntimeError:
                resp = {}
            for n, t in enumerate(lote):
                cat = resp.get(str(n), "Outros")
                t.categoria = cat if cat in CATEGORIAS else "Outros"
                cache[normaliza(t.descricao)] = t.categoria
    else:
        for t in pendentes:
            t.categoria = "Outros"

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Planilha
# --------------------------------------------------------------------------- #
ABA_TRANS, ABA_RESUMO = "Transações", "Resumo"
CABECALHO = ["Data", "Descrição", "Valor (R$)", "Tipo", "Categoria", "Origem", "Mês", "Status", "ID", "Banco", "Obs."]
FONTE = "Arial"


def gerar_ids(transacoes: list[Transacao]):
    """ID estável: mesma transação reimportada gera o mesmo ID (permite deduplicar).
    O contador distingue compras idênticas no mesmo dia (ex.: 2 cafés iguais)."""
    contagem: dict[str, int] = {}
    for t in transacoes:
        if t.id:                       # ID nativo do banco (ex.: Nubank)
            continue
        base = f"{t.data.isoformat()}|{normaliza(t.descricao)}|{t.valor:.2f}"
        contagem[base] = contagem.get(base, 0) + 1
        t.id = hashlib.sha1(f"{base}|{contagem[base]}".encode()).hexdigest()[:12]


def abrir_ou_criar(caminho: Path):
    if caminho.exists():
        wb = load_workbook(caminho)
        if ABA_TRANS in wb.sheetnames:
            return wb, wb[ABA_TRANS]
    else:
        wb = Workbook()
        wb.remove(wb.active)
    ws = wb.create_sheet(ABA_TRANS, 0)
    ws.append(CABECALHO)
    for c in ws[1]:
        c.font = Font(name=FONTE, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(horizontal="center")
    for letra, larg in zip("ABCDEFGHIJK", (12, 46, 14, 10, 20, 24, 10, 10, 16, 16, 40)):
        ws.column_dimensions[letra].width = larg
    ws.freeze_panes = "A2"
    return wb, ws


def gravar_planilha(transacoes: list[Transacao], caminho: Path) -> tuple[int, int]:
    wb, ws = abrir_ou_criar(caminho)
    ids_existentes = {ws.cell(r, 9).value for r in range(2, ws.max_row + 1)}
    novas = [t for t in transacoes if t.id not in ids_existentes]

    linha_inicial = ws.max_row + 1
    for t in sorted(novas, key=lambda x: x.data):
        ws.append([
            t.data, t.descricao, t.valor,
            f'=IF(C{ws.max_row + 1}<0,"Saída","Entrada")',
            t.categoria, t.origem,
            f"=YEAR(A{ws.max_row + 1})*100+MONTH(A{ws.max_row + 1})",
            t.status, t.id, t.banco, t.nota,
        ])
        r = ws.max_row
        ws.cell(r, 1).number_format = "dd/mm/yyyy"
        ws.cell(r, 3).number_format = '#,##0.00;[Red]-#,##0.00'
        ws.cell(r, 7).number_format = "0"
        for c in range(1, 12):
            cell = ws.cell(r, c)
            cell.font = Font(name=FONTE, color="0000FF" if c in (1, 2, 3, 5, 6) else "000000")
        if t.status == "REVISAR":
            for c in range(1, 12):
                ws.cell(r, c).fill = PatternFill("solid", fgColor="FFF2CC")

    reconstruir_resumo(wb, ws)
    wb.save(caminho)
    return len(novas), len(transacoes) - len(novas)


def reconstruir_resumo(wb, ws):
    """Aba Resumo: categoria x mês, 100% fórmulas SUMIFS sobre a aba de transações."""
    if ABA_RESUMO in wb.sheetnames:
        del wb[ABA_RESUMO]
    meses, categorias = set(), set()
    for r in range(2, ws.max_row + 1):
        d, cat = ws.cell(r, 1).value, ws.cell(r, 5).value
        if isinstance(d, (date, datetime)):
            meses.add(d.year * 100 + d.month)
        if cat:
            categorias.add(cat)
    meses, categorias = sorted(meses), sorted(categorias)

    rs = wb.create_sheet(ABA_RESUMO)
    rs["A1"] = "Categoria \\ Mês (AAAAMM)"
    for j, m in enumerate(meses, start=2):
        rs.cell(1, j, m).number_format = "0"
    col_tot = len(meses) + 2
    rs.cell(1, col_tot, "Total")

    q = f"'{ABA_TRANS}'"
    for i, cat in enumerate(categorias, start=2):
        rs.cell(i, 1, cat)
        for j in range(2, len(meses) + 2):
            L = rs.cell(1, j).column_letter
            rs.cell(i, j, f"=SUMIFS({q}!$C:$C,{q}!$E:$E,$A{i},{q}!$G:$G,{L}$1)")
        rs.cell(i, col_tot, f"=SUM(B{i}:{rs.cell(i, col_tot - 1).column_letter}{i})")

    ult = len(categorias) + 1
    rs.cell(ult + 1, 1, "Saldo do período")
    for j in range(2, col_tot + 1):
        L = rs.cell(1, j).column_letter
        rs.cell(ult + 1, j, f"=SUM({L}2:{L}{ult})")

    for row in rs.iter_rows(min_row=1, max_row=ult + 1, max_col=col_tot):
        for c in row:
            c.font = Font(name=FONTE, bold=(c.row in (1, ult + 1) or c.column == 1))
            if c.row > 1 and c.column > 1:
                c.number_format = '#,##0.00;[Red]-#,##0.00'
    for c in rs[1]:
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.font = Font(name=FONTE, bold=True, color="FFFFFF")
    rs.column_dimensions["A"].width = 26
    rs.freeze_panes = "B2"


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def processar_arquivo(caminho: Path, llm: OllamaClient | None, args) -> list[Transacao]:
    ext = caminho.suffix.lower()
    chave = args.banco
    print(f"→ {caminho.name}", file=sys.stderr)

    if ext == ".csv":
        ts = bancos.extrair_csv(caminho, args.ano, args.cartao, chave)
    elif ext in (".ofx", ".qfx"):
        ts = bancos.extrair_ofx(caminho, chave)
    elif ext in (".xlsx", ".xls"):
        ts = bancos.extrair_planilha(caminho, args.ano, args.cartao, chave)
    elif ext in (".pdf", ".txt"):
        cache_texto = caminho.with_suffix(".texto.txt")
        if ext == ".pdf" and cache_texto.exists() and cache_texto.stat().st_mtime >= caminho.stat().st_mtime:
            texto = cache_texto.read_text(encoding="utf-8")
            print(f"  texto em cache: {cache_texto.name}", file=sys.stderr)
        else:
            texto = extrair_texto_pdf(caminho) if ext == ".pdf" else ler_texto_arquivo(caminho)
        if args.salvar_texto:
            destino = caminho.with_suffix(".texto.txt")
            destino.write_text(texto, encoding="utf-8")
            print(f"  texto extraído salvo em {destino}", file=sys.stderr)
        chave = chave or bancos.detectar_banco(texto, caminho.name)
        ts = [] if args.forcar_llm else bancos.extrair_texto_extrato(texto, chave, args.ano, caminho.name)
        perfil = bancos.PERFIS.get(chave, bancos.GENERICO)
        if ts:
            aviso = " (perfil experimental: confira)" if perfil.experimental else ""
            print(f"  perfil {NOMES.get(chave, 'genérico')}: {len(ts)} transações{aviso}", file=sys.stderr)
        else:
            if llm is None:
                sys.exit("O perfil não achou transações e o LLM está desligado (--sem-llm).")
            st = llm.status()
            if not st["online"]:
                sys.exit(f"Ollama não respondeu em {llm.host}. Inicie com: ollama serve")
            if not st["modelo_ok"]:
                sys.exit(f"Modelo '{llm.modelo}' não instalado. Rode: ollama pull {llm.modelo}")
            print("  perfil não encontrou transações -> usando LLM local", file=sys.stderr)
            ts = extrair_com_llm(texto, caminho.name, llm, args.ano)
            for t in ts:
                t.banco = NOMES.get(chave, "")
    else:
        sys.exit(f"Formato não suportado: {ext}")

    return ts


def main():
    ap = argparse.ArgumentParser(description="Agente local de extratos bancários -> planilha")
    ap.add_argument("arquivos", nargs="+", type=Path)
    ap.add_argument("--planilha", type=Path, default=Path("financas.xlsx"))
    ap.add_argument("--modelo", default=MODELO_PADRAO)
    ap.add_argument("--host", default=OLLAMA_HOST)
    ap.add_argument("--ano", type=int, help="Ano para datas sem ano (ex.: 12/03)")
    ap.add_argument("--cartao", action="store_true", help="Fatura de cartão: positivo = compra")
    ap.add_argument("--banco", choices=sorted(NOMES), help="Força o perfil do banco (padrão: detecta sozinho)")
    ap.add_argument("--forcar-llm", action="store_true", help="PDF/TXT: pula o perfil do banco e usa só o LLM")
    ap.add_argument("--salvar-texto", action="store_true", help="PDF: salva o texto extraído (para diagnóstico)")
    ap.add_argument("--sem-llm", action="store_true", help="Só regras (CSV/OFX); sem categorização por IA")
    ap.add_argument("--dry-run", action="store_true", help="Mostra o resultado sem gravar")
    args = ap.parse_args()

    llm = None if args.sem_llm else OllamaClient(args.modelo, args.host)

    todas: list[Transacao] = []
    for arq in args.arquivos:
        if not arq.exists():
            sys.exit(f"Arquivo não encontrado: {arq}")
        ts = processar_arquivo(arq, llm, args)
        gerar_ids(ts)
        todas.extend(ts)

    if not todas:
        sys.exit("Nenhuma transação encontrada.")

    categorizar(todas, llm, args.planilha.with_name("categorias_cache.json"))

    if args.dry_run:
        for t in todas:
            obs = f"  <- {t.nota}" if t.nota else ""
            print(f"{t.data:%d/%m/%Y} | {t.valor:>10.2f} | {t.categoria:<18} | {t.status:<7} | {t.descricao}{obs}")
        return

    novas, repetidas = gravar_planilha(todas, args.planilha)
    revisar = sum(1 for t in todas if t.status == "REVISAR")
    print(f"✓ {novas} novas | {repetidas} já existiam | {revisar} para revisar → {args.planilha}")


if __name__ == "__main__":
    main()
