import io
import os
import pandas as pd
import streamlit as st
from analysis import load_data, data_quality_report, clean_data, detect_columns, detect_location, calculate_kpis
from insights import build_kpi_summary, generate_summary
from chart_query import question_to_spec, validate_spec, build_chart
import charts as C

st.set_page_config(page_title="AI Business Insights Generator", page_icon="📊", layout="wide")


def show(fig, where=st):
    # Each chart sits on its own card. Each needs a unique key, because the same chart
    # can appear in more than one tab
    st.session_state["_chart_no"] = st.session_state.get("_chart_no", 0) + 1
    n = st.session_state["_chart_no"]
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")  # let the card show through
    with where.container(border=False, key=f"card_{n}"):
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False}, key=f"chart_{n}")


st.session_state["_chart_no"] = 0  # restart chart numbering on every run

# ---------- Light / dark switch ----------
# Start from the viewer's current theme, then let the button flip it
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = st.context.theme.type != "light"

title_col, theme_col = st.columns([5, 1], vertical_alignment="center")
with theme_col:
    st.toggle("🌙 Dark", key="dark_mode", help="Switch between light and dark mode")

wanted = "dark" if st.session_state.dark_mode else "light"
if st._config.get_option("theme.base") != wanted:
    st._config.set_option("theme.base", wanted)  # Streamlit has no public API for this yet
    st.rerun()  # redraw the page with the new theme
C.MODE = wanted  # charts use matching colours

# ---------- Card look (same style as the downloaded report) ----------
PAGE = {"light": "#f4f8fb", "dark": "#0e1117"}[wanted]
CARD = {"light": "#ffffff", "dark": "#1a1d24"}[wanted]
SHADOW = {"light": "0 1px 3px rgba(15,40,80,.10)", "dark": "0 1px 3px rgba(0,0,0,.45)"}[wanted]
C.SURFACE[wanted] = CARD  # gaps between bars/slices use the card colour
st.html(f"""<style>
 .stApp, [data-testid="stHeader"] {{ background: {PAGE}; }}
 [class*="st-key-card_"] {{ background: {CARD}; border-radius: 14px; padding: 10px 12px 4px;
                           box-shadow: {SHADOW}; }}
 [data-testid="stMetric"] {{ background: {CARD}; border-radius: 14px; min-height: 128px; box-shadow: {SHADOW};
                            border: none !important; border-top: 4px solid #10A5F5 !important; }}
 [data-testid="stMetricLabel"] p {{ font-size: 0.85rem; opacity: .75; }}
 [data-testid="stFileUploaderDropzone"] {{ background: {CARD}; }}
</style>""")

# ---------- Load ----------
title_col.title("📊 AI Business Insights Generator")
st.caption("Upload a sales or business file (CSV or Excel) to get a cleaned dataset, an interactive dashboard, "
           "answers to key business questions, an AI executive summary and charts on demand.")

uploaded = st.file_uploader("Upload your file", type=["csv", "xlsx"])
use_sample = st.checkbox("Or try it with sample sales data")

if uploaded is None and not use_sample:
    st.info("Upload a file or tick the sample data box to get started.")
    st.stop()

source_name = uploaded.name if uploaded is not None else "sample_sales.csv"
file_bytes = uploaded.getvalue() if uploaded is not None else open(os.path.join(os.path.dirname(__file__), "sample_sales.csv"), "rb").read()


# Read and clean the file ONCE, then reuse the result on every click (much faster).
# Streamlit re-runs this only when a different file is uploaded.
@st.cache_data(show_spinner="Reading and cleaning your file...", max_entries=5)
def prepare(data, name):
    buffer = io.BytesIO(data)
    buffer.name = name  # load_data uses the name to tell CSV from Excel
    raw = load_data(buffer)
    cleaned, log = clean_data(raw)
    return raw, data_quality_report(raw), cleaned, log, detect_columns(cleaned), detect_location(cleaned)


try:
    raw_df, quality, clean_df, clean_log, (date_col, metric_col, category_cols), location = prepare(file_bytes, source_name)
except Exception as e:
    st.error(f"Could not read the file: {e}")
    st.stop()

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

st.sidebar.header("Chart colours")
C.THEME = st.sidebar.selectbox("Colour theme", list(C.THEMES), label_visibility="collapsed")

if location:
    st.sidebar.header("Map colours")
    scale_name = st.sidebar.selectbox("Colour scale", list(C.MAP_SCALES), label_visibility="collapsed")
    C.MAP_SCALE = C.MAP_SCALES[scale_name]
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
        map_fig = C.location_map(df, location, metric_col)
        if map_fig is not None:
            map_fig.update_layout(meta="map")  # tag it so the map can get a full-width row
            figs.append(map_fig)
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


