#!/usr/bin/env python3
"""Quick script to pull current open positions from moomoo."""

import os
import sys
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).parent.parent / ".env")

try:
    import moomoo as ft
    from moomoo import OpenSecTradeContext, TrdMarket, SecurityFirm
except ImportError:
    print("ERROR: moomoo SDK not installed. Run: pip install moomoo-api")
    sys.exit(1)


def get_positions():
    """Connect to moomoo and fetch all open positions."""
    # Get credentials from env
    host = os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("MOOMOO_OPEND_PORT", 11111))
    account_id = int(os.getenv("MOOMOO_ACCOUNT_ID", "0"))
    trade_env = ft.TrdEnv.SIMULATE  # Default to simulate mode

    print(f"Connecting to moomoo OpenD at {host}:{port}")
    print(f"Account ID: {account_id}")
    print(f"Trade environment: SIMULATE")
    print()

    try:
        # Create trade context
        trd_ctx = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.US,
            host=host,
            port=port,
            security_firm=SecurityFirm.FUTUINC,
        )
        print("[OK] Connected to OpenD")

        # Query positions
        ret, df = trd_ctx.position_list_query(
            trd_env=trade_env,
            acc_id=account_id,
        )

        if ret != 0:
            print(f"ERROR: position_list_query returned {ret}")
            return

        if df is None or df.empty:
            print("[OK] No open positions")
            return

        print(f"[OK] Found {len(df)} open position(s):\n")

        # Format output
        for idx, row in df.iterrows():
            code = row.get("code", "N/A")
            qty = row.get("qty", 0)
            avg_price = row.get("cost_price", 0.0)
            market_price = row.get("market_price", 0.0)
            pnl = row.get("pl_value", 0.0)
            pnl_pct = row.get("pl_ratio", 0.0)

            print(f"  Code:          {code}")
            print(f"  Quantity:      {qty}")
            print(f"  Avg Entry:     ${avg_price:.2f}")
            print(f"  Market Price:  ${market_price:.2f}")
            print(f"  P&L:           ${pnl:+.2f} ({pnl_pct:+.1%})")
            print()

        trd_ctx.close()

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    get_positions()
