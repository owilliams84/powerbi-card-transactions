"""Draw the portfolio card image for the model performance case study.

    python perf/etl/build_card_image.py --out <path to assets/work/model-performance.png>

The other work cards on milestonebi.com show a screenshot of the report, because the artefact
is a report. Here the artefact is a measurement, so the card shows the measurement - drawn from
perf/web/model-performance.json, not laid out by hand, so it cannot drift from the numbers on
the page it links to.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "perf" / "web" / "model-performance.json"

W, H = 2560, 1600
INK = (10, 9, 23)
PAPER = (244, 246, 250)
CARD = (255, 255, 255)
RULE = (227, 231, 239)
BODY = (74, 87, 104)
MUTED = (102, 114, 132)
GOLD = (201, 162, 39)
NAVY = (17, 31, 56)
MB = 1048576

FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int):
    for candidate in (name, "segoeui.ttf", "arial.ttf"):
        p = FONTS / candidate
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d = json.loads(DATA.read_text(encoding="utf-8"))

    f_band = font("segoeuib.ttf", 46)
    f_h1 = font("segoeuib.ttf", 88)
    f_lede = font("segoeui.ttf", 40)
    f_kpi = font("segoeuib.ttf", 104)
    f_kpi_lab = font("segoeui.ttf", 34)
    f_axis = font("segoeui.ttf", 32)
    f_bar = font("segoeuib.ttf", 40)

    im = Image.new("RGB", (W, H), PAPER)
    dr = ImageDraw.Draw(im)

    # Brand band, the same one the reports carry.
    dr.rectangle([0, 0, W, 150], fill=INK)
    dr.text((64, 52), "Milestone ", font=f_band, fill=CARD)
    w = dr.textlength("Milestone ", font=f_band)
    dr.text((64 + w, 52), "BI", font=f_band, fill=GOLD)
    dr.text((W - 64, 58), "MODEL PERFORMANCE", font=font("consolab.ttf", 30), fill=GOLD,
            anchor="ra")

    dr.text((64, 210), "The same data, modelled twice", font=f_h1, fill=INK)
    dr.text((64, 330),
            "1,682,640 transactions as a star schema, and as one flat table.",
            font=f_lede, fill=BODY)
    dr.text((64, 386),
            "Identical rows. Nine of ten queries return identical numbers.",
            font=f_lede, fill=BODY)

    # KPI strip.
    two = d["naive"]["top_columns"][0]["total"] + d["naive"]["top_columns"][1]["total"]
    div = d["divergence"][0]
    wrong = float(div["naive"][-1]["[YoY]"]) * 100
    right = float(div["star"][-1]["[YoY]"]) * 100
    kpis = [
        (f"{d['star']['total_bytes'] / MB:.1f} MB", "Star schema"),
        (f"{d['naive']['total_bytes'] / MB:.1f} MB", "One flat table"),
        (f"{d['naive']['total_bytes'] / d['star']['total_bytes']:.1f}x", "The difference"),
        (f"{two / d['naive']['total_bytes'] * 100:.0f}%", "Is two unused columns"),
    ]
    y0, kh = 480, 220
    kw = (W - 128) // len(kpis)
    dr.rectangle([64, y0, W - 64, y0 + kh], fill=CARD, outline=RULE, width=2)
    for i, (value, label) in enumerate(kpis):
        x = 64 + i * kw
        dr.rectangle([x + 28, y0 + 34, x + 34, y0 + kh - 34], fill=GOLD)
        dr.text((x + 62, y0 + 44), value, font=f_kpi, fill=INK)
        dr.text((x + 62, y0 + 160), label, font=f_kpi_lab, fill=MUTED)

    # The four storage measures, star against flat.
    groups = [("Total", "total_bytes"), ("Dictionaries", "dictionary_bytes"),
              ("Compressed data", "data_bytes"), ("Hierarchies", "hierarchy_bytes")]
    cx0, cy0, cx1, cy1 = 64, 760, W - 64, 1430
    dr.rectangle([cx0, cy0, cx1, cy1], fill=CARD, outline=RULE, width=2)
    dr.text((cx0 + 44, cy0 + 34), "In memory, by component",
            font=font("segoeuib.ttf", 44), fill=INK)

    base = cy1 - 110
    # The tallest bar carries a label above it, so the plot top has to clear both that and
    # the legend - which now sits on the right, where no bar reaches.
    top = cy0 + 200
    peak = max(d["naive"][k] for _, k in groups)
    band = (cx1 - cx0 - 160) / len(groups)
    for i, (label, key) in enumerate(groups):
        gx = cx0 + 80 + band * i
        for j, (model, colour) in enumerate((("star", NAVY), ("naive", GOLD))):
            v = d[model][key]
            h = (base - top) * (v / peak)
            bw = band * 0.24
            x = gx + band * 0.18 + j * (bw + 22)
            dr.rectangle([x, base - h, x + bw, base], fill=colour)
            dr.text((x + bw / 2, base - h - 14), f"{v / MB:.1f}", font=f_bar,
                    fill=INK, anchor="mb")
        dr.text((gx + band / 2, base + 22), label, font=f_axis, fill=MUTED, anchor="ma")
        ratio = d["naive"][key] / d["star"][key]
        dr.text((gx + band / 2, base + 66),
                f"{ratio:.0f}x" if ratio >= 10 else f"{ratio:.1f}x",
                font=font("segoeuib.ttf", 34), fill=GOLD if ratio > 2 else MUTED, anchor="ma")
    dr.line([cx0 + 80, base, cx1 - 80, base], fill=RULE, width=3)

    for j, (name, colour) in enumerate((("Star schema", NAVY), ("One flat table", GOLD))):
        lx = cx1 - 700 + j * 360
        dr.rectangle([lx, cy0 + 44, lx + 26, cy0 + 70], fill=colour)
        dr.text((lx + 40, cy0 + 42), name, font=f_axis, fill=BODY)

    # The finding that is not about memory at all.
    dr.rectangle([64, 1456, W - 64, 1568], fill=INK)
    dr.text((110, 1480),
            f"And the flat model reports {div['star'][-1]['Date[Year]']} spend down "
            f"{abs(wrong):.1f}%.",
            font=font("segoeuib.ttf", 40), fill=CARD)
    dr.text((110, 1526), f"It fell {abs(right):.1f}%.",
            font=font("segoeui.ttf", 36), fill=GOLD)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)
    print(f"{out} {W}x{H}, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
