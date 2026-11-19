from functools import lru_cache
import pandas as pd

# Label given to missing text values during cleaning. It is kept in totals and shares
# (so they still add up to 100%), but never reported as a "top" or "strongest" group.
PLACEHOLDER = "Unknown"


# Step 1: Load the file (CSV or Excel)
def load_data(file):
    name = file if isinstance(file, str) else file.name
    if name.endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


# Step 2: Data quality check
def data_quality_report(df):
    report = {
        "rows": len(df),
        "columns": len(df.columns),
        "duplicate_rows": int(df.duplicated().sum()),
        "missing_values": {col: int(n) for col, n in df.isnull().sum().items() if n > 0},
    }
    return report


# Step 3: Clean the data
def clean_data(df):
    log = []
    df = df.copy()

    # Standardize text: trim spaces and merge case variants ("electronics", "Electronics " -> "Electronics")
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        original = df[col].copy()
        trimmed = df[col].astype("string").str.strip()
        most_common = (
            trimmed.dropna().groupby(trimmed.dropna().str.lower()).agg(lambda v: v.value_counts().index[0])
        )
        df[col] = trimmed.str.lower().map(most_common).where(trimmed.notna(), None)
        changed = int((original.astype("string").fillna("") != df[col].fillna("")).sum())
        if changed:
            log.append(f"Standardized {changed} inconsistent text values in '{col}' (spacing/capitalization)")
        df[col] = df[col].astype(object)

    before = len(df)
    df = df.drop_duplicates()
    if before - len(df):
        log.append(f"Removed {before - len(df)} duplicate rows")

    # Remove columns that are mostly empty (more than 50% missing)
    mostly_empty = [col for col in df.columns if df[col].isnull().mean() > 0.5]
    if mostly_empty:
        df = df.drop(columns=mostly_empty)
        log.append(f"Removed mostly empty columns: {', '.join(mostly_empty)}")

    for col in df.columns:
        missing = int(df[col].isnull().sum())
        if missing == 0:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            if missing / len(df) <= 0.05:
                # A few gaps: drop those rows so totals aren't distorted
                df = df.dropna(subset=[col])
                log.append(f"Dropped {missing} rows with missing '{col}'")
            else:
                # Many gaps: fill with the median instead of losing lots of rows
                df[col] = df[col].fillna(df[col].median())
                log.append(f"Filled {missing} missing values in '{col}' with the median")
        else:
            df[col] = df[col].fillna(PLACEHOLDER)
            log.append(f"Filled {missing} missing values in '{col}' with '{PLACEHOLDER}'")

    df, date_log = parse_dates(df)
    df, metric_log = add_revenue(df)
    return df, log + date_log + metric_log


DATE_WORDS = ["date", "time", "day", "month", "period", "timestamp"]
DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y", "%b %d, %Y"]


def parse_dates(df):
    """Turn date-like text columns into real dates, handling day-first (15-03-2024) and
    month-first (03-15-2024) files."""
    log = []
    for col in df.columns:
        if not any(w in col.lower() for w in DATE_WORDS) or pd.api.types.is_numeric_dtype(df[col]):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        text = df[col].astype(str).str.strip()
        # Each format is applied to the WHOLE column, so 05-03-2024 can't be read as May in
        # one row and 5 March in another. Keep the format that reads the most values;
        # on a tie (e.g. every day is 12 or less) keep the one giving the shorter, more plausible span.
        tries = [pd.to_datetime(text, errors="coerce", format=f) for f in DATE_FORMATS]
        tries.append(pd.to_datetime(text, errors="coerce"))  # let pandas infer anything else
        def quality(t):
            span = (t.max() - t.min()).days if t.notna().any() else 0
            return (round(t.notna().mean(), 3), -span)
        best = max(tries, key=quality)
        if best.notna().mean() > 0.8:
            df[col] = best
            log.append(f"Read '{col}' as dates")
    return df, log


QTY_WORDS = ["quantity", "qty", "units", "unit sold", "units sold", "volume"]
PRICE_WORDS = ["price", "rate", "unit cost", "cost per"]


