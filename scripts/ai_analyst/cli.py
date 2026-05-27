"""CLI interface for AI Analyst."""

import argparse
import sys
from pathlib import Path

from .config import CHROMA_DIR, TRADINGBOT_HOME
from .query_engine import RAGEngine
from .ingestion import ingest_all
from .agents import (
    HealthAgent, ExplainAgent, AnomalyAgent, DailyAgent, PerformanceAgent
)


def setup_parser() -> argparse.ArgumentParser:
    """Set up command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="AI Analyst — Trading Bot Intelligence System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.ai_analyst --ingest
  python -m scripts.ai_analyst --agent health
  python -m scripts.ai_analyst --agent explain --ticker SPY
  python -m scripts.ai_analyst --agent daily
  python -m scripts.ai_analyst --agent anomaly --days 7
  python -m scripts.ai_analyst --query "Why were zero trades approved?"
        """
    )

    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Ingest all data sources into the vector database"
    )

    parser.add_argument(
        "--agent",
        choices=["health", "explain", "anomaly", "daily", "performance"],
        help="Run a specific analysis agent"
    )

    parser.add_argument(
        "--query",
        type=str,
        help="Free-form question about the trading system"
    )

    parser.add_argument(
        "--ticker",
        type=str,
        help="Ticker for explain agent"
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Date for analysis (YYYY-MM-DD format)"
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to analyze (default: 7)"
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show vector database statistics"
    )

    return parser


def ensure_chroma_db():
    """Ensure ChromaDB directory exists."""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)


def cmd_ingest(args):
    """Handle --ingest command."""
    ensure_chroma_db()

    rag = RAGEngine()
    collection = rag.collection

    results = ingest_all(collection)

    print("[green]Ingestion complete![/green]")
    print(f"Knowledge base ready for queries at: {CHROMA_DIR}")

    return 0


def cmd_agent(args):
    """Handle --agent command."""
    agent_name = args.agent

    if agent_name == "health":
        agent = HealthAgent()
        print(agent.run())

    elif agent_name == "explain":
        if not args.ticker:
            print("Error: --ticker required for explain agent")
            return 1
        agent = ExplainAgent()
        print(agent.run(args.ticker, args.date))

    elif agent_name == "anomaly":
        agent = AnomalyAgent()
        print(agent.run(args.days))

    elif agent_name == "daily":
        agent = DailyAgent()
        print(agent.run(args.date))

    elif agent_name == "performance":
        agent = PerformanceAgent()
        print(agent.run(args.ticker, args.date))

    return 0


def cmd_query(args):
    """Handle --query command."""
    rag = RAGEngine()
    rag.query(args.query, n_results=10)
    return 0


def cmd_stats(args):
    """Handle --stats command."""
    try:
        rag = RAGEngine()
        stats = rag.get_collection_stats()
        print(f"\n[cyan]Vector Database Statistics[/cyan]")
        print(f"  Total documents: {stats['total_documents']}")
        print(f"  Collection: {stats['collection_name']}")
        print(f"  Location: {CHROMA_DIR}\n")
    except Exception as e:
        print(f"Error: {e}")
        print("Have you run --ingest yet?")
        return 1
    return 0


def main():
    """Main CLI entry point."""
    parser = setup_parser()
    args = parser.parse_args()

    # Check TRADINGBOT_HOME
    if not TRADINGBOT_HOME.exists():
        print(f"Error: TRADINGBOT_HOME not found: {TRADINGBOT_HOME}")
        print("Please set TRADINGBOT_HOME in .env or environment")
        return 1

    # Route to appropriate command
    if args.ingest:
        return cmd_ingest(args)
    elif args.agent:
        return cmd_agent(args)
    elif args.query:
        return cmd_query(args)
    elif args.stats:
        return cmd_stats(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
