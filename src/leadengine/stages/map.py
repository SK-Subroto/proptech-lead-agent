"""Stage 8: interactive map, static PNG and district summary.

Tier colours are the first three validated categorical slots; each marker also
carries its tier letter, so tier never depends on colour alone.
Only OSM and Nominatim coordinates reach this stage.
"""

import html

import folium
import geopandas as gpd
import matplotlib
import pandas as pd
from branca.element import Element
from folium.plugins import MarkerCluster

from leadengine.stages.geo import load_boundaries
from leadengine.stages.validate import load_leads
from leadengine.store import blank

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

TIER_COLOURS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}
TIERS = ("A", "B", "C")
# Sequential blue ramp: lightest means fewest leads.
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
NO_LEADS = "#f0efec"
INK, MUTED, EDGE = "#0b0b0b", "#52514e", "#ffffff"
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"


def district_summary(leads: pd.DataFrame, districts: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per district, including districts with no leads."""
    placed = leads[leads["district"].notna()]
    rows = []
    for name in sorted(districts["district"]):
        group = placed[placed["district"] == name]
        rows.append({
            "district": name, "leads": len(group),
            **{f"tier_{t}": int((group["lead_tier"] == t).sum()) for t in TIERS},
            "mean_score": round(float(group["lead_score"].mean()), 1) if len(group) else None,
            "share_of_leads_pct": round(100 * len(group) / len(placed), 1) if len(placed) else 0.0,
        })
    return pd.DataFrame(rows)


def ramp_colour(count: int, maximum: int) -> str:
    if count <= 0 or maximum <= 0:
        return NO_LEADS
    step = min(int((count - 1) / maximum * len(RAMP)), len(RAMP) - 1)
    return RAMP[step]


def popup_html(row: dict) -> str:
    def esc(value, fallback="not stated"):
        return html.escape(str(value)) if not blank(value) else fallback

    website = ""
    if not blank(row.get("website")):
        url = html.escape(str(row["website"]), quote=True)
        website = f'<div><a href="{url}" target="_blank" rel="noopener">Website</a></div>'
    return (
        '<div style="font-family:system-ui,sans-serif;font-size:13px;min-width:190px">'
        f'<div style="font-weight:600;margin-bottom:4px">{esc(row.get("company_name"), "Name not stated")}</div>'
        f'<div>Score: <b>{esc(row.get("lead_score"))}</b> (Tier {esc(row.get("lead_tier"))})</div>'
        f'<div>Units: {esc(row.get("units_managed"))}</div>'
        f'<div>Contact: {esc(str(row.get("email_status", "")).replace("_", " "))}</div>'
        f'<div>District: {esc(row.get("district"))}</div>{website}</div>'
    )


def marker_icon(tier: str) -> folium.DivIcon:
    return folium.DivIcon(icon_size=(24, 24), icon_anchor=(12, 12), html=(
        f'<div style="width:24px;height:24px;border-radius:50%;background:{TIER_COLOURS[tier]};'
        f'border:2px solid {EDGE};box-sizing:border-box;color:#fff;font:600 12px/20px system-ui,sans-serif;'
        f'text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.35)">{tier}</div>'))


def legend_html(summary: pd.DataFrame, attribution: str) -> str:
    maximum = int(summary["leads"].max()) if len(summary) else 0
    tiers = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:3px 0"><span style="width:18px;height:18px;border-radius:50%;'
        f'background:{TIER_COLOURS[t]};color:#fff;font-size:11px;font-weight:600;text-align:center;line-height:18px">{t}</span>Tier {t}</div>'
        for t in TIERS)
    ramp = "".join(f'<span style="display:inline-block;width:20px;height:10px;background:{c}"></span>' for c in RAMP)
    return f"""
    <div style="position:fixed;bottom:26px;left:12px;z-index:9999;background:#fcfcfb;color:{INK};padding:10px 12px;
      border-radius:8px;box-shadow:0 1px 5px rgba(0,0,0,.3);font:13px system-ui,sans-serif;max-width:240px">
      <div style="font-weight:600;margin-bottom:4px">Lead tier</div>{tiers}
      <div style="font-weight:600;margin:8px 0 4px">Leads per district</div>
      <div style="line-height:0">{ramp}</div>
      <div style="display:flex;justify-content:space-between;color:{MUTED};font-size:11px;margin-top:3px"><span>1</span><span>{maximum}</span></div>
      <div style="color:{MUTED};font-size:10px;margin-top:8px">{html.escape(attribution)}</div>
    </div>"""


def build_interactive(leads: pd.DataFrame, districts: gpd.GeoDataFrame, summary: pd.DataFrame, ctx):
    center = ctx.city["center"]
    world = folium.Map(location=[center["lat"], center["lng"]], zoom_start=11, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Basemap", control=False).add_to(world)

    maximum = int(summary["leads"].max()) if len(summary) else 0
    joined = districts.merge(summary, on="district")
    joined["mean_score"] = joined["mean_score"].map(lambda v: "n/a" if pd.isna(v) else v)
    folium.GeoJson(
        joined.to_json(), name="Leads per district",
        style_function=lambda f: {"fillColor": ramp_colour(f["properties"]["leads"], maximum), "color": "#52514e", "weight": 1, "fillOpacity": 0.45},
        highlight_function=lambda f: {"weight": 3, "color": INK},
        tooltip=folium.GeoJsonTooltip(fields=["district", "leads", "tier_A", "tier_B", "tier_C", "mean_score"],
                                      aliases=["District", "Leads", "Tier A", "Tier B", "Tier C", "Mean score"]),
    ).add_to(world)

    placed = leads[leads["lat"].notna() & leads["lng"].notna()]
    for tier in TIERS:
        cluster = MarkerCluster(name=f"Tier {tier}", show=True, options={"maxClusterRadius": 40})
        for row in placed[placed["lead_tier"] == tier].to_dict("records"):
            folium.Marker([row["lat"], row["lng"]], icon=marker_icon(tier),
                          tooltip=html.escape(str(row.get("company_name") or "Name not stated")),
                          popup=folium.Popup(popup_html(row), max_width=300)).add_to(cluster)
        cluster.add_to(world)

    folium.LayerControl(collapsed=False).add_to(world)
    world.get_root().html.add_child(Element(legend_html(summary, f"Companies and tiles: {OSM_ATTRIBUTION}. {ctx.city['boundary']['attribution']}")))
    world.get_root().header.add_child(Element(f"<title>{ctx.city['name']} lead map</title>"))
    return world


def build_png(leads: pd.DataFrame, districts: gpd.GeoDataFrame, summary: pd.DataFrame, path, ctx) -> None:
    metric = districts.to_crs(25833).merge(summary, on="district")
    placed = leads[leads["lat"].notna() & leads["lng"].notna()]
    points = gpd.GeoDataFrame(placed, geometry=gpd.points_from_xy(placed["lng"], placed["lat"]), crs=4326).to_crs(25833)
    maximum = int(summary["leads"].max()) if len(summary) else 0

    plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(14, 7.2), facecolor="#fcfcfb")

    metric.plot(ax=left, color="#f0efec", edgecolor="#c3c2b7", linewidth=0.8)
    handles = []
    for tier in TIERS[::-1]:
        subset = points[points["lead_tier"] == tier]
        if len(subset):
            subset.plot(ax=left, color=TIER_COLOURS[tier], markersize=48, edgecolor=EDGE, linewidth=0.8, zorder=3)
        handles.insert(0, Line2D([], [], marker="o", linestyle="", markersize=8, markerfacecolor=TIER_COLOURS[tier],
                                 markeredgecolor=EDGE, label=f"Tier {tier} ({len(subset)})"))
    left.legend(handles=handles, loc="lower left", frameon=False, fontsize=11, title="Lead tier", title_fontsize=11)
    left.set_title(f"{len(placed)} located leads by tier", loc="left", fontsize=14, fontweight="bold", color=INK)

    metric.plot(ax=right, color=[ramp_colour(c, maximum) for c in metric["leads"]], edgecolor=EDGE, linewidth=1.5)
    offsets = ctx.city["boundary"].get("label_offsets_m", {})
    for row in metric.itertuples():
        spot = row.geometry.representative_point()
        shade = "#ffffff" if ramp_colour(row.leads, maximum) in RAMP[3:] else INK
        # Long double-barrelled names wrap at the hyphen so labels stay inside their district.
        name = row.district.replace("-", "-\n")
        dx, dy = offsets.get(row.district, (0, 0))
        right.text(spot.x + dx, spot.y + dy, f"{name}\n{row.leads}", ha="center", va="center", fontsize=7.5,
                   color=shade, linespacing=1.05)
    leader = summary.sort_values("leads", ascending=False).iloc[0] if len(summary) else None
    title = f"Most leads: {leader['district']} ({leader['leads']})" if leader is not None else "Leads per district"
    right.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK)
    if maximum:
        patches = [Patch(facecolor=NO_LEADS, label="0")] + [Patch(facecolor=RAMP[0], label="fewest")] + [Patch(facecolor=RAMP[-1], label=f"most ({maximum})")]
        right.legend(handles=patches, loc="lower left", frameon=False, fontsize=10, title="Leads", title_fontsize=10)

    for ax in (left, right):
        ax.set_axis_off()
        ax.set_aspect("equal")
    fig.text(0.012, 0.012, f"{OSM_ATTRIBUTION}. {ctx.city['boundary']['attribution']}", fontsize=8.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)


def run(ctx) -> dict:
    leads = load_leads(ctx)
    districts = load_boundaries(ctx)
    summary = district_summary(leads, districts)
    summary.to_csv(ctx.paths.outputs / "district_summary.csv", index=False, encoding="utf-8-sig")
    build_interactive(leads, districts, summary, ctx).save(str(ctx.paths.outputs / "lead_map.html"))
    build_png(leads, districts, summary, ctx.paths.outputs / "lead_map.png", ctx)
    return {"districts": len(summary), "leads_with_district": int(summary["leads"].sum()),
            "leads_on_map": int((leads["lat"].notna() & leads["lng"].notna()).sum())}
