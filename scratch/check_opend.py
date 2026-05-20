import moomoo as ft

def main():
    ctx = ft.OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        ret, data = ctx.get_global_state()
        if ret == 0:
            print("Global State:")
            for k, v in data.items():
                print(f"  {k}: {v}")
        else:
            print(f"Error getting global state: ret={ret}, data={data}")
    finally:
        ctx.close()

if __name__ == "__main__":
    main()
