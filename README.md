# Agente local de extratos bancários → planilha

100% offline: nenhum dado financeiro sai do seu computador.
Bancos com perfil próprio: **Bradesco, Nubank, Itaú, Santander, Caixa Econômica e Banco do Brasil**.

## 1. Instalação

```bash
ollama pull qwen2.5:7b-instruct      # ~5 GB (alternativas: llama3.1:8b, qwen2.5:3b-instruct p/ PC fraco)
pip install -r requirements.txt
```

## 2. Interface gráfica (recomendado)

```bash
./iniciar.sh        # Linux/macOS      |      iniciar.bat   # Windows (duplo clique)
# ou, manualmente:  pip install -r requirements.txt && streamlit run app_gui.py
```

Abre no navegador em `http://localhost:8501`. O servidor aceita conexões só deste computador e a telemetria do Streamlit está desligada.

| Tela | Para que serve |
|---|---|
| **Importar** | Arraste PDF/CSV/OFX/Excel, clique em *Ler extratos*, **confira e edite a tabela** (data, descrição, valor, categoria) e grave. Linhas **Revisar** (saldo não bateu) vêm desmarcadas e com o motivo em *Obs.*; linhas **Já na planilha** também vêm desmarcadas. |
| **Painel** | Entradas, saídas e saldo; gastos por categoria; mês a mês; busca nas transações. Filtros por mês, banco e categoria, e "Fora dos totais" (ex.: pagamento de fatura). |
| **Regras** | Suas palavras-chave → categoria, e as categorias aprendidas (apague uma linha para esquecê-la). |
| **Configurações** | Caminho da planilha, liga/desliga a IA, endereço e modelo do Ollama. |

Ao mudar uma categoria na tela de importação, o programa lembra: descrições iguais passam a receber a categoria escolhida.
Ordem de decisão: categorias que você corrigiu → suas regras → regras do programa → IA local.

## 2b. Linha de comando


```bash
python agente_extratos.py extrato.pdf --planilha financas.xlsx
python agente_extratos.py bradesco.ofx nubank.csv caixa.pdf bb.pdf     # vários de uma vez
python agente_extratos.py fatura_nubank.csv                            # detecta que é cartão
python agente_extratos.py extrato.pdf --dry-run                        # só mostra, não grava
python agente_extratos.py extrato.pdf --salvar-texto                   # salva o texto extraído (diagnóstico)
```

| Opção | O que faz |
|---|---|
| `--banco bradesco\|nubank\|itau\|santander\|caixa\|bb` | Força o perfil (por padrão o banco é detectado sozinho) |
| `--ano 2026` | Ano para datas sem ano (Santander/Itaú imprimem `DD/MM`) |
| `--cartao` | Fatura de cartão em que compras vêm positivas |
| `--forcar-llm` | PDF/TXT: ignora o perfil e usa só o LLM |
| `--sem-llm` | Só regras (CSV/OFX/XLSX e PDFs com perfil); sem categorização por IA |
| `--dry-run` | Imprime sem gravar |

## 3. O que baixar de cada banco (do mais ao menos confiável)

**Prefira OFX**: é padrão único, sem ambiguidade de sinal, e todos os bancos abaixo exportam.

| Banco | Melhor formato | Detalhes do parser |
|---|---|---|
| **Nubank** | CSV ou OFX (o app envia PDF, OFX e CSV por e-mail) | Conta: `Data,Valor,Identificador,Descrição` (valor com ponto e sinal). Cartão: `date,category,title,amount` (invertido automaticamente; "Pagamento recebido" é ignorado para não duplicar). O `Identificador` vira o ID, então CSV + OFX do mesmo mês **não duplicam**. PDF cai no LLM. |
| **Bradesco** | OFX ou PDF | PDF: `Data Lançamento Dcto. Crédito Débito Saldo`; débito já vem negativo; **data omitida** em lançamentos do mesmo dia (herdada) e data sozinha na linha são tratadas. CSV: colunas Crédito/Débito separadas. |
| **Caixa** | OFX ou PDF | PDF: `DD/MM/AAAA NRDOC HISTÓRICO VALOR[C/D] SALDO[C/D]` (sufixo C/D colado no valor). Também lê CSV com coluna `Deb_Cred`. |
| **Banco do Brasil** | OFX, PDF ou CSV | PDF: ignora colunas Ag. origem/Lote/Cód. e o nº do documento; sufixo C/D; complementos de PIX (`26/07 12:35 NOME`) entram na descrição. CSV: usa `Valor` + `Detalhamento Hist.`. |
| **Santander** | OFX ou PDF | PDF: data `DD/MM` sem ano (inferido de "junho/2025" ou do período), débito com **sinal no fim** (`567,86-`), data herdada. Ignora a tabela de investimentos do "Extrato Consolidado". |
| **Itaú** | **OFX** (recomendado) ou XLS | PDF/XLS: perfil **experimental** (genérico, valor com sinal). Se algo vier estranho, use OFX. |

## 4. Como o agente garante os números

1. **OFX/CSV/XLSX** → leitura determinística (o LLM nunca toca em valores).
2. **PDF** → perfil do banco (regex). Só se não achar nada, o LLM local extrai em JSON, e cada valor é conferido contra o texto original.
3. **Conferência pelo saldo** (Bradesco, Caixa, BB, Santander): o agente soma *saldo anterior + lançamentos* e compara com o saldo impresso.
   - Sinal trocado e o saldo prova → **corrige sozinho** (anotado em `Obs.`).
   - Saldo não bate → linha amarela com `Status = REVISAR` e o motivo em `Obs.`.
4. **Sem duplicar**: reimportar o mesmo extrato não repete linhas.
5. **Categoria**: regras → cache (`categorias_cache.json`) → LLM só para o que sobrar.

A planilha tem as abas `Transações` (com colunas Banco e Obs.) e `Resumo` (categoria × mês, fórmulas `SUMIFS`).
Se importar fatura do cartão **e** a conta, filtre fora a categoria `Pagamento de fatura` ao somar gastos.

## 5. Testes

```bash
python test_bancos.py     # parsers: 14 cenários (um por banco/formato), sem Ollama
python test_gui.py        # interface: importar → editar → gravar → painel, sem navegador
```

## 6. Limitações (leia)

- Os layouts de PDF foram montados a partir de **extratos reais públicos** (Caixa, BB, Bradesco, Santander) e o formato do CSV do Nubank, mas **o código nunca rodou nos seus arquivos**. Bancos mudam o layout; faça sempre um `--dry-run` primeiro.
- **Itaú** (exceto OFX) e **CSV do Banco do Brasil/Caixa** não puderam ser confirmados com amostra real.
- Extratos de **cartão de crédito em PDF** (Bradesco, Itaú, Santander...) têm layout diferente e não têm perfil: use CSV/OFX da fatura.
- A descrição complementar (nome do favorecido em linhas seguintes) é *best-effort* em PDFs multilinha; data, valor e saldo são conferidos, a descrição não.
- A interface foi testada por execução simulada de cada tela e com o servidor no ar, mas **não pude abri-la em um navegador real** para avaliar o visual. Se algo ficar estranho (colunas, cores), me diga.
- As colunas `Tipo`/`Mês` e a aba `Resumo` da planilha são fórmulas e só mostram valores depois de abrir no Excel/LibreOffice; o Painel calcula por conta própria e não depende disso.
- PDF escaneado precisa de OCR: `ocrmypdf entrada.pdf saida.pdf`.
- Se um PDF sair errado: rode com `--salvar-texto`, abra o `.texto.txt` e veja como as linhas realmente ficaram; esse arquivo é o ponto de partida para ajustar o perfil.
