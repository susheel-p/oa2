# AI Analyst — Local Q&A Over Trading Logs

A local RAG (Retrieval-Augmented Generation) system that lets you ask questions about your trading history. Everything runs on your laptop — no cloud, no cost.

---

## What It Does

- **Ingests** all logs, reports, debater opinions, and execution records
- **Embeds** them into a local vector database (ChromaDB)
- **Answers questions** using Mistral 7B via Ollama

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements_ai.txt
```

### 2. Verify Ollama models

```bash
ollama list
# Should show: mistral:7b and nomic-embed-text:latest
# If nomic-embed-text missing: ollama pull nomic-embed-text
```

### 3. Ingest your data (one-time, ~5-10 minutes)

```bash
python -m scripts.ai_analyst --ingest
```

### 4. Start asking

```bash
python -m scripts.ai_analyst --agent health
python -m scripts.ai_analyst --agent daily
python -m scripts.ai_analyst --query "Why were zero trades approved yesterday?"
```

---

## Commands

| Command | What It Does |
|---------|-------------|
| `--agent health` | Daemon status, last scan, error count |
| `--agent daily` | AI narrative of today's signals and trades |
| `--agent daily --date YYYY-MM-DD` | Narrative for a specific date |
| `--agent explain --ticker SPY` | Why was SPY approved or rejected? |
| `--agent anomaly --days 7` | Unusual conviction drops, error spikes, regime changes |
| `--agent performance` | Hit rates, P&L distribution, best regimes |
| `--query "..."` | Free-form question over all indexed data |

---

## System Specs

| Aspect | Details |
|--------|---------|
| LLM | Mistral 7B (4.4 GB, runs on 16 GB laptop) |
| Embeddings | nomic-embed-text (274 MB, 384-dim) |
| Vector DB | ChromaDB embedded (no server) |
| Cost | Free (fully local) |
| Query time | 5–15 seconds |
| Memory | ~2 GB active |

---

## Configuration

All settings in `scripts/ai_analyst/config.py`:

```python
TRADINGBOT_HOME = Path(os.getenv("TRADINGBOT_HOME"))
OLLAMA_MODEL = "mistral"          # or "gemma4:latest" (faster, less accurate)
EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 800                  # chars per document chunk
OVERLAP = 100
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "No relevant documents found" | Run `--ingest` first |
| "Ollama connection refused" | Run `ollama serve` |
| Slow queries (>20s) | Try `OLLAMA_MODEL = "gemma4:latest"` |
| "Collection expecting embedding" | Delete `chroma_db/` folder; will recreate on next ingest |

---

## What It Does NOT Do

- No forward-looking signals — purely historical analysis
- No trade modification — read-only access to logs
- No automated reports — interactive Q&A only

---

## Files

```
scripts/ai_analyst/
  __main__.py      CLI entry point
  config.py        Paths and model settings
  ingestion.py     Log reading, chunking, embedding
  query_engine.py  ChromaDB + Ollama integration
  agents.py        6 analysis agents
  cli.py           Command routing
requirements_ai.txt  Dependencies (chromadb, ollama, rich)
```
