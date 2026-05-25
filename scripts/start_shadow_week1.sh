#!/bin/bash
# Start Week 1 shadow mode testing
# Usage: ./scripts/start_shadow_week1.sh

set -e

echo "==============================================="
echo "OA2 PAPER TRADING VALIDATION - WEEK 1 (SHADOW)"
echo "==============================================="
echo ""
echo "This script will:"
echo "  1. Verify environment setup"
echo "  2. Start signal monitoring (no real trades)"
echo "  3. Print daily checklist"
echo ""
read -p "Ready to start? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Check environment
echo ""
echo "Step 1: Verifying environment..."
if [ ! -f .env ]; then
    echo "❌ ERROR: .env file not found"
    exit 1
fi

if ! grep -q "MOOMOO_USERNAME" .env; then
    echo "❌ ERROR: MOOMOO_USERNAME not in .env"
    exit 1
fi
echo "✓ .env OK"

# Create validation directory
mkdir -p ~/.tradingbot/validation
echo "✓ Validation directory created: ~/.tradingbot/validation"

# Print Week 1 checklist
echo ""
echo "Step 2: Printing Week 1 Shadow Mode Checklist"
python scripts/validate_paper_trading.py --mode shadow --days 5

# Start signal monitoring
echo ""
echo "Step 3: Starting live signal monitoring..."
echo ""
echo "================== LAUNCH INSTRUCTIONS =================="
echo ""
echo "TERMINAL 1 (Signal generation):"
echo "  $ python scripts/paper_trade.py --shadow"
echo ""
echo "TERMINAL 2 (Log monitor):"
echo "  $ tail -f ~/.tradingbot/logs/paper_*.log | grep -E 'consensus_direction|debater_opinion|ENTRY|income'"
echo ""
echo "TERMINAL 3 (Daily metrics):"
echo "  $ python scripts/rag_status.py"
echo ""
echo "========================================================"
echo ""
echo "📋 Daily template: See TESTING_CHECKLIST.md"
echo "📊 Save results to: ~/.tradingbot/validation/shadow_day{N}.json"
echo ""
echo "After 5 trading days, run:"
echo "  python scripts/validate_paper_trading.py --mode paper --week 2 --days 5"
echo ""
