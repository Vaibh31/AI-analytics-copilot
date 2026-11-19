from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()
llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.2, max_tokens=2000)


# Turn the KPIs into a short text summary (only numbers, never raw data)
def build_kpi_summary(quality, clean_log, metric_col, kpis):
    lines = [
        f"Dataset: {quality['rows']} rows, {quality['columns']} columns.",
        "Data cleaning: " + ("; ".join(clean_log) if clean_log else "no issues found") + ".",
        f"Main metric: {metric_col}. Total {metric_col} across the full period (all years combined): {kpis['total']:,.2f}.",
    ]
    if "growth" in kpis:
        g = kpis["growth"]
        lines.append(
            f"{metric_col} in {g['from_year']}: {g['from_value']:,.0f}. {metric_col} in {g['to_year']}: {g['to_value']:,.0f}. "
            f"Year-over-year growth: {g['growth_pct']}%."
        )
    if "monthly_trend" in kpis:
        trend = kpis["monthly_trend"]
        lines.append(f"Best month: {trend.idxmax()} ({trend.max():,.0f}). Weakest month: {trend.idxmin()} ({trend.min():,.0f}).")
    lines.append(f"Average {metric_col} per record: {kpis['average']:,.0f}. Median: {kpis['median']:,.0f}.")
    if "seasonality" in kpis:
        season = kpis["seasonality"]
        lines.append(f"Strongest calendar months (all years): {', '.join(season.sort_values(ascending=False).head(2).index)}.")
    for col, share in kpis["share_by_category"].items():
        items = ", ".join(f"{name} ({value}%)" for name, value in share.head(5).items())
        lines.append(f"Share of {metric_col} by {col}: {items}.")
    for col, change in kpis.get("movers", {}).items():
        up = ", ".join(f"{n} ({v:+}%)" for n, v in change.head(2).items())
        down = ", ".join(f"{n} ({v:+}%)" for n, v in change.tail(2).items())
        lines.append(f"Change by {col} (last year vs previous) — strongest: {up}; weakest: {down}.")
    if "concentration" in kpis:
        c = kpis["concentration"]
        lines.append(f"Concentration: top {c['top_n']} of {c['items']} {c['column']} values bring {c['share_pct']}% of {metric_col}.")
    return "\n".join(lines)


# Ask the LLM to write an executive summary from the KPI summary
def generate_summary(kpi_summary):
    system = """You are a senior business analyst writing for busy managers.
Use ONLY the numbers given. Never invent figures. If something is unclear, say so.
Do not add currency symbols or units unless they are given. Do not combine numbers from different periods in one claim.
Do not claim what caused or drove a result; the data shows totals, not causes.
Write in this format:
**Headline:** one sentence on the most important takeaway.
**Key findings:** 3-4 bullet points, each with a specific number.
**Watch-outs:** 1-2 risks or data quality notes.
**Recommended actions:** 2-3 practical next steps."""

    prompt = ChatPromptTemplate.from_messages([("system", system), ("user", "{kpis}")])
    chain = prompt | llm
    return chain.invoke({"kpis": kpi_summary}).content


# Quick test
if __name__ == "__main__":
    from analysis import load_data, data_quality_report, clean_data, detect_columns, calculate_kpis

    df = load_data("sample_sales.csv")
    quality = data_quality_report(df)
    df, log = clean_data(df)
    date_col, metric_col, category_cols = detect_columns(df)
    kpis = calculate_kpis(df, date_col, metric_col, category_cols)

    kpi_summary = build_kpi_summary(quality, log, metric_col, kpis)
    print("=== Sent to the AI ===")
    print(kpi_summary)
    print("\n=== AI summary ===")
    print(generate_summary(kpi_summary))
