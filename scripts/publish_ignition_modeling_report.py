"""Package frozen modelling results for reading; never fit or choose a model."""
import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ignition_modeling.report import render


def inline(text):
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)


def report_html(markdown, output):
    """Render the known heading/paragraph/table/code/image blocks in this report."""
    blocks = []
    for block in markdown.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        if block.startswith("```"):
            blocks.append("<pre>" + html.escape("\n".join(lines[1:-1])) + "</pre>")
        elif lines[0].startswith("#"):
            level = len(lines[0]) - len(lines[0].lstrip("#"))
            blocks.append(f"<h{level}>" + inline(lines[0][level:].strip()) + f"</h{level}>")
        elif block.startswith("|"):
            rows = [line.strip("|").split("|") for line in lines]
            header = "".join("<th>" + inline(cell.strip()) + "</th>" for cell in rows[0])
            body = "".join("<tr>" + "".join("<td>" + inline(cell.strip()) + "</td>" for cell in row) + "</tr>" for row in rows[2:])
            blocks.append(f"<div class='table'><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>")
        elif match := re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", block):
            path = (output / match[2]).resolve()
            if path.parent != output.resolve() or path.suffix != ".png":
                raise ValueError("Report image must be a local generated PNG")
            data = base64.b64encode(path.read_bytes()).decode()
            blocks.append(f"<img alt='{html.escape(match[1], quote=True)}' src='data:image/png;base64,{data}'>")
        else:
            blocks.append("<p>" + inline(block) + "</p>")
    css = "body{font:16px/1.6 system-ui,sans-serif;color:#183045;max-width:1160px;margin:40px auto;padding:0 24px}h1{font-size:32px}h2{margin-top:36px;border-bottom:1px solid #c8d4df;padding-bottom:8px}img{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #d9e1e8}th{background:#edf3f7}pre,code{background:#edf3f7;border-radius:4px}pre{padding:18px;overflow:auto}.table{overflow:auto}code{font-size:90%;overflow-wrap:anywhere}a{color:#17649b}"
    return "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ignition modelling results</title><style>" + css + "</style><body>" + "\n".join(blocks) + "<p><a href='verification_report.md'>Independent verification</a> · <a href='feature_manifest.json'>Feature manifest</a> · <a href='test_threshold_tradeoffs.csv'>All threshold tradeoffs</a></p></body></html>"


def publish(run, output):
    render(run, output)
    report = output / "final_report.md"
    text = report.read_text(encoding="utf-8")
    text = re.sub(r"\b(was|exactly|adds|use|years|on|at|is|before|saved|per|approved|in)(?=\d)", r"\1 ", text)
    text = text.replace("Selected **hgb**", "Selected **histogram gradient boosting**")
    text = text.replace("python -m scripts.ignition_modeling.report", "python scripts/publish_ignition_modeling_report.py")
    trades = pd.read_csv(output / "test_threshold_tradeoffs.csv")
    examples = trades.iloc[[1, 3, 5, 7]]
    table = ["### Examples of frozen threshold tradeoffs", "", "| Threshold | Precision | Recall | Alerts/day | False alerts/day |", "|---:|---:|---:|---:|---:|"]
    for row in examples.itertuples():
        table.append(f"|{row.threshold:.6f}|{row.precision:.2%}|{row.recall:.2%}|{row.alerts_per_day:.2f}|{row.false_alerts_per_day:.2f}|")
    table += ["", "These are examples from the eight thresholds frozen using 2018 scores. They are not recommended operating points. Ranking improves over the prevalence baseline, but absolute precision remains low and false alerts dominate. ROC-AUC is a ranking statistic, not classification accuracy.", ""]
    text = text.replace("## Artifacts and reproduction", "\n".join(table) + "\n## Artifacts and reproduction")
    pr = pd.read_csv(run / "curves/test_2019_precision_recall.csv")
    final = json.loads((run / "final_test.json").read_text())
    figure, axis = plt.subplots(figsize=(8, 4.5), layout="constrained")
    axis.plot(pr.recall, pr.precision, color="#1d7545", label="Selected HGB")
    axis.axhline(final["prevalence"], color="#777", linestyle="--", label="Test prevalence")
    axis.set(xlabel="Recall", ylabel="Precision (logarithmic scale)", yscale="log", xlim=(0, 1),
             ylim=(final["prevalence"] / 2, 1), title="2019 precision–recall: rare-event detail")
    axis.legend(); axis.grid(alpha=.2)
    figure.savefig(output / "precision_recall_detail.png", dpi=160); plt.close(figure)
    text = text.replace("### Examples of frozen threshold tradeoffs", "![Precision–recall with a logarithmic precision axis](precision_recall_detail.png)\n\n### Examples of frozen threshold tradeoffs")
    report.write_text(text, encoding="utf-8")
    (output / "report.html").write_text(report_html(text, output), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "artifacts/ignition_modeling/full_run")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/modeling")
    args = parser.parse_args()
    publish(args.run, args.output)
