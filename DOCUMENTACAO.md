# Agente Local de Extratos Bancários → Planilha

Documentação completa do projeto. Leia primeiro o **README.md** para o guia rápido; este documento cobre arquitetura, módulos, fluxo de dados, detalhes de implementação e exemplos avançados.

---

## 1. Visão Geral

O projeto é uma aplicação **100% offline** que lê extratos bancários (PDF, CSV, OFX, Excel), categoriza transações (regras → cache → IA local via Ollama), grava em planilha Excel sem duplicar, e exibe um painel com gráficos.

**Bancos com perfil próprio:** Bradesco, Nubank, Itaú, Santander, Caixa Econômica, Banco do Brasil.

**Nenhum dado financeiro sai do seu computador.** A IA roda localmente via Ollama.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                    Interface (Streamlit)                     │
│  app_gui.py  ──►  Importar │ Painel │ Regras │ Configurações  │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│             Lógica da GUI (testável sem navegador)          │
│              gui_core.py (processar, gravar, KPIs)           │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
┌──────────────┐    ┌──────────────────┐
│ agente_extratos.py │  bancos.py    │
│  Núcleo CLI   │    │ Leitores/bancos│
│  • Ollama     │    │  • Transacao   │
│  • Planilha   │    │  • Perfis PDF  │
│  • Categorizar│    │  • CSV/OFX/XLS │
└──────────────┘    └──────────────────┘
```

### Camadas

| Camada | Responsabilidade |
|--------|------------------|
| `app_gui.py` | Interface Streamlit: upload, data_editor, páginas, download |
| `gui_core.py` | Orquestração: ler arquivos, montar DataFrame, gravar planilha, KPIs do painel |
| `agente_extratos.py` | Núcleo: parsers, cliente Ollama, geração de IDs, gravação XLSX, CLI |
| `bancos.py` | Leitura por banco: `Transacao`, perfis regex, CSV/OFX/XLSX, detecção de banco |

---

## 3. Instalação

### Pré-requisitos

- Python 3.9+
- [Ollama](https://ollama.ai) (opcional, apenas para PDFs sem perfil e categorização por IA)
- Modelo: `qwen2.5:7b-instruct` (recomendado) ou `llama3.1:8b`, `qwen2.5:3b-instruct` para PCs fracos

### Passos

```bash
# Linux/macOS
./iniciar.sh

# Windows (duplo clique)
iniciar.bat

# Ou manualmente
pip install -r requirements.txt
streamlit run app_gui.py
```

O launcher cria um `.venv/`, instala dependências e abre o navegador em `http://localhost:8501`.

### Dependências (`requirements.txt`)

| Pacote | Função |
|--------|--------|
| `requests` | Comunicação com Ollama |
| `openpyxl` | Leitura/escrita de planilhas .xlsx |
| `pdfplumber` | Extração de texto de PDFs |
| `pandas` | Leitura de Excel e manipulação de dados |
| `xlrd` | Suporte a arquivos .xls antigos |
| `altair` | Gráficos do painel |
| `streamlit` | Interface gráfica |

---

## 4. Módulos Detalhados

### 4.1 `bancos.py`

Leitores de extratos por banco. Contém:

#### `Transacao` (dataclass)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `data` | `date` | Data da transação |
| `descricao` | `str` | Descrição/lancamento |
| `valor` | `float` | Negativo = saída, positivo = entrada |
| `origem` | `str` | Nome do arquivo de origem |
| `categoria` | `str` | Categoria atribuída |
| `status` | `str` | `"OK"` ou `"REVISAR"` |
| `id` | `str` | ID estável para deduplicação |
| `banco` | `str` | Nome do banco detectado |
| `saldo` | `float\|None` | Saldo após a linha (quando informado) |
| `nota` | `str` | Observações (sinal corrigido, saldo não confere, etc.) |

#### Utilitários

