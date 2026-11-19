import pandas as pd
import streamlit as st
from analysis import load_data, data_quality_report, clean_data, detect_columns, detect_location, calculate_kpis
from insights import build_kpi_summary, generate_summary
from chart_query import question_to_spec, validate_spec, build_chart
import charts as C

st.set_page_config(page_title="AI Business Insights Generator", page_icon="📊", layout="wide")


def show(fig, where=st):
    where.plotly_chart(fig, width="stretch", config={"displaylogo": False})


# ---------- Load ----------
st.title("📊 AI Business Insights Generator")
st.caption("Upload a sales or business file (CSV or Excel) to get a cleaned dataset, an interactive dashboard, "
           "answers to key business questions, an AI executive summary and charts on demand.")

uploaded = st.file_uploader("Upload your file", type=["csv", "xlsx"])
use_sample = st.checkbox("Or try it with sample sales data")

if uploaded is None and not use_sample:
    st.info("Upload a file or tick the sample data box to get started.")
    st.stop()

source = uploaded if uploaded is not None else "sample_sales.csv"
source_name = uploaded.name if uploaded is not None else "sample_sales.csv"

try:
    raw_df = load_data(source)
except Exception as e:
    st.error(f"Could not read the file: {e}")
    st.stop()

quality = data_quality_report(raw_df)
clean_df, clean_log = clean_data(raw_df)
date_col, metric_col, category_cols = detect_columns(clean_df)
location = detect_location(clean_df)

if metric_col is None or clean_df.empty:
    st.error("No usable numeric data found. Please upload a file with at least one number column (e.g. sales, revenue, amount).")
    st.stop()

# Reset saved results when a different file is loaded
if st.session_state.get("loaded_file") != source_name:
    st.session_state.loaded_file = source_name
    st.session_state.summaries = {}
    st.session_state.pinned = []
    st.session_state.last_chart = None

# ---------- Filters (sidebar) - apply to every tab ----------
st.sidebar.header("Filters")
df = clean_df.copy()
active_filters = []

if date_col:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    first, last = dates.min().date(), dates.max().date()
    picked = st.sidebar.date_input("Date range", value=(first, last), min_value=first, max_value=last)
    if isinstance(picked, (list, tuple)) and len(picked) == 2 and (picked[0], picked[1]) != (first, last):
        df = df[(dates.dt.date >= picked[0]) & (dates.dt.date <= picked[1])]
        active_filters.append(f"{date_col}: {picked[0]} to {picked[1]}")

filter_cols = list(category_cols)
if location and location["column"] not in filter_cols:
    filter_cols.append(location["column"])
for col in filter_cols:
    options = sorted(clean_df[col].dropna().astype(str).unique())
    chosen = st.sidebar.multiselect(col.capitalize(), options, placeholder="All")
    if chosen:
        df = df[df[col].astype(str).isin(chosen)]
        active_filters.append(f"{col}: {', '.join(chosen)}")

if active_filters:
    st.sidebar.caption("Active: " + " · ".join(active_filters))
if df.empty:
    st.warning("No data matches these filters. Clear some filters in the sidebar.")
    st.stop()

kpis = calculate_kpis(df, date_col, metric_col, category_cols)
hierarchy = C.find_hierarchy(df, category_cols)
growth = kpis.get("growth")

tab_dash, tab_questions, tab_ai, tab_ask, tab_quality = st.tabs(
    ["📊 Dashboard", "❓ Business questions", "🤖 AI summary", "💬 Ask your data", "🧹 Data quality"]
)


# ---------- Dashboard ----------
def dashboard_figures():
    figs = []
    if "monthly_trend" in kpis:
        figs.append(C.trend_line(kpis["monthly_trend"], metric_col))
    if location:
        figs.append(C.location_map(df, location, metric_col))
    if category_cols:
        main_cat = hierarchy[0] if hierarchy else min(category_cols, key=lambda c: df[c].nunique())
        figs.append(C.pie(df.groupby(main_cat)[metric_col].sum().sort_values(ascending=False), main_cat, metric_col))
    if hierarchy:
        figs.append(C.treemap(df, hierarchy[0], hierarchy[1], metric_col))
    if growth and category_cols:
        wf_col = hierarchy[0] if hierarchy else min(category_cols, key=lambda c: df[c].nunique())
        figs.append(C.waterfall(df, date_col, metric_col, wf_col, growth["from_year"], growth["to_year"]))
    small = sorted(category_cols, key=lambda c: df[c].nunique())
    if len(small) >= 2:
        figs.append(C.stacked_bar(df, small[0], small[1], metric_col))
    return figs


def pinned_figures():
    figs = []
    for spec in st.session_state.pinned:
        try:
            figs.append(build_chart(spec, df, date_col, metric_col, location))
        except ValueError:
            pass  # a pinned chart may not fit the current filters
    return figs


