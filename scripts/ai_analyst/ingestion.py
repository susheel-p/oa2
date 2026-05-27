"""Data ingestion and chunking for the AI Analyst."""

import json
import hashlib
from pathlib import Path
from typing import Generator
from datetime import datetime
import re

from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import (
    LOGS_DIR, REPORTS_DIR, DEBATER_LOG_FILE, KB_FILE, DAEMON_LOG,
    CHUNK_SIZE, OVERLAP, COLLECTION_NAME
)


def hash_content(content: str) -> str:
    """Generate a hash for content to track ingestion."""
    return hashlib.md5(content.encode()).hexdigest()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[tuple[str, int]]:
    """Split text into overlapping chunks. Returns list of (chunk, start_position)."""
    if len(text) <= chunk_size:
        return [(text, 0)]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        chunks.append((chunk, start))
        start = end - overlap
        if start >= len(text) - overlap:
            break

    return chunks


def flatten_jsonl_record(record: dict, source_date: str = None) -> str:
    """Flatten a JSONL record into readable text."""
    lines = []

    if "ticker" in record:
        lines.append(f"Ticker: {record['ticker']}")
    if "ts" in record:
        lines.append(f"Timestamp: {record['ts']}")
    if "status" in record:
        lines.append(f"Status: {record['status']}")
    if "direction" in record:
        lines.append(f"Direction: {record['direction']}")
    if "conviction" in record:
        lines.append(f"Conviction: {record['conviction']}")
    if "debater_name" in record:
        lines.append(f"Debater: {record['debater_name']}")
    if "reasoning" in record:
        lines.append(f"Reasoning: {record['reasoning']}")
    if "consensus_score" in record:
        lines.append(f"Consensus Score: {record['consensus_score']}")
    if "p_bull" in record:
        lines.append(f"P(Bull): {record['p_bull']}")
    if "event" in record:
        lines.append(f"Event: {record['event']}")
    if "sizing_reject_reason" in record:
        lines.append(f"Reject Reason: {record['sizing_reject_reason']}")
    if "sizing_reject_gate" in record:
        lines.append(f"Reject Gate: {record['sizing_reject_gate']}")
    if "contracts" in record:
        lines.append(f"Contracts: {record['contracts']}")
    if "pnl" in record:
        lines.append(f"P&L: {record['pnl']}")
    if "hit" in record:
        lines.append(f"Hit: {record['hit']}")

    return " | ".join(lines)


def ingest_paper_trade_logs(collection) -> int:
    """Ingest paper_trade_*.jsonl files."""
    count = 0

    for log_file in sorted(LOGS_DIR.glob("paper_trade_*.jsonl")):
        date_str = log_file.stem.replace("paper_trade_", "")

        with open(log_file) as f:
            for line_no, line in enumerate(f):
                try:
                    record = json.loads(line.strip())
                    content = flatten_jsonl_record(record, date_str)

                    doc_id = f"paper_trade_{date_str}_{line_no}_{hash_content(content)[:8]}"

                    collection.add(
                        ids=[doc_id],
                        documents=[content],
                        metadatas=[{
                            "source": "paper_trade",
                            "date": date_str,
                            "ticker": record.get("ticker", ""),
                            "status": record.get("status", ""),
                            "direction": record.get("direction", ""),
                        }]
                    )
                    count += 1
                except json.JSONDecodeError:
                    continue

    return count


def ingest_execution_logs(collection) -> int:
    """Ingest executions_*.jsonl files."""
    count = 0

    for log_file in sorted(LOGS_DIR.glob("executions_*.jsonl")):
        date_str = log_file.stem.replace("executions_", "")

        with open(log_file) as f:
            for line_no, line in enumerate(f):
                try:
                    record = json.loads(line.strip())
                    content = flatten_jsonl_record(record, date_str)

                    doc_id = f"execution_{date_str}_{line_no}_{hash_content(content)[:8]}"

                    collection.add(
                        ids=[doc_id],
                        documents=[content],
                        metadatas=[{
                            "source": "execution",
                            "date": date_str,
                            "ticker": record.get("ticker", ""),
                            "event": record.get("event", ""),
                        }]
                    )
                    count += 1
                except json.JSONDecodeError:
                    continue

    return count


