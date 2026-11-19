import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
import insights  # loads the API key from .env

# temperature=0: the same question should give the same chart every time
recipe_llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0, max_tokens=2000)
import charts as C

ALLOWED_CHARTS = ["bar", "line", "histogram", "scatter", "pie", "map", "treemap", "box"]
ALLOWED_AGGS = ["sum", "mean", "count", "median", "percent"]
ALLOWED_OPS = ["==", "!=", ">", "<", ">=", "<=", "in"]


# Describe the columns for the AI: names, types and category labels (no rows)
def describe_columns(df, date_col):
    lines = []
    for col in df.columns:
        if col == date_col:
            lines.append(f"- {col} (date)")
        elif pd.api.types.is_numeric_dtype(df[col]):
            lines.append(f"- {col} (number)")
        elif df[col].nunique() <= 25:
            values = ", ".join(map(str, df[col].dropna().unique()[:25]))
            lines.append(f"- {col} (category: {values})")
        else:
            lines.append(f"- {col} (text)")
    return "\n".join(lines)


# Step 1: The AI turns the question into a small JSON "chart recipe"
def question_to_spec(question, df, date_col, metric_col, location=None):
    system = """You turn a business question into a chart recipe as JSON. Reply with JSON only.
Format:
{{"chart": "bar|line|histogram|scatter|pie|map|treemap|box",
  "x": "column name",
  "y": "column name or null",
  "color": "second category column or null",
  "agg": "sum|mean|count|median|percent",
  "time_grain": "month|year|null",
  "filters": [{{"column": "name", "op": "==|!=|>|<|>=|<=|in", "value": "..."}}],
  "top_n": number or null,
  "title": "short chart title"}}
Rules:
- Use ONLY the column names listed. Never invent columns.
- Words like sales, revenue, amount or value mean the main metric column: {metric}.
- "percent", "share" or "% of total" = agg "percent" (each group's share of the total).
- line = trend over time (x = the date column, set time_grain).
- bar = compare categories. Use "color" for a stacked bar split by a second category.
- pie = share of total by one category (only if the user asks for a pie chart).
- map = values by location; x must be the location column: {location}.
- treemap = breakdown of a category into sub-items; x = parent column, color = child column. Words like "break down", "breakdown", "made up of", "composition" or "X inside each Y" mean treemap.
- box = spread/distribution of a number across a category (x = category, y = number). Words like "spread", "range", "variation", "outliers" or "how much X varies by Y" always mean box, never bar.
- histogram = distribution of one number (x = the number, y = null).
- scatter = relationship between two numbers (x = one number, y = the other). Questions like "does X affect Y", "relationship between X and Y" or "is X linked to Y", where both are number columns, mean scatter.
- For a year filter on the date column, use op "==" and the year as value, e.g. "2025".
- If the question cannot be answered with these columns, reply {{"error": "short reason"}}."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("user", "Columns:\n{columns}\n\nQuestion: {question}"),
    ])
    response = (prompt | recipe_llm).invoke({
        "columns": describe_columns(df, date_col),
        "question": question,
        "metric": metric_col,
        "location": location["column"] if location else "none (no map possible)",
    }).content
    text = response.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except json.JSONDecodeError:
        raise ValueError("the AI reply could not be read as a chart recipe")


# Step 2: Check the recipe against the real data (guardrail)
def validate_spec(spec, df, location=None):
    if "error" in spec:
        raise ValueError(spec["error"])
    if spec.get("chart") not in ALLOWED_CHARTS:
        raise ValueError(f"unsupported chart type: {spec.get('chart')}")
    for key in ["x", "y", "color"]:
        if spec.get(key) and spec[key] not in df.columns:
            raise ValueError(f"column not found: {spec[key]}")
    if not spec.get("x") and spec["chart"] != "histogram":
        raise ValueError("no column chosen for the chart")
    if (spec.get("agg") or "sum") not in ALLOWED_AGGS:
        raise ValueError(f"unsupported calculation: {spec.get('agg')}")
    if spec["chart"] == "map" and (not location or spec.get("x") != location["column"]):
        raise ValueError("a map needs a column of countries or states")
    for f in spec.get("filters") or []:
        if f.get("column") not in df.columns or f.get("op") not in ALLOWED_OPS:
            raise ValueError(f"invalid filter: {f}")
    return spec


# Step 3: Apply filters (pandas only, no AI-written code is run)
def apply_filters(df, filters, date_col):
    for f in filters or []:
        col, op, value = f["column"], f["op"], f["value"]
        series = df[col]
        if col == date_col:
            series = pd.to_datetime(series, errors="coerce").dt.year
            value = [int(v) for v in value] if isinstance(value, list) else int(value)
        elif pd.api.types.is_numeric_dtype(series) and op != "in":
            value = float(value)
        if op == "in":
            df = df[series.isin(value if isinstance(value, list) else [value])]
        else:
            df = df[{"==": series == value, "!=": series != value, ">": series > value,
                     "<": series < value, ">=": series >= value, "<=": series <= value}[op]]
    return df


def aggregate(data, group, y, agg):
    if agg == "count":
        return data.groupby(group).size()
    if agg == "percent":
        return (data.groupby(group)[y].sum() / data[y].sum() * 100).round(1)
    return data.groupby(group)[y].agg(agg)


# Step 4: Build the chart
def build_chart(spec, df, date_col, metric_col, location=None):
    data = apply_filters(df, spec.get("filters"), date_col)
    if data.empty:
        raise ValueError("no data matches those filters")

    chart, x, color = spec["chart"], spec.get("x"), spec.get("color")
    agg = spec.get("agg") or "sum"
    y = spec.get("y") or metric_col
    title = spec.get("title", "")

    if chart == "map":
        fig = C.location_map(data, location, y)
        if fig is None:
            raise ValueError(f"none of these rows has a {location['column']} that can be placed on the map")
    elif chart == "pie":
        fig = C.pie(data.groupby(x)[y].sum().sort_values(ascending=False), x, y)
    elif chart == "treemap":
        if not color:
            raise ValueError("a treemap needs a parent and a child column")
        fig = C.treemap(data, x, color, y)
    elif chart == "box":
        fig = C.box(data, x, y)
    elif chart == "histogram":
        column = x if x and pd.api.types.is_numeric_dtype(data[x]) else y
        fig = C.histogram(data, column, data[column].median())
    elif chart == "scatter":
        fig = px.scatter(data, x=x, y=y, opacity=0.5, color_discrete_sequence=[C.blue()])
    elif x == date_col:
        dates = pd.to_datetime(data[x], errors="coerce")
        group = dates.dt.to_period("Y" if spec.get("time_grain") == "year" else "M").astype(str)
        if color:
            result = data.groupby([group, color])[y].sum().reset_index().rename(columns={x: "period"})
            result.columns = ["period", color, y]
            fig = px.line(result, x="period", y=y, color=color, markers=True)
        else:
            result = aggregate(data, group, y, agg)
            fig = C.trend_line(result, y)  # same gradient line as the dashboard
    elif color:
        fig = C.stacked_bar(data, x, color, y)
    else:
        result = aggregate(data, x, y, agg).sort_values(ascending=False)
        if spec.get("top_n"):
            result = result.head(int(spec["top_n"]))
        result = result.sort_values()
        label = f"% of total {y}" if agg == "percent" else f"{agg} of {y}" if agg != "count" else "count"
        low, high = result.min(), result.max()
        # Same gradient bars as the dashboard: bigger value = stronger colour
        fig = go.Figure(go.Bar(
            x=result.values, y=result.index.astype(str), orientation="h",
            marker=dict(color=C.bar_colours(result.values, result.index)),
            text=[f"{v}%" if agg == "percent" else C.compact(v) for v in result.values],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}: <b>%{x:,.2f}</b><extra></extra>",
        ))
        fig.update_xaxes(title=label, range=[min(0, low * 1.15), high * 1.15])  # room for the value labels
        fig.update_yaxes(title=x)
        fig.update_layout(height=max(320, 130 + 36 * len(result)))

    return C.style(fig, title, height=fig.layout.height or 400)
