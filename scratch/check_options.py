import moomoo as ft
import pandas as pd

def main():
    try:
        quote_ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
        print("Connected to quote context.")
        
        # Get option chain for AAPL
        ret, data = quote_ctx.get_option_chain(
            code="US.AAPL",
            index_option_type=ft.IndexOptionType.NORMAL,
            start="2026-05-22",
            end="2026-05-22",
            option_type=ft.OptionType.ALL
        )
        
        if ret == 0 and data is not None and not data.empty:
            print("\nAAPL rows:")
            print(data[["code", "strike_price", "option_type"]].head(5).to_string())
        else:
            print("\nFailed to get option chain:", data)
            
        quote_ctx.close()
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    main()
