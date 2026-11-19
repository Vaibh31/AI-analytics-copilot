"""Decision dashboard: pick the charts that matter most for THIS dataset.

Every possible chart is a "card" that answers one business question and states the
takeaway in words. Each card gets a relevance score from the data itself (e.g. a
seasonality chart scores high only if some months really are much stronger), and the
dashboard shows the highest-scoring cards. No AI is used here: the choices are
explainable rules, and every number comes from pandas.
"""
import pandas as pd
import charts as C
from analysis import PLACEHOLDER

MAX_CARDS = 6  # plus the map, if there is one


def _pct(a, b):
    return (a - b) / abs(b) * 100 if b else 0.0


def _real_groups(series):
    return series.drop(PLACEHOLDER, errors="ignore")


def _numeric_drivers(df, metric):
    """Other number columns (not IDs) and how strongly each moves with the main metric."""
    cols = [c for c in df.select_dtypes("number").columns
            if c != metric and not c.lower().replace("_", "").replace(" ", "").endswith("id")
            and df[c].nunique() > 2]
    corr = {c: df[c].corr(df[metric]) for c in cols}
    return pd.Series({c: r for c, r in corr.items() if pd.notna(r)}).sort_values(key=abs, ascending=False)


# Columns people usually make decisions on get priority; status-like columns rarely help
BUSINESS_WORDS = ["product", "category", "region", "channel", "segment", "line", "department", "team",
                  "country", "city", "state", "store", "brand", "type", "customer", "market", "source"]
WEAK_WORDS = ["status", "flag", "is_", "has_", "gender", "code"]


def rank_categories(df, metric, category_cols, max_groups=20):
    """Category columns, most useful first: business-like names first, then by how unequal
    the groups are (a column where every group is the same tells you nothing)."""
    scored = []
    for col in category_cols:
        all_totals = df.groupby(col)[metric].sum()
        totals = _real_groups(all_totals)
        if not 2 <= len(totals) <= max_groups or totals.mean() == 0:
            continue
        if totals.sum() < 0.7 * all_totals.sum():
            continue  # mostly missing ("Unknown"): not reliable enough to decide on
        spread = totals.std() / abs(totals.mean())
        name = col.lower()
        weight = 2.0 if any(w in name for w in BUSINESS_WORDS) else 0.3 if any(w in name for w in WEAK_WORDS) else 1.0
        scored.append((weight * (0.5 + spread), col))
    return [col for _, col in sorted(scored, reverse=True)]


def focus_category(df, metric, category_cols):
    ranked = rank_categories(df, metric, category_cols)
    return ranked[0] if ranked else None


