import os
from datetime import datetime, timedelta
import moomoo as ft

def main():
    print("Connecting to OpenD...")
    ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        # Find next Friday
        today = datetime.now()
        days_ahead = 4 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_friday = today + timedelta(days=days_ahead)
        exp_date = next_friday.strftime("%Y-%m-%d")
        
        print(f"Fetching option chain for SPY on {exp_date}...")
        ret, data = ctx.get_option_chain(
            "US.SPY",
            index_option_type=ft.IndexOptionType.NORMAL,
            start=exp_date,
            end=exp_date,
            option_type=ft.OptionType.ALL
        )
        if ret == 0:
            print("Successfully fetched option chain!")
            # Filter for strikes close to 735
            filtered = data[(data['strike_price'] >= 730) & (data['strike_price'] <= 745)]
            print(filtered[['code', 'name', 'strike_price', 'option_type']])
        else:
            print(f"Error fetching option chain: ret={ret}, data={data}")
    finally:
        ctx.close()

if __name__ == "__main__":
    main()
