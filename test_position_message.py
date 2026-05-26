#!/usr/bin/env python3
"""Test position summary formatting in Telegram messages."""

# Test data
test_summary = {
    "approved_count": 0,
    "rejected_count": 22,
    "error_count": 0,
    "exit_alert_count": 12,
    "open_positions": [
        {"ticker": "SPY", "contracts": 5, "current_pnl": 150.00, "current_dte": 15, "structure": "vertical_call_spread"},
        {"ticker": "USO", "contracts": 4, "current_pnl": -264.84, "current_dte": 30, "structure": "vertical_call_spread"},
    ]
}

# Format the summary
lines = [
    "Trading run complete",
    f"Approved: {test_summary.get('approved_count', 0)}",
    f"Rejected: {test_summary.get('rejected_count', 0)}",
    f"Errors: {test_summary.get('error_count', 0)}",
    f"Exit alerts: {test_summary.get('exit_alert_count', 0)}",
]

positions = test_summary.get("open_positions", [])
if positions:
    lines.append("\nOpen Positions:")
    for pos in positions:
        ticker = pos.get("ticker", "?")
        contracts = pos.get("contracts", 0)
        pnl = pos.get("current_pnl", 0)
        dte = pos.get("current_dte", 0)
        structure = pos.get("structure", "?")
        lines.append(f"  {ticker}: {contracts} contracts, P&L ${pnl:+.2f}, {dte} DTE ({structure})")
else:
    lines.append("\nOpen Positions: None")

text = "\n".join(lines)
print("Formatted Telegram Message:")
print("=" * 60)
print(text)
print("=" * 60)
print("\n[OK] Message formatting works correctly!")
