# -*- coding: utf-8 -*-
"""
Leitores de extratos por banco: Bradesco, Nubank, Itaú, Santander, Caixa e Banco do Brasil.

Ordem de preferência (da mais confiável para a menos):
  1. OFX                       -> padrão único, igual em todos os bancos
  2. CSV / XLS(X)              -> colunas reconhecidas por nome (inclui layouts de cada banco)
  3. PDF / TXT                 -> perfil por banco (regex) + conferência pelo saldo corrente
  4. (fora daqui) LLM local    -> só quando o perfil não encontra nada

Layouts de PDF baseados em extratos reais:
  Caixa:     DD/MM/AAAA NRDOC HISTÓRICO  199,29C  3.451,33C        (sufixo C/D)
  BB:        DD/MM/AAAA [DD/MM/AAAA] AG LOTE COD HISTÓRICO DOC  424,91 D  1.000,00 C
  Bradesco:  [DD/MM/AAAA] HISTÓRICO DCTO  -19,00  29,30           (débito negativo; data pode faltar)
  Santander: [DD/MM[/AAAA]] DESCRIÇÃO [DOC]  567,86-  6.202,74    (débito com '-' no fim; sem ano)
  Itaú:      EXPERIMENTAL (sem amostra confirmada) -> perfil genérico
  Nubank:    use CSV/OFX (o PDF não segue tabela); PDF cai no LLM
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


# --------------------------------------------------------------------------- #
# Modelo e utilitários
# --------------------------------------------------------------------------- #
@dataclass
class Transacao:
    data: date
    descricao: str
    valor: float                 # negativo = saída, positivo = entrada
    origem: str = ""
    categoria: str = ""
    status: str = "OK"           # OK | REVISAR
    id: str = ""
    banco: str = ""
    saldo: float | None = None   # saldo após a linha, quando o extrato informa
    nota: str = ""


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normaliza(s: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(str(s)).lower()).strip()


def parse_valor(texto) -> float:
    """'R$ 1.234,56', '-50.00', '1.234,56 D', '199,29C', '567,86-', '(50,00)' -> float com sinal."""
    t = str(texto).strip().upper().replace("R$", "").replace(" ", "")
    negativo = t.startswith("-") or t.endswith("-") or (t.startswith("(") and t.endswith(")"))
    if re.search(r"(D|DB|DEB)$", t):
        negativo = True
    t = re.sub(r"[^\d,.]", "", t)
    if not t:
        raise ValueError(f"valor inválido: {texto!r}")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    v = float(t)
    return -v if negativo else v


def parse_data(texto, ano_padrao: int | None = None) -> date:
    t = str(texto).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", t)
    if m and ano_padrao:
        return date(ano_padrao, int(m.group(2)), int(m.group(1)))
    raise ValueError(f"data inválida: {texto!r}")


def ler_texto_arquivo(caminho: Path) -> str:
    bruto = Path(caminho).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return bruto.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Detecção do banco
# --------------------------------------------------------------------------- #
NOMES = {
    "bradesco": "Bradesco", "nubank": "Nubank", "itau": "Itaú",
    "santander": "Santander", "caixa": "Caixa Econômica", "bb": "Banco do Brasil",
}
OFX_BANKID = {"237": "bradesco", "260": "nubank", "341": "itau", "33": "santander",
              "104": "caixa", "1": "bb"}
ASSINATURAS = [
    ("nubank", r"nubank|nu pagamentos|nu financeira|nuconta|(^|\n)nu_\d{6,}"),
    ("bradesco", r"bradesco"),
    ("itau", r"\bitau\b|itau unibanco|itaucard"),
    ("santander", r"santander"),
    ("caixa", r"caixa econ|caixa federal|caixa\.gov|siibc|\bcef\b|\bsac caixa"),
    ("bb", r"banco do brasil|\bbb\.com|autoatendimento\.bb|dt\.? ?balancete"),
]


def detectar_banco(texto: str, nome_arquivo: str = "") -> str | None:
    m = re.search(r"<BANKID>\s*0*(\d+)", texto, flags=re.I)
    if m and m.group(1) in OFX_BANKID:
        return OFX_BANKID[m.group(1)]
    amostra = normaliza(nome_arquivo + "\n" + texto[:8000])
    for chave, padrao in ASSINATURAS:
        if re.search(padrao, amostra):
            return chave
    if re.search(r"\bcaixa\b", amostra[:1500]):
        return "caixa"
    return None


# --------------------------------------------------------------------------- #
# Perfis de PDF/TXT
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Perfil:
    datas: str          # "completa" (DD/MM/AAAA) | "flex" (DD/MM ou DD/MM/AAAA)
    herda_data: bool    # linhas sem data usam a data da linha anterior
    docs_ini: bool      # descarta números no início (ag/lote/doc)
    docs_fim: bool      # descarta nº de documento no fim
    experimental: bool = False


PERFIS = {
    "bradesco": Perfil("flex", True, False, True),
    "santander": Perfil("flex", True, False, True),
    "caixa": Perfil("completa", False, True, False),
    "bb": Perfil("completa", False, True, True),
    "itau": Perfil("flex", False, False, False, experimental=True),
}
GENERICO = Perfil("flex", False, False, False, experimental=True)

_VAL = r"-?\d[\d.]*,\d{2}-?(?:\s?[CD](?![A-Za-z0-9]))?"
RE_VAL = re.compile(_VAL)
RE_FIM = re.compile(rf"^(?P<cab>.*?)(?P<vals>(?:\s+{_VAL}){{1,2}})\s*$")
RE_DATA = {"completa": re.compile(r"^(\d{2}/\d{2}/\d{4})\s*"),
           "flex": re.compile(r"^(\d{2}/\d{2}(?:/\d{2,4})?)(?=\s|$)\s*")}
RE_LETRAS = re.compile(r"[^\W\d_]{2,}")

# saldo da conta (usado para conferir a soma das linhas)
RE_MARCADOR = re.compile(
    r"saldo\s*(anterior|dia\b|em\b|inicial|final|atual|do dia|total\b(?!\s*disp))|\bs a l d o\b")
# linhas que nunca são transação (cabeçalhos, totais, rodapés)
RE_IGNORAR = re.compile(
    r"^total\b|^subtotal|^pagina|^data\b|^dt\.?(\s|$)|^lancamentos?\b|^extrato|^agencia|^ag\.|"
    r"^conta\b|^cliente|^periodo|^observ|ouvidoria|^sac\b|^alo\b|http|www\.|^limite|^vlr\.|"
    r"^creditos?\s+debitos?|^movimenta|^opcao de pesquisa|^saldo\b|saldo\s*(disponivel|total disp|aplic|bloqueado)|"
    r"valor\s*\(?r\$\)?|movimentos?\s*\(r\$\)|saldo\s*\(r\$\)|ag\.? origem|^balancete|^historico\b|"
    r"rendimento bruto|^aplicacao n|^produto\b|^investimento|^aplicacao\b")

MESES = {m: i for i, m in enumerate(
    "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro".split(), 1)}


def inferir_ano(texto: str, ano: int | None) -> int:
    if ano:
        return ano
    n = normaliza(texto)
    m = re.search(rf"({'|'.join(MESES)})\s*/\s*(20\d{{2}})", n)
    if m:
        return int(m.group(2))
    m = re.search(r"periodo[^\n]*?\d{2}/\d{2}/(20\d{2})", n)
    if m:
        return int(m.group(1))
    anos = Counter(re.findall(r"\d{2}/\d{2}/(20\d{2})", texto))
    return int(anos.most_common(1)[0][0]) if anos else date.today().year


def _parse_linha(linha: str, perfil: Perfil, ultima_data: date | None, ano: int):
    m = RE_FIM.match(linha)
    if not m:
        return None
    cab = m.group("cab").strip()
    vals = RE_VAL.findall(m.group("vals"))
    herdada = False
    md = RE_DATA[perfil.datas].match(cab)
    if md:
        try:
            data = parse_data(md.group(1), ano)
        except ValueError:
            return None
        cab = cab[md.end():].strip()
        cab = re.sub(r"^\d{2}/\d{2}/\d{4}\s+", "", cab)      # BB: segunda data (movimento)
    elif perfil.herda_data and ultima_data:
        data, herdada = ultima_data, True
    else:
        return None
    if RE_VAL.search(cab):          # mais de 2 valores na linha: tabela de resumo/investimento
        return None
    toks = cab.split()
    if perfil.docs_ini:
        while toks and re.fullmatch(r"\d+", toks[0]):
            toks.pop(0)
    if perfil.docs_fim:
        while toks and re.fullmatch(r"[\d.]{3,}", toks[-1]):
            toks.pop()
    desc = " ".join(toks).strip(" -")
    if re.match(r"saldo\b", normaliza(desc)):
        return None
    try:
        valor = parse_valor(vals[0])
        saldo = parse_valor(vals[1]) if len(vals) > 1 else None
    except ValueError:
        return None
    return data, desc, valor, saldo, herdada


def _marcar(t: Transacao, msg: str):
    t.status = "REVISAR"
    t.nota = f"{t.nota}; {msg}" if t.nota else msg


def conferir_saldos(eventos: list):
    """Confere (saldo anterior + linhas) com o saldo impresso. Corrige sinal quando o saldo prova o erro."""
    corrente, ultimo = None, None
    for e in eventos:
        if isinstance(e, float):                    # marcador de saldo (anterior/dia/final)
            if corrente is not None and ultimo is not None and abs(corrente - e) > 0.015:
                _marcar(ultimo, f"saldo do extrato {e:.2f} difere do calculado {corrente:.2f}")
            corrente = e
            continue
        ultimo = e
        if corrente is not None:
            corrente += e.valor
        if e.saldo is not None:
            if corrente is not None and abs(corrente - e.saldo) > 0.015:
                alt = corrente - 2 * e.valor
                if abs(alt - e.saldo) <= 0.015:
                    e.valor, e.nota, corrente = -e.valor, "sinal corrigido pelo saldo", alt
                else:
                    _marcar(e, f"saldo não confere (extrato {e.saldo:.2f} x calculado {corrente:.2f})")
            corrente = e.saldo


def extrair_texto_extrato(texto: str, chave: str | None, ano: int | None, origem: str) -> list[Transacao]:
    perfil = PERFIS.get(chave, GENERICO)
    ano = inferir_ano(texto, ano)
    nome = NOMES.get(chave, "")
    eventos: list = []
    ultima_data = alvo = None
    n_cont, pend = 0, []

    for bruta in texto.splitlines():
        linha = re.sub(r"\s+", " ", bruta).strip()
        if not linha:
            continue
        n = normaliza(linha)
        if re.fullmatch(r"\d{2}/\d{2}(/\d{4})?", linha):     # data sozinha na linha (Bradesco)
            try:
                ultima_data = parse_data(linha, ano)
            except ValueError:
                pass
            continue
        if RE_MARCADOR.search(n):
            vs = RE_VAL.findall(linha)
            if 1 <= len(vs) <= 2:      # 3+ valores = tabela de investimentos, não é o saldo da conta
                try:
                    eventos.append(float(parse_valor(vs[-1])))
                except ValueError:
                    pass
            alvo, pend = None, []
            continue
        if RE_IGNORAR.search(n):
            alvo, pend = None, []
            continue

        r = _parse_linha(linha, perfil, ultima_data, ano)
        if r:
            data, desc, valor, saldo, herdada = r
            if not RE_LETRAS.search(desc):
                if pend:
                    desc = " ".join(pend)
                elif herdada:
                    continue
                else:
                    desc = "(sem descrição)"
            t = Transacao(data, desc, valor, origem, banco=nome, saldo=saldo)
            eventos.append(t)
            ultima_data, alvo, n_cont, pend = data, t, 0, []
            continue

        if RE_FIM.match(linha):        # tem valores mas não é transação (resumo/cabeçalho)
            continue
        txt = re.sub(r"^\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", linha)   # BB: "26/07 12:35 NOME"
        if RE_LETRAS.search(txt) and len(txt) <= 60:                 # complemento da descrição
            pend = (pend + [txt])[-2:]
            if alvo and n_cont < 2:
                alvo.descricao = f"{alvo.descricao} {txt}".strip()
                n_cont += 1

    conferir_saldos(eventos)
    return [e for e in eventos if isinstance(e, Transacao)]


# --------------------------------------------------------------------------- #
# Tabelas: CSV e planilhas (XLS/XLSX)
# --------------------------------------------------------------------------- #
def _delimitador(linhas: list[str]) -> str:
    melhor, pontos = ";", -1
    amostra = linhas[:40]
    for d in (";", ",", "\t"):
        c = Counter(len(l) for l in csv.reader(amostra, delimiter=d) if len(l) > 1)
        score = max((k * f for k, f in c.items()), default=0)
        if score > pontos:
            melhor, pontos = d, score
    return melhor


def tabela_para_transacoes(linhas: list[list[str]], ano, cartao: bool, origem: str, banco: str) -> list[Transacao]:
    idx = next((i for i, l in enumerate(linhas)
                if any(re.search(r"^data|^date", normaliza(c)) for c in l)
                and any(re.search(r"valor|amount|debito|credito|quantia|entrada|saida", normaliza(c)) for c in l)),
               None)
    if idx is None:
        raise ValueError("Não encontrei cabeçalho com colunas de data e valor.")
    cab = [normaliza(c) for c in linhas[idx]]

    def col(*padroes):
        for i, c in enumerate(cab):
            if any(re.search(p, c) for p in padroes):
                return i
        return None

    i_data = col(r"^data", r"^date")
    i_desc = col(r"descri", r"historico", r"lancamento", r"title", r"memo", r"estabelecimento")
    i_val = col(r"^valor", r"^amount", r"quantia")
    i_deb = col(r"^debito", r"^saida")
    i_cre = col(r"^credito", r"^entrada")
    i_dc = col(r"deb_cred", r"^d/?c$", r"^c/?d$", r"^inf\.?$", r"natureza")
    i_det = col(r"detalh")
    i_id = col(r"identificador")
    if i_data is None or i_desc is None or (i_val is None and i_deb is None and i_cre is None):
        raise ValueError(f"Colunas não reconhecidas: {cab}")

    if {"date", "category", "title", "amount"} <= set(cab):      # fatura do cartão Nubank
        cartao, banco = True, banco or NOMES["nubank"]
    elif i_id is not None and not banco:                         # conta Nubank: Data,Valor,Identificador,Descrição
        banco = NOMES["nubank"]
    pular_pgto = cartao and "nubank" in normaliza(banco)

    resultado, ultima = [], None
    for l in linhas[idx + 1:]:
        def get(i):
            return l[i].strip() if i is not None and i < len(l) else ""
        if not any(str(c).strip() for c in l):
            continue
        desc = get(i_desc)
        nd = normaliza(desc)
        if re.match(r"saldo\b|total\b", nd) or re.search(r"\bs a l d o\b", nd):
            continue
        rd = get(i_data)
        if rd:
            try:
                ultima = parse_data(rd, ano)
            except ValueError:
                continue
        elif ultima is None:
            continue
        try:
            if i_val is not None:
                txt = get(i_val)
                if not txt:
                    continue
                v = parse_valor(txt)
            else:
                dt, ct = get(i_deb), get(i_cre)
                if not dt and not ct:
                    continue
                v = (abs(parse_valor(ct)) if ct else 0.0) - (abs(parse_valor(dt)) if dt else 0.0)
        except ValueError:
            continue
        dc = get(i_dc).upper()
        if dc.startswith("D"):
            v = -abs(v)
        elif dc.startswith("C"):
            v = abs(v)
        if cartao:
            v = -v
        if pular_pgto and "pagamento recebido" in nd:
            continue
        det = get(i_det)
        if det and normaliza(det) not in nd:
            desc = f"{desc} - {det}".strip(" -")
        t = Transacao(ultima, desc, v, origem, banco=banco)
        if i_id is not None and get(i_id):
            t.id = get(i_id)               # ID nativo do banco (Nubank) -> deduplica entre CSV e OFX
        resultado.append(t)
    return resultado


def extrair_csv(caminho: Path, ano, cartao: bool, chave: str | None) -> list[Transacao]:
    texto = ler_texto_arquivo(caminho)
    chave = chave or detectar_banco(texto, caminho.name)
    linhas_txt = texto.splitlines()
    linhas = list(csv.reader(linhas_txt, delimiter=_delimitador(linhas_txt)))
    return tabela_para_transacoes(linhas, ano, cartao, caminho.name, NOMES.get(chave, ""))


def extrair_planilha(caminho: Path, ano, cartao: bool, chave: str | None) -> list[Transacao]:
    import pandas as pd
    df = pd.read_excel(caminho, header=None, dtype=object)

    def cel(x):
        if x is None or (isinstance(x, float) and x != x):
            return ""
        if isinstance(x, (datetime, date)):
            return x.strftime("%d/%m/%Y")
        if isinstance(x, float):
            return f"{x:.2f}".replace(".", ",")
        return str(x).strip()

    linhas = [[cel(x) for x in row] for row in df.values.tolist()]
    amostra = "\n".join(" ".join(l) for l in linhas[:30])
    chave = chave or detectar_banco(amostra, caminho.name)
    return tabela_para_transacoes(linhas, ano, cartao, caminho.name, NOMES.get(chave, ""))


# --------------------------------------------------------------------------- #
# OFX (todos os bancos)
# --------------------------------------------------------------------------- #
def extrair_ofx(caminho: Path, chave: str | None = None) -> list[Transacao]:
    texto = ler_texto_arquivo(caminho)
    chave = chave or detectar_banco(texto, caminho.name)
    banco = NOMES.get(chave, "")
    cartao = bool(re.search(r"<CCSTMTRS>|<CREDITCARDMSGSRSV1>", texto, flags=re.I))
    resultado = []
    for bloco in re.findall(r"<(?:STMTTRN|CCSTMTTRN)>(.*?)(?:</(?:STMTTRN|CCSTMTTRN)>|(?=<(?:STMTTRN|CCSTMTTRN)>)|$)", texto, flags=re.S | re.I):
        def tag(nome):
            m = re.search(rf"<{nome}>([^<\r\n]+)", bloco, flags=re.I)
            return m.group(1).strip() if m else ""
        dt, amt = tag("DTPOSTED")[:8], tag("TRNAMT")
        if not (dt and amt):
            continue
        memo, nome = tag("MEMO"), tag("NAME")
        desc = memo or nome
        if memo and nome and normaliza(nome) not in normaliza(memo):
            desc = f"{nome} - {memo}"
        if cartao and re.search(r"pagamento recebido", normaliza(desc)):
            continue                      # pagamento da fatura: já é saída na conta corrente
        valor = parse_valor(amt) if "," in amt else float(amt)
        t = Transacao(datetime.strptime(dt, "%Y%m%d").date(), desc, valor, caminho.name, banco=banco)
        if chave == "nubank" and len(tag("FITID")) >= 8:
            t.id = tag("FITID")
        resultado.append(t)
    return resultado