def add_revenue(df):
    """If there's no revenue/sales column but there is a quantity and a price, create
    Revenue = quantity x price. Adding up prices alone would be meaningless."""
    names = {c: c.lower().replace("_", " ") for c in df.columns}
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if any(any(k in names[c] for k in ["revenue", "sales", "amount", "total"]) for c in numeric):
        return df, []
    qty = next((c for c in numeric if any(w in names[c] for w in QTY_WORDS)), None)
    price = next((c for c in numeric if any(w in names[c] for w in PRICE_WORDS) and c != qty), None)
    if qty and price:
        df["Revenue"] = (df[qty] * df[price]).round(2)
        return df, [f"Created 'Revenue' = '{qty}' × '{price}'"]
    return df, []


# Step 4: Detect the important columns automatically
def detect_columns(df):
    # Date column: name contains "date", and the values can be read as dates
    date_col = next((c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])), None)
    for col in ([] if date_col else df.columns):
        if "date" in col.lower():
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().mean() > 0.8:
                date_col = col
                break

    # Main metric: prefer columns like revenue/sales/amount, else the biggest numeric column
    # Skip ID-like columns (e.g. order_id, EmployeeID) - adding up IDs is meaningless
    numeric_cols = [
        col for col in df.select_dtypes(include="number").columns
        if not col.lower().replace("_", "").replace(" ", "").endswith("id")
    ]
    metric_col = None
    for keyword in ["revenue", "sales", "amount", "profit", "total"]:
        for col in numeric_cols:
            if keyword in col.lower():
                metric_col = col
                break
        if metric_col:
            break
    if metric_col is None and numeric_cols:
        # Prices and rates don't add up meaningfully, so prefer other columns (e.g. quantity)
        addable = [c for c in numeric_cols if not any(w in c.lower() for w in PRICE_WORDS)] or numeric_cols
        metric_col = max(addable, key=lambda c: df[c].sum())

    # Category columns: text columns with a small number of unique values (2-20)
    category_cols = [
        col for col in df.columns
        if not pd.api.types.is_numeric_dtype(df[col])
        and col != date_col and 2 <= df[col].nunique() <= 20
    ]
    return date_col, metric_col, category_cols


# Step 4b: Detect a location column for maps
INDIA_ALIASES = {
    "orissa": "Odisha", "new delhi": "Delhi", "nct of delhi": "Delhi", "pondicherry": "Puducherry",
    "uttaranchal": "Uttarakhand", "jammu and kashmir": "Jammu & Kashmir",
    "andaman and nicobar islands": "Andaman & Nicobar", "andaman and nicobar": "Andaman & Nicobar",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
}
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


@lru_cache(maxsize=1)
def india_state_names():
    """Read the state names from the map file once, then reuse them."""
    import json, os
    path = os.path.join(os.path.dirname(__file__), "geo", "india_states.geojson")
    with open(path, encoding="utf-8") as f:
        return [feat["properties"]["name"] for feat in json.load(f)["features"]]


@lru_cache(maxsize=1)
def _india_lookup():
    return {n.lower(): n for n in india_state_names()}


def match_india_state(value):
    names = _india_lookup()
    key = str(value).strip().lower()
    return names.get(key) or INDIA_ALIASES.get(key)


def match_us_state(value):
    key = str(value).strip()
    if key.upper() in US_STATES.values():
        return key.upper()
    return US_STATES.get(key.lower())


def detect_location(df):
    """Return {'column', 'level'} where level is 'india_state', 'us_state' or 'country', or None.
    A state map is only used when most rows have a recognisable state; if the data spans several
    countries and many rows have no matching state, a world map by country is used instead."""
    text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]

    country_col = next((c for c in text_cols
                        if any(w in c.lower() for w in ["country", "nation"]) and df[c].nunique() >= 2), None)

    for col in text_cols:
        values = df[col].dropna().unique()
        if len(values) < 2:
            continue
        for level, matcher in [("india_state", match_india_state), ("us_state", match_us_state)]:
            # Check each distinct value once, then count how many rows those values cover
            is_match = {v: matcher(v) is not None for v in values}
            names_matched = sum(is_match.values()) / len(values)
            rows_matched = df[col].map(is_match).fillna(False).astype(bool).mean()
            if names_matched >= 0.5 and (rows_matched >= 0.8 or country_col is None):
                return {"column": col, "level": level}

    if country_col:
        return {"column": country_col, "level": "country"}
    return None


