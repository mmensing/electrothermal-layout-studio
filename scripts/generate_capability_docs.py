# SPDX-License-Identifier: MPL-2.0
"""Update only marked feature tables from the Qt-free capability registry."""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
from html import escape
from pathlib import Path

from etlayout.capabilities import CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- BEGIN GENERATED CAPABILITIES -->"
END = "<!-- END GENERATED CAPABILITIES -->"


def markdown_table():
    rows = [
        START,
        "## Versioned capability index",
        "",
        "The source registry records when each capability became available. Compare **Since**",
        "with the installed version in About or `etlayout --version`; an entry does not",
        "claim that a matching binary has been published. Analysis names describe supported",
        "modes, subject to the stated limits and prerequisites. Test links identify behavior",
        "coverage locations, not a claim that a particular CI run passed.",
        "",
        "| Stable ID / feature | GUI or CLI route | Analyses | Since | Boundary / behavior tests |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in CAPABILITIES:
        tests = ", ".join(f"[{Path(name).stem.removeprefix('test_')}]({ '../' + name })" for name in row.test_references)
        rows.append(f"| `{row.id}` · **{row.title}** | {row.gui_route} | {', '.join(row.analyses)} | {row.introduced_in} | {row.limitations} {tests} |")
    return "\n".join([*rows, "", END])


def offline_table():
    rows = [START, '<h2 id="capability-index">Versioned capability index</h2>',
            "<p>Compare the <strong>Since</strong> column with your installed application's version. "
            "Source availability does not establish that a binary has been published, a model "
            "is calibrated, or a selected result is verified. The online feature catalog links "
            "the maintained behavior tests.</p>",
            "<table><thead><tr><th>Feature / stable ID</th><th>Route</th><th>Since / limits</th></tr></thead><tbody>"]
    for row in CAPABILITIES:
        rows.append(f"<tr><td>{escape(row.title)}<br><code>{escape(row.id)}</code></td>"
                    f"<td>{escape(row.gui_route)}</td><td>{row.introduced_in}<br>{escape(row.limitations)}</td></tr>")
    return "\n".join([*rows, "</tbody></table>", END])


def replace_block(text, block, *, anchor):
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
            raise ValueError("Expected one ordered capability marker pair")
        start, end = text.index(START), text.index(END) + len(END)
        return text[:start] + block + text[end:]
    index = text.index(anchor)
    return text[:index] + block + "\n\n" + text[index:]


def generate(*, check=False, root=ROOT):
    changed = []
    for relative, render, anchor in [
        ("docs/features.md", markdown_table, "## Build the experiment"),
        ("src/etlayout/data/user-guide.html", offline_table, '<h2 id="layout">'),
    ]:
        path = root / relative
        before = path.read_text(encoding="utf-8")
        after = replace_block(before, render(), anchor=anchor)
        if before != after:
            changed.append(relative)
            if not check:
                # Preserve exact unrelated line endings in historical prose.
                old_lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
                new_lines = after.splitlines(keepends=True)
                matcher = SequenceMatcher(a=[line.rstrip("\r\n") for line in old_lines],
                                          b=[line.rstrip("\r\n") for line in new_lines], autojunk=False)
                output = []
                for tag, left, right, start, stop in matcher.get_opcodes():
                    output.extend(old_lines[left:right] if tag == "equal" else new_lines[start:stop])
                path.write_bytes("".join(output).encode("utf-8"))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated capability tables are stale; do not write")
    args = parser.parse_args()
    changed = generate(check=args.check)
    if changed:
        print(("Stale: " if args.check else "Updated: ") + ", ".join(changed))
    return int(bool(changed) and args.check)


if __name__ == "__main__":
    raise SystemExit(main())
