"""Testes dos parsers de banco: 14 cenários (um por banco/formato), sem Ollama.
Rode: python test_bancos.py"""
import sys
from datetime import date
from pathlib import Path

import bancos as B
import agente_extratos as A

PASS = 0
FALHA = 0


def checar(nome, condicao, detalhe=""):
    global PASS, FALHA
    if condicao:
        PASS += 1
        print(f"  OK   {nome}")
    else:
        FALHA += 1
        print(f"  FALHA {nome} {detalhe}")


def ts_lista(ts):
    return [(t.data, t.descricao, round(t.valor, 2), t.banco, t.status) for t in ts]


# ------------------------------------------------------------------ #
# 1. Nubank CSV (conta): Data,Valor,Identificador,Descrição
# ------------------------------------------------------------------ #
NU_CSV = """Data,Valor,Identificador,Descrição
01/05/2026,-45.90,uuid-0001,Compra no débito - IFOOD
02/05/2026,1500.00,uuid-0002,Transferência recebida pelo Pix - JOAO
03/05/2026,-12.50,uuid-0003,Uber - corrida
"""
import tempfile, os
tmp = tempfile.mkdtemp()
p = Path(tmp) / "nu_conta.csv"
p.write_text(NU_CSV, encoding="utf-8")
ts = B.extrair_csv(p, None, False, "nubank")
checar("Nubank CSV conta: 3 transações", len(ts) == 3, f"obteve {len(ts)}")
if len(ts) == 2:
    checar("Nubank IFOOD valor -45.90", ts[0].valor == -45.90, f"{ts[0].valor}")
    checar("Nubank Pix valor 1500.00", ts[1].valor == 1500.00, f"{ts[1].valor}")
    checar("Nubank IDs nativos", ts[0].id == "uuid-0001" and ts[1].id == "uuid-0002", f"{ts[0].id}, {ts[1].id}")


# ------------------------------------------------------------------ #
# 2. Nubank OFX (cartão)
# ------------------------------------------------------------------ #
NU_OFX = """<OFX><BANKID>0260</BANKID>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS></SONRS></SIGNONMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CCSTMTTRN><DTPOSTED>20260501<TRNAMT>-45.90<FITID>fit-00001<NAME>IFOOD<MEMO>Compra no débito IFOOD</CCSTMTTRN>
<CCSTMTTRN><DTPOSTED>20260502<TRNAMT>150.00<FITID>fit-002<NAME>Estabelecimento<MEMO>Pagamento recebido</CCSTMTTRN>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>
</OFX>"""
p = Path(tmp) / "nu_cartao.ofx"
p.write_text(NU_OFX, encoding="utf-8")
ts = B.extrair_ofx(p, "nubank")
checar("Nubank OFX cartão: ignora 'Pagamento recebido'", len(ts) == 1, f"obteve {len(ts)}")
if len(ts) == 1:
    checar("Nubank OFX IFOOD", ts[0].descricao == "Compra no débito IFOOD", ts[0].descricao)
    checar("Nubank OFX ID nativo", ts[0].id == "fit-00001", ts[0].id)


# ------------------------------------------------------------------ #
# 3. Bradesco PDF (perfil flex, débito negativo, data herdada)
# ------------------------------------------------------------------ #
BRA_PDF = """Bradesco - Extrato
Data Lançamento Dcto. Crédito Débito Saldo
01/05/2026 SALDO ANTERIOR                  1.000,00
02/05/2026 DOC    12345   -45,90   954,10
           PIX    67890   -20,00   934,10
03/05/2026 DEP    54321    500,00  1.434,10
"""
from unittest.mock import patch
import builtins

# Simular leitura de PDF via texto (extrair_texto_pdf lê arquivo real, então testamos extrair_texto_extrato diretamente)
ts = B.extrair_texto_extrato(BRA_PDF, "bradesco", 2026, "bradesco.pdf")
checar("Bradesco PDF: 3 transações", len(ts) == 3, f"obteve {len(ts)}: {ts_lista(ts)}")
if len(ts) >= 3:
    checar("Bradesco data herdada na 2a linha", ts[1].data == date(2026, 5, 2), str(ts[1].data))
    checar("Bradesco débito negativo", ts[0].valor == -45.90, f"{ts[0].valor}")
    checar("Bradesco crédito positivo", ts[2].valor == 500.00, f"{ts[2].valor}")