- `normaliza(s)` → remove acentos, lower, colapsa espaços
- `parse_valor(texto)` → converte `1.234,56`, `-50,00`, `199,29C`, `567,86-`, `(50,00)`, `R$ 1.234,56`
- `parse_data(texto, ano_padrao)` → múltiplos formatos, incluindo `DD/MM` sem ano
- `ler_texto_arquivo(caminho)` → tenta utf-8-sig, cp1252, latin-1

#### Detecção de banco (`detectar_banco`)

1. Lê `<BANKID>` do OFX e mapeia via `OFX_BANKID`
2. Busca assinaturas regex no nome do arquivo + primeiros 8000 chars do texto
3. Fallback para "caixa" se encontrar a palavra isolada no início

#### Perfis de PDF/TXT (`PERFIS`)

| Banco | Datas | Herda data | Docs ini | Docs fim | Experimental |
|-------|-------|------------|----------|----------|--------------|
| Bradesco | flex | ✅ | ❌ | ✅ | ❌ |
| Santander | flex | ✅ | ❌ | ✅ | ❌ |
| Caixa | completa | ❌ | ✅ | ❌ | ❌ |
| Banco do Brasil | completa | ❌ | ✅ | ✅ | ❌ |
| Itaú | flex | ❌ | ❌ | ❌ | ✅ |
| Genérico | flex | ❌ | ❌ | ❌ | ✅ |

#### Leitura de tabelas (`tabela_para_transacoes`)

Detecta automaticamente o cabeçalho com colunas de data e valor. Suporta:
- CSV com colunas separadas de débito/crédito
- CSV do Nubank conta: `Data,Valor,Identificador,Descrição`
- CSV do Nubank cartão: `date,category,title,amount` (valor invertido automaticamente)
- Coluna `Deb_Cred` (suffix C/D)
- Complemento de descrição via coluna `Detalhamento Hist.`

#### OFX (`extrair_ofx`)

Suporta tanto contas corrente (`<STMTTRN>`) quanto cartão de crédito (`<CCSTMTTRN>`). Ignora "Pagamento recebido" em faturas para evitar duplicação. Extrai `DTPOSTED`, `TRNAMT`, `MEMO`, `NAME`, `FITID`.

---

### 4.2 `agente_extratos.py`

Núcleo do agente. Contém:

#### `OllamaClient`

- `status()` → consulta `/api/tags` sem travar o programa
- `json_chat()` → envia prompt para `/api/chat` com `format: json`, temperature 0, retry de 3 tentativas, extração de JSON do texto bruto se necessário

#### Extração com LLM (`extrair_com_llm`)

Divide o texto em blocos de 50 linhas. Para cada bloco:
1. Envia ao Ollama com prompt do sistema (`SISTEMA_EXTRACAO`)
2. Anti-alucinação: verifica se o valor extraído aparece no texto original (`valor_aparece_no_texto`)
3. Marca como `REVISAR` se o valor não conferir

#### Categorização (`categorizar`)

Ordem de decisão:
1. **Cache** (`categorias_cache.json`) — descrição exata já vista
2. **Regras do usuário** (`regras_usuario.json`)
3. **Regras embutidas** (`REGRAS` em `agente_extratos.py`)
4. **LLM local** — para o que sobrar (lotes de 20)

#### Planilha (`gravar_planilha`)

- Cria/abre arquivo `.xlsx` com aba `Transações`
- Coluna 9 (I) = ID para deduplicação
- Formatação: moeda, data, cores (linha `REVISAR` em amarelo)
- Reconstroi aba `Resumo` com fórmulas `SUMIFS` (categoria × mês)

#### `processar_arquivo`

Roteador por extensão:
- `.csv` → `bancos.extrair_csv`
- `.ofx/.qfx` → `bancos.extrair_ofx`
- `.xlsx/.xls` → `bancos.extrair_planilha`
- `.pdf/.txt` → perfil regex → fallback LLM

#### CLI (`main`)

