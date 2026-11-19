import json
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from analysis import match_india_state, match_us_state

# ---------- Colours ----------
# Two colour themes. "Ocean" (default) is a matching aqua / blue family; "Vibrant" is multi-colour.
# Both were checked for colour-blind separation. Colours are assigned in fixed order, never cycled.
THEMES = {
    "Aqua & Blue": {
        # Your palette, ordered light / dark / light... so neighbouring colours are easy to tell apart
        "light": ["#10A5F5", "#0859C6", "#00DBFF", "#0C71E0", "#00B8D9"],
        "dark":  ["#00FFFF", "#0C71E0", "#00DBFF", "#0859C6", "#10A5F5"],
        # Gradient for magnitude, low -> high (on a dark page high = brightest aqua)
        "bar_ramp_light": ["#9ef0ff", "#00DBFF", "#10A5F5", "#0C71E0", "#0859C6"],
        "bar_ramp_dark":  ["#0859C6", "#0C71E0", "#10A5F5", "#00DBFF", "#00FFFF"],
        "ramp_light": ["#e0fcff", "#9ef0ff", "#00DBFF", "#10A5F5", "#0C71E0", "#0859C6"],
        "ramp_dark":  ["#06306e", "#0859C6", "#0C71E0", "#10A5F5", "#00DBFF", "#00FFFF"],
        "fill": (0, 219, 255),    # #00DBFF, for the gradient under the trend line
        "up": "#00DBFF", "down": "#f0609e",  # pink is kept only to show decreases
    },
    "Ocean (aqua & blue)": {
        "light": ["#0aa7c9", "#2f6fe0", "#d9589a", "#0891b2", "#9b4de8", "#c98500"],
        "dark":  ["#1ed7fc", "#3b82f6", "#d9589a", "#0891b2", "#a855f7", "#c98500"],
        # gradient for bars (low -> high): blue to bright cyan
        "bar_ramp_light": ["#bfdbfe", "#60a5fa", "#2f6fe0", "#0aa7c9", "#0e7490"],
        "bar_ramp_dark":  ["#1e3a8a", "#1d4ed8", "#3b82f6", "#0ea5e9", "#1ed7fc"],
        # teal gradient kept for the heatmap and the map
        "ramp_light": ["#ccfbf1", "#5eead4", "#14b8a6", "#0891b2", "#2563eb", "#1e3a8a"],
        "ramp_dark":  ["#1e3a8a", "#1d4ed8", "#0e7490", "#0d9488", "#2dd4bf", "#99f6e4"],
        "fill": (30, 215, 252),   # #1ed7fc, for the gradient under the trend line
        "up": 0, "down": 2,       # cyan for increases, pink for decreases
    },
    "Vibrant (multi-colour)": {
        "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
        "dark":  ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
        "ramp_light": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        "ramp_dark":  ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
        "bar_ramp_light": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        "bar_ramp_dark":  ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
        "fill": (57, 135, 229),
        "up": 2, "down": 7,
    },
}
THEME = "Aqua & Blue"
SURFACE = {"light": "#ffffff", "dark": "#0e1117"}   # Streamlit's page background
MUTED = {"light": "#c3c2b7", "dark": "#4a4a46"}

MODE = "dark"  # set by the app from the viewer's light/dark theme

# Colour scale used for maps (the app lets the user change it). Light = lower value, dark = higher.
MAP_SCALES = {
    "Aqua → Blue": [[0, "#00FFFF"], [0.25, "#00DBFF"], [0.5, "#10A5F5"], [0.75, "#0C71E0"], [1, "#0859C6"]],
    "Aqua → Teal → Navy": [[0, "#ccfbf1"], [0.35, "#2dd4bf"], [0.65, "#0891b2"], [1, "#1e3a8a"]],
    "Yellow → Green → Blue": "YlGnBu",
    "Viridis (colour-blind friendly)": "Viridis_r",
    "Yellow → Orange → Red": "YlOrRd",
    "Purple": "Purples",
}
MAP_SCALE = MAP_SCALES["Aqua → Blue"]


