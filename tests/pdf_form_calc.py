# -*- coding: utf-8 -*-
"""Run a PDF form's calculation scripts the way a viewer does, for tests.

The scripts are run by Windows' own JScript engine (cscript, ES3), against a small
stand-in for the viewer's form API: `this.getField(name).value` and `event.value`.
After the given edits, every field in the form's calculation order (/CO) is worked
out once, as Acrobat does when a field changes. Returns the field values, or None
where cscript is not available.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import fitz


def form_scripts(pdf: bytes) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """({field: value}, [(field, calculation script)] in the form's /CO order)."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    values: dict[str, str] = {}
    by_xref: dict[int, tuple[str, str]] = {}
    for page in doc:
        for widget in page.widgets():
            values[widget.field_name] = widget.field_value or ""
            if widget.script_calc:
                by_xref[widget.xref] = (widget.field_name, widget.script_calc)
    kind, co = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/CO")
    order = [int(token) for token in co.strip("[]").split()[::3]] if kind == "array" else []
    return values, [by_xref[x] for x in order if x in by_xref]


def run_calculations(pdf: bytes, edits: dict[str, str]) -> dict[str, str] | None:
    if not shutil.which("cscript"):
        return None
    values, scripts = form_scripts(pdf)
    values.update(edits)
    lines = ["var fields = {};"]
    for name, value in values.items():
        lines.append(f"fields[{json.dumps(name)}] = {{value: {json.dumps(value)}}};")
    lines.append("var doc = {getField: function (n) { return fields[n] || null; }};")
    lines.append("var event = {value: ''};")
    for name, script in scripts:
        lines.append(f"event.value = fields[{json.dumps(name)}].value;")
        lines.append(f"(function () {{ {script} }}).call(doc);")
        lines.append(f"fields[{json.dumps(name)}].value = String(event.value);")
    # The script is pure ASCII (json escapes the rest) and the values come back through
    # escape(): cscript's console output is in the ANSI code page.
    lines.append("for (var k in fields) { WScript.Echo(escape(k) + ' ' + escape(fields[k].value)); }")
    handle, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(handle, "w", encoding="ascii") as out:
            out.write("\n".join(lines))
        run = subprocess.run(["cscript", "//nologo", "//E:jscript", path],
                             capture_output=True, timeout=60)
    finally:
        os.remove(path)
    text = run.stdout.decode("ascii", "replace")
    if run.returncode != 0:
        raise RuntimeError(text + run.stderr.decode("ascii", "replace"))
    out: dict[str, str] = {}
    for line in text.splitlines():
        if " " in line:
            key, value = line.split(" ", 1)
            out[_unescape(key)] = _unescape(value)
    return out


def _unescape(text: str) -> str:
    """The inverse of JavaScript's escape(): %XX and %uXXXX."""
    import re
    return re.sub(r"%u([0-9A-Fa-f]{4})|%([0-9A-Fa-f]{2})",
                  lambda m: chr(int(m.group(1) or m.group(2), 16)), text)
