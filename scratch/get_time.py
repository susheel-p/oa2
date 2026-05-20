import datetime
from zoneinfo import ZoneInfo

def main():
    et = ZoneInfo("America/New_York")
    now_local = datetime.datetime.now()
    now_et = datetime.datetime.now(et)
    print("Local time:", now_local)
    print("ET time:", now_et)

if __name__ == "__main__":
    main()
