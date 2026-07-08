"""
app/forecasting.py

Predicts each clinic/item's expected daily usage rate from historical
usage data, using a lightweight Linear Regression model (scikit-learn),
falling back to a simple mean if there isn't enough history.
"""

import numpy as np
import pandas as pd
from datetime import timedelta

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USAGE_CSV = os.path.join(BASE_DIR, "clinic_inventory_usage.csv")
MIN_ROWS_FOR_REGRESSION = 5
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def predict_daily_usage() -> pd.DataFrame:
    """
    Returns a DataFrame with columns:
        clinic, item, predicted_daily_usage, upcoming_forecast (list),
        weekday_multipliers (dict), season_insight (str)
    representing the model's estimate of near-future daily usage,
    accounting for baseline trend and weekday seasonality.
    """
    df = pd.read_csv(USAGE_CSV, parse_dates=["date"])
    df["day_of_week"] = df["date"].dt.dayofweek

    results = []
    for (clinic, item), group in df.groupby(["clinic", "item"]):
        group = group.sort_values("date").reset_index(drop=True)
        n = len(group)
        last_date = group["date"].max()

        # 1. Seasonality Multipliers (Day of week)
        overall_mean = float(group["units_used"].mean())
        if overall_mean <= 0:
            overall_mean = 0.1
        
        dow_means = group.groupby("day_of_week")["units_used"].mean()
        multipliers = {}
        for i in range(7):
            day_mean = dow_means.get(i, overall_mean)
            multipliers[WEEKDAY_NAMES[i]] = round(float(day_mean / overall_mean), 2)

        # 2. Baseline linear trend fit (y = slope * x + intercept)
        if n >= MIN_ROWS_FOR_REGRESSION:
            x = np.arange(n)
            y = group["units_used"].values
            slope, intercept = np.polyfit(x, y, 1)
        else:
            slope = 0.0
            intercept = overall_mean

        # 3. Predict upcoming 7 days
        upcoming_forecast = []
        for k in range(1, 8):
            pred_date = last_date + timedelta(days=k)
            pred_dow = pred_date.weekday()
            t = n + k - 1
            baseline = slope * t + intercept
            mult = multipliers[WEEKDAY_NAMES[pred_dow]]
            predicted_val = max(baseline * mult, 0.1)
            upcoming_forecast.append(round(predicted_val, 1))

        avg_upcoming = float(np.mean(upcoming_forecast))
        
        # 4. Generate a human-readable season insight
        sorted_mults = sorted(multipliers.items(), key=lambda x: x[1])
        weakest_day, weak_val = sorted_mults[0]
        strongest_day, strong_val = sorted_mults[-1]
        
        insight = f"Demand peaks on {strongest_day} (x{strong_val})"
        if weak_val < 1.0:
            insight += f" and dips on {weakest_day} (x{weak_val})"
        else:
            insight += "."
        
        results.append({
            "clinic": clinic,
            "item": item,
            "predicted_daily_usage": round(avg_upcoming, 2),
            "upcoming_forecast": upcoming_forecast,
            "weekday_multipliers": multipliers,
            "season_insight": insight
        })

    return pd.DataFrame(results)