with tab_dash:
    if active_filters:
        st.caption("Filtered by " + " · ".join(active_filters))

    k1, k2, k3, k4 = st.columns(4)
    spark = kpis["monthly_trend"].values.tolist() if "monthly_trend" in kpis else None
    k1.metric(f"Total {metric_col}", C.compact(kpis["total"]), border=True, chart_data=spark, chart_type="area")
    if growth:
        k2.metric(f"{growth['to_year']} vs {growth['from_year']}", C.compact(growth["to_value"]),
                  f"{growth['growth_pct']}%", border=True)
    else:
        k2.metric("Records", f"{kpis['records']:,}", border=True)
    k3.metric(f"Average {metric_col}", C.compact(kpis["average"]), border=True)
    if kpis["share_by_category"]:
        top_col = hierarchy[1] if hierarchy else next(iter(kpis["share_by_category"]))
        share = kpis["share_by_category"][top_col]
        k4.metric(f"Top {top_col}", str(share.index[0]), f"{share.iloc[0]}% of total", delta_color="off", border=True)

    figs = dashboard_figures()
    for i in range(0, len(figs), 2):
        cols = st.columns(2)
        for col, fig in zip(cols, figs[i:i + 2]):
            show(fig, col)

    pinned = pinned_figures()
    if st.session_state.pinned:
        st.subheader("Your pinned charts")
        for i, (spec, fig) in enumerate(zip(st.session_state.pinned, pinned)):
            show(fig)
            if st.button("Remove from dashboard", key=f"unpin_{i}"):
                st.session_state.pinned.pop(i)
                st.rerun()
    else:
        st.caption("Tip: create a chart in **Ask your data** and add it to this dashboard.")

    # Download the dashboard as a single HTML file
    header = (f"<h1>Business dashboard</h1><p>Source: {source_name}"
              + (f" | Filters: {' · '.join(active_filters)}" if active_filters else "") + "</p>"
              + f"<p><b>Total {metric_col}:</b> {C.compact(kpis['total'])}"
              + (f" | <b>{growth['to_year']} vs {growth['from_year']}:</b> {growth['growth_pct']}%" if growth else "")
              + f" | <b>Average:</b> {C.compact(kpis['average'])}</p>")
    body = "".join(f.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False)
                   for i, f in enumerate(figs + pinned))
    html = f"<html><head><meta charset='utf-8'><title>Dashboard</title></head><body style='font-family:sans-serif;max-width:1100px;margin:auto'>{header}{body}</body></html>"
    st.download_button("⬇️ Download dashboard (HTML)", html, file_name="dashboard.html", mime="text/html")


# ---------- Business questions ----------
with tab_questions:
    left, right = st.columns(2)

    if "monthly_trend" in kpis:
        with left:
            st.subheader("Are we growing?")
            if growth:
                word = "grew" if growth["growth_pct"] >= 0 else "fell"
                st.write(f"{metric_col.capitalize()} **{word} {abs(growth['growth_pct'])}%** from {growth['from_year']} "
                         f"({C.compact(growth['from_value'])}) to {growth['to_year']} ({C.compact(growth['to_value'])}).")
            show(C.trend_line(kpis["monthly_trend"], metric_col))

    if "seasonality" in kpis and category_cols:
        with right:
            st.subheader("When do we sell the most?")
            best = kpis["seasonality"].sort_values(ascending=False).head(2).index.tolist()
            st.write(f"The strongest months are **{' and '.join(best)}** (all years combined).")
            heat_col = min(category_cols, key=lambda c: df[c].nunique())
            show(C.seasonality_heatmap(df, date_col, metric_col, heat_col))

    if kpis["share_by_category"]:
        st.subheader(f"Where does {metric_col} come from?")
        tabs = st.tabs([col.capitalize() for col in kpis["share_by_category"]])
        for tab, (col, share) in zip(tabs, kpis["share_by_category"].items()):
            tab.write(f"**{share.index[0]}** is the largest {col}, with **{share.iloc[0]}%** of total {metric_col}.")
            show(C.share_bar(share, col, metric_col), tab)

    if location:
        st.subheader(f"Where are our customers?")
        totals = df.groupby(location["column"])[metric_col].sum().sort_values(ascending=False)
        st.write(f"**{totals.index[0]}** brings the most {metric_col} ({C.compact(totals.iloc[0])}), "
                 f"followed by **{totals.index[1]}** ({C.compact(totals.iloc[1])})." if len(totals) > 1 else "")
        show(C.location_map(df, location, metric_col))

    if kpis.get("movers"):
        left, right = st.columns(2)
        mover_col = hierarchy[1] if hierarchy else max(kpis["movers"], key=lambda c: len(kpis["movers"][c]))
        change = kpis["movers"][mover_col]
        with left:
            st.subheader("What is rising and what is falling?")
            st.write(f"Strongest {mover_col}: **{change.index[0]} ({change.iloc[0]:+}%)**. "
                     f"Weakest: **{change.index[-1]} ({change.iloc[-1]:+}%)**.")
            show(C.movers_bar(change, mover_col, metric_col, growth["from_year"], growth["to_year"]))
        with right:
            wf_col = hierarchy[0] if hierarchy else min(category_cols, key=lambda c: df[c].nunique())
            st.subheader(f"What drove the change?")
            st.write(f"How each {wf_col} added to or took away from {metric_col} between {growth['from_year']} and {growth['to_year']}.")
            show(C.waterfall(df, date_col, metric_col, wf_col, growth["from_year"], growth["to_year"]))

    if "concentration" in kpis:
        c = kpis["concentration"]
        st.subheader("How dependent are we on a few items?")
        st.write(f"The top **{c['top_n']} of {c['items']}** {c['column']} values bring **{c['share_pct']}%** of total {metric_col}.")
        show(C.pareto(df, c["column"], metric_col))

    left, right = st.columns(2)
    with left:
        st.subheader(f"What does a typical {metric_col} look like?")
        note = " A few large values pull the average above the median." if kpis["average"] > kpis["median"] * 1.2 else ""
        st.write(f"Average: **{C.compact(kpis['average'])}** · Median: **{C.compact(kpis['median'])}**.{note}")
        show(C.histogram(df, metric_col, kpis["median"]))
    if category_cols:
        with right:
            box_col = hierarchy[0] if hierarchy else min(category_cols, key=lambda c: df[c].nunique())
            st.subheader(f"How does it differ by {box_col}?")
            st.write(f"The box shows the middle 50% of values; the line inside is the median.")
            show(C.box(df, box_col, metric_col))