def theme():
    return THEMES[THEME]


def cat():
    return theme()[MODE]


def ramp():
    """Low -> high gradient. On a dark page high values are the brightest; on a light page the darkest."""
    return theme()["ramp_dark" if MODE == "dark" else "ramp_light"]


def blue():
    return {"Aqua & Blue": "#0C71E0", "Ocean (aqua & blue)": cat()[1]}.get(THEME, cat()[0])


def up_colour():
    up = theme()["up"]
    return up if isinstance(up, str) else cat()[up]


def down_colour():
    down = theme()["down"]
    return down if isinstance(down, str) else cat()[down]


def colour_list(n, other_last=False):
    """n colours from the theme in fixed order; anything past the palette (or 'Other') is grey."""
    palette = cat()
    colours = [palette[i] if i < len(palette) else MUTED[MODE] for i in range(n)]
    if other_last and n:
        colours[-1] = MUTED[MODE]
    return colours


def surface():
    return SURFACE[MODE]


def bar_ramp():
    return theme()["bar_ramp_dark" if MODE == "dark" else "bar_ramp_light"]


def ramp_colour(value, low, high, start=0.2):
    """Pick a colour from the bar gradient for a value between low and high."""
    from plotly.colors import sample_colorscale
    position = 1.0 if high == low else (value - low) / (high - low)
    scale = bar_ramp()
    return sample_colorscale([[i / (len(scale) - 1), c] for i, c in enumerate(scale)],
                             [start + (1 - start) * position])[0]


def ink_for(colour):
    """Dark text on bright colours (like #1ed7fc), white text on deep ones."""
    from plotly.colors import hex_to_rgb, unlabel_rgb
    rgb = unlabel_rgb(colour) if colour.startswith("rgb") else hex_to_rgb(colour)
    lum = sum(w * (c / 255) for w, c in zip((0.2126, 0.7152, 0.0722), rgb))
    return "#0b0b0b" if lum > 0.5 else "#ffffff"


def fill_gradient():
    r, g, b = theme()["fill"]
    return dict(type="vertical", colorscale=[[0, f"rgba({r},{g},{b},0)"], [1, f"rgba({r},{g},{b},0.5)"]])


def blend(colour, amount):
    """Mix a colour with the page background (amount 1 = full colour, 0 = background)."""
    from plotly.colors import hex_to_rgb
    c, bg = hex_to_rgb(colour), hex_to_rgb(surface())
    return "rgb({},{},{})".format(*[round(bg[i] + (c[i] - bg[i]) * amount) for i in range(3)])


def compact(n):
    # 21413135 -> 21.41M, 126099 -> 126.10K
    for size, label in [(1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if abs(n) >= size:
            return f"{n / size:,.2f}{label}"
    return f"{n:,.0f}"


def style(fig, title, height=380):
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15)),
        height=height, margin=dict(l=10, r=10, t=55, b=10), hovermode="closest",
        colorway=cat(), barcornerradius=4, bargap=0.25,
        hoverlabel=dict(font_size=12),
    )
    if not fig.layout.legend.title.text:
        fig.update_layout(legend_title_text="")
    return fig


# ---------- Trend and time ----------
def trend_line(trend, metric):
    months = trend.index.astype(str)
    c = up_colour() if THEME == "Aqua & Blue" else cat()[0]
    fig = go.Figure(go.Scatter(
        x=months, y=trend.values, mode="lines+markers", name=metric,
        line=dict(color=c, width=2.5, shape="spline", smoothing=0.6),
        marker=dict(size=7, color=c, line=dict(width=2, color=surface())),
        fill="tozeroy",
        # gradient under the line: strong near the line, fading to transparent at the bottom
        fillgradient=fill_gradient(),
        hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra></extra>",
    ))
    fig.update_xaxes(type="category", showgrid=False)
    fig.update_yaxes(title=metric, rangemode="tozero")
    return style(fig, f"Monthly {metric}")