```
python agente_extratos.py extrato.pdf --planilha financas.xlsx
python agente_extratos.py bradesco.ofx nubank.csv caixa.pdf   # vários
python agente_extratos.py fatura.csv --cartao                 # fatura de cartão
python agente_extratos.py extrato.pdf --dry-run               # só mostra
python agente_extratos.py extrato.pdf --salvar-texto          # diagnóstico
python agente_extratos.py extrato.pdf --banco bradesco        # forçar perfil
python agente_extratos.py extrato.pdf --ano 2026              # ano para DD/MM
python agente_extratos.py extrato.pdf --forcar-llm            # pular perfil
python agente_extratos.py extrato.pdf --sem-llm               # só regras
```

---

### 4.3 `gui_core.py`

Lógica da interface, independente do Streamlit.

#### `processar`

Recebe dicionário `{nome_arquivo: bytes}`, inicia o cliente Ollama se disponível, processa cada arquivo em temp dir, categoriza todas as transações.

#### `para_dataframe`

Converte lista de `Transacao` em DataFrame para o `st.data_editor`:
- `Incluir`: `True` para novas, `False` para `Revisar` e `Já na planilha`
- Colunas: Data, Descrição, Valor, Categoria, Situação, Obs., Banco, Origem

#### `dataframe_para_transacoes`

Aplica edições do usuário. Preserva o ID original (dedup mantido). Se `status == REVISAR`, força `OK` e adiciona nota "conferido na interface".

#### `gravar`

Salva cache de categorias (se categoria mudou), chama `agente_extratos.gravar_planilha`, captura `PermissionError` (planilha aberta no Excel).

#### Painel

- `carregar_planilha` → lê aba `Transações`, cria colunas `Mês` e `Tipo`
- `kpis` → entradas, saídas, saldo
- `por_categoria` → gastos por categoria (saídas apenas)
- `por_mes` → entradas/saídas mês a mês
- `brl` → formatação brasileira (`-R$ 1.234,50`)

---

### 4.4 `app_gui.py`

Interface Streamlit com 4 páginas.

#### Página: Importar

- Upload de múltiplos arquivos (PDF, CSV, OFX, QFX, XLSX, XLS, TXT)
- Opções: banco (detectar ou forçar), ano, cartão, forçar LLM, salvar texto
- `st.data_editor` para revisar/editar antes de gravar
- Toggle "Selecionar / desmarcar todas as linhas"
- Botão "Gravar N transações" com toast de confirmação
- Download da planilha após gravação

#### Página: Painel

- Filtros: mês, banco, categoria, fora dos totais
- KPIs: entradas, saídas, saldo do período
- Gráfico: gastos por categoria (barras horizontais)
- Gráfico: mês a mês (entradas vs saídas)
- Tabela de transações com busca por descrição
- Botão "Exportar filtrado (CSV)"
- Botão "Reprocessar todas (recategorizar com regras atuais)"
- Aviso se houver linhas com Status `REVISAR`

#### Página: Regras

- Minhas regras: palavra-chave → categoria (contém)
- Categorias aprendidas: cache editável, apague para esquecer
- Regras embutidas do programa (tabela de referência)

#### Página: Configurações

- Caminho da planilha (.xlsx)
- IA local: ligar/desligar, endereço Ollama, seleção de modelo
- Status do Ollama (online/modelo instalado)

---

## 5. Fluxo de Dados (Detalhado)