def export_html(plotlyjs="cdn"):
    """Build a stand-alone HTML report. Outside Streamlit, charts don't get Streamlit's
    automatic spacing, so we redraw them in light mode with room for axis labels."""
    old_mode = C.MODE
    C.MODE = "light"  # a downloaded report opens on a white page
    try:
        figs = dashboard_figures() + pinned_figures()
    finally:
        C.MODE = old_mode

    cards = []
    for i, fig in enumerate(figs):
        fig.update_layout(
            template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
            font=dict(family="Segoe UI, Arial, sans-serif", size=12, color="#1f2937"),
            margin=dict(l=20, r=20, t=60, b=20), autosize=True, width=None,
        )
        fig.update_xaxes(automargin=True, title_standoff=12)
        fig.update_yaxes(automargin=True, title_standoff=12)
        chart = fig.to_html(full_html=False, include_plotlyjs=plotlyjs if i == 0 else False,
                            default_width="100%", config={"responsive": True, "displaylogo": False})
        pinned_chart = st.session_state.pinned and i >= len(figs) - len(st.session_state.pinned)
        wide = " wide" if pinned_chart or fig.layout.meta == "map" else ""
        cards.append(f"<div class='card{wide}'>{chart}</div>")

    growth_text = (f"<div class='kpi'><span>{growth['to_year']} vs {growth['from_year']}</span>"
                   f"<b>{growth['growth_pct']}%</b></div>") if growth else ""
    filters_text = f" · Filters: {' · '.join(active_filters)}" if active_filters else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Business dashboard</title>
<style>
 body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#f4f8fb; color:#1f2937; margin:0; padding:24px; }}
 .wrap {{ max-width:1200px; margin:auto; }}
 h1 {{ margin:0 0 4px; font-size:26px; }}
 .sub {{ color:#6b7280; margin-bottom:18px; }}
 .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:18px; }}
 .kpi {{ background:white; border-radius:12px; padding:14px 16px; border-top:4px solid #10A5F5;
        box-shadow:0 1px 3px rgba(0,0,0,.08); }}
 .kpi span {{ display:block; color:#6b7280; font-size:13px; }} .kpi b {{ font-size:24px; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(480px,1fr)); gap:16px; }}
 .card {{ background:white; border-radius:12px; padding:8px; box-shadow:0 1px 3px rgba(0,0,0,.08); min-width:0; }}
 .card.wide {{ grid-column:1/-1; }}
 @media (max-width:560px) {{ .grid {{ grid-template-columns:1fr; }} body {{ padding:12px; }} }}
</style></head><body><div class="wrap">
<h1>Business dashboard</h1><div class="sub">Source: {source_name}{filters_text}</div>
<div class="kpis">
 <div class="kpi"><span>Total {metric_col}</span><b>{C.compact(kpis['total'])}</b></div>{growth_text}
 <div class="kpi"><span>Average {metric_col}</span><b>{C.compact(kpis['average'])}</b></div>
 <div class="kpi"><span>Records</span><b>{kpis['records']:,}</b></div>
</div>
<div class="grid">{''.join(cards)}</div></div>
<script>window.addEventListener('load', () =>
  document.querySelectorAll('.plotly-graph-div').forEach(d => Plotly.Plots.resize(d)));</script>
</body></html>"""


with tab_dash:
    if active_filters:
        st.caption("Filtered by " + " · ".join(active_filters))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(f"Total {metric_col}", C.compact(kpis["total"]), border=True)
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
    map_figs = [f for f in figs if f.layout.meta == "map"]
    grid = [f for f in figs if f.layout.meta != "map"]
    for i in range(0, len(grid), 2):
        cols = st.columns(2)
        for col, fig in zip(cols, grid[i:i + 2]):
            show(fig, col)
        if i == 0 and map_figs:  # the map gets its own full-width row, so it can be bigger
            show(map_figs[0])
    if not grid and map_figs:
        show(map_figs[0])

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

    st.download_button("⬇️ Download dashboard (HTML)", export_html(), file_name="dashboard.html", mime="text/html")


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
        map_fig = C.location_map(df, location, metric_col)
        if map_fig is not None:
            show(map_fig)
        else:
            st.info(f"None of the selected rows has a {location['column']} that can be placed on the map.")

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
            st.subheader("Where did the change come from?")
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
            # on_click runs BEFORE the page redraws, so the Dashboard tab
            # (drawn earlier on the page) already includes the new chart
            def pin(chart=spec):
                if chart not in st.session_state.pinned:
                    st.session_state.pinned.append(chart)

            if spec in st.session_state.pinned:
                st.success("✅ On your dashboard. Open the Dashboard tab and scroll to **Your pinned charts**.")
            else:
                st.button("📌 Add to dashboard", on_click=pin)
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