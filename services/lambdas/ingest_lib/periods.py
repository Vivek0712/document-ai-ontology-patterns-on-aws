"""Document period detection (enrichment.period) and party-name detection for breadcrumbs.

Periods use the forms the ontology and the golden set use:
``TY2025`` (tax year), ``2026-09`` (pay/statement month), ``FY2025-Q3`` (quarter).
"""

from __future__ import annotations

import re
from collections import Counter

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_YEAR = re.compile(
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?P<year>20\d{2})\b", re.I)
_ISO_MONTH = re.compile(r"\b(?P<year>20\d{2})-(?P<mon>0[1-9]|1[0-2])(?:-\d{2})?\b")
_SLASH_DATE = re.compile(r"\b(?P<mon>0?[1-9]|1[0-2])/(?:\d{1,2})/(?P<year>20\d{2})\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
_QUARTER = re.compile(r"\bQ(?P<q>[1-4])\s*(?:FY\s*)?(?P<year>20\d{2})\b|\bFY\s*(?P<year2>20\d{2})\s*Q(?P<q2>[1-4])\b",
                      re.I)
_PERIOD_HINT = re.compile(r"\b(pay\s*period|period|statement\s*period|pay\s*date)\b", re.I)

_PARTY = re.compile(
    r"\b(?i:employee(?:'s)?(?:\s+name)?|borrower|account\s+holders?|name)\s*[:\-]?\s*"
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
)


_PERIOD_MONTH = re.compile(
    r"\bperiod\b.{0,60}?\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?P<year>20\d{2})\b",
    re.I)


def _month_period(text: str) -> str | None:
    """The month after the word "period" (table cells and line breaks flattened), else the first month
    in a line that mentions a period, else the first month anywhere."""
    flat = re.sub(r"[│|\s]+", " ", text)
    m = _PERIOD_MONTH.search(flat)
    if m:
        return f"{m.group('year')}-{MONTHS[m.group('mon').lower()[:3]]:02d}"
    lines = text.splitlines()
    ordered = [ln for ln in lines if _PERIOD_HINT.search(ln)] + lines
    for ln in ordered:
        for rx in (_MONTH_YEAR, _ISO_MONTH, _SLASH_DATE):
            m = rx.search(ln)
            if m:
                mon = m.group("mon").lower()
                month = MONTHS.get(mon[:3]) if not mon.isdigit() else int(mon)
                if month:
                    return f"{m.group('year')}-{month:02d}"
    return None


def detect_period(kind: str, title_text: str, full_text: str) -> str | None:
    if kind == "tax_year":
        for source in (title_text, full_text):
            years = _YEAR.findall(source)
            if years:
                return f"TY{Counter(years).most_common(1)[0][0] if source is full_text else years[0]}"
        return None
    if kind in ("pay_period", "statement_period"):
        return _month_period(full_text)
    if kind == "quarter":
        m = _QUARTER.search(title_text) or _QUARTER.search(full_text)
        if m:
            q = m.group("q") or m.group("q2")
            y = m.group("year") or m.group("year2")
            return f"FY{y}-Q{q}"
    return None


def period_display(period: str | None) -> str | None:
    """``TY2025`` -> ``2025``; ``2026-09`` -> ``Sep 2026``; ``FY2025-Q3`` -> ``Q3 FY2025``."""
    if not period:
        return None
    if period.startswith("TY"):
        return period[2:]
    m = re.fullmatch(r"(20\d{2})-(\d{2})", period)
    if m:
        names = list(MONTHS)
        return f"{names[int(m.group(2)) - 1].capitalize()} {m.group(1)}"
    m = re.fullmatch(r"FY(20\d{2})-Q([1-4])", period)
    if m:
        return f"Q{m.group(2)} FY{m.group(1)}"
    return period


def period_bounds(period: str | None) -> tuple[str, str] | None:
    """ISO start/end dates for a period (graph literals)."""
    if not period:
        return None
    if period.startswith("TY") and period[2:].isdigit():
        return f"{period[2:]}-01-01", f"{period[2:]}-12-31"
    m = re.fullmatch(r"(20\d{2})-(\d{2})", period)
    if m:
        import calendar

        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y}-{mo:02d}-01", f"{y}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
    m = re.fullmatch(r"FY(20\d{2})-Q([1-4])", period)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        start_m = 3 * (q - 1) + 1
        import calendar

        end_m = start_m + 2
        return f"{y}-{start_m:02d}-01", f"{y}-{end_m:02d}-{calendar.monthrange(y, end_m)[1]:02d}"
    return None


_NOT_NAME = {"Pay", "Ssn", "Employer", "Account", "Statement", "Period", "Date", "Address", "Number", "And"}


def detect_party(text: str) -> str | None:
    m = _PARTY.search(text)
    if not m:
        return None
    words = m.group("name").split()
    while words and words[-1].capitalize() in _NOT_NAME:
        words.pop()
    return " ".join(words) if len(words) >= 2 else None