```
ARQUIVOS DE ENTRADA
    │
    ▼
gui_core.processar()  [arquivos em temp dir]
    │
    ├─► Verifica Ollama (se usar_llm=True)
    │
    ▼
agente_extratos.processar_arquivo(caminho, llm, args)  [por arquivo]
    │
    ├─ ext == .csv      → bancos.extrair_csv()
    ├─ ext == .ofx      → bancos.extra_ofx()
    ├─ ext == .xlsx     → bancos.extrair_planilha()
    └─ ext == .pdf/.txt → extrair_texto_pdf()
                             │
                             ├─ cache .texto.txt (se mais novo)
                             ├─ bancos.detectar_banco()
                             ├─ bancos.extrair_texto_extrato()  [perfil regex]
                             │   ├─ conferir_saldos()           [corrige sinal / REVISAR]
                             │   └─ fallback extrair_com_llm()  [se perfil vazio]
                             │
                             └─ texto salvo se --salvar-texto
    │
    ▼
agente_extratos.gerar_ids(ts)      [ID estável: data|desc|valor|contador]
    │
    ▼
agente_extratos.categorizar(todas, llm, cache_path)
    │  1. cache (descrição exata)
    │  2. regras usuário
    │  3. REGRAS embutidas
    │  4. LLM (lotes de 20)
    │
    ▼
gui_core.para_dataframe(ts, ids_existentes)   [tabela para o editor]
    │
    ▼  [usuário edita: marca Incluir, ajusta categoria, data, valor]
gui_core.gravar(df, originais, cfg, lembrar)
    │  ├─ salva categorias_cache.json (se mudou)
    │  └─ agente_extratos.gravar_planilha(ts, caminho)
    │       ├─ dedup por ID (coluna I)
    │       ├─ formatação (moeda, data, cores)
    │       └─ reconstruir_resumo()  [fórmulas SUMIFS]
    │
    ▼
PLANILHA .xlsx  [abas: Transações, Resumo]
    │
    ▼
gui_core.carregar_planilha()  → Painel (KPIs, gráficos, tabela)
```

---

## 6. Formatos Suportados por Banco

| Banco | Melhor formato | Detalhes |
|-------|---------------|----------|
| **Nubank** | CSV ou OFX | Conta: `Data,Valor,Identificador,Descrição`. Cartão: `date,category,title,amount` (invertido). ID nativo evita duplicação CSV+OFX. PDF cai no LLM. |
| **Bradesco** | OFX ou PDF | PDF: `Data Lançamento Dcto. Crédito Débito Saldo`; débito negativo; data omitida herdada; data sozinha na linha tratada. |
| **Caixa** | OFX ou PDF | PDF: `DD/MM/AAAA NRDOC HISTÓRICO VALOR[C/D] SALDO[C/D]`. CSV com `Deb_Cred`. |
| **Banco do Brasil** | OFX, PDF ou CSV | PDF: ignora Ag/Lote/Cód; sufixo C/D; complementos PIX na descrição. CSV: `Valor` + `Detalhamento Hist.`. |
| **Santander** | OFX ou PDF | PDF: data `DD/MM` sem ano (inferido de "junho/2025"); débito com `-` no fim; data herdada. Ignora tabela de investimentos. |
| **Itaú** | **OFX** (recomendado) ou XLS | PDF/XLS: perfil experimental (genérico). Use OFX se possível. |

---

## 7. Garantia de Números

1. **OFX/CSV/XLSX** → leitura determinística, LLM nunca toca em valores
2. **PDF** → perfil regex do banco; só se não achar nada, LLM extrai em JSON; cada valor conferido contra o texto original
3. **Conferência por saldo** (Bradesco, Caixa, BB, Santander): soma `saldo anterior + lançamentos` e compara com saldo impresso
   - Sinal trocado e saldo prova → corrige sozinho (anotado em `Obs.`)
   - Saldo não bate → linha amarela `Status = REVISAR` com motivo em `Obs.`
4. **Sem duplicar**: reimportar o mesmo extrato não repete linhas (ID estável)
5. **Categoria**: regras → cache → LLM só para o que sobrar

---

## 8. Testes

### `test_bancos.py` — Parsers (41 cenários)

Rode sem Ollama:
```bash
python test_bancos.py
```

Cobre: Nubank CSV/OFX, Bradesco PDF, Santander PDF, Caixa PDF, BB PDF, Itaú genérico, detecção por BANKID/assinatura, `parse_valor`, `parse_data`, `normaliza`, `conferir_saldos`, regras `REGRAS`.

### `test_gui.py` — Interface (lógica + páginas simuladas)

