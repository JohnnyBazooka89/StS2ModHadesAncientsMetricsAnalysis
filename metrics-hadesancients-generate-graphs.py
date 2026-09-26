import argparse
import json
import pandas as pd
import plotly.express as px
import plotly.io as pio
from pathlib import Path

# ============================================================
# 1. CONFIGURATION
# ============================================================

# Base folder containing the Python script
BASE_FOLDER = Path(__file__).resolve().parent

# Load the list of Ancients to process from ancient-map.json.
ANCIENT_MAP_FILE = BASE_FOLDER / "ancient-map.json"
with ANCIENT_MAP_FILE.open("r", encoding="utf-8") as f:
    GODS = list(json.load(f).keys())

# Allow input and output folders to be specified on the command line.
parser = argparse.ArgumentParser(description="Generate Ancient choice graphs from CSV reports")
parser.add_argument("--source_directory", default="report-hadesancients", type=Path,
                    help="Directory containing the CSV reports (default: report-hadesancients)")
parser.add_argument("--destination_directory", default="report-hadesancients-graphs", type=Path,
                    help="Directory for the generated HTML file (default: report-hadesancients-graphs)")
args = parser.parse_args()

# Relative paths are resolved from the script's directory, as before.
DATA_FOLDER = args.source_directory
if not DATA_FOLDER.is_absolute():
    DATA_FOLDER = BASE_FOLDER / DATA_FOLDER

OUTPUT_FOLDER = args.destination_directory
if not OUTPUT_FOLDER.is_absolute():
    OUTPUT_FOLDER = BASE_FOLDER / OUTPUT_FOLDER

# Create the output folder if it doesn't exist
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

# Output HTML file
OUTPUT_FILE = OUTPUT_FOLDER / "ancient_choices_all_gods.html"


# ============================================================
# 2. GRAPH GENERATION
# ============================================================

def create_graph(god_name):

    # Construct the CSV filename
    filename = DATA_FOLDER / f"06_ancient_choices_{god_name}.csv"

    # Skip missing files
    if not filename.exists():
        print(f"WARNING: File not found: {filename.name}")
        return None

    # Load CSV
    df = pd.read_csv(filename)

    # Remove the technical prefix from relic names
    df["relic_name"] = (
        df["ancient_relic_id"]
        .str.replace("HADESANCIENTS-", "", regex=False)
        .str.replace("-", " ")
        .str.title()
    )

    # Create scatter plot
    fig = px.scatter(
        df,

        x="pick_pct",
        y="picked_win_pct",

        # Display relic names directly on the graph
        text="relic_name",
        hover_name="relic_name",

        # Point size represents sample size
        size="picked_runs",
        size_max=30,

        # Color represents win rate
        color="picked_win_pct",
        color_continuous_scale="Viridis",

        # Additional information on hover
        hover_data={
            "pick_pct": ":.2f",
            "picked_win_pct": ":.2f",
            "picked_runs": True,
            "offered_runs": True,
            "picked_wins": True,
            "picked_losses": True,
            "skipped_win_pct": ":.2f",
            "win_pct_point_difference": ":.2f",
        },

        title=f"{god_name}",

        labels={
            "pick_pct": "Pick Rate (%)",
            "picked_win_pct": "Win Rate (%)",
            "picked_runs": "Times Picked",
            "offered_runs": "Times Offered",
            "picked_wins": "Wins When Picked",
            "picked_losses": "Losses When Picked",
            "skipped_win_pct": "Win Rate When Skipped (%)",
            "win_pct_point_difference": "Win Rate Difference (pp)",
        },
    )

    # ========================================================
    # 3. GRAPH FORMATTING
    # ========================================================

    fig.update_xaxes(
        range=[0, 100],
        ticksuffix="%",
        dtick=10,
    )

    fig.update_yaxes(
        range=[0, 100],
        ticksuffix="%",
        dtick=10,
    )

    # Show names above each point
    fig.update_traces(
        textposition="top center",
        textfont=dict(size=11),
        cliponaxis=False,
    )

    fig.update_layout(
        template="plotly_dark",

        # Increase height to accommodate relic labels
        height=750,

        # Hide redundant color scale
        coloraxis_showscale=False,

        hovermode="closest",

        title=dict(
            text=god_name,
            x=0.5,
            xanchor="center",
            font=dict(size=30),
        ),

        margin=dict(
            l=80,
            r=80,
            t=100,
            b=80,
        ),
    )

    return fig


# ============================================================
# 4. GENERATE ALL GRAPHS
# ============================================================

html_graphs = []

for god in GODS:

    print(f"Processing {god}...")

    fig = create_graph(god)

    if fig is None:
        continue

    # Convert Plotly graph to HTML.
    # Load Plotly's JavaScript only once, in the first graph.
    graph_html = pio.to_html(
        fig,
        full_html=False,
        include_plotlyjs=True if not html_graphs else False,
    )

    html_graphs.append(graph_html)


# ============================================================
# 5. COMBINE INTO ONE HTML FILE
# ============================================================

html_content = """
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">

    <title>Ancient Choices - All Gods</title>

    <style>

        body {
            background-color: #111111;
            color: white;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 30px;
        }

        h1 {
            text-align: center;
            margin-bottom: 40px;
        }

        .graph-container {
            max-width: 1500px;
            margin: 0 auto 50px auto;
            padding: 15px;
            background-color: #1a1a1a;
            border-radius: 12px;
        }

        hr {
            border: none;
            border-top: 1px solid #444;
            margin: 50px 0;
        }

    </style>

</head>

<body>

<h1>Ancient Choices: Pick Rate vs Win Rate</h1>

"""

# Append each graph vertically
for graph_html in html_graphs:

    html_content += f"""
    <div class="graph-container">
        {graph_html}
    </div>
    <hr>
    """

html_content += """

</body>
</html>
"""

# Save combined HTML
OUTPUT_FILE.write_text(
    html_content,
    encoding="utf-8",
)

print()
print(f"Successfully generated {len(html_graphs)} graphs.")
print(f"Output saved to: {OUTPUT_FILE}")

# Open the HTML file automatically in your browser
import webbrowser

# webbrowser.open(OUTPUT_FILE.as_uri())