import moomoo as ft
import pandas as pd

pd.set_option('display.max_rows', 100)

def main():
    ctx = ft.OpenSecTradeContext(
        filter_trdmarket=ft.TrdMarket.US,
        host="127.0.0.1",
        port=11111,
        security_firm=ft.SecurityFirm.FUTUINC
    )
    try:
        # We also need a quote context to get options
        quote_ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
        quote_ctx.start()
        try:
            print("Fetching SPY option chain...")
            ret, data = quote_ctx.get_option_chain(
                "US.SPY",
                index_option_type=ft.IndexOptionType.NORMAL,
                start="2026-05-22",
                end="2026-05-22"
            )
            if ret == 0:
                print("SPY option codes sample:")
                print(data[['code', 'strike_price']].head(10))
                print(data[['code', 'strike_price']].tail(10))
            else:
                print("Failed to fetch SPY option chain:", data)

            print("\nFetching a lower priced ticker (e.g. F)...")
            ret, data = quote_ctx.get_option_chain(
                "US.F",
                index_option_type=ft.IndexOptionType.NORMAL,
                start="2026-05-22",
                end="2026-05-22"
            )
            if ret == 0:
                print("F option codes sample:")
                print(data[['code', 'strike_price']].head(10))
            else:
                print("Failed to fetch F option chain:", data)
        finally:
            quote_ctx.close()
    finally:
        ctx.close()

if __name__ == "__main__":
    main()