def seasonality_heatmap(df, date_col, metric, col):
    dates = pd.to_datetime(df[date_col], errors="coerce")
    heat = df.pivot_table(index=col, columns=dates.dt.month, values=metric, aggfunc="sum").fillna(0)
    heat.columns = [pd.Timestamp(2000, m, 1).strftime("%b") for m in heat.columns]
    # Higher value = stronger colour: dark blue on a light page, bright blue on a dark page
    scale = ramp() if MODE == "dark" else ramp()
    fig = px.imshow(heat, aspect="auto", color_continuous_scale=scale, labels=dict(color=metric))
    fig.update_traces(xgap=3, ygap=3, hovertemplate="%{y} · %{x}<br><b>%{z:,.0f}</b><extra></extra>")
    fig.update_xaxes(side="bottom", tickangle=0, type="category", showgrid=False)
    fig.update_yaxes(showgrid=False)
    fig.update_layout(coloraxis_colorbar=dict(title="", thickness=10, tickformat="~s"))
    return style(fig, f"{metric} by month and {col}", height=130 + 45 * len(heat))


# ---------- Composition ----------
def share_bar(share, col, metric, top=10):
    top_share = share.head(top).sort_values()
    low, high = top_share.min(), top_share.max()
    fig = go.Figure(go.Bar(
        x=top_share.values, y=top_share.index.astype(str), orientation="h",
        marker=dict(color=[ramp_colour(v, low, high) for v in top_share.values]),  # bigger share = stronger blue
        text=[f"{v}%" for v in top_share.values], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: <b>%{x}%</b><extra></extra>",
    ))
    fig.update_xaxes(title=f"% of total {metric}", showgrid=False, range=[0, high * 1.18])
    return style(fig, f"Share of {metric} by {col}", height=130 + 32 * len(top_share))


def pie(totals, col, metric):
    shares = totals / totals.sum() * 100
    keep = shares[shares >= 3].head(5).index
    other = totals.drop(keep).sum()
    totals = totals[keep]
    if other > 0:
        totals = pd.concat([totals, pd.Series({"Other": other})])
    colours = colour_list(len(totals), other_last=other > 0)  # "Other" in neutral grey
    fig = go.Figure(go.Pie(
        labels=totals.index.astype(str), values=totals.values, hole=0.58, sort=False,
        marker=dict(colors=colours, line=dict(color=surface(), width=3)),  # gaps between slices
        textinfo="percent", textposition="inside", insidetextorientation="horizontal",
        textfont=dict(color=[ink_for(c) for c in colours]),
        hovertemplate="%{label}<br><b>%{value:,.0f}</b> (%{percent})<extra></extra>",
    ))
    fig.add_annotation(text=f"<b>{compact(totals.sum())}</b><br><span style='font-size:11px'>total</span>",
                       showarrow=False, font=dict(size=20))
    return style(fig, f"Share of {metric} by {col}")


def find_hierarchy(df, category_cols):
    """Find a parent -> child pair (e.g. category -> product) where each child belongs to one parent."""
    best = None
    for parent in category_cols:
        for child in category_cols:
            if parent == child or df[child].nunique() <= df[parent].nunique():
                continue
            if (df.groupby(child)[parent].nunique() == 1).all():
                if best is None or df[child].nunique() > df[best[1]].nunique():
                    best = (parent, child)
    return best


