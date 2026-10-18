"""
Data visualisation (matplotlib) - survey trend charts saved as PNG files.

Pure functions over plain data (no database access) so they are easy to test.
"""
import os

import config as cfg


def _save(fig, out_dir, name):
    """Tidy, save and close a figure; return the file path."""
    import matplotlib.pyplot as plt

    path = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def save_charts(trend, species, out_dir=None):
    """
    Save two charts and return their paths.

    :param trend: ``[{"date": "YYYY-MM-DD", "total_surveys": int, "total_cost": float}]``
    :param species: ``[(species_name, surveys_seen_in)]`` most frequent first
    """
    import matplotlib

    matplotlib.use("Agg")  # render to files; no display needed
    import matplotlib.pyplot as plt

    out_dir = out_dir or cfg.REPORT_DIR
    os.makedirs(out_dir, exist_ok=True)

    labels = [p["date"][5:] for p in trend]  # MM-DD
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(labels, [p["total_surveys"] for p in trend], color="#0b5c73")
    ax.set_ylabel("Surveys per day")
    ax.tick_params(axis="x", rotation=45)
    ax2 = ax.twinx()
    ax2.plot(labels, [p["total_cost"] for p in trend], color="#e07a1f", marker="o")
    ax2.set_ylabel("Survey cost ($)")
    ax.set_title(f"Survey trends - last {len(trend)} days")
    trend_path = _save(fig, out_dir, "survey_trends.png")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    if species:
        ax.barh([s[0] for s in species][::-1], [s[1] for s in species][::-1], color="#2a9d8f")
        ax.set_xlabel("Surveys in which the species was observed")
    else:
        ax.text(0.5, 0.5, "No observations in this period", ha="center", va="center")
        ax.set_axis_off()
    ax.set_title("Most observed species")
    species_path = _save(fig, out_dir, "top_species.png")
    return [trend_path, species_path]
