#!/usr/bin/env python3
"""Render the demo status line to docs/screenshot.png.

Usage: uv run --with playwright scripts/screenshot.py [output.png]
"""
import html
import os
import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
import demo

ROOT = os.path.join(os.path.dirname(__file__), "..")
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def xterm(n):
    """256-colour index -> css colour (cube and grey ramp only; the script uses nothing below 16)."""
    if n >= 232:
        v = 8 + (n - 232) * 10
        return f"rgb({v},{v},{v})"
    n -= 16
    r, g, b = ((0 if x == 0 else 55 + x * 40) for x in (n // 36, n % 36 // 6, n % 6))
    return f"rgb({r},{g},{b})"


def to_html(line):
    out, pos, open_span = [], 0, False
    for m in SGR.finditer(line):
        out.append(html.escape(line[pos:m.start()]))
        pos = m.end()
        if open_span:
            out.append("</span>")
            open_span = False
        parts = m.group(1).split(";")
        if parts[:2] == ["38", "5"]:
            style = f"color:{xterm(int(parts[2]))}"
            if parts[3:5] == ["48", "5"]:
                style += f";background:{xterm(int(parts[5]))}"
            out.append(f'<span style="{style}">')
            open_span = True
    out.append(html.escape(line[pos:]))
    return "".join(out) + ("</span>" if open_span else "")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshot.png")
    rows = "\n".join(to_html(l) for l in demo.render())
    page_html = (
        "<body style='margin:0;background:#1e1e2e'><pre id='t' style=\"display:inline-block;margin:0;"
        "padding:14px 18px;font:15px/1.5 'DejaVu Sans Mono',monospace;color:#d0d0d0\">" + rows + "</pre></body>"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=2)
        page.set_content(page_html)
        page.locator("#t").screenshot(path=target)
        browser.close()
    print(target)


if __name__ == "__main__":
    main()