def treemap(df, parent, child, metric):
    """Each parent group gets its own colour; items inside are shaded from light to full colour by size."""
    data = df.groupby([parent, child], as_index=False)[metric].sum()
    parent_totals = data.groupby(parent)[metric].sum().sort_values(ascending=False)
    total = parent_totals.sum()
    palette = cat()

    ink = "#0b0b0b" if MODE == "light" else "#ffffff"
    ids, labels, parents, values, colours = ["All"], ["All"], [""], [total], ["rgba(0,0,0,0)"]
    text_colours = [ink]
    for i, (p, p_total) in enumerate(parent_totals.items()):
        base = palette[i] if i < len(palette) else MUTED[MODE]
        ids.append(f"p::{p}"); labels.append(str(p)); parents.append("All")
        values.append(p_total); colours.append(blend(base, 0.35)); text_colours.append(ink)
        items = data[data[parent] == p].sort_values(metric, ascending=False)
        low, high = items[metric].min(), items[metric].max()
        for _, row in items.iterrows():
            strength = 1.0 if high == low else 0.55 + 0.45 * (row[metric] - low) / (high - low)
            ids.append(f"c::{p}::{row[child]}"); labels.append(str(row[child])); parents.append(f"p::{p}")
            values.append(row[metric]); colours.append(blend(base, strength))
            text_colours.append(ink_for(colours[-1]))

    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=colours, line=dict(color=surface(), width=2)), tiling=dict(pad=3),
        texttemplate="<b>%{label}</b><br>%{percentRoot:.1%}", textfont=dict(color=text_colours),
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} (%{percentRoot:.1%} of total)<extra></extra>",
        root_color="rgba(0,0,0,0)",
    ))
    return style(fig, f"{metric} breakdown: {parent} > {child}", height=460)


def stacked_bar(df, col_a, col_b, metric):
    data = df.groupby([col_a, col_b], as_index=False)[metric].sum()
    order = data.groupby(col_a)[metric].sum().sort_values(ascending=False).index.tolist()
    groups = data.groupby(col_b)[metric].sum().sort_values(ascending=False).index.tolist()
    # One colour per group; past the palette size the rest are grouped as "Other" so colours never repeat
    cap = len(cat())
    if len(groups) > cap:
        data[col_b] = data[col_b].where(data[col_b].isin(groups[:cap - 1]), "Other")
        data = data.groupby([col_a, col_b], as_index=False)[metric].sum()
        groups = groups[:cap - 1] + ["Other"]
    colour_map = dict(zip(groups, colour_list(len(groups))))
    if "Other" in colour_map:
        colour_map["Other"] = MUTED[MODE]
    fig = px.bar(data, x=col_a, y=metric, color=col_b, category_orders={col_a: order, col_b: groups},
                 color_discrete_map=colour_map)
    fig.update_traces(marker_line=dict(color=surface(), width=2),
                      hovertemplate="%{x} · %{fullData.name}<br><b>%{y:,.0f}</b><extra></extra>")
    fig.update_xaxes(showgrid=False)
    return style(fig, f"{metric} by {col_a}, split by {col_b}")


# ---------- Change ----------
def movers_bar(change, col, metric, y1, y2):
    data = change.sort_values()
    fig = go.Figure(go.Bar(
        x=data.values, y=data.index.astype(str), orientation="h",
        marker_color=[up_colour() if v >= 0 else down_colour() for v in data.values],
        text=[f"{v:+.1f}%" for v in data.values], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: <b>%{x:+.1f}%</b><extra></extra>",
    ))
    span = max(abs(data.min()), abs(data.max()))
    fig.update_xaxes(title=f"% change in {metric}", zeroline=True, zerolinewidth=1.5, showgrid=False,
                     range=[min(0, data.min()) - span * 0.25, max(0, data.max()) + span * 0.2])
    return style(fig, f"Change in {metric} by {col} ({y2} vs {y1})", height=130 + 28 * len(data))


def waterfall(df, date_col, metric, col, y1, y2):
    dates = pd.to_datetime(df[date_col], errors="coerce")
    by_year = df.assign(_year=dates.dt.year).pivot_table(index=col, columns="_year", values=metric, aggfunc="sum").fillna(0)
    diff = (by_year[y2] - by_year[y1]).sort_values(ascending=False)
    diff = diff[diff.round(2) != 0]  # skip groups with no change
    start, end = by_year[y1].sum(), by_year[y2].sum()
    fig = go.Figure(go.Waterfall(
        x=[str(y1)] + diff.index.astype(str).tolist() + [str(y2)],
        y=[start] + diff.values.tolist() + [end],
        measure=["absolute"] + ["relative"] * len(diff) + ["total"],
        text=[compact(start)] + [("+" if v >= 0 else "") + compact(v) for v in diff.values] + [compact(end)],
        textposition="outside",
        increasing={"marker": {"color": up_colour()}}, decreasing={"marker": {"color": down_colour()}},
        totals={"marker": {"color": blue()}}, connector={"line": {"color": MUTED[MODE], "dash": "dot"}},
        hovertemplate="%{x}<br><b>%{text}</b><extra></extra>",
    ))
    fig.update_xaxes(type="category", showgrid=False)  # keep "2024"/"2025" as labels, not numbers
    running = start + diff.cumsum()
    low, high = min(start, end, running.min() if len(running) else start), max(start, end, running.max() if len(running) else start)
    fig.update_yaxes(range=[low * 0.85, high * 1.1])
    return style(fig, f"What changed {metric} from {y1} to {y2}, by {col}")