Rode sem Ollama nem navegador:
```bash
python test_gui.py
```

Cobre: processamento de 3 arquivos válidos + 1 inválido, dedup, marcação `Revisar`, gravação, cache aprendido, regras de usuário, KPIs, `brl()`, renderização simulada das 4 páginas via `streamlit.testing`.

---

## 9. Exemplos Práticos

### Importar extrato de vários bancos de uma vez (CLI)

```bash
python agente_extratos.py bradesco.pdf nubank.csv caixa.ofx bb.xlsx --planilha financas.xlsx
```

### Usar apenas regras, sem IA

```bash
python agente_extratos.py extrato.pdf --sem-llm --planilha financas.xlsx
```

### Ver o que seria gravado sem gravar

```bash
python agente_extratos.py extrato.pdf --dry-run
```

### Diagnosticar problema de PDF

```bash
python agente_extratos.py extrato.pdf --salvar-texto
# Abra extrato.texto.txt e veja como as linhas ficaram
```

### Forçar perfil do banco

```bash
python agente_extratos.py desconhecido.pdf --banco santander --planilha financas.xlsx
```

### Fatura de cartão de crédito

```bash
python agente_extratos.py fatura_nubank.csv --cartao --planilha financas.xlsx
```
> Compras vêm positivas na fatura; a flag `--cartao` inverte o sinal para saída.

---

## 10. Limitações Conhecidas

- Layouts de PDF baseados em extratos reais públicos; bancos mudam o layout — faça sempre `--dry-run` primeiro
- Itaú (exceto OFX) e CSV de BB/Caixa não confirmados com amostra real
- PDF de fatura de cartão (Bradesco, Itaú, Santander) não tem perfil: use CSV/OFX da fatura
- Descrição complementar em PDFs multilinha é *best-effort*; data, valor e saldo são conferidos, a descrição não
- PDF escaneado precisa de OCR: `ocrmypdf entrada.pdf saida.pdf`
- Colunas `Tipo`/`Mês` e aba `Resumo` são fórmulas do Excel; só mostram valores após abrir no Excel/LibreOffice. O Painel calcula por conta própria.

---

## 11. Estrutura de Arquivos

```
agenteBanco/
├── README.md                 # Guia rápido de uso
├── DOCUMENTACAO.md           # Este documento
├── requirements.txt          # Dependências Python
├── .gitignore                # Arquivos ignorados pelo git
├── iniciar.bat               # Launcher Windows
├── iniciar.sh                # Launcher Linux/macOS
├── app_gui.py                # Interface Streamlit (4 páginas)
├── gui_core.py               # Lógica da GUI (testável)
├── test_gui.py               # Testes da interface
├── agente_extratos.py        # Núcleo: parsers, LLM, planilha, CLI
├── bancos.py                 # Leitores por banco, Transacao, utilitários
├── test_bancos.py            # Testes dos parsers (41 cenários)
├── config_gui.json           # Configuração da GUI (gerado)
├── categorias_cache.json     # Cache de categorias aprendidas (gerado)
├── regras_usuario.json       # Regras de categoria do usuário (gerado)
├── financas.xlsx             # Planilha de saída (gerada)
└── venv/                     # Ambiente virtual (gerado)
```

---

## 12. Como Contribuir

1. Adicione novos perfis de banco em `bancos.py` (`PERFIS` dict e regex)
2. Adicione novas regras em `REGRAS` em `agente_extratos.py`
3. Adicione cenários de teste em `test_bancos.py`
4. Atualize o `README.md` se mudar a interface ou CLI

---

## 13. Versões

| Data | Mudança |
|------|---------|
| v1.0 | Lançamento inicial: parsers para 6 bancos, GUI Streamlit, Ollama local, planilha com fórmulas |
| v1.1 | Correção: Ollama não exigido para CSV/OFX; coluna ID dinâmica; `altair` no requirements; cache de texto PDF; export CSV do Painel; reprocessar todas; testes de banco |
