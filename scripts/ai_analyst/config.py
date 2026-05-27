"""Configuration for AI Analyst RAG system."""

from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

TRADINGBOT_HOME = Path(os.getenv("TRADINGBOT_HOME", "."))
DATA_ROOT = TRADINGBOT_HOME

LOGS_DIR = DATA_ROOT / "logs"
REPORTS_DIR = DATA_ROOT / "reports"
DEBATER_LOGS_DIR = DATA_ROOT / "debater_logs"
CHROMA_DIR = DATA_ROOT / "chroma_db"

DEBATER_LOG_FILE = DEBATER_LOGS_DIR / "opinions.jsonl"
KB_FILE = DATA_ROOT / "knowledge_base.json"
DAEMON_LOG = LOGS_DIR / "daemon.log"
WATCHDOG_STATE = LOGS_DIR / "watchdog_state.json"

OLLAMA_MODEL = "mistral"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL = "http://localhost:11434"

CHUNK_SIZE = 800
OVERLAP = 100

COLLECTION_NAME = "trading_data"

SYSTEM_PROMPT = """You are an expert AI analyst for an automated options trading system.
You have access to trading logs, debater opinions, execution records, and performance reports.

Your role:
- Interpret trading decisions and explain the reasoning behind them
- Identify patterns and anomalies in trading behavior
- Provide concise, data-driven insights
- Acknowledge uncertainty and limitations in the data
- Focus on actionable information and risk awareness

When analyzing data:
1. Look at multiple data sources (debater opinions, execution logs, reports)
2. Consider regime context when available
3. Reference specific metrics (conviction, p_bull, Greeks, P&L)
4. Be specific about dates and tickers

Keep responses concise and structured."""