# ---------- Concentration and distribution ----------
def pareto(df, col, metric, top_share=0.2):
    """Items sorted by value. The top 20% are highlighted, so you can see how much they carry."""
    totals = df.groupby(col)[metric].sum().sort_values(ascending=False)
    top_n = max(1, round(len(totals) * top_share))
    share = totals.head(top_n).sum() / totals.sum() * 100
    top_values = totals.head(top_n)
    colours = [ramp_colour(v, top_values.min(), top_values.max(), start=0.55) for v in top_values] \
        + [MUTED[MODE]] * (len(totals) - top_n)
    cumulative = (totals.cumsum() / totals.sum() * 100).round(1)
    fig = go.Figure(go.Bar(
        x=totals.index.astype(str), y=totals.values, marker_color=colours,
        customdata=cumulative.values,
        hovertemplate="%{x}<br><b>%{y:,.0f}</b><br>Running total: %{customdata}%<extra></extra>",
    ))
    fig.add_annotation(x=top_n - 0.5, y=totals.max(), xanchor="left", yanchor="top", showarrow=False,
                       text=f"  Top {top_n} = <b>{share:.1f}%</b> of {metric}", font=dict(size=13))
    fig.add_vline(x=top_n - 0.5, line_dash="dot", line_color=MUTED[MODE])
    fig.update_xaxes(showgrid=False, tickangle=-45)
    return style(fig, f"{metric} by {col}: how concentrated is it?")


