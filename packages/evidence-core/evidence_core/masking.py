"""Display masking and artifact preview masking."""

from __future__ import annotations

import re

_TWELVE = re.compile(r"(?<!\d)\d{12}(?!\d)")
_LONG_DIGITS = re.compile(r"(?<![\d])(\d[\d -]{6,}\d)(?![\d])")
_ARN = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[^\s\"']+")


def mask_account_ids(text: str) -> str:
    """Any standalone 12-digit sequence (AWS account id shape) -> ``************``."""
    return _TWELVE.sub("*" * 12, text)


def mask_arns(text: str) -> str:
    return _ARN.sub(lambda m: m.group(0).split(":")[0] + ":…", text)


def mask_long_digits(text: str, min_digits: int = 8, keep_last: int = 4) -> str:
    """Mask digit runs with >= ``min_digits`` digits (spaces/dashes allowed inside), keeping the
    last ``keep_last`` digits. Used for Silver/Bronze artifact previews (held PII never shown)."""

    def repl(m: re.Match[str]) -> str:
        span = m.group(1)
        digits = [c for c in span if c.isdigit()]
        if len(digits) < min_digits:
            return span
        keep = "".join(digits[-keep_last:]) if keep_last else ""
        return "•" * (len(digits) - len(keep)) + keep

    return _LONG_DIGITS.sub(repl, text)


def mask_for_display(text: str) -> str:
    return mask_arns(mask_account_ids(text))


def short_execution(arn: str | None, n: int = 6) -> str | None:
    """Execution ARN -> last segment truncated to n chars (UI rule)."""
    if not arn:
        return None
    return arn.rsplit(":", 1)[-1][:n]
