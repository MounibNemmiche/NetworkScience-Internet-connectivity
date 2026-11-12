"""Full-scale connectivity curves and explicitly labelled loss views."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, PercentFormatter
import numpy as np

from .robustness import METRICS


LABELS = {
    "focal_external_fraction": "Italian ASes retaining an external connection",
    "focal_external_pair_fraction": "Reachable Italian-to-external pairs",
    "largest_component_fraction": "Largest remaining component",
}
BLUE, ORANGE, INK = "#245F85", "#AD6128", "#23313D"


def _save(figure, directory, stem):
    paths = [directory / f"{stem}.png", directory / f"{stem}.pdf"]
    figure.savefig(paths[0], dpi=180, bbox_inches="tight", facecolor="white")
    figure.savefig(paths[1], bbox_inches="tight", facecolor="white",
                   metadata={"CreationDate": None, "ModDate": None})
    plt.close(figure)
    return paths


def _curves(axis, rows, *, loss=False):
    x = np.array([row["k"] for row in rows])
    target = np.array([row["targeted"] for row in rows])
    median = np.array([row["random_median"] for row in rows])
    lower = np.array([row["random_q10"] for row in rows])
    upper = np.array([row["random_q90"] for row in rows])
    if loss:
        target, median, lower, upper = 100 * (1 - target), 100 * (1 - median), 100 * (1 - upper), 100 * (1 - lower)
    axis.fill_between(x, lower, upper, color=BLUE, alpha=0.16, label="Random 10th-90th percentiles")
    axis.plot(x, median, color=BLUE, linewidth=2, linestyle="--", label="Random median")
    axis.plot(x, target, color=ORANGE, linewidth=2.2, marker="o", markersize=3.2, label="Targeted")
    axis.set_xlim(0, max(int(x[-1]), 1))
    axis.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    axis.set_xlabel("Providers removed (k)")
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(color="#E5EAEE", linewidth=0.7)
    axis.set_axisbelow(True)
    if loss:
        high = max(float(target.max()), float(upper.max()), 0.001)
        axis.set_ylim(0, high * 1.15)
        axis.set_ylabel("Loss from baseline (percentage points)")
    else:
        axis.set_ylim(0, 1.02)
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.set_ylabel("Fraction of the baseline population")


def render_robustness_figures(summary, *, output_directory: Path, focal_count: int,
                              external_count: int, graph_count: int, provider_count: int,
                              random_trials: int) -> list[Path]:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    style = {"font.family": "DejaVu Sans", "font.size": 11, "text.color": INK,
             "axes.labelcolor": INK, "axes.titlesize": 13, "pdf.fonttype": 42}
    with plt.rc_context(style):
        for metric in METRICS:
            rows = sorted((row for row in summary if row["metric"] == metric), key=lambda row: row["k"])
            if not rows:
                raise ValueError(f"Missing summary rows for {metric}")
            figure, axes = plt.subplots(1, 2, figsize=(13.5, 5.4))
            figure.subplots_adjust(left=0.08, right=0.97, top=0.76, bottom=0.24, wspace=0.30)
            _curves(axes[0], rows)
            _curves(axes[1], rows, loss=True)
            axes[0].set_title("Full fraction scale", loc="left")
            axes[1].set_title("Same data as loss from baseline", loc="left")
            figure.suptitle(LABELS[metric], x=0.08, y=0.97, ha="left", fontsize=19, fontweight="bold")
            handles, labels = axes[0].get_legend_handles_labels()
            figure.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.07, 0.91), ncol=3, frameon=False)
            figure.text(0.08, 0.06,
                        f"Fixed denominators: F={focal_count:,}, E={external_count:,}, G={graph_count:,}. "
                        f"Same {provider_count} candidate providers; {random_trials} random trials.\n"
                        "Target order: direct Italian-customer count, then baseline degree, then ASN. "
                        "The band shows simulation spread, not a confidence interval.", fontsize=10, color="#526876")
            paths.extend(_save(figure, directory, f"robustness_{metric}"))
        figure, axes = plt.subplots(1, 3, figsize=(15.5, 5.5))
        figure.subplots_adjust(left=0.065, right=0.98, top=0.72, bottom=0.25, wspace=0.35)
        headings = ("Italian ASes", "Italian-external pairs", "Largest component")
        for axis, metric, heading in zip(axes, METRICS, headings):
            rows = sorted((row for row in summary if row["metric"] == metric), key=lambda row: row["k"])
            _curves(axis, rows, loss=True)
            axis.set_title(heading, loc="left", fontweight="bold")
        figure.suptitle("Connectivity loss under provider removals", x=0.065, y=0.97,
                       ha="left", fontsize=21, fontweight="bold")
        handles, labels = axes[0].get_legend_handles_labels()
        figure.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.055, 0.90), ncol=3, frameon=False)
        figure.text(0.065, 0.06,
                    "Each panel uses its own loss scale; values are percentage-point reductions from 100% baseline connectivity.\n"
                    f"Fixed F={focal_count:,}, E={external_count:,}, G={graph_count:,}; "
                    f"{provider_count} candidates, {random_trials} random permutations. Bands are 10th-90th percentiles.",
                    fontsize=10.5, color="#526876")
        paths.extend(_save(figure, directory, "robustness_loss_overview"))
    return paths