def build_cards(df, date_col, metric, category_cols, location, kpis):
    cards = []
    growth = kpis.get("growth")
    # When there is a map, don't also pick the location column as the focus (it would repeat the map)
    focus_cols = [c for c in category_cols if not (location and c == location["column"])] or category_cols
    focus = focus_category(df, metric, focus_cols)
    partial_note = ""
    if growth:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        months = dates.dt.to_period("M").groupby(dates.dt.year).nunique()
        y1, y2 = growth["from_year"], growth["to_year"]
        if months.get(y2, 12) < months.get(y1, 12):
            partial_note = f" Note: {y2} only has {months[y2]} months of data, so this is not a full-year comparison."

    # 1. Are we growing? ------------------------------------------------------
    trend = kpis.get("monthly_trend")
    if trend is not None and len(trend) < 3 and date_col:
        # Less than 3 months of data: look at weeks (or days) instead of months
        dates = pd.to_datetime(df[date_col], errors="coerce")
        weeks = df.groupby(dates.dt.to_period("W").dt.start_time.dt.date)[metric].sum()
        short = weeks if len(weeks) >= 4 else df.groupby(dates.dt.date)[metric].sum()
        if len(short) >= 4:
            unit = "weeks" if short is weeks else "days"
            half = len(short) // 2
            change = _pct(short.iloc[half:].sum() / (len(short) - half), short.iloc[:half].sum() / half)
            text = (f"The data covers {len(short)} {unit}. The second half averages **{change:+.1f}%** vs the first half. "
                    f"Best {unit[:-1]}: **{short.idxmax()}** ({C.compact(short.max())}).")
            fig = C.trend_line(pd.Series(short.values, index=[str(i) for i in short.index]), metric)
            cards.append(dict(score=100, question=f"Is {metric} growing?", takeaway=text, fig=fig))
    if trend is not None and len(trend) >= 3:
        recent, before = trend.tail(3).sum(), trend.iloc[-6:-3].sum() if len(trend) >= 6 else None
        best = trend.idxmax()
        if before:
            change = _pct(recent, before)
            text = (f"The last 3 months are **{change:+.1f}%** vs the 3 months before. "
                    f"Best month: **{best}** ({C.compact(trend.max())}).")
        else:
            text = f"Best month: **{best}** ({C.compact(trend.max())})."
        cards.append(dict(score=100, question=f"Is {metric} growing?", takeaway=text,
                          fig=C.trend_line(trend, metric)))

    # 2. What drove the change? -----------------------------------------------
    if growth and category_cols:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        y1, y2 = growth["from_year"], growth["to_year"]
        best_col, best_diff = None, None
        for col in rank_categories(df, metric, focus_cols, max_groups=12):
            by_year = df.assign(_y=dates.dt.year).pivot_table(index=col, columns="_y", values=metric, aggfunc="sum").fillna(0)
            if y1 in by_year and y2 in by_year:
                best_col, best_diff = col, _real_groups(by_year[y2] - by_year[y1])
                break
        if best_col is not None and len(best_diff):
            ups, downs = best_diff[best_diff > 0], best_diff[best_diff < 0]
            parts = []
            if len(ups):
                parts.append(f"**{ups.idxmax()}** added the most (+{C.compact(ups.max())})")
            if len(downs):
                parts.append(f"**{downs.idxmin()}** pulled it down the most ({C.compact(downs.min())})")
            text = "; ".join(parts) + "." + partial_note
            cards.append(dict(score=90, question=f"What drove the change from {y1} to {y2}?", takeaway=text,
                              fig=C.waterfall(df, date_col, metric, best_col, y1, y2)))

    # 3. Where should we focus? -----------------------------------------------
    if focus:
        totals = df.groupby(focus)[metric].sum().sort_values(ascending=False)
        real = _real_groups(totals)
        share = (real / totals.sum() * 100).round(1)
        ratio = real.iloc[0] / real.iloc[-1] if real.iloc[-1] > 0 else None
        text = f"**{real.index[0]}** brings **{share.iloc[0]}%** of {metric}"
        text += f", {ratio:.1f}× the smallest ({real.index[-1]})." if ratio and ratio < 100 else "."
        fig = C.pie(totals, focus, metric) if len(totals) <= 6 else C.share_bar(share, focus, metric)
        cards.append(dict(score=85, question=f"Which {focus} matters most?", takeaway=text, fig=fig))

    # 4. What is rising and what is falling? ----------------------------------
    movers = kpis.get("movers") or {}
    mover_col = next((c for c in rank_categories(df, metric, focus_cols) if len(movers.get(c, [])) >= 3), None)
    if mover_col:
        change = movers[mover_col]
        falling = change[change < 0]
        if len(falling):
            text = (f"**{len(falling)} of {len(change)}** {mover_col} values declined; the biggest drop is "
                    f"**{falling.index[-1]} ({falling.iloc[-1]:+.1f}%)**. Fastest growth: **{change.index[0]} ({change.iloc[0]:+.1f}%)**.")
        else:
            text = (f"Every {mover_col} grew. Fastest: **{change.index[0]} ({change.iloc[0]:+.1f}%)**, "
                    f"slowest: **{change.index[-1]} ({change.iloc[-1]:+.1f}%)**.")
        text += partial_note
        cards.append(dict(score=88 if len(falling) else 62, question=f"Which {mover_col} is rising or falling?",
                          takeaway=text, fig=C.movers_bar(change if len(change) <= 10 else pd.concat([change.head(5), change.tail(5)]),
                                                mover_col, metric, growth["from_year"], growth["to_year"])))

    # 5. How dependent are we on a few items? ---------------------------------
    conc = kpis.get("concentration")
    if conc and conc["items"] >= 8:
        risky = conc["share_pct"] >= 60
        text = (f"The top **{conc['top_n']} of {conc['items']}** {conc['column']} values bring **{conc['share_pct']}%** of {metric}"
                + (" — a high dependency worth protecting." if risky else f", so {metric} is fairly spread out."))
        cards.append(dict(score=80 if risky else 55, question=f"How dependent are we on a few {conc['column']}s?",
                          takeaway=text, fig=C.pareto(df, conc["column"], metric)))

    # 6. What moves the metric? -----------------------------------------------
    drivers = _numeric_drivers(df, metric)
    # Skip near-copies of the metric (e.g. cost = 70% of revenue): they say nothing new
    strong = drivers[(drivers.abs() >= 0.3) & (drivers.abs() < 0.95)]
    if len(strong):
        col, r = strong.index[0], strong.iloc[0]
        text = (f"**{col}** moves most closely with {metric} (correlation {r:+.2f}: "
                f"{'higher' if r > 0 else 'lower'} {col} goes with higher {metric}). This is a link, not proof of cause.")
        cards.append(dict(score=70 + min(20, abs(r) * 20), question=f"What moves {metric}?", takeaway=text,
                          fig=C.driver_bar(drivers[drivers.abs() < 0.95].head(8), metric)))

    # 7. When should we plan for peaks? ---------------------------------------
    season = kpis.get("seasonality")
    if season is not None and trend is not None and len(trend) >= 12 and focus:
        strength = season.max() / season.mean()
        # a short list of rows keeps the heatmap readable
        heat_col = next((c for c in rank_categories(df, metric, focus_cols, max_groups=8)), focus)
        top_months = season.sort_values(ascending=False).head(2).index.tolist()
        text = (f"**{' and '.join(top_months)}** are the strongest months, "
                f"{(strength - 1) * 100:.0f}% above an average month: worth planning for ahead of time.")
        cards.append(dict(score=78 if strength >= 1.3 else 40, question="When should we plan for peaks?",
                          takeaway=text, fig=C.seasonality_heatmap(df, date_col, metric, heat_col)))

    # 8. No dates? Show what a typical value looks like -----------------------
    if not date_col:
        note = "A few large values pull the average up." if kpis["average"] > kpis["median"] * 1.2 else "Values are fairly balanced."
        cards.append(dict(score=75, question=f"What does a typical {metric} look like?",
                          takeaway=f"Median **{C.compact(kpis['median'])}**, average **{C.compact(kpis['average'])}**. {note}",
                          fig=C.histogram(df, metric, kpis["median"])))
        if focus:
            cards.append(dict(score=72, question=f"How much does {metric} vary by {focus}?",
                              takeaway="Each box shows the middle 50% of values; the line inside is the median.",
                              fig=C.box(df, focus, metric)))

    # 9. Always-available cards, used when the data supports fewer of the ones above ----
    asked = {c["question"] for c in cards}
    ranked = rank_categories(df, metric, focus_cols)
    typical_q = f"What does a typical {metric} look like?"
    if typical_q not in asked:
        note = "A few large values pull the average up." if kpis["average"] > kpis["median"] * 1.2 else "Values are fairly balanced."
        cards.append(dict(score=50, question=typical_q,
                          takeaway=f"Median **{C.compact(kpis['median'])}** per record, average **{C.compact(kpis['average'])}**. {note}",
                          fig=C.histogram(df, metric, kpis["median"])))
    for col in ranked[1:3]:  # the next most useful categories
        totals = _real_groups(df.groupby(col)[metric].sum()).sort_values(ascending=False)
        share = (totals / df[metric].sum() * 100).round(1)
        q = f"Which {col} matters most?"
        if q in asked or len(totals) < 2:
            continue
        fig = C.pie(df.groupby(col)[metric].sum().sort_values(ascending=False), col, metric) if len(totals) <= 6 \
            else C.share_bar(share, col, metric)
        cards.append(dict(score=46, question=q, fig=fig,
                          takeaway=f"**{totals.index[0]}** leads with **{share.iloc[0]}%**; **{totals.index[-1]}** is lowest at **{share.iloc[-1]}%**."))
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        small, big = sorted([a, b], key=lambda c: df[c].nunique())
        if df[small].nunique() <= 8:
            cards.append(dict(score=42, question=f"How does each {big} split across {small}?",
                              takeaway=f"Each bar is one {big}; the colours show how much each {small} contributes.",
                              fig=C.stacked_bar(df, big, small, metric) if df[big].nunique() <= 12 else C.stacked_bar(df, small, big, metric)))
    if focus and f"How much does {metric} vary by {focus}?" not in asked:
        cards.append(dict(score=40, question=f"How much does {metric} vary by {focus}?",
                          takeaway="Each box shows the middle 50% of values; the line inside is the median.",
                          fig=C.box(df, focus, metric)))

    if focus:
        counts = _real_groups(df[focus].value_counts())
        if len(counts) >= 2:
            avg = _real_groups(df.groupby(focus)[metric].mean()).sort_values(ascending=False)
            cards.append(dict(score=38, question=f"Which {focus} has the most records, and the highest average?",
                              takeaway=f"**{counts.index[0]}** has the most records ({counts.iloc[0]:,}); "
                                       f"**{avg.index[0]}** has the highest average {metric} ({C.compact(avg.iloc[0])}).",
                              fig=C.share_bar((avg / avg.sum() * 100).round(1), focus, f"average {metric}")))

    cards = sorted(cards, key=lambda c: -c["score"])[:MAX_CARDS]

    # The map always gets its own full-width row when the data has locations
    if location:
        fig = C.location_map(df, location, metric)
        if fig is not None:
            totals = _real_groups(df.groupby(location["column"])[metric].sum()).sort_values(ascending=False)
            text = f"**{totals.index[0]}** leads with {C.compact(totals.iloc[0])}"
            text += f", followed by **{totals.index[1]}** ({C.compact(totals.iloc[1])})." if len(totals) > 1 else "."
            cards.insert(min(2, len(cards)), dict(score=0, question=f"Where does {metric} come from?",
                                                  takeaway=text, fig=fig, wide=True))
    return cards
