"""Analysis agents for trading system insights."""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from .config import (
    LOGS_DIR, REPORTS_DIR, DAEMON_LOG, WATCHDOG_STATE, DEBATER_LOG_FILE, KB_FILE
)
from .query_engine import RAGEngine


class HealthAgent:
    """Monitor daemon health and system status."""

    def __init__(self):
        self.daemon_log = DAEMON_LOG
        self.watchdog_state = WATCHDOG_STATE

    def run(self) -> str:
        """Analyze daemon health."""
        report = ["# Daemon Health Check\n"]

        # Check daemon log
        if not self.daemon_log.exists():
            report.append("Warning: No daemon.log found\n")
        else:
            try:
                with open(self.daemon_log, encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()

                report.append(f"[OK] Daemon log size: {len(lines)} lines")

                # Get last 5 lines
                if lines:
                    report.append("\nLast 5 log entries:")
                    for line in lines[-5:]:
                        report.append(f"  {line.strip()}")

                # Look for errors
                error_count = sum(1 for line in lines if "ERROR" in line or "error" in line)
                if error_count > 0:
                    report.append(f"\n[!] Found {error_count} error lines in daemon.log")

                # Look for timeouts
                timeout_count = sum(1 for line in lines if "timeout" in line.lower())
                if timeout_count > 0:
                    report.append(f"\n[!] Found {timeout_count} timeout lines")

            except Exception as e:
                report.append(f"Error reading daemon.log: {e}\n")

        # Check watchdog state
        if self.watchdog_state.exists():
            try:
                with open(self.watchdog_state) as f:
                    state = json.load(f)

                report.append(f"\nWatchdog state:")
                report.append(f"  Last heartbeat: {state.get('last_heartbeat_time', 'N/A')}")
                report.append(f"  Last alert: {state.get('last_alert_time', 'N/A')}")
                report.append(f"  Was stale: {state.get('was_stale', False)}")

            except Exception as e:
                report.append(f"Error reading watchdog state: {e}\n")

        # Check recent log files
        report.append("\nRecent log files:")
        for log_file in sorted(LOGS_DIR.glob("*.jsonl"))[-5:]:
            size_kb = log_file.stat().st_size / 1024
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            report.append(f"  {log_file.name}: {size_kb:.1f}KB ({mtime})")

        return "\n".join(report)


class ExplainAgent:
    """Explain trading decisions for specific tickers."""

    def __init__(self):
        self.rag = RAGEngine()

    def run(self, ticker: str, date: Optional[str] = None) -> str:
        """Explain decision for a ticker on a given date."""
        filters = {"ticker": ticker}
        if date:
            filters["date"] = date

        question = f"Explain the trading decision for {ticker} on {date or 'today'}. " \
                   f"What did each debater think? Why was it approved or rejected?"

        report = [f"# Decision Explanation: {ticker}\n"]
        report.append(f"Query date: {date or 'latest'}\n")
        report.append("---\n")

        # Use RAG to explain
        self.rag.query(question, where=filters if date else None)

        return "\n".join(report)


class AnomalyAgent:
    """Detect unusual patterns in trading logs."""

    def __init__(self):
        self.rag = RAGEngine()

    def run(self, days: int = 7) -> str:
        """Scan for anomalies in recent logs."""
        report = [f"# Anomaly Detection — Last {days} days\n"]

        question = f"In the last {days} days of trading logs, identify any unusual patterns: " \
                   f"1) Sudden spikes in error rates, 2) Unusual regime changes, 3) Conviction drops, " \
                   f"4) Rejected trades with similar reasons (systemic issues), 5) Timeout or timeout patterns, " \
                   f"6) Repeated broker errors. Be specific with dates and counts."

        report.append("---\n")
        self.rag.query(question, n_results=12)

        return "\n".join(report)


class DailyAgent:
    """Generate daily narrative summary."""

    def __init__(self):
        self.rag = RAGEngine()
        self.reports_dir = REPORTS_DIR

    def run(self, date: Optional[str] = None) -> str:
        """Generate daily summary from reports and logs."""
        if not date:
            # Find most recent date
            dates = [d.name for d in self.reports_dir.iterdir() if d.is_dir()]
            if not dates:
                return "No reports found."
            date = sorted(dates)[-1]

        report = [f"# Daily Trading Summary — {date}\n"]
        report.append("---\n")

        # Check for premarket and postmarket reports
        date_dir = self.reports_dir / date
        has_premarket = (date_dir / "premarket.md").exists()
        has_postmarket = (date_dir / "postmarket.md").exists()

        question = f"Summarize the trading day {date}: "
        if has_premarket:
            question += "What was approved premarket and why? "
        if has_postmarket:
            question += "What trades were executed? What positions are open? Any exits? "
        question += "What was the net P&L impact? Any notable patterns or concerns?"

        self.rag.query(question, where={"date": date})

        return "\n".join(report)


class PerformanceAgent:
    """Analyze trading performance and hit rates."""

    def __init__(self):
        self.rag = RAGEngine()
        self.kb_file = KB_FILE

    def run(self, ticker: Optional[str] = None, regime: Optional[str] = None) -> str:
        """Analyze performance metrics."""
        report = ["# Trading Performance Analysis\n"]

        filters = {}
        if ticker:
            filters["ticker"] = ticker
        if regime:
            filters["regime"] = regime

        question = "Analyze trading performance: "
        if ticker:
            question += f"For {ticker}, what is the hit rate, average P&L, " \
                       f"and trade count? Any patterns in when this ticker works well?"
        else:
            question += "Which tickers have the best hit rates? " \
                       f"How does performance vary by regime? " \
                       f"What's the overall P&L distribution?"

        report.append("---\n")
        self.rag.query(question, n_results=10, where=filters if filters else None)

        return "\n".join(report)
