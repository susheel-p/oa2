from datetime import date
from oa2.execution.broker import LegSpec
from oa2.execution.moomoo_broker import _format_option_code

def main():
    leg1 = LegSpec(
        underlying="SPY",
        expiry=date(2026, 5, 22),
        strike=735.0,
        right="C",
        side=1,
        contracts=5,
        limit_price=None
    )
    print("Formatted SPY leg:", _format_option_code(leg1))
    
    leg2 = LegSpec(
        underlying="AAPL",
        expiry=date(2026, 5, 22),
        strike=302.5,
        right="C",
        side=1,
        contracts=5,
        limit_price=None
    )
    print("Formatted AAPL leg:", _format_option_code(leg2))

if __name__ == "__main__":
    main()
