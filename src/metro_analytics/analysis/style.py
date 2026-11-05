"""Shared matplotlib style: recessive axes, thin marks, fixed categorical order."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
# Categorical slots in fixed order (validated palette, light mode).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_CMAP = "Blues"


def apply() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 10.5, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 9.5, "ytick.labelsize": 9.5,
        "lines.linewidth": 2, "font.family": "DejaVu Sans", "legend.frameon": False,
        "legend.fontsize": 9.5, "figure.dpi": 110, "savefig.dpi": 160, "savefig.bbox": "tight",
    })


def subtitle(ax, text: str) -> None:
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK_2, fontsize=9.5, va="bottom")


def titles(ax, title: str, sub: str | None = None) -> None:
    ax.set_title(title, pad=24 if sub else 8)
    if sub:
        subtitle(ax, sub)
