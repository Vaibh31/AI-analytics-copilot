"""Accuracy test for "Ask your data".

Runs 20 business questions on the sample data through the same steps the app uses
(AI recipe -> guardrail check -> chart), and checks the AI picked the right chart type,
columns, calculation and filters. Prints a score and saves eval_results.csv.

Run:  python eval_charts.py
"""
import time
import pandas as pd
from analysis import load_data, clean_data, detect_columns, detect_location
from chart_query import question_to_spec, validate_spec, build_chart

# Each test: the question, and what a correct chart recipe must contain.
# "chart" lists every chart type that is a fair answer (e.g. share can be a pie or a bar).
TESTS = [
    ("Total revenue by region", {"chart": ["bar"], "x": "region"}),
    ("Monthly revenue trend", {"chart": ["line"], "x": "order_date", "time_grain": "month"}),
    ("Revenue by year", {"chart": ["line", "bar"], "x": "order_date", "time_grain": "year"}),
    ("Revenue by state on a map", {"chart": ["map"], "x": "state"}),
    ("Top 5 products by revenue", {"chart": ["bar"], "x": "product", "top_n": 5}),
    ("Pie chart of revenue by category", {"chart": ["pie"], "x": "category"}),
    ("Revenue by channel, split by category", {"chart": ["bar"], "x": "channel", "color": "category"}),
    ("Average revenue per order by channel", {"chart": ["bar"], "x": "channel", "agg": "mean"}),
    ("Number of orders by region", {"chart": ["bar"], "x": "region", "agg": "count"}),
    ("What percent of revenue comes from each category?", {"chart": ["bar", "pie"], "x": "category", "agg": ["percent", "sum"]}),
    ("Distribution of order revenue", {"chart": ["histogram"]}),
    ("Spread of revenue by category", {"chart": ["box"], "x": "category"}),
    ("Break down each category into its products", {"chart": ["treemap"], "x": "category", "color": "product"}),
    ("Does discount affect units sold?", {"chart": ["scatter"], "x": "discount", "y": "units"}),
    ("Monthly revenue for Electronics in 2025", {"chart": ["line"], "x": "order_date",
                                                  "filters": [("category", "Electronics"), ("order_date", "2025")]}),
    ("Revenue by product in the North region", {"chart": ["bar"], "x": "product", "filters": [("region", "North")]}),
    ("Units sold by category", {"chart": ["bar"], "x": "category", "y": "units"}),
    ("Revenue by state for the Website channel", {"chart": ["map", "bar"], "x": "state",
                                                   "filters": [("channel", "Website")]}),
    ("Total cost by region", {"chart": ["bar"], "x": "region", "y": "cost"}),
    ("What is each customer's favourite colour?", {"error": True}),  # can't be answered: should be refused
]


def check(spec, expected):
    """Return a list of problems (empty list = correct)."""
    problems = []
    if spec.get("chart") not in expected["chart"]:
        problems.append(f"chart {spec.get('chart')} (wanted {'/'.join(expected['chart'])})")
    for key in ["x", "y", "color", "time_grain"]:
        if key in expected and spec.get(key) != expected[key]:
            problems.append(f"{key}={spec.get(key)} (wanted {expected[key]})")
    if "agg" in expected:
        allowed = expected["agg"] if isinstance(expected["agg"], list) else [expected["agg"]]
        if (spec.get("agg") or "sum") not in allowed:
            problems.append(f"agg={spec.get('agg')} (wanted {'/'.join(allowed)})")
    if "top_n" in expected and str(spec.get("top_n")) != str(expected["top_n"]):
        problems.append(f"top_n={spec.get('top_n')} (wanted {expected['top_n']})")
    for column, value in expected.get("filters", []):
        found = any(f.get("column") == column and value.lower() in str(f.get("value")).lower()
                    for f in spec.get("filters") or [])
        if not found:
            problems.append(f"missing filter {column}={value}")
    return problems


def main():
    df, _ = clean_data(load_data("sample_sales.csv"))
    date_col, metric_col, _ = detect_columns(df)
    location = detect_location(df)

    rows = []
    for question, expected in TESTS:
        try:
            spec = validate_spec(question_to_spec(question, df, date_col, metric_col, location), df, location)
            if expected.get("error"):
                problems = ["should have been refused"]
            else:
                problems = check(spec, expected)
                build_chart(spec, df, date_col, metric_col, location)  # the chart must also draw
        except ValueError as e:
            spec = {}
            problems = [] if expected.get("error") else [f"refused: {e}"]
        except Exception as e:
            spec = {}
            problems = [f"error: {e}"]

        passed = not problems
        rows.append({"question": question, "passed": passed, "problems": "; ".join(problems), "recipe": spec})
        print(f"{'PASS' if passed else 'FAIL'}  {question}" + ("" if passed else f"  ->  {'; '.join(problems)}"))
        time.sleep(2)  # stay under the free Groq rate limit

    results = pd.DataFrame(rows)
    results.to_csv("eval_results.csv", index=False)
    score = results["passed"].mean() * 100
    print(f"\nAccuracy: {results['passed'].sum()}/{len(results)} questions correct ({score:.0f}%)")
    print("Details saved to eval_results.csv")


if __name__ == "__main__":
    main()
