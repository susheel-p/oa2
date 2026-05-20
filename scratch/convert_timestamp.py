import datetime
from zoneinfo import ZoneInfo

def main():
    et = ZoneInfo("America/New_York")
    ts = 1779213526.7473173
    print("Timestamp ET:", datetime.datetime.fromtimestamp(ts, tz=et))

if __name__ == "__main__":
    main()
