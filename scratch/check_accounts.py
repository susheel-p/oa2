import os
import moomoo as ft

def main():
    try:
        trd_ctx = ft.OpenSecTradeContext(
            filter_trdmarket=ft.TrdMarket.US,
            host="127.0.0.1",
            port=11111,
            security_firm=ft.SecurityFirm.FUTUINC,
        )
        print("Connected to OpenD.")
        
        # Check simulated accounts
        ret, df = trd_ctx.get_acc_list()
        if ret == 0:
            print("\nAccounts (get_acc_list):")
            print(df.to_string())
        else:
            print("\nFailed to get trade account list:", df)
            
        trd_ctx.close()
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    main()