def histogram(df, metric, median):
    """Bars shaded as a gradient: the most common value ranges get the strongest colour."""
    import numpy as np
    values = df[metric].dropna()
    counts, edges = np.histogram(values, bins=min(40, max(10, len(values) // 10)))
    mids = (edges[:-1] + edges[1:]) / 2
    fig = go.Figure(go.Bar(
        x=mids, y=counts, width=(edges[1] - edges[0]) * 0.9,
        marker_color=[ramp_colour(c, counts.min(), counts.max(), start=0.25) for c in counts],
        customdata=np.stack([edges[:-1], edges[1:]], axis=1),
        hovertemplate="%{customdata[0]:,.0f} – %{customdata[1]:,.0f}<br><b>%{y}</b> records<extra></extra>",
    ))
    fig.add_vline(x=median, line_dash="dash", line_color=down_colour(),
                  annotation_text=f"Median {compact(median)}", annotation_font_color=down_colour())
    fig.update_xaxes(title=metric, showgrid=False)
    fig.update_yaxes(title="Count")
    return style(fig, f"Distribution of {metric}")


def box(df, col, metric):
    order = df.groupby(col)[metric].median().sort_values(ascending=False).index.tolist()
    colour_map = {g: (cat()[i] if i < len(cat()) else MUTED[MODE]) for i, g in enumerate(order)}
    fig = px.box(df, x=col, y=metric, color=col, category_orders={col: order},
                 color_discrete_map=colour_map, points=False)
    fig.update_layout(showlegend=False)
    fig.update_xaxes(showgrid=False)
    return style(fig, f"Spread of {metric} by {col}")


# ---------- Map ----------
def _india_geojson():
    with open(os.path.join(os.path.dirname(__file__), "geo", "india_states.geojson"), encoding="utf-8") as f:
        return json.load(f)


def location_map(df, location, metric):
    """Shade each location by its value, and list every location with its value in a legend
    (sorted highest first). Clicking a legend item hides or shows that location."""
    from plotly.colors import sample_colorscale
    from analysis import US_STATES

    col, level = location["column"], location["level"]
    data = df[[col, metric]].copy()

    if level == "india_state":
        data["_loc"] = data[col].map(match_india_state)
        data["_name"] = data["_loc"]
    elif level == "us_state":
        full_names = {code: name.title() for name, code in US_STATES.items()}
        data["_loc"] = data[col].map(match_us_state)
        data["_name"] = data["_loc"].map(full_names)
    else:
        data["_loc"] = data[col].astype(str).str.strip()
        data["_name"] = data["_loc"]

    totals = (data.dropna(subset=["_loc"]).groupby(["_loc", "_name"], as_index=False)[metric].sum()
              .sort_values(metric, ascending=False))
    if totals.empty:
        return None  # none of these rows has a location that can be drawn on the map
    low, high = totals[metric].min(), totals[metric].max()

    fig = go.Figure()
    if level == "india_state":
        # Draw every state in light grey first, so states with no data still show the full map
        geojson = _india_geojson()
        all_states = [f["properties"]["name"] for f in geojson["features"]]
        fig.add_trace(go.Choropleth(geojson=geojson, locations=all_states, featureidkey="properties.name",
                                    z=[0] * len(all_states), colorscale=[[0, "#e5e7eb"], [1, "#e5e7eb"]],
                                    showscale=False, hoverinfo="skip", showlegend=False,
                                    marker_line_color="white"))
        shapes = {f["properties"]["name"]: f for f in geojson["features"]}
        where = dict(featureidkey="properties.name")
    elif level == "us_state":
        where = dict(locationmode="USA-states")
    else:
        where = dict(locationmode="country names")

    total_all = totals[metric].sum()
    for _, row in totals.iterrows():
        if level == "india_state":
            if row["_loc"] not in shapes:
                continue
            # Give each trace only its own state's outline, not the whole map (much faster and smaller)
            where["geojson"] = {"type": "FeatureCollection", "features": [shapes[row["_loc"]]]}
        # 0.25-1.0 of the scale (skips near-white); a single location gets the strongest colour
        shade = 1.0 if high == low else 0.25 + 0.75 * ((row[metric] - low) / (high - low))
        # On a dark page the highest values should be the brightest, so the scale is flipped
        color = sample_colorscale(MAP_SCALE, [shade if MODE == "light" else 1.25 - shade])[0]
        share = row[metric] / total_all * 100
        fig.add_trace(go.Choropleth(
            locations=[row["_loc"]], z=[1], colorscale=[[0, color], [1, color]], showscale=False,
            name=f"{row['_name']}  {compact(row[metric])} ({share:.1f}%)", showlegend=True,
            hovertemplate=f"<b>{row['_name']}</b><br>{metric}: {compact(row[metric])} ({share:.1f}%)<extra></extra>",
            marker_line_color="white", marker_line_width=0.5, **where,
        ))

    if level == "india_state":
        # Zoom to the states (not the whole world) and give the map most of the width
        fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
        title, height = f"{metric} by state (India)", 540
    elif level == "us_state":
        fig.update_geos(scope="usa")
        title, height = f"{metric} by state (USA)", 450
    else:
        fig.update_geos(showframe=False, projection_type="natural earth", projection_rotation=dict(lon=0))
        title, height = f"{metric} by country", 450

    fig.update_layout(
        # Map fills the left 65%; the legend sits in the remaining space on the right
        geo=dict(bgcolor="rgba(0,0,0,0)", domain=dict(x=[0, 0.65], y=[0, 1])),
        legend=dict(title=f"{metric} (highest first)", itemclick="toggle", font=dict(size=11),
                    x=0.67, xanchor="left", y=1, yanchor="top", tracegroupgap=0),
    )
    return style(fig, title, height=height)