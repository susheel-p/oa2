import os
import moomoo as ft

def test_order(code):
    ctx = ft.OpenSecTradeContext(
        filter_trdmarket=ft.TrdMarket.US,
        host="127.0.0.1",
        port=11111,
        security_firm=ft.SecurityFirm.FUTUINC
    )
    try:
        print(f"Placing SIMULATE order for {code} on account 2650403...")
        ret, data = ctx.place_order(
            price=0.01,
            qty=1,
            code=code,
            trd_side=ft.TrdSide.BUY,
            order_type=ft.OrderType.MARKET,
            trd_env=ft.TrdEnv.SIMULATE,
            acc_id=2650403
        )
        print(f"Result: ret={ret}")
        print(data)
    finally:
        ctx.close()

def main():
    print("--- Testing with 6-digit strike (US.SPY260522C735000) ---")
    test_order("US.SPY260522C735000")
    print("\n--- Testing with 8-digit strike (US.SPY260522C00735000) ---")
    test_order("US.SPY260522C00735000")

if __name__ == "__main__":
    main()
