# SPDX-License-Identifier: MPL-2.0
"""Render measured spatial/time sensitivity; JSON observations remain authoritative."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, ScalarFormatter


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=root / 'docs/example-science/operating-scenarios.json')
    parser.add_argument('--output', type=Path, default=root / 'docs/images/thermal-sensitivity')
    parser.add_argument('--grayscale', action='store_true', help='Independent print-readability preview')
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding='utf-8'))
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.labelsize': 9,
        'axes.titlesize': 10, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
        'svg.fonttype': 'none', 'axes.linewidth': .6, 'savefig.facecolor': 'white'})
    color = '#242424' if args.grayscale else '#1F6F5F'
    figure, axes = plt.subplots(1, 3, figsize=(178 / 25.4, 86 / 25.4))
    figure.subplots_adjust(left=.105, right=.98, bottom=.24, top=.80, wspace=.53)
    panels = [('E26 · tapered metal', 'mesh', 'E26'), ('E35 · partial die attach', 'mesh', 'E35'),
              ('E36 · pulsed bridge', 'time', 'E36')]
    for ax, (title, kind, identifier) in zip(axes, panels, strict=True):
        if kind == 'mesh':
            rows = [row for row in data['spatial_sensitivity'] if row['id'] == identifier]
            x = [row['factor'] for row in rows]
            y = [row['temperature_max_K'] for row in rows]
            ax.set_xlabel('Mesh size factor')
            ax.set_xticks(x, ['1.0', '0.8', '0.65'])
            ax.set_xlim(1.04, .61)
            detail = ' / '.join(f"{row['elements']/1000:.1f}k" for row in rows) + ' triangles'
        else:
            rows = data['fixed_mesh_time_sensitivity']['rows']
            x = [row['step_s']*1e6 for row in rows]
            y = [row['peak_K'] for row in rows]
            ax.set_xlabel('Time step (µs)')
            ax.set_xticks(x, ['10', '5', '2.5'])
            ax.set_xlim(10.8, 1.7)
            detail = 'Same mesh · 3,054 triangles'
        ax.plot(x, y, color=color, marker='o', markersize=4.5, linewidth=1.5)
        ax.set_title(title, loc='left', pad=18)
        ax.text(0, 1.015, detail, transform=ax.transAxes, fontsize=7.5, color='#555555', va='bottom')
        ax.set_ylabel('Peak temperature (K)', labelpad=5)
        ax.yaxis.set_major_locator(MaxNLocator(4))
        formatter = ScalarFormatter(useOffset=False)
        formatter.set_scientific(False)
        ax.yaxis.set_major_formatter(formatter)
        ax.grid(axis='y', color='#E4E6E7', linewidth=.5)
        ax.set_axisbelow(True)
        ax.spines[['top', 'right']].set_visible(False)
        ax.spines[['left', 'bottom']].set_color('#777777')
        ax.tick_params(length=3, width=.6, colors='#242424')
        ax.margins(y=.25)
    figure.text(.105, .055, 'Actual synthetic calculations · separate vertical scales · no experimental calibration', fontsize=8, color='#555555')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('.svg', '.png'):
        figure.savefig(args.output.with_suffix(suffix), dpi=200,
            metadata={'Creator': 'Electrothermal Layout Studio'} if suffix == '.svg' else {'Software': 'Electrothermal Layout Studio'})
    plt.close(figure)


if __name__ == '__main__':
    main()
