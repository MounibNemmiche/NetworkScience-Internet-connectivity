"""Readable, reproducible figures for a selected Louvain partition.

The caller supplies complete community summaries and unique, undirected
community-pair edge counts. Any display selection is stated in the figure; it
does not alter the graph or the partitions used to calculate quality scores.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter
import numpy as np


BLUE = "#245F85"
ORANGE = "#AD6128"
GRAY = "#637888"
INK = "#23313D"
GRID = "#E5EAEE"


def _save(figure, directory: Path, stem: str) -> list[Path]:
    paths = [directory / f"{stem}.png", directory / f"{stem}.pdf"]
    figure.savefig(paths[0], dpi=180, bbox_inches="tight", facecolor="white",
                   metadata={"Software": "Network Science Student B"})
    figure.savefig(paths[1], bbox_inches="tight", facecolor="white",
                   metadata={"CreationDate": None, "ModDate": None})
    plt.close(figure)
    return paths


def _axis_style(axis) -> None:
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["left", "bottom"]].set_color("#A5B0B9")
    axis.set_axisbelow(True)
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.tick_params(axis="both", colors=INK, length=3)


def _sizes(rows: list[dict], seed: int, directory: Path) -> list[Path]:
    ordered = sorted(rows, key=lambda row: (-row["node_count"], row["community_id"]))
    shown = ordered[:12]
    labels = [f"C{row['community_id']}" for row in shown]
    counts = [row["node_count"] for row in shown]
    omitted = ordered[12:]
    if omitted:
        labels.append(f"Other {len(omitted)} " + ("community" if len(omitted) == 1 else "communities"))
        counts.append(sum(row["node_count"] for row in omitted))
    total = sum(row["node_count"] for row in rows)
    figure, axis = plt.subplots(figsize=(11.5, max(5.4, 0.43 * len(labels) + 2.3)))
    figure.subplots_adjust(left=0.25, right=0.95, top=0.80, bottom=0.18)
    positions = np.arange(len(labels))
    colors = [BLUE] * len(shown) + ([GRAY] if omitted else [])
    bars = axis.barh(positions, counts, color=colors, height=0.69)
    if omitted:
        bars[-1].set_hatch("///")
        bars[-1].set_edgecolor("white")
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    largest = max(counts)
    axis.set_xlim(0, max(1, largest) * 1.27)
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    axis.set_xlabel("Autonomous systems (ASes)", labelpad=10)
    _axis_style(axis)
    for y, count in zip(positions, counts):
        axis.text(count + largest * 0.02, y, f"{count:,}  ({count / total:.1%})",
                  va="center", fontsize=11, color=INK)
    figure.suptitle("Community sizes", x=0.25, y=0.96, ha="left", fontsize=20, fontweight="bold")
    figure.text(0.25, 0.895, f"Selected Louvain seed {seed}  |  {len(rows)} communities  |  {total:,} ASes",
                fontsize=12, color=INK)
    note = ("Largest 12 communities shown individually; the remaining communities are summed as Other."
            if omitted else "All communities are shown.")
    figure.text(0.25, 0.065, note + "\nLabels show counts and shares of all ASes; exact values are in community_summary.csv.",
                fontsize=10.5, color=GRAY, linespacing=1.6)
    return _save(figure, directory, "community_sizes")


def _composition(rows: list[dict], seed: int, directory: Path) -> list[Path]:
    if len(rows) <= 16:
        selected = rows
    else:
        focal = sorted(rows, key=lambda row: (-row["focal_asn_count"], row["community_id"]))[:8]
        providers = sorted(rows, key=lambda row: (-row["candidate_provider_count"], row["community_id"]))[:6]
        ids = {row["community_id"] for row in focal + providers}
        selected = [row for row in rows if row["community_id"] in ids]
    shown = sorted(selected, key=lambda row: (-row["focal_asn_count"],
                                             -row["candidate_provider_count"], row["community_id"]))
    ids = {row["community_id"] for row in shown}
    omitted = [row for row in rows if row["community_id"] not in ids]
    keys = ("node_count", "focal_asn_count", "candidate_provider_count")
    totals = [sum(row[key] for row in rows) for key in keys]
    labels = [f"C{row['community_id']}" for row in shown]
    values = [[row[key] for row in shown] for key in keys]
    if omitted:
        labels.append(f"Other {len(omitted)}")
        for key, column in zip(keys, values):
            column.append(sum(row[key] for row in omitted))
    positions = np.arange(len(labels))
    figure, axes = plt.subplots(1, 3, sharey=True, figsize=(15, max(7.0, 0.43 * len(labels) + 3.3)))
    figure.subplots_adjust(left=0.13, right=0.97, wspace=0.20, top=0.77, bottom=0.27)
    headings = ("All ASes", "Italian ASes", "Candidate providers")
    colors = (GRAY, BLUE, ORANGE)
    for axis, heading, color, column, total in zip(axes, headings, colors, values, totals):
        shares = np.array(column, dtype=float) * (100 / total) if total else np.zeros(len(column))
        bars = axis.barh(positions, shares, color=color, height=0.67)
        if omitted:
            bars[-1].set_hatch("///")
            bars[-1].set_edgecolor("white")
        axis.set_xlim(0, 120)
        axis.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
        axis.set_xlabel("Share of the column total (%)", labelpad=10, fontsize=11)
        axis.set_title(f"{heading}\nTotal: {total:,}", loc="left", fontsize=13, pad=15)
        _axis_style(axis)
        for y, share, count in zip(positions, shares, column):
            text = f"{share:.1f}% ({count:,})" if total else "0 (none observed)"
            inside = share > 75
            axis.text(share - 2 if inside else share + 2, y, text, va="center",
                      ha="right" if inside else "left", fontsize=10.5,
                      color="white" if inside else INK, clip_on=False)
    axes[0].set_yticks(positions, labels)
    axes[0].invert_yaxis()
    figure.suptitle("Where Italian ASes and their external providers fall", x=0.13, y=0.97,
                   ha="left", fontsize=20, fontweight="bold")
    figure.text(0.13, 0.915, f"Selected Louvain seed {seed}  |  Each column uses its own stated population",
                fontsize=12, color=INK)
    selection = ("Shown: union of the 8 communities with most Italian ASes and the 6 with most candidate providers."
                 if len(rows) > 16 else "All communities are shown, ordered by Italian-AS count.")
    other_note = "Other sums all remaining communities. " if omitted else ""
    figure.text(0.13, 0.045,
                selection + "\n" + other_note + "Shares in each column sum to 100% when its total is nonzero."
                + "\nCandidates are external ASes directly serving at least one Italian AS under the supplied country policy.",
                fontsize=10.5, color=GRAY, linespacing=1.6)
    return _save(figure, directory, "community_composition")


def _connectivity(rows: list[dict], edges: list[dict], seed: int, directory: Path) -> list[Path]:
    ordered = sorted(rows, key=lambda row: (-row["node_count"], row["community_id"]))[:12]
    ids = [row["community_id"] for row in ordered]
    indices = {community: position for position, community in enumerate(ids)}
    matrix = np.zeros((len(ids), len(ids)), dtype=np.int64)
    for edge in edges:
        source, target = edge["source_community"], edge["target_community"]
        if source != target and source in indices and target in indices:
            i, j = indices[source], indices[target]
            matrix[i, j] = matrix[j, i] = edge["edge_count"]
    masked = np.ma.array(np.log1p(matrix.astype(float)), mask=np.eye(len(ids), dtype=bool))
    maximum = int(matrix.max())
    color_max = max(float(np.log1p(maximum)), 1.0)
    cmap = LinearSegmentedColormap.from_list("community_links", ["#F4F8FC", "#CADDEC", "#6B9CBF", "#20587D"])
    cmap.set_bad("#E6EBEF")
    figure, axis = plt.subplots(figsize=(12, 10.5))
    figure.subplots_adjust(left=0.14, right=0.86, top=0.81, bottom=0.17)
    image = axis.imshow(masked, cmap=cmap, vmin=0, vmax=color_max, interpolation="nearest", aspect="equal")
    axis.set_xticks(np.arange(len(ids)), [f"C{community}" for community in ids])
    axis.set_yticks(np.arange(len(ids)), [f"C{community}" for community in ids])
    axis.set_xlabel("Community (ordered by total AS count)", labelpad=12)
    axis.set_ylabel("Community", labelpad=12)
    axis.set_xticks(np.arange(len(ids) + 1) - 0.5, minor=True)
    axis.set_yticks(np.arange(len(ids) + 1) - 0.5, minor=True)
    axis.grid(which="minor", color="white", linewidth=1)
    axis.tick_params(which="minor", bottom=False, left=False)
    for i in range(len(ids)):
        for j in range(len(ids)):
            if i == j:
                axis.text(j, i, "-", ha="center", va="center", fontsize=12, color=GRAY)
            else:
                value = int(matrix[i, j])
                # Choose the higher-contrast text against the rendered cell,
                # using WCAG relative luminance rather than a data threshold.
                rgba = cmap(float(np.log1p(value)) / color_max)
                rgb = np.asarray(rgba[:3])
                linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
                luminance = float(np.dot(linear, [0.2126, 0.7152, 0.0722]))
                dark = np.array([int(INK[index:index + 2], 16) / 255 for index in (1, 3, 5)])
                dark_linear = np.where(dark <= 0.04045, dark / 12.92, ((dark + 0.055) / 1.055) ** 2.4)
                dark_luminance = float(np.dot(dark_linear, [0.2126, 0.7152, 0.0722]))
                white_contrast = 1.05 / (luminance + 0.05)
                dark_contrast = (luminance + 0.05) / (dark_luminance + 0.05)
                color = "white" if white_contrast >= dark_contrast else INK
                if max(white_contrast, dark_contrast) < 4.5:
                    color = "#000000"
                axis.text(j, i, f"{value:,}", ha="center", va="center", fontsize=10.5, color=color)
    colorbar = figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04, shrink=0.88)
    ticks = [0] + [10**power for power in range(7) if 10**power <= maximum]
    if maximum > 0 and maximum not in ticks:
        ticks.append(maximum)
    ticks = sorted(set(ticks))
    # A maximum close to a round tick adds no useful visual distinction.
    if len(ticks) >= 3 and ticks[-1] / max(ticks[-2], 1) < 1.5:
        ticks.pop(-2)
    colorbar.set_ticks(np.log1p(ticks), labels=[f"{value:,}" for value in ticks])
    colorbar.set_label("Undirected AS links (log1p color scale)", labelpad=12)
    selected_nodes = sum(row["node_count"] for row in ordered)
    all_nodes = sum(row["node_count"] for row in rows)
    figure.suptitle("Connections between large communities", x=0.14, y=0.96,
                   ha="left", fontsize=20, fontweight="bold")
    figure.text(0.14, 0.905,
                f"Selected Louvain seed {seed}  |  {len(ids)} of {len(rows)} communities  |  "
                f"{selected_nodes:,} of {all_nodes:,} ASes", fontsize=12, color=INK)
    figure.text(0.14, 0.055,
                "Cells count links between different communities; symmetry repeats the same undirected count."
                + "\nThe diagonal is excluded (internal links are in community_summary.csv); zero cells mean no observed link."
                + f"\nThis display shows the {len(ids)} largest communities; all quality scores use the full graph and partition.",
                fontsize=10.5, color=GRAY, linespacing=1.6)
    return _save(figure, directory, "community_connectivity")


def render_community_figures(summary_rows: list[dict], edge_rows: list[dict], *,
                             selected_seed: int, output_directory: Path) -> list[Path]:
    """Save PNG/PDF sizes, population composition and community-link figures.

    Summary rows must describe every community of the selected partition. Edge
    rows contain each unordered community pair once; diagonal internal-edge rows
    are allowed and are intentionally omitted from the intercommunity heatmap.
    """
    if not summary_rows:
        raise ValueError("A nonempty selected-partition community summary is required")
    count_fields = ("community_id", "node_count", "focal_asn_count", "candidate_provider_count")
    rows = [{**row, **{key: int(row[key]) for key in count_fields}} for row in summary_rows]
    ids = {row["community_id"] for row in rows}
    if len(ids) != len(rows):
        raise ValueError("Community summaries contain duplicate IDs")
    for row in rows:
        if int(row.get("seed", selected_seed)) != selected_seed:
            raise ValueError("Community summary mixes seeds")
        if row["node_count"] <= 0 or min(row["focal_asn_count"], row["candidate_provider_count"]) < 0:
            raise ValueError("Community counts must be nonnegative and node counts positive")
        if row["focal_asn_count"] + row["candidate_provider_count"] > row["node_count"]:
            raise ValueError("Focal ASes and external candidate providers must be disjoint community subsets")
    edges = []
    pairs = set()
    for original in edge_rows:
        edge = {**original, **{key: int(original[key])
                              for key in ("source_community", "target_community", "edge_count")}}
        source, target = edge["source_community"], edge["target_community"]
        if int(edge.get("seed", selected_seed)) != selected_seed:
            raise ValueError("Community edges mix seeds")
        if source not in ids or target not in ids or source > target or edge["edge_count"] < 0:
            raise ValueError("Community edge endpoints/counts are invalid")
        if (source, target) in pairs:
            raise ValueError("Community edges repeat an unordered pair")
        pairs.add((source, target))
        edges.append(edge)
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    style = {"font.family": "DejaVu Sans", "font.size": 12, "axes.labelsize": 12,
             "axes.titlesize": 14, "xtick.labelsize": 11, "ytick.labelsize": 11,
             "text.color": INK, "axes.labelcolor": INK, "axes.facecolor": "white",
             "figure.facecolor": "white", "savefig.facecolor": "white", "pdf.fonttype": 42}
    with plt.rc_context(style):
        return (_sizes(rows, selected_seed, directory)
                + _composition(rows, selected_seed, directory)
                + _connectivity(rows, edges, selected_seed, directory))