def ingest_debater_opinions(collection) -> int:
    """Ingest debater_logs/opinions.jsonl."""
    count = 0

    if not DEBATER_LOG_FILE.exists():
        return count

    with open(DEBATER_LOG_FILE) as f:
        for line_no, line in enumerate(f):
            try:
                record = json.loads(line.strip())
                content = flatten_jsonl_record(record)

                doc_id = f"opinion_{line_no}_{hash_content(content)[:8]}"

                collection.add(
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[{
                        "source": "debater_opinion",
                        "ticker": record.get("ticker", ""),
                        "debater": record.get("debater_name", ""),
                        "direction": record.get("direction", ""),
                        "conviction": str(record.get("conviction", "")),
                    }]
                )
                count += 1
            except json.JSONDecodeError:
                continue

    return count


def ingest_reports(collection) -> int:
    """Ingest markdown reports from reports/**/*.md."""
    count = 0

    for report_file in sorted(REPORTS_DIR.rglob("*.md")):
        if not report_file.is_file():
            continue

        try:
            with open(report_file, encoding="utf-8") as f:
                content = f.read()

            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(report_file))
            date_str = date_match.group(1) if date_match else "unknown"

            report_type = report_file.stem

            for chunk, pos in chunk_text(content):
                doc_id = f"report_{date_str}_{report_type}_{pos}_{hash_content(chunk)[:8]}"

                collection.add(
                    ids=[doc_id],
                    documents=[chunk],
                    metadatas=[{
                        "source": "report",
                        "report_type": report_type,
                        "date": date_str,
                    }]
                )
                count += 1
        except Exception:
            continue

    return count


def ingest_daemon_log(collection) -> int:
    """Ingest daemon.log."""
    count = 0

    if not DAEMON_LOG.exists():
        return count

    try:
        with open(DAEMON_LOG, encoding="utf-8") as f:
            content = f.read()

        for chunk, pos in chunk_text(content):
            doc_id = f"daemon_log_{pos}_{hash_content(chunk)[:8]}"

            collection.add(
                ids=[doc_id],
                documents=[chunk],
                metadatas=[{
                    "source": "daemon_log",
                }]
            )
            count += 1
    except Exception:
        pass

    return count


def ingest_knowledge_base(collection) -> int:
    """Ingest knowledge_base.json."""
    count = 0

    if not KB_FILE.exists():
        return count

    try:
        with open(KB_FILE) as f:
            kb = json.load(f)

        if "tickers" in kb:
            for ticker, data in kb["tickers"].items():
                content = f"Knowledge Base Entry: {ticker}\n{json.dumps(data, indent=2)}"
                doc_id = f"kb_{ticker}_{hash_content(content)[:8]}"

                collection.add(
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[{
                        "source": "knowledge_base",
                        "ticker": ticker,
                    }]
                )
                count += 1
    except Exception:
        pass

    return count


def ingest_all(collection) -> dict:
    """Ingest all data sources into the collection."""
    results = {}

    print("\n[cyan]Starting data ingestion...[/cyan]\n")

    results["paper_trade"] = ingest_paper_trade_logs(collection)
    print(f"[+] Ingested {results['paper_trade']} paper_trade records")

    results["executions"] = ingest_execution_logs(collection)
    print(f"[+] Ingested {results['executions']} execution records")

    results["debater_opinions"] = ingest_debater_opinions(collection)
    print(f"[+] Ingested {results['debater_opinions']} debater opinion records")

    results["reports"] = ingest_reports(collection)
    print(f"[+] Ingested {results['reports']} report chunks")

    results["daemon_log"] = ingest_daemon_log(collection)
    print(f"[+] Ingested {results['daemon_log']} daemon log chunks")

    results["knowledge_base"] = ingest_knowledge_base(collection)
    print(f"[+] Ingested {results['knowledge_base']} knowledge base entries")

    total = sum(results.values())
    print(f"\n[green]Total indexed: {total} documents[/green]\n")

    return results
