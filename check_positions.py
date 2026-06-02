#!/usr/bin/env python3
"""Check how many positions are actually on broker."""
from tradingbot.execution.moomoo_broker import MoomooBroker

broker = MoomooBroker()
positions = broker.query_positions()
print(f'Total positions on broker: {len(positions)}')
for i, p in enumerate(positions, 1):
    print(f'{i:2d}. {p.underlying:6s} {p.right:1s} ${p.strike:7.2f} exp {p.expiry} qty {p.quantity:3d} P&L ${p.pnl:+8.2f}')