# ---------- AI summary ----------
with tab_ai:
    kpi_summary = build_kpi_summary(quality, clean_log, metric_col, kpis)
    summary_key = " | ".join(active_filters) or "all data"
    if active_filters:
        st.caption(f"The summary covers the filtered data: {summary_key}")

    if st.button("Generate AI summary", type="primary"):
        with st.spinner("Writing summary..."):
            try:
                st.session_state.summaries[summary_key] = generate_summary(kpi_summary)
            except Exception as e:
                st.error(f"AI summary failed: {e}")

    summary = st.session_state.summaries.get(summary_key)
    if summary:
        st.markdown(summary)
        st.download_button("Download summary", summary, file_name="executive_summary.md")

    with st.expander("What the AI sees (KPIs only, never the raw rows)"):
        st.text(kpi_summary)


# ---------- Ask your data ----------
with tab_ask:
    st.write("Ask a question and get a chart. Examples: *monthly revenue for Electronics in 2025*, "
             "*revenue by state on a map*, *top 5 products by units in the North region*, "
             "*pie chart of sales by category*, *spread of revenue by category*.")
    question = st.text_input("Your question", placeholder="e.g. Which channel had the most orders in 2025?")

    if st.button("Create chart") and question:
        with st.spinner("Building chart..."):
            try:
                spec = validate_spec(question_to_spec(question, df, date_col, metric_col, location), df, location)
                build_chart(spec, df, date_col, metric_col, location)  # check it works before saving
                st.session_state.last_chart = spec
            except ValueError as e:
                st.session_state.last_chart = None
                st.warning(f"Couldn't build that chart: {e}. Try rephrasing using the column names.")
            except Exception as e:
                st.session_state.last_chart = None
                st.error(f"Something went wrong: {e}")

    spec = st.session_state.last_chart
    if spec:
        try:
            show(build_chart(spec, df, date_col, metric_col, location))
            if st.button("📌 Add to dashboard"):
                st.session_state.pinned.append(spec)
                st.success("Added. Open the Dashboard tab to see it.")
            with st.expander("How this chart was built"):
                st.json(spec)
        except ValueError as e:
            st.warning(f"This chart doesn't fit the current filters: {e}.")


# ---------- Data quality ----------
with tab_quality:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", f"{quality['rows']:,}", border=True)
    c2.metric("Columns", quality["columns"], border=True)
    c3.metric("Duplicate rows", quality["duplicate_rows"], border=True)
    c4.metric("Missing values", sum(quality["missing_values"].values()), border=True)

    if clean_log:
        st.write("**Cleaning applied:**")
        for item in clean_log:
            st.write(f"- {item}")
    else:
        st.success("No duplicates, missing values or inconsistent text found.")

    st.write(
        f"**Detected columns** → metric: `{metric_col}` · date: `{date_col or 'none'}` · "
        f"categories: `{', '.join(category_cols) if category_cols else 'none'}` · "
        f"location: `{location['column'] + ' (' + location['level'].replace('_', ' ') + ')' if location else 'none'}`"
    )
    st.write("**Preview of the cleaned data (first 10 rows):**")
    st.markdown(clean_df.head(10).to_html(index=False), unsafe_allow_html=True)

st.markdown("---")
st.caption("Built with Python · pandas · Plotly · LangChain · Groq · Streamlit. "
           "India state boundaries: DataMeet India community (CC BY 4.0).")
