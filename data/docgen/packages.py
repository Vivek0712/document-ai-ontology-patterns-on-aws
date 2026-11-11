"""Package definitions (which documents, which seeds) and the golden questions over them.

Packages
    lending      pkg-lending-01    D3 headline package, fixed values, user A
    lending-02   pkg-lending-02    D3 seeded variant (titled balance chart for the figure question), user A
    quarterly    pkg-quarterly-01  D1 x3 (FY2025 reference values, FY2024 and FY2023 seeded), both groups
    tradeconf    pkg-tradeconf-01  D2 x3 (XS-2031 reference values, XS-2032 and XS-2033 seeded), both groups
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from evidence_core.ids import doc_id_for
from evidence_core.models.golden import GoldenEntry

from . import bank, payslip, quarterly, tradeconf, w2
from .common import GoldDoc, d, money, rng

EMPLOYER_SERVICES = ("NewAge Global Fintech Services", ("400 Market Street", "Seattle, WA 98104"), "00-4417305")
EMPLOYER_PAYMENTS = ("NewAge Global Fintech Payments", ("410 Market Street", "Seattle, WA 98104"), "00-4417306")


@dataclass
class PackageSpec:
    key: str  # sample directory name
    package_id: str
    name: str
    owner_email: str
    allowed_groups: list[str]
    seed: int
    builders: list[Callable[[Path], GoldDoc]]
    questions: Callable[[PackageSpec, dict[str, GoldDoc]], list[GoldenEntry]]
    description: str = ""
    expected: dict[str, Any] = field(default_factory=dict)


def _bbox_of(doc: GoldDoc, region_id: str):
    return doc.region(region_id).bbox.rounded()


def _q(pkg: PackageSpec, doc: GoldDoc, *, qid: str, qtype: str, question: str, answer: str, pattern: str,
       region_id: str, headline: bool = False, aliases: list[str] | None = None) -> GoldenEntry:
    region = doc.region(region_id)
    return GoldenEntry(qid=qid, type=qtype, question=question, answer=answer, answer_pattern=pattern,
                       package_id=pkg.package_id, doc_id=doc_id_for(pkg.package_id, doc.file_name),
                       file_name=doc.file_name, page=region.page, gold_bbox=region.bbox.rounded(),
                       headline=headline, aliases=aliases or [])


def _amount_pattern(value: Decimal | str) -> str:
    """``169650.00`` -> regex accepting ``169,650.00`` / ``169650`` / ``$169,650``."""
    q = d(value)
    whole = f"{int(q):,}".replace(",", ",?")
    cents = f"{q:.2f}".split(".")[1]
    return rf"(?<![\d,])\$?{whole}(?:\.{cents})?(?!\.?\d)"


# --------------------------------------------------------------------------- lending


@dataclass(frozen=True)
class Borrower:
    name: str
    slug: str
    address: tuple[str, str]
    ssn_last4: str
    employer: tuple[str, tuple[str, str], str]
    gross_2025: Decimal
    deferral_2025: Decimal
    federal_2025: Decimal
    monthly_gross_2026: Decimal
    monthly_deferral_2026: Decimal
    monthly_federal_2026: Decimal
    scanned_payslip: bool


def _lending_builders(borrowers: tuple[Borrower, Borrower], *, tax_year: int, account_number: str, opening: Decimal,
                      others: tuple[bank.Txn, ...], interest: Decimal, titled_chart: bool,
                      scan_seed: int) -> list[Callable[[Path], GoldDoc]]:
    builders: list[Callable[[Path], GoldDoc]] = []
    payslip_specs = []
    for i, b in enumerate(borrowers):
        emp_name, emp_addr, ein = b.employer
        w2_spec = w2.W2Spec(
            file_name=f"w2_{tax_year}_{b.slug}.pdf", tax_year=tax_year, employee_name=b.name,
            employee_address=b.address, ssn_last4=b.ssn_last4, employer_name=emp_name, employer_address=emp_addr,
            ein=ein, control_number=f"NGF-{tax_year % 100}-{b.ssn_last4}", gross=b.gross_2025,
            deferral_401k=b.deferral_2025, federal_tax=b.federal_2025,
        )
        builders.append(lambda out, s=w2_spec: w2.generate(s, out))
        suffix = "_scan" if b.scanned_payslip else ""
        ps = payslip.PayslipSpec(
            file_name=f"payslip_{tax_year + 1}-09_{b.slug}{suffix}.pdf", employee_name=b.name,
            employee_id=f"E-{b.ssn_last4}", employer_name=emp_name, employer_address=", ".join(emp_addr),
            year=tax_year + 1, month=9, month_label="Sep", last_day=30, gross=b.monthly_gross_2026,
            deferral=b.monthly_deferral_2026, federal=b.monthly_federal_2026, scanned=b.scanned_payslip,
            scan_seed=scan_seed + i,
        )
        payslip_specs.append(ps)
    for ps in payslip_specs:
        builders.append(lambda out, s=ps: payslip.generate(s, out))
    deposits = tuple(
        bank.Txn(30, f"Payroll {ps.employer_name}", ps.lines()["net"][0]) for ps in payslip_specs
    ) + (bank.Txn(30, "Interest credit", interest),)
    bank_spec = bank.BankSpec(
        file_name=f"bank_statement_{tax_year + 1}-09.pdf", holders=tuple(b.name for b in borrowers),
        account_number=account_number, year=tax_year + 1, month=9, month_label="Sep", last_day=30, opening=opening,
        deposits=deposits, others=others, titled_chart=titled_chart,
    )
    builders.append(lambda out, s=bank_spec: bank.generate(s, out))
    return builders


def _non_luhn_digits(r, n: int) -> str:
    """Random n-digit string that fails the Luhn check (cannot pass as a card number)."""
    while True:
        digits = str(r.randrange(1, 10)) + "".join(str(r.randrange(10)) for _ in range(n - 1))
        if not luhn_valid(digits):
            return digits


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 1:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


OTHERS_01 = (
    bank.Txn(1, "Rent payment", Decimal("-2450.00")),
    bank.Txn(3, "Utilities", Decimal("-186.40")),
    bank.Txn(7, "Card payment", Decimal("-1212.35")),
    bank.Txn(12, "Groceries", Decimal("-264.80")),
    bank.Txn(18, "Insurance premium", Decimal("-310.00")),
    bank.Txn(22, "Transfer to savings", Decimal("-1500.00")),
    bank.Txn(26, "Mobile phone", Decimal("-85.00")),
)

LENDING_01_BORROWERS = (
    Borrower("Maya Fictus", "maya_fictus", ("18 Harbor Lane", "Seattle, WA 98101"), "0417", EMPLOYER_SERVICES,
             Decimal("105000.00"), Decimal("6600.00"), Decimal("13776.00"),
             Decimal("9000.00"), Decimal("600.00"), Decimal("1170.00"), False),
    Borrower("Arjun Fictus", "arjun_fictus", ("18 Harbor Lane", "Seattle, WA 98101"), "2290", EMPLOYER_PAYMENTS,
             Decimal("75000.00"), Decimal("3750.00"), Decimal("8550.00"),
             Decimal("6500.00"), Decimal("325.00"), Decimal("715.00"), True),
)


def _lending_questions(prefix: str, pkg: PackageSpec, docs: dict[str, GoldDoc], borrowers: tuple[Borrower, Borrower],
                       *, tax_year: int, full: bool) -> list[GoldenEntry]:
    w2s = [docs[f"w2_{tax_year}_{b.slug}.pdf"] for b in borrowers]
    box1 = [d(w.attributes["boxes"]["1"]) for w in w2s]
    total = sum(box1, Decimal("0"))
    pay = {b.slug: next(v for k, v in docs.items() if k.startswith(f"payslip_{tax_year + 1}-09_{b.slug}"))
           for b in borrowers}
    out = [
        _q(pkg, w2s[0], qid=f"{prefix}-01", qtype="table", question="What is the total income across this package?",
           answer=money(total), pattern=_amount_pattern(total), region_id="w2-box1", headline=True,
           aliases=["What is the total qualifying income across this package?",
                    "What is the total income for this lending package?"]),
    ]
    n = 2
    for b, w, v in zip(borrowers, w2s, box1, strict=True):
        out.append(_q(pkg, w, qid=f"{prefix}-{n:02d}", qtype="table",
                      question=f"What are {b.name}'s {tax_year} wages in W-2 Box 1?", answer=money(v),
                      pattern=_amount_pattern(v), region_id="w2-box1"))
        n += 1
    scanned = [b for b in borrowers if b.scanned_payslip]
    for b in scanned + ([b for b in borrowers if not b.scanned_payslip] if full else []):
        doc = pay[b.slug]
        ytd = d(doc.attributes["grossYtd"])
        out.append(_q(pkg, doc, qid=f"{prefix}-{n:02d}", qtype="table",
                      question=f"What is {b.name}'s year-to-date gross pay on the September {tax_year + 1} payslip?",
                      answer=money(ytd), pattern=_amount_pattern(ytd), region_id="ps-gross-ytd"))
        n += 1
    first = [b for b in borrowers if not b.scanned_payslip][0]
    out.append(_q(pkg, pay[first.slug], qid=f"{prefix}-{n:02d}", qtype="prose",
                  question=f"What pay period does {first.name}'s September {tax_year + 1} payslip cover?",
                  answer=f"01–30 Sep {tax_year + 1}", pattern=rf"0?1\s*[–-]\s*30 Sep(tember)? {tax_year + 1}",
                  region_id="ps-pay-period"))
    n += 1
    if full:
        out.append(_q(pkg, w2s[0], qid=f"{prefix}-{n:02d}", qtype="prose",
                      question=f"Who is the employer on {borrowers[0].name}'s {tax_year} W-2?",
                      answer=borrowers[0].employer[0], pattern=borrowers[0].employer[0].replace(" ", r"\s+"),
                      region_id="w2-employer"))
        n += 1
    statement = docs[f"bank_statement_{tax_year + 1}-09.pdf"]
    if statement.attributes["titledChart"]:
        end = d(statement.attributes["closing"])
        out.append(_q(pkg, statement, qid=f"{prefix}-{n:02d}", qtype="figure",
                      question=f"What end-of-month balance does Figure 1 of the September {tax_year + 1} bank "
                               "statement show?",
                      answer=money(end), pattern=_amount_pattern(end), region_id="bs-chart-end"))
        n += 1
    return out


def lending_01() -> PackageSpec:
    pkg = PackageSpec(
        key="lending", package_id="pkg-lending-01", name="Lending package · Maya and Arjun Fictus",
        owner_email="user-a@example.com", allowed_groups=["lending-analyst"], seed=305,
        builders=_lending_builders(LENDING_01_BORROWERS, tax_year=2025, account_number="40917364258107",
                                   opening=Decimal("12480.65"), others=OTHERS_01, interest=Decimal("4.18"),
                                   titled_chart=False, scan_seed=305),
        questions=lambda p, docs: _lending_questions("q-lend", p, docs, LENDING_01_BORROWERS, tax_year=2025,
                                                     full=True),
        description="Lending package: two W-2s, two payslips (one scanned) and a bank statement. "
                    "Qualifying income 169,650.00 = 98,400.00 + 71,250.00.",
        expected={"qualifyingIncome": "169650.00", "operands": ["98400.00", "71250.00"], "taxYear": "2025"},
    )
    return pkg


def _seeded_borrowers(seed: int) -> tuple[Borrower, Borrower]:
    r = rng(seed, "lending-borrowers")
    out = []
    for name, slug, emp, scanned in (("Lena Fictus", "lena_fictus", EMPLOYER_SERVICES, False),
                                     ("Omar Fictus", "omar_fictus", EMPLOYER_PAYMENTS, True)):
        gross = Decimal(r.randrange(60, 130) * 1000)
        deferral = d(gross * Decimal(r.choice(["0.04", "0.05", "0.06"])))
        federal = d(gross * Decimal(r.choice(["0.11", "0.12", "0.13"])))
        monthly = d(Decimal(r.randrange(50, 110) * 100))
        out.append(Borrower(name, slug, ("7 Orchard Row", "Tacoma, WA 98402"), f"{r.randrange(1000, 9999)}", emp,
                            gross, deferral, federal, monthly, d(monthly * Decimal("0.05")),
                            d(monthly * Decimal("0.12")), scanned))
    return out[0], out[1]


def lending_02() -> PackageSpec:
    seed = 306
    borrowers = _seeded_borrowers(seed)
    r = rng(seed, "lending-bank")
    pkg = PackageSpec(
        key="lending-02", package_id="pkg-lending-02", name="Lending package · Lena and Omar Fictus",
        owner_email="user-a@example.com", allowed_groups=["lending-analyst"], seed=seed,
        builders=_lending_builders(borrowers, tax_year=2025, account_number=_non_luhn_digits(r, 14),
                                   opening=d(r.randrange(800000, 2000000) / 100), others=OTHERS_01,
                                   interest=d(r.randrange(100, 900) / 100), titled_chart=True, scan_seed=seed),
        questions=lambda p, docs: _lending_questions("q-lend2", p, docs, borrowers, tax_year=2025, full=False),
        description="Second lending package, used by the golden set; "
                    "the bank statement carries a titled balance chart.",
    )
    total = sum((d(b.gross_2025 - b.deferral_2025) for b in borrowers), Decimal("0"))
    pkg.expected = {"qualifyingIncome": f"{total:.2f}",
                    "operands": [f"{d(b.gross_2025 - b.deferral_2025):.2f}" for b in borrowers], "taxYear": "2025"}
    return pkg


# --------------------------------------------------------------------------- D1, D2


def _quarterly_questions(pkg: PackageSpec, docs: dict[str, GoldDoc], specs: list[quarterly.QuarterlySpec]
                         ) -> list[GoldenEntry]:
    out: list[GoldenEntry] = []
    for spec in specs:
        doc = docs[spec.file_name]
        fy = spec.fiscal_year
        p = f"q-qr{fy % 100}"
        q3 = spec.revenue[2]
        out.append(_q(pkg, doc, qid=f"{p}-01", qtype="table", question=f"What was total revenue in Q3 FY{fy}?",
                      answer=f"{q3:,}", pattern=rf"{q3:,}".replace(",", ",?"), region_id="qr-table3-q3",
                      aliases=[f"What was Q3 FY{fy} total revenue?"]))
        out.append(_q(pkg, doc, qid=f"{p}-02", qtype="prose",
                      question=f"How does management describe the revenue outlook for H2 in the Q3 FY{fy} report?",
                      answer="modest growth in H2", pattern=r"modest\s+growth", region_id="qr-commentary"))
        out.append(_q(pkg, doc, qid=f"{p}-03", qtype="prose",
                      question=f"How many customer accounts did NewAge Global Fintech have at the end of Q3 FY{fy}?",
                      answer=f"{spec.customer_accounts_m} million",
                      pattern=rf"{spec.customer_accounts_m.replace('.', r'\.')}\s*million", region_id="qr-accounts"))
        for i, (seg, v) in enumerate(spec.segments):
            out.append(_q(pkg, doc, qid=f"{p}-{4 + i:02d}", qtype="figure",
                          question=f"What was {seg} revenue in Q3 FY{fy} according to Figure 2?", answer=f"{v:,}",
                          pattern=rf"{v:,}".replace(",", ",?"), region_id=f"qr-fig2-{seg.lower()}"))
        out.append(_q(pkg, doc, qid=f"{p}-07", qtype="footnote",
                      question=f"Are the Q4 FY{fy} revenue figures in Table 3 final?",
                      answer="No. Q4 figures are preliminary.", pattern=r"preliminary",
                      region_id="qr-table3-footnote"))
        if fy == 2025:
            out.append(_q(pkg, doc, qid=f"{p}-08", qtype="footnote",
                          question=f"What does the Corporate line of Table 4 in the Q3 FY{fy} report exclude?",
                          answer="Discontinued operations.", pattern=r"discontinued\s+operations",
                          region_id="qr-table4-footnote"))
    return out


def quarterly_pkg() -> PackageSpec:
    specs = [quarterly.fy2025_deck(), quarterly.seeded_variant(2024, 2024), quarterly.seeded_variant(2023, 2023)]
    return PackageSpec(
        key="quarterly", package_id="pkg-quarterly-01", name="Quarterly reports · Q3 FY2023–FY2025",
        owner_email="user-a@example.com", allowed_groups=["lending-analyst", "markets-analyst"], seed=2025,
        builders=[lambda out, s=s: quarterly.generate(s, out) for s in specs],
        questions=lambda p, docs: _quarterly_questions(p, docs, specs),
        description="Quarterly report: Table 3 on page 14, footnote 1 marks Q4 as preliminary, "
                    "Table 4 split across pages 15–16.",
    )


def _trade_questions(pkg: PackageSpec, docs: dict[str, GoldDoc], specs: list[tradeconf.TradeConfSpec]
                     ) -> list[GoldenEntry]:
    out: list[GoldenEntry] = []
    for spec in specs:
        doc = docs[spec.file_name]
        sec = spec.security
        p = f"q-tc{sec[-2:]}"
        out.append(_q(pkg, doc, qid=f"{p}-01", qtype="table", question=f"What is the coupon on {sec} at issue?",
                      answer=f"{spec.coupon}%", pattern=rf"{spec.coupon.replace('.', r'\.')}\s*%",
                      region_id="tc-coupon"))
        out.append(_q(pkg, doc, qid=f"{p}-02", qtype="footnote", question=f"What coupon can {sec} step up to?",
                      answer=f"{spec.step_rate}%", pattern=rf"{spec.step_rate.replace('.', r'\.')}\s*%",
                      region_id="tc-footnote3", aliases=[f"What is the step-up coupon on {sec}?"]))
        out.append(_q(pkg, doc, qid=f"{p}-03", qtype="footnote",
                      question=f"From which year can the {sec} coupon step up?", answer=f"Year {spec.step_year}",
                      pattern=rf"year\s+{spec.step_year}\b", region_id="tc-footnote3"))
        out.append(_q(pkg, doc, qid=f"{p}-04", qtype="footnote",
                      question=f"Which rating event triggers the {sec} coupon step-up?",
                      answer=f"Issuer rating below {spec.trigger_rating}",
                      pattern=re_escape_rating(spec.trigger_rating), region_id="tc-footnote3"))
        out.append(_q(pkg, doc, qid=f"{p}-05", qtype="prose",
                      question=f"Does the {sec} step-up reverse if the rating later recovers?",
                      answer="No. The step-up applies once and does not reverse.", pattern=r"does\s+not\s+reverse",
                      region_id="tc-stepup-expl"))
    return out


def re_escape_rating(rating: str) -> str:
    return rating.replace("+", r"\+").replace("-", r"\-") + r"(?![+\-])"


def tradeconf_pkg() -> PackageSpec:
    specs = [tradeconf.xs2031(), tradeconf.seeded_variant(2, 2032), tradeconf.seeded_variant(3, 2033)]
    return PackageSpec(
        key="tradeconf", package_id="pkg-tradeconf-01", name="Trade confirmations · XS-2031–XS-2033",
        owner_email="user-a@example.com", allowed_groups=["lending-analyst", "markets-analyst"], seed=2031,
        builders=[lambda out, s=s: tradeconf.generate(s, out) for s in specs],
        questions=lambda p, docs: _trade_questions(p, docs, specs),
        description="Trade confirmation: coupon 4.25% with marker 3 on page 1, step-up footnote on page 2.",
    )


def all_packages() -> list[PackageSpec]:
    return [lending_01(), lending_02(), quarterly_pkg(), tradeconf_pkg()]
