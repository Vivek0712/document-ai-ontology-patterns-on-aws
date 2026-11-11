"""Finding the figures in answer text, and deciding which ones are accounted for.

A "figure" is an amount-like number: it has a thousands separator or decimals, or at least
three digits. One- and two-digit integers (``Box 1``, ``p.1``) and plain years (``2025``)
are not figures. Numbers glued to letters, hyphens or colons (``W-2``, ``eu-01``,
``TY2025``) are identifiers, not figures.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"(?<![\w\-:.,/])\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w\-:])")
_ARITH_TOKEN = re.compile(r"([+\-−])?\s*\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def to_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(text))
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def norm(text: str | None) -> str | None:
    """Canonical two-decimal string used to compare figures: ``$169,650`` -> ``169650.00``."""
    d = to_decimal(text)
    return f"{d:.2f}" if d is not None else None


def display(value: str | None) -> str | None:
    """``169650.00`` -> ``169,650.00``."""
    d = to_decimal(value)
    return f"{d:,.2f}" if d is not None else None


def is_figure(token: str) -> bool:
    if "," in token or "." in token:
        return True
    digits = token.lstrip("$")
    if len(digits) < 3:
        return False
    return not (len(digits) == 4 and 1900 <= int(digits) <= 2100)


def figures(text: str | None) -> list[str]:
    """Figures in reading order, as written (without ``$``)."""
    if not text:
        return []
    return [m.group(1) for m in _NUMBER.finditer(text) if is_figure(m.group(1))]


def evaluate_arithmetic(text: str | None) -> tuple[list[str], str, bool] | None:
    """``98,400.00 + 71,250.00 = 169,650.00`` -> (operands (normalised), result (normalised), holds).
    None when the text is not a single ``lhs = rhs`` sum/difference."""
    if not text or text.count("=") != 1:
        return None
    lhs, rhs = text.split("=")
    tokens = _ARITH_TOKEN.findall(lhs)
    result = norm(rhs.strip())
    if not tokens or result is None:
        return None
    total = Decimal(0)
    operands: list[str] = []
    for sign, number in tokens:
        value = to_decimal(number)
        if value is None:
            return None
        total += -value if sign in ("-", "−") else value
        operands.append(f"{value:.2f}")
    return operands, result, f"{total:.2f}" == result
