import os
import moomoo as ft
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

def main():
    print("Connecting to OpenD with US market filter...")
    ctx = ft.OpenSecTradeContext(
        filter_trdmarket=ft.TrdMarket.US,
        host="127.0.0.1",
        port=11111,
        security_firm=ft.SecurityFirm.FUTUINC
    )
    try:
        print("Fetching trading accounts...")
        ret, data = ctx.get_acc_list()
        if ret == 0:
            print("Successfully fetched accounts!")
            print(data)
        else:
            print(f"Error fetching accounts: ret={ret}, data={data}")
    finally:
        ctx.close()

if __name__ == "__main__":
    main()