# ------------------------------------------------------------------ #
# 4. Santander PDF (flex, débito com '-' no fim, sem ano)
# ------------------------------------------------------------------ #
SAN_PDF = """Santander - Extrato Consolidado
junho/2025
01/06   DESCRIÇÃO DOC  567,86-  6.202,74
02/06   PIX RECEBIDO     100,00  6.302,74
Investimentos: Tesouro Selic 1.000,00
"""
ts = B.extrair_texto_extrato(SAN_PDF, "santander", None, "santander.pdf")
checar("Santander PDF: ignora tabela de investimentos", len(ts) == 2, f"obteve {len(ts)}: {ts_lista(ts)}")
if len(ts) >= 2:
    checar("Santander débito com sinal no fim", ts[0].valor == -567.86, f"{ts[0].valor}")
    checar("Santander ano inferido de junho/2025", ts[0].data == date(2025, 6, 1), str(ts[0].data))


# ------------------------------------------------------------------ #
# 5. Caixa PDF (completa, sufixo C/D colado no valor)
# ------------------------------------------------------------------ #
CAI_PDF = """Caixa Econômica Federal - Extrato
Data Mov. Nr. Doc. Histórico Valor Saldo
01/05/2026 000000 SALDO ANTERIOR 3.252,04C
02/05/2026 067400 CIEL VS CD 199,29C 3.451,33C
09/05/2026 000268 AP CDB FLX 8.139,13D 9.999,99C
"""
ts = B.extrair_texto_extrato(CAI_PDF, "caixa", 2026, "caixa.pdf")
checar("Caixa PDF: 2 transações", len(ts) == 2, f"obteve {len(ts)}: {ts_lista(ts)}")
if len(ts) >= 2:
    checar("Caixa crédito sufixo C", ts[0].valor == 199.29, f"{ts[0].valor}")
    checar("Caixa débito sufixo D", ts[1].valor == -8139.13, f"{ts[1].valor}")


# ------------------------------------------------------------------ #
# 6. Banco do Brasil PDF (completa, sufixo C/D separado)
# ------------------------------------------------------------------ #
BB_PDF = """Banco do Brasil - Extrato
01/05/2026 01/05/2026 0123 456 789 PIX ENVIADO JOAO 424,91 D 1.000,00 C
02/05/2026 02/05/2026 0123 456 790 DEPÓSITO 500,00 C 1.500,00 C
"""
ts = B.extrair_texto_extrato(BB_PDF, "bb", 2026, "bb.pdf")
checar("BB PDF: 2 transações", len(ts) == 2, f"obteve {len(ts)}: {ts_lista(ts)}")
if len(ts) >= 2:
    checar("BB débito com D separado", ts[0].valor == -424.91, f"{ts[0].valor}")
    checar("BB crédito com C separado", ts[1].valor == 500.00, f"{ts[1].valor}")


# ------------------------------------------------------------------ #
# 7. Itaú perfil genérico (experimental)
# ------------------------------------------------------------------ #
ITAU_PDF = """Itaú - Extrato
01/05/2026 Compra no supermercado -150,00 2.850,00
02/05/2026 Salário 3.000,00 5.850,00
"""
ts = B.extrair_texto_extrato(ITAU_PDF, "itau", 2026, "itau.pdf")
checar("Itaú perfil genérico: 2 transações", len(ts) == 2, f"obteve {len(ts)}: {ts_lista(ts)}")
if len(ts) >= 2:
    checar("Itaú débito negativo", ts[0].valor == -150.00, f"{ts[0].valor}")
    checar("Itaú experimental flag", B.PERFIS["itau"].experimental is True)


# ------------------------------------------------------------------ #
# 8. Detecção de banco por BANKID (OFX)
# ------------------------------------------------------------------ #
OFX_BB = """<OFX><BANKID>001</BANKID><STMTTRN><DTPOSTED>20260501<TRNAMT>-10.00<FITID>1<MEMO>TESTE</STMTTRN></OFX>"""
chave = B.detectar_banco(OFX_BB, "")
checar("Detecta BB por BANKID 001", chave == "bb", f"obteve {chave}")


