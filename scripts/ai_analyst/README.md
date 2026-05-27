# AI Analyst — Trading Bot Intelligence System

A fully local RAG (Retrieval-Augmented Generation) system for analyzing trading bot logs, decisions, and performance using Ollama + Mistral 7B + ChromaDB.

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements_ai.txt
# OR
pip install chromadb ollama rich
```

### 2. Verify Ollama Models
```bash
ollama list
```

You should see:
- `mistral:7b` — for chat/analysis
- `nomic-embed-text:latest` — for embeddings

If `nomic-embed-text` is missing:
```bash
ollama pull nomic-embed-text
```

### 3. Ingest Trading Data
First time only — builds the vector database from your logs:
```bash
python -m scripts.ai_analyst --ingest
```

This reads from:
- `logs/paper_trade_*.jsonl` — per-ticker trading decisions
- `logs/executions_*.jsonl` — broker execution events
- `logs/daemon.log` — system logs
- `reports/**/*.md` — premarket/postmarket reports
- `debater_logs/opinions.jsonl` — per-debater signals

### 4. Run Queries

**Health check:**
```bash
python -m scripts.ai_analyst --agent health
```

**Explain a ticker decision:**
```bash
python -m scripts.ai_analyst --agent explain --ticker SPY
python -m scripts.ai_analyst --agent explain --ticker NVDA --date 2026-05-26
```

**Find anomalies:**
```bash
python -m scripts.ai_analyst --agent anomaly
python -m scripts.ai_analyst --agent anomaly --days 7
```

**Daily summary:**
```bash
python -m scripts.ai_analyst --agent daily
python -m scripts.ai_analyst --agent daily --date 2026-05-26
```

**Trading performance:**
```bash
python -m scripts.ai_analyst --agent performance
python -m scripts.ai_analyst --agent performance --ticker SPY
```

**Free-form Q&A:**
```bash
python -m scripts.ai_analyst --query "Why were zero trades approved yesterday?"
python -m scripts.ai_analyst --query "Which regime gives the best hit rates?"
python -m scripts.ai_analyst --query "Show me debater conflicts on 2026-05-26"
```

**Check database stats:**
```bash
python -m scripts.ai_assistant --stats
```

## Architecture

```
Trading Logs             Data Ingestion          Vector DB            LLM
─────────────────        ──────────────          ─────────            ────
paper_trade_*.jsonl  ──► chunk + flatten ──┐
executions_*.jsonl       (json → text)     │
exit_alerts_*.jsonl                        ├──► embed with    ──► ChromaDB
daemon.log                                 │    nomic-embed      (local)
pipeline.log                               │    
reports/**/*.md     ────────────────────────
opinions.jsonl
knowledge_base.json
```

Query flow:
```
Question ──► Embed ──► ChromaDB.query() ──► Top-K docs ──► Mistral 7B ──► Answer
                       (vector similarity)    (context)     (streaming)
```

## System Components

### `config.py`
- Paths to data sources (logs, reports, debater logs)
- Ollama model configuration
- Chunking parameters (800 chars, 100 char overlap)

### `ingestion.py`
- Reads all JSONL, markdown, and JSON files
- Flattens complex records to readable text
- Hash-based deduplication
- Chunks large files and embeds with `nomic-embed-text`

### `query_engine.py`
- RAGEngine class: manages ChromaDB collection
- Embeds queries, retrieves top-K documents, streams response from Mistral
- Filters support (by date, ticker, source)

### `agents.py`
Six named analysis agents:
- **health** — daemon status, last scan, errors
- **explain** — why a ticker was approved/rejected
- **anomaly** — unusual patterns in recent logs
- **daily** — narrative daily summary
- **performance** — hit rates by ticker/regime
- **ask** (via --query) — free-form Q&A

### `cli.py`
- Argument parser for all commands
- Routes to appropriate agent or query engine

## Performance Notes

- **Ingestion speed**: ~10-15 documents/second (dominated by embedding time)
  - Full ingest of 3000+ records takes ~5-10 minutes
  - Run once, then incremental re-ingests are optional
  
- **Query latency**: ~5-15 seconds
  - 500ms embedding (question)
  - 100ms vector DB query (retrieval)
  - 4-14s Mistral response generation (7B model is chatty)

- **Memory usage**: ~2GB active (Mistral + embeddings + ChromaDB in memory)

## Configuration

Edit `config.py` to change:
- `CHUNK_SIZE` — smaller = more granular (default 800)
- `OLLAMA_MODEL` — use `gemma4:latest` for faster responses (less accurate)
- `EMBED_MODEL` — only tested with `nomic-embed-text`

Set TRADINGBOT_HOME in `.env`:
```
TRADINGBOT_HOME=C:\Users\pamed\Susheel\tradingbot-docker
```

## Troubleshooting

### "No relevant documents found"
- Run `--ingest` to populate the database
- Or increase `n_results` in query (default 8)

### Ollama connection errors
- Ensure Ollama is running: `ollama serve`
- Check port 11434 is accessible

### Slow queries
- Mistral 7B is inherently slow. For faster (lower quality) responses, use:
  ```python
  OLLAMA_MODEL = "phi4-mini:latest"  # 3.8B, ~2x faster
  ```

### Encoding errors in logs
- Already handled with `errors='ignore'` in health agent
- Some logs may have binary data — this is ignored safely

## Example Sessions

### Session 1: Daily Health Check
```bash
python -m scripts.ai_analyst --agent health
python -m scripts.ai_analyst --agent daily
```

### Session 2: Trader Explains Bad Day
```bash
python -m scripts.ai_analyst --query "Why did we approve zero trades on 2026-05-26?"
python -m scripts.ai_analyst --agent anomaly --days 1
```

### Session 3: Backtester Analysis
```bash
python -m scripts.ai_analyst --agent performance
python -m scripts.ai_analyst --query "Which debater has the worst accuracy?"
python -m scripts.ai_analyst --query "Show regimes where flow debater consistently fails"
```

## Cost

**Zero cloud cost.** Everything runs locally:
- Ollama: free, open-source
- ChromaDB: free, embedded
- All processing on your 16GB laptop

## Notes

- System prompt focuses LLM on trading context (risk, signal quality, regime awareness)
- All data is read-only; no modifications to logs or reports
- Vector DB persists in `$TRADINGBOT_HOME/chroma_db/` for reuse
- Designed for interactive Q&A, not automated batch processing

## Files

```
scripts/ai_analyst/
  __init__.py         — package marker
  __main__.py         — CLI entry point
  config.py           — paths, models, settings
  ingestion.py        — data loading and chunking
  query_engine.py     — RAG core (ChromaDB + Ollama)
  agents.py           — six analysis agents
  cli.py              — argument parser and routing
  README.md           — this file

requirements_ai.txt   — isolated dependencies
```