# Step 5: Calculate KPIs
def calculate_kpis(df, date_col, metric_col, category_cols):
    kpis = {
        "total": round(float(df[metric_col].sum()), 2),
        "records": len(df),
        "average": round(float(df[metric_col].mean()), 2),
        "median": round(float(df[metric_col].median()), 2),
    }

    if date_col:
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        yearly = df.groupby(df[date_col].dt.year)[metric_col].sum()
        if len(yearly) >= 2:
            last, prev = yearly.iloc[-1], yearly.iloc[-2]
            kpis["growth"] = {
                "from_year": int(yearly.index[-2]),
                "to_year": int(yearly.index[-1]),
                "from_value": round(float(prev), 2),
                "to_value": round(float(last), 2),
                "growth_pct": round(float((last - prev) / prev * 100), 1),
            }
        kpis["monthly_trend"] = df.groupby(df[date_col].dt.to_period("M"))[metric_col].sum()

        # Seasonality: total by calendar month (Jan-Dec) across all years
        by_month = df.groupby(df[date_col].dt.month)[metric_col].sum()
        kpis["seasonality"] = by_month.rename(lambda m: pd.Timestamp(2000, m, 1).strftime("%b"))

        # Rising and falling items: change between the last two years
        if "growth" in kpis and category_cols:
            kpis["movers"] = {}
            y1, y2 = kpis["growth"]["from_year"], kpis["growth"]["to_year"]
            for col in category_cols:
                pivot = df[df[date_col].dt.year.isin([y1, y2])].pivot_table(
                    index=col, columns=df[date_col].dt.year, values=metric_col, aggfunc="sum"
                )
                if y1 in pivot and y2 in pivot:
                    change = ((pivot[y2] - pivot[y1]) / pivot[y1] * 100).dropna().round(1)
                    change = change.drop(PLACEHOLDER, errors="ignore")  # not a real group
                    kpis["movers"][col] = change.sort_values(ascending=False)

    kpis["top_by_category"] = {
        col: df.groupby(col)[metric_col].sum().drop(PLACEHOLDER, errors="ignore").sort_values(ascending=False).head(5)
        for col in category_cols
    }

    # Share of total for each category value (e.g. North = 29%)
    kpis["share_by_category"] = {
        col: (df.groupby(col)[metric_col].sum() / kpis["total"] * 100).round(1).sort_values(ascending=False)
        for col in category_cols
    }

    # Concentration: what share of the total comes from the top 20% of items
    if category_cols:
        # Prefer a product-like column; otherwise the most detailed category column
        product_like = [c for c in category_cols if any(w in c.lower() for w in ["product", "item", "sku"])]
        detail_col = product_like[0] if product_like else max(category_cols, key=lambda c: df[c].nunique())
        totals = df.groupby(detail_col)[metric_col].sum().drop(PLACEHOLDER, errors="ignore").sort_values(ascending=False)
        top_n = max(1, round(len(totals) * 0.2))
        kpis["concentration"] = {
            "column": detail_col,
            "top_n": top_n,
            "items": len(totals),
            "share_pct": round(float(totals.head(top_n).sum() / totals.sum() * 100), 1),
        }
    return kpis


# Quick test
if __name__ == "__main__":
    df = load_data("sample_sales.csv")
    print(data_quality_report(df))
    df, log = clean_data(df)
    print(log)
    date_col, metric_col, category_cols = detect_columns(df)
    print("Date:", date_col, "| Metric:", metric_col, "| Categories:", category_cols)
    kpis = calculate_kpis(df, date_col, metric_col, category_cols)
    print("Total:", kpis["total"], "| Growth:", kpis.get("growth"))
    print(kpis["top_by_category"]["region"])
