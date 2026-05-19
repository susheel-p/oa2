import json
from collections import defaultdict
import numpy as np

def load_weekly_metrics(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
    
    # Group returns by week
    weekly_rets = defaultdict(list)
    for d in data["days"]:
        # Find ISO week: we can parse date string YYYY-MM-DD
        dt_str = d["date"]
        # Convert YYYY-MM-DD to year and week number
        from datetime import datetime
        dt = datetime.strptime(dt_str, "%Y-%m-%d")
        year, week, _ = dt.isocalendar()
        week_key = f"{year}-W{week:02d}"
        
        # Check if trade occurred (consensus direction is not NEUTRAL)
        active = (d["consensus_direction"] != "NEUTRAL")
        ret = d["next_day_return"] if active else 0.0
        weekly_rets[week_key].append(ret)
        
    results = {}
    for week_key, rets in weekly_rets.items():
        total_ret = sum(rets)
        active_count = sum(1 for r in rets if r != 0.0)
        # Compute Sharpe of active trades in the week
        active_rets = [r for r in rets if r != 0.0]
        if active_count > 1 and np.std(active_rets) > 1e-6:
            sharpe = np.mean(active_rets) / np.std(active_rets) * np.sqrt(252)
        else:
            sharpe = 0.0
        results[week_key] = {
            "return": total_ret,
            "active": active_count,
            "sharpe": sharpe
        }
    return results

def main():
    base_file = r"C:\Users\pamed\.oa2\backtest\results_20260519_164213.json"
    adapt_file = r"C:\Users\pamed\.oa2\backtest\results_20260519_164245.json"
    
    base = load_weekly_metrics(base_file)
    adapt = load_weekly_metrics(adapt_file)
    
    all_weeks = sorted(list(set(base.keys()) | set(adapt.keys())))
    
    print("| Week | Baseline Active | Adaptive Active | Baseline Return | Adaptive Return | Baseline Sharpe | Adaptive Sharpe | Diff (Return) |")
    print("|---|---|---|---|---|---|---|---|")
    
    total_base_ret = 0.0
    total_adapt_ret = 0.0
    
    for w in all_weeks:
        b_data = base.get(w, {"return": 0.0, "active": 0, "sharpe": 0.0})
        a_data = adapt.get(w, {"return": 0.0, "active": 0, "sharpe": 0.0})
        
        diff_ret = a_data["return"] - b_data["return"]
        total_base_ret += b_data["return"]
        total_adapt_ret += a_data["return"]
        
        print(f"| {w} | {b_data['active']:3d} | {a_data['active']:3d} | {b_data['return']*100:+7.2f}% | {a_data['return']*100:+7.2f}% | {b_data['sharpe']:+6.2f} | {a_data['sharpe']:+6.2f} | {diff_ret*100:+7.2f}% |")

    print(f"| **Total** | | | **{total_base_ret*100:+.2f}%** | **{total_adapt_ret*100:+.2f}%** | | | **{(total_adapt_ret - total_base_ret)*100:+.2f}%** |")

if __name__ == "__main__":
    main()