# ------------------------------------------------------------------ #
# 9. Detecção de banco por assinatura no texto
# ------------------------------------------------------------------ #
texto_bradesco = "Extrato Bradesco - conta corrente"
chave = B.detectar_banco(texto_bradesco, "")
checar("Detecta Bradesco por assinatura", chave == "bradesco", f"obteve {chave}")

texto_nubank = "Nu Pagamentos S.A. - Extrato"
chave = B.detectar_banco(texto_nubank, "")
checar("Detecta Nubank por assinatura", chave == "nubank", f"obteve {chave}")


# ------------------------------------------------------------------ #
# 10. parse_valor: formatos variados
# ------------------------------------------------------------------ #
checar("parse_valor 1.234,56", B.parse_valor("1.234,56") == 1234.56)
checar("parse_valor -50,00", B.parse_valor("-50,00") == -50.0)
checar("parse_valor 199,29C", B.parse_valor("199,29C") == 199.29)
checar("parse_valor 567,86-", B.parse_valor("567,86-") == -567.86)
checar("parse_valor (50,00)", B.parse_valor("(50,00)") == -50.0)
checar("parse_valor R$ 1.234,56", B.parse_valor("R$ 1.234,56") == 1234.56)


# ------------------------------------------------------------------ #
# 11. parse_data: formatos variados
# ------------------------------------------------------------------ #
checar("parse_data DD/MM/AAAA", B.parse_data("01/05/2026") == date(2026, 5, 1))
checar("parse_data DD/MM sem ano", B.parse_data("01/05", 2026) == date(2026, 5, 1))
checar("parse_data YYYY-MM-DD", B.parse_data("2026-05-01") == date(2026, 5, 1))


# ------------------------------------------------------------------ #
# 12. normaliza: remove acento, lower, espaços
# ------------------------------------------------------------------ #
checar("normaliza IFOOD", B.normaliza("IFOOD") == "ifood")
checar("normaliza compra no débito", B.normaliza("Compra no débito") == "compra no debito")
checar("normaliza espaços múltiplos", B.normaliza("  um   dois  ") == "um dois")


# ------------------------------------------------------------------ #
# 13. conferir_saldos: corrige sinal trocado
# ------------------------------------------------------------------ #
from dataclasses import replace
evts = [1000.0]  # saldo anterior
t1 = B.Transacao(date(2026, 5, 2), "Compra", -45.90, "orig", saldo=954.10)
t2 = B.Transacao(date(2026, 5, 3), "Depósito", 500.00, "orig", saldo=1454.10)
evts.extend([t1, t2])
B.conferir_saldos(evts)
checar("conferir_saldos: sinal OK mantido", t1.valor == -45.90 and t2.valor == 500.00)


# ------------------------------------------------------------------ #
# 14. REGRAS: primeira casa que casa vence
# ------------------------------------------------------------------ #
from agente_extratos import REGRAS, CATEGORIAS
import re
def aplicar_regras(desc):
    chave = B.normaliza(desc)
    for kw, cat in REGRAS.items():
        if re.search(r"\b" + re.escape(kw), chave):
            return cat
    return "Outros"
checar("Regra: tarifa -> Serviços e Tarifas", aplicar_regras("TARIFA DE MANUTENÇÃO") == "Serviços e Tarifas")
checar("Regra: ifood -> Alimentação", aplicar_regras("COMPRA IFOOD") == "Alimentação")
checar("Regra: pagamento da fatura -> Pagamento de fatura", aplicar_regras("PAGAMENTO DA FATURA") == "Pagamento de fatura")
checar("Regra: resgate -> Investimentos", aplicar_regras("RESGATE CDB") == "Investimentos")
checar("Regra desconhecido -> Outros", aplicar_regras("DESCRICAO QUALQUER COISA") == "Outros")


# ------------------------------------------------------------------ #
# Resultado
# ------------------------------------------------------------------ #
print(f"\n{PASS} passou, {FALHA} falhou")
sys.exit(1 if FALHA else 0)
