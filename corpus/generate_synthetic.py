"""
corpus/generate_synthetic.py — Deterministic synthetic tender corpus generator.

Emits paired PDFs and ground-truth annotations so retrieval and extraction can be
measured against known answers.

Why synthetic: hand-annotating real tenders is the long pole, and until that
exists there is no honest way to report Recall@K at all. These documents are
*not* a substitute for real tenders — they are cleanly-rendered text PDFs, so
they omit the scanned pages, broken tables and inconsistent formatting that make
real procurement documents hard. Any paper using this corpus must say so in its
limitations section.

Design constraints that keep the numbers meaningful:

* Each `gold_span` is written into the PDF and into the annotation from the *same*
  variable, so they cannot drift. `eval_harness.py --mode smoke` then independently
  re-verifies every span against freshly extracted text.
* Section order is shuffled per document, so answers sit at varying depths rather
  than always near the front.
* Every quantitative answer gets a **distractor** elsewhere in the document: a
  clause using the same vocabulary with a different number. Without these,
  retrieval saturates at 100% and the metric measures nothing.
* Money is expressed in mixed Indian formats (`Rs. 5,00,00,000`, `5 crore`,
  `50 lakhs`) to exercise numeric normalization.
* Two documents render their financial requirements as a table rather than prose.

Usage:
    python corpus/generate_synthetic.py --seed 42 --count 18
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF — already a project dependency; no extra PDF library needed

CORPUS_DIR = Path(__file__).resolve().parent
PDF_DIR = CORPUS_DIR / "pdfs"
ANNOTATION_DIR = CORPUS_DIR / "annotations"

# --- Page geometry (A4) ----------------------------------------------------
PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 56.0
LINE_HEIGHT = 13.0
BODY_SIZE = 10.0
WRAP_COLS = 92


# ===========================================================================
# Number / money formatting
# ===========================================================================

def inr_group(n: int) -> str:
    """Indian digit grouping: 50000000 -> '5,00,00,000'."""
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts: List[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def money(value: int, style: str) -> str:
    """Render an INR amount in one of several real-world formats."""
    if style == "crore" and value % 10_000_000 == 0:
        n = value // 10_000_000
        return f"Rs. {n} crore"
    if style == "lakh" and value % 100_000 == 0:
        n = value // 100_000
        return f"Rs. {n} lakhs"
    if style == "inr":
        return f"INR {inr_group(value)}"
    return f"Rs. {inr_group(value)}"


# ===========================================================================
# PDF writer with page tracking
# ===========================================================================

class PdfWriter:
    """
    Minimal flowing-text PDF writer.

    Line wrapping is done here with textwrap rather than delegated to
    insert_textbox, so paragraph-to-page attribution is exact — `para()` returns
    the 1-based page number its first line landed on, which becomes `gold_page`.
    """

    def __init__(self) -> None:
        self.doc = fitz.open()
        self.page: Optional[fitz.Page] = None
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = MARGIN

    def _room_for(self, lines: int) -> None:
        if self.y + lines * LINE_HEIGHT > PAGE_H - MARGIN:
            self._new_page()

    @property
    def page_number(self) -> int:
        return self.doc.page_count  # current page, 1-based

    def _write_lines(self, lines: List[str], size: float, bold: bool = False) -> int:
        """Write physical lines, returning the page number of the first one."""
        font = "hebo" if bold else "helv"
        self._room_for(1)
        first_page = self.page_number
        for line in lines:
            self._room_for(1)
            self.page.insert_text(
                (MARGIN, self.y), line, fontname=font, fontsize=size
            )
            self.y += LINE_HEIGHT
        return first_page

    def heading(self, text: str) -> int:
        self.y += LINE_HEIGHT * 0.6
        page = self._write_lines([text.upper()], size=11.5, bold=True)
        self.y += LINE_HEIGHT * 0.3
        return page

    def para(self, text: str) -> int:
        """Write a wrapped paragraph. Returns the page its first line is on."""
        lines = textwrap.wrap(" ".join(text.split()), width=WRAP_COLS) or [""]
        page = self._write_lines(lines, size=BODY_SIZE)
        self.y += LINE_HEIGHT * 0.4
        return page

    def table_row(self, label: str, value: str) -> int:
        """
        Single aligned row. Extracted text collapses the padding to whitespace,
        which the harness normalizes away before matching.
        """
        line = f"{label:<52}{value}"
        return self._write_lines([line], size=BODY_SIZE)

    def save(self, path: Path) -> None:
        self.doc.save(str(path), deflate=True)
        self.doc.close()


# ===========================================================================
# Document specification
# ===========================================================================

SECTOR_TEMPLATES = [
    ("Supply, Installation and Commissioning of Enterprise Data Centre Infrastructure", "IT Infrastructure"),
    ("Selection of System Integrator for Statewide e-Governance Platform", "System Integration"),
    ("Procurement of Solar Photovoltaic Modules and Balance of System", "Renewable Energy"),
    ("Appointment of Agency for Municipal Solid Waste Processing Facility", "Municipal Works"),
    ("Design and Development of Integrated Command and Control Centre", "Smart Cities"),
    ("Supply of Networking Equipment and Structured Cabling for Campus LAN", "Networking"),
    ("Engagement of Managed Security Services Provider for SOC Operations", "Cybersecurity"),
    ("Construction of Water Treatment Plant with O&M for Five Years", "Civil Works"),
    ("Implementation of Hospital Management Information System", "Healthcare IT"),
]

AUTHORITIES = [
    "National Institute of Advanced Systems",
    "State Urban Development Authority",
    "Directorate of Information Technology",
    "Metropolitan Transport Corporation",
    "Central Public Works Organisation",
    "State Health Services Corporation",
]

CITIES = ["New Delhi", "Mumbai", "Bengaluru", "Chennai", "Hyderabad", "Pune", "Ahmedabad"]

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

FILLER = [
    "Bidders are advised to read all instructions contained in this document carefully before "
    "preparing and submitting their bids. Non-compliance with any instruction may render a bid "
    "liable to rejection at the sole discretion of the Purchaser.",
    "The Purchaser reserves the right to accept or reject any bid, and to annul the bidding process "
    "and reject all bids at any time prior to award of contract, without thereby incurring any "
    "liability to the affected bidder or bidders.",
    "All pages of the bid document shall be serially numbered and signed by the authorised signatory "
    "of the bidder. Any interlineation, erasure or overwriting shall be valid only if initialled by "
    "the person signing the bid.",
    "Bids shall be submitted online through the designated e-procurement portal. Physical bids sent "
    "by post, courier, facsimile or electronic mail shall not be entertained under any circumstances.",
    "The successful bidder shall depute a full-time project manager for the entire duration of the "
    "contract. Any replacement of key personnel shall require prior written approval of the Purchaser.",
    "Any clarification sought by prospective bidders shall be addressed to the office of the "
    "undersigned in writing. Responses to material clarifications shall be published as a corrigendum "
    "on the portal and shall form an integral part of this document.",
    "The bidder shall be deemed to have satisfied itself as to the correctness and sufficiency of the "
    "bid and of the rates and prices quoted, which shall cover all its obligations under the contract.",
    "Conditional bids, bids with deviations from the mandatory technical specifications, and bids "
    "received after the prescribed deadline shall be summarily rejected without further correspondence.",
    "The Purchaser may, at its discretion, extend the deadline for submission of bids by issuing a "
    "corrigendum, in which case all rights and obligations of the Purchaser and the bidders shall "
    "stand extended to the revised deadline.",
    "Nothing in this document shall be construed as creating any obligation on the part of the "
    "Purchaser to shortlist, negotiate with, or award a contract to any bidder responding hereto.",
    "The bidder shall maintain complete confidentiality in respect of all data, drawings and "
    "specifications received from the Purchaser, and shall not disclose the same to any third party "
    "without prior written consent.",
    "The contractor shall comply with all applicable labour laws, minimum wage notifications and "
    "workmen's compensation requirements in respect of personnel deployed at site.",
    "Insurance cover for the full replacement value of all materials in transit and at site shall be "
    "arranged by the contractor at its own cost, with the Purchaser named as co-insured.",
    "The Purchaser shall provide reasonable access to the site and to such existing records as are "
    "necessary for execution of the work, subject to the contractor's compliance with prevailing "
    "security and safety protocols.",
    "No escalation in rates shall be admissible on account of variation in statutory levies, exchange "
    "rates or market prices during the currency of the contract, unless expressly provided herein.",
    "The contractor shall submit monthly progress reports in the prescribed format, along with an "
    "updated project schedule reflecting actual progress against planned milestones.",
    "Force majeure events shall include acts of God, war, civil commotion, epidemic and any order of a "
    "competent authority that renders performance impossible. Notice shall be given within fifteen "
    "days of the occurrence of such event.",
    "Upon completion, the contractor shall remove all temporary works, surplus materials and debris "
    "from the site and restore the premises to the satisfaction of the engineer-in-charge.",
    "Any notice required to be given under this contract shall be in writing and shall be deemed to "
    "have been served when delivered by hand against acknowledgement or by registered post.",
    "The contractor shall not assign or transfer the contract or any part thereof, or any benefit or "
    "interest therein, without the prior written consent of the Purchaser.",
]

# Parameterised clause templates. Rendering these with randomized figures keeps
# the bulk of each document unique, so filler chunks do not collapse into
# near-duplicates that would distort retrieval scores.
SPEC_CLAUSES = [
    "All active components shall be rack-mountable in standard {ru}U enclosures and shall support "
    "front-to-rear airflow. Operating temperature range shall be {tmin} to {tmax} degrees Celsius at "
    "relative humidity of up to {rh} percent non-condensing.",
    "The proposed solution shall sustain a minimum of {tps} concurrent transactions per second at a "
    "ninety-fifth percentile response time not exceeding {ms} milliseconds, as demonstrated during "
    "acceptance testing.",
    "Redundancy shall be provided such that no single point of failure exists. Availability of not "
    "less than {avail} percent shall be maintained on a monthly basis, excluding pre-approved "
    "maintenance windows not exceeding {mw} hours per month.",
    "All cabling shall conform to the applicable structured cabling standard and shall be tested "
    "end-to-end. Test reports for a minimum of {ports} ports shall be submitted in soft copy prior to "
    "commissioning.",
    "Backup of all configuration and transactional data shall be taken at intervals not exceeding "
    "{rpo} hours, with a recovery time objective of {rto} hours for the primary application tier.",
    "The contractor shall conduct {sessions} structured training sessions for a minimum of {trainees} "
    "designated personnel, and shall furnish training material in editable soft-copy format.",
    "Helpdesk support shall be available on a {shift} basis, with first response within {resp} minutes "
    "for critical incidents and resolution within {res} hours.",
    "The contractor shall carry out preventive maintenance at least once every {pm} months during the "
    "support period, and shall submit a signed checklist after each visit.",
    "All software supplied shall be licensed in the name of the Purchaser in perpetuity, with "
    "entitlement to updates and patches for a period of not less than {lic} years from commissioning.",
    "Physical security of the installation shall include access control with audit logging retained "
    "for a minimum of {logs} days, and surveillance coverage of all equipment rooms.",
]


def render_clause(template: str, rng: random.Random) -> str:
    """Fill a clause template with plausible randomized parameters."""
    return template.format(
        ru=rng.choice([42, 45, 47]),
        tmin=rng.choice([0, 5, 10]),
        tmax=rng.choice([40, 45, 50]),
        rh=rng.choice([80, 85, 90, 95]),
        tps=rng.choice([500, 1000, 2500, 5000]),
        ms=rng.choice([200, 300, 500, 800]),
        avail=rng.choice(["99.5", "99.9", "99.95"]),
        mw=rng.choice([4, 6, 8]),
        ports=rng.choice([250, 500, 1200, 2400]),
        rpo=rng.choice([4, 6, 12, 24]),
        rto=rng.choice([2, 4, 8]),
        sessions=rng.choice([3, 4, 6, 8]),
        trainees=rng.choice([20, 30, 50, 75]),
        shift=rng.choice(["24x7", "12x6", "round-the-clock"]),
        resp=rng.choice([15, 30, 45]),
        res=rng.choice([4, 8, 12, 24]),
        pm=rng.choice([3, 4, 6]),
        lic=rng.choice([3, 5, 7]),
        logs=rng.choice([90, 180, 365]),
    )



def build_spec(rng: random.Random, doc_id: str, use_table: bool) -> Dict[str, Any]:
    """Randomize every ground-truth value for one document."""
    title, sector = rng.choice(SECTOR_TEMPLATES)
    turnover = rng.choice([30_000_000, 50_000_000, 75_000_000, 100_000_000, 120_000_000, 250_000_000])
    emd = rng.choice([500_000, 750_000, 1_000_000, 1_500_000, 2_500_000])

    return {
        "doc_id": doc_id,
        "title": title,
        "sector": sector,
        "authority": rng.choice(AUTHORITIES),
        "city": rng.choice(CITIES),
        "ref_no": f"{rng.choice(['NIT', 'RFP', 'TEN'])}/{rng.randint(2026, 2027)}/"
                  f"{rng.choice(['IT', 'CIV', 'PWD', 'HLT'])}/{rng.randint(100, 999)}",
        "use_table": use_table,

        # Dates
        "deadline_day": rng.randint(1, 28),
        "deadline_month": rng.choice(MONTHS),
        "deadline_year": 2027,
        "deadline_time": rng.choice(["15:00", "17:00", "11:00", "14:30"]),
        "prebid_day": rng.randint(1, 28),
        "prebid_month": rng.choice(MONTHS),

        # Money — value plus the surface format it is written in
        "turnover": turnover,
        "turnover_style": rng.choice(["plain", "crore", "inr", "lakh"]),
        "emd": emd,
        "emd_style": rng.choice(["plain", "lakh", "inr"]),
        "doc_fee": rng.choice([2_000, 5_000, 10_000, 25_000]),

        # Distractor amounts — same vocabulary, different values
        "distractor_turnover": turnover + rng.choice([-10_000_000, 10_000_000, 20_000_000]),
        "distractor_emd": emd + rng.choice([-250_000, 250_000, 500_000]),

        # Terms
        "solvency_required": rng.random() < 0.75,
        "experience_years": rng.choice([3, 5, 7, 10]),
        "duration_months": rng.choice([12, 18, 24, 36, 60]),
        "ld_rate": rng.choice(["0.5%", "0.25%", "1%"]),
        "ld_cap": rng.choice(["5%", "10%", "15%"]),
        "ld_period": rng.choice(["week", "fortnight", "month"]),
        "pbg_pct": rng.choice(["3%", "5%", "10%"]),
        "pbg_months": rng.choice([24, 30, 36, 42]),
        "warranty_years": rng.choice([1, 2, 3, 5]),
        "pay_advance": rng.choice([10, 15, 20, 25]),
        "pay_delivery": rng.choice([50, 60, 65]),
        "validity_days": rng.choice([90, 120, 150, 180]),
        "jv_allowed": rng.random() < 0.4,
        "subcontract_pct": rng.choice(["10%", "20%", "25%", "30%"]),
        "arbitration_seat": rng.choice(CITIES),
        "arbitration_rules": rng.choice([
            "the Arbitration and Conciliation Act, 1996",
            "the UNCITRAL Arbitration Rules",
            "the Indian Council of Arbitration Rules",
        ]),
        "liability_cap": rng.choice(["100%", "50%", "150%"]),
        "iso_primary": rng.choice(["ISO 9001:2015", "ISO 20000-1:2018"]),
        "iso_secondary": rng.choice(["ISO 27001:2022", "ISO 14001:2015"]),
        "local_content": rng.random() < 0.6,
        "bid_language": rng.choice(["English", "English or Hindi"]),
    }


# ===========================================================================
# Section renderers — each returns a list of question dicts
# ===========================================================================

def _q(qid: int, query: str, gold_span: str, page: int, field: str) -> Dict[str, Any]:
    return {
        "id": qid,
        "query": query,
        "gold_span": " ".join(gold_span.split()),
        "gold_page": page,
        "field": field,
    }


def sec_nit(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Notice Inviting Tender")
    w.para(
        f"{s['authority']}, {s['city']}, invites online bids from eligible and experienced "
        f"bidders for the work titled '{s['title']}' under reference number {s['ref_no']}."
    )

    deadline = (f"The last date and time for submission of bids is "
                f"{s['deadline_day']} {s['deadline_month']} {s['deadline_year']} at "
                f"{s['deadline_time']} hours IST.")
    page = w.para(deadline)
    qs.append(_q(len(qs) + 1, "What is the proposal submission deadline?", deadline, page, "submission_deadline"))

    prebid = (f"A pre-bid meeting shall be convened on {s['prebid_day']} {s['prebid_month']} "
              f"{s['deadline_year']} at the office of the {s['authority']}.")
    page = w.para(prebid)
    qs.append(_q(len(qs) + 1, "When is the pre-bid meeting scheduled?", prebid, page, "prebid_date"))

    fee = (f"A non-refundable tender document fee of {money(s['doc_fee'], 'plain')} shall be paid "
           f"online at the time of bid submission.")
    page = w.para(fee)
    qs.append(_q(len(qs) + 1, "What is the tender document fee?", fee, page, "document_fee"))

    w.para(
        "Bidders shall register on the e-procurement portal and obtain a valid digital signature "
        "certificate of Class III or above prior to submission."
    )


def sec_eligibility(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Section II — Eligibility Criteria")
    w.para(
        "The bidder shall satisfy each of the following pre-qualification requirements. "
        "Documentary evidence for every criterion shall be enclosed with the technical bid."
    )

    exp = (f"The bidder shall have a minimum of {s['experience_years']} years of experience in "
           f"executing projects of a similar nature in the field of {s['sector']}.")
    page = w.para(exp)
    qs.append(_q(len(qs) + 1, "What is the minimum years of experience required?", exp, page, "experience_years"))

    iso = (f"The bidder shall hold valid {s['iso_primary']} and {s['iso_secondary']} certifications "
           f"as on the date of bid submission.")
    page = w.para(iso)
    qs.append(_q(len(qs) + 1, "Which ISO certifications are required?", iso, page, "iso_certifications"))

    if s["jv_allowed"]:
        jv = ("Joint ventures and consortium bids are permitted, subject to a maximum of two members "
              "and submission of a registered joint venture agreement.")
    else:
        jv = ("Joint ventures, consortiums and any form of collaborative bidding are strictly "
              "prohibited. Only single-entity bids shall be accepted.")
    page = w.para(jv)
    qs.append(_q(len(qs) + 1, "Are joint ventures or consortiums allowed?", jv, page, "joint_ventures"))

    sub = (f"Sub-contracting of the awarded work is permitted only up to {s['subcontract_pct']} of the "
           f"total contract value, and only with the prior written approval of the Purchaser.")
    page = w.para(sub)
    qs.append(_q(len(qs) + 1, "Is sub-contracting allowed and to what extent?", sub, page, "subcontracting"))

    # Distractor: same vocabulary as the turnover question, different figure and
    # a non-mandatory framing.
    w.para(
        f"For the limited purpose of empanelling sub-contractors, an indicative annual turnover of "
        f"{money(s['distractor_turnover'], 'plain')} is suggested. This is a guidance value only and "
        f"is not a pre-qualification requirement for the bidder."
    )

    if s["local_content"]:
        w.para(
            "Purchase preference shall be extended to Class-I local suppliers in accordance with the "
            "prevailing Public Procurement (Preference to Make in India) Order."
        )


def sec_financial(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Section III — Financial Requirements")

    turnover_str = money(s["turnover"], s["turnover_style"])
    emd_str = money(s["emd"], s["emd_style"])

    if s["use_table"]:
        w.para("The financial pre-qualification thresholds are tabulated below.")
        turn = f"Minimum Average Annual Turnover {turnover_str}"
        page = w.table_row("Minimum Average Annual Turnover", turnover_str)
        qs.append(_q(len(qs) + 1, "What is the minimum annual turnover requirement?", turn, page, "minimum_turnover"))

        emd_row = f"Earnest Money Deposit (EMD) {emd_str}"
        page = w.table_row("Earnest Money Deposit (EMD)", emd_str)
        qs.append(_q(len(qs) + 1, "What is the Earnest Money Deposit amount?", emd_row, page, "emd"))

        w.table_row("Bid Validity Period", f"{s['validity_days']} days")
        w.y += LINE_HEIGHT * 0.5
    else:
        turn = (f"The bidder shall have achieved a minimum average annual turnover of {turnover_str} "
                f"in each of the last three audited financial years.")
        page = w.para(turn)
        qs.append(_q(len(qs) + 1, "What is the minimum annual turnover requirement?", turn, page, "minimum_turnover"))

        emd_row = (f"Bids shall be accompanied by an Earnest Money Deposit (EMD) of {emd_str}, "
                   f"furnished as a demand draft or an unconditional bank guarantee.")
        page = w.para(emd_row)
        qs.append(_q(len(qs) + 1, "What is the Earnest Money Deposit amount?", emd_row, page, "emd"))

    if s["solvency_required"]:
        solv = ("A bank solvency certificate issued by a scheduled commercial bank within the preceding "
                "six months is mandatory and shall be enclosed with the technical bid.")
    else:
        solv = ("A bank solvency certificate is not required for this procurement. Audited financial "
                "statements for the last three years shall be sufficient.")
    page = w.para(solv)
    qs.append(_q(len(qs) + 1, "Is a bank solvency certificate required?", solv, page, "solvency_certificate"))

    # Distractor: EMD vocabulary with a different figure, scoped to a different party.
    w.para(
        f"Where a bidder is exempted from furnishing earnest money under a subsisting government "
        f"notification, a bid security declaration shall be submitted in lieu thereof. The notional "
        f"value of such declaration shall be reckoned as {money(s['distractor_emd'], 'plain')} solely "
        f"for record-keeping purposes."
    )

    validity = (f"Bids shall remain valid for a period of {s['validity_days']} days from the date of "
                f"opening of the technical bid.")
    page = w.para(validity)
    qs.append(_q(len(qs) + 1, "What is the bid validity period?", validity, page, "bid_validity"))


def sec_payment(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Section IV — Payment Terms")
    final = 100 - s["pay_advance"] - s["pay_delivery"]
    pay = (f"Payments shall be released against milestones as follows: {s['pay_advance']}% as mobilisation "
           f"advance against an equivalent bank guarantee, {s['pay_delivery']}% on delivery and "
           f"installation, and the balance {final}% on final acceptance testing and handover.")
    page = w.para(pay)
    qs.append(_q(len(qs) + 1, "What is the payment milestone schedule?", pay, page, "payment_schedule"))

    w.para(
        "All payments shall be subject to deduction of tax at source as applicable. Invoices shall be "
        "raised only after written certification of the corresponding milestone by the project officer."
    )

    dur = (f"The contract shall be executed over a period of {s['duration_months']} months from the "
           f"date of issue of the letter of award.")
    page = w.para(dur)
    qs.append(_q(len(qs) + 1, "What is the contract execution duration?", dur, page, "contract_duration"))


def sec_penalties(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Section V — Penalties, Guarantees and Liability")

    ld = (f"In the event of delay in completion, liquidated damages shall be levied at "
          f"{s['ld_rate']} of the contract value per {s['ld_period']} of delay, subject to an overall "
          f"ceiling of {s['ld_cap']} of the total contract value.")
    page = w.para(ld)
    qs.append(_q(len(qs) + 1, "What is the liquidated damages rate for delay?", ld, page, "liquidated_damages"))

    pbg = (f"The successful bidder shall furnish a performance bank guarantee equal to {s['pbg_pct']} of "
           f"the contract value, valid for {s['pbg_months']} months from the date of award.")
    page = w.para(pbg)
    qs.append(_q(len(qs) + 1, "What is the performance bank guarantee requirement?", pbg, page, "performance_guarantee"))

    war = (f"All supplied equipment shall carry a comprehensive on-site warranty of "
           f"{s['warranty_years']} years from the date of successful commissioning.")
    page = w.para(war)
    qs.append(_q(len(qs) + 1, "What is the warranty period required?", war, page, "warranty_period"))

    liab = (f"The aggregate liability of the contractor under this contract shall not exceed "
            f"{s['liability_cap']} of the total contract value, save in cases of fraud, wilful "
            f"misconduct or breach of confidentiality obligations.")
    page = w.para(liab)
    qs.append(_q(len(qs) + 1, "What is the limitation of liability cap?", liab, page, "liability_cap"))

    # Distractor: penalty vocabulary with a different rate, for a different breach.
    w.para(
        "Separately, failure to depute the approved project manager within thirty days of award shall "
        "attract a one-time administrative penalty of 2% of the first milestone value, which shall be "
        "recovered from the next due payment and shall not count towards the liquidated damages ceiling."
    )


def sec_dispute(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]]) -> None:
    w.heading("Section VI — Dispute Resolution and General Conditions")

    arb = (f"Any dispute arising out of or in connection with this contract shall be referred to "
           f"arbitration under {s['arbitration_rules']}, with {s['arbitration_seat']} as the seat of "
           f"arbitration.")
    page = w.para(arb)
    qs.append(_q(len(qs) + 1, "What is the dispute resolution mechanism?", arb, page, "dispute_resolution"))

    lang = (f"All bid documents, correspondence and deliverables shall be submitted in "
            f"{s['bid_language']}.")
    page = w.para(lang)
    qs.append(_q(len(qs) + 1, "In what language must bid documents be submitted?", lang, page, "bid_language"))

    w.para(
        "The contract shall be governed by and construed in accordance with the laws of India. "
        "The courts at the seat of arbitration shall have exclusive jurisdiction."
    )


def sec_filler(w: PdfWriter, s: Dict[str, Any], qs: List[Dict[str, Any]], rng: random.Random,
               heading: str, paras: int) -> None:
    """
    Emit a numbered boilerplate section.

    Filler is what gives the corpus realistic length and forces retrieval to
    actually discriminate. Clauses are drawn from both the fixed prose pool and
    the parameterised templates so that repeated sections are not near-identical
    across documents.
    """
    w.heading(heading)
    pool = list(FILLER)
    rng.shuffle(pool)
    clauses = list(SPEC_CLAUSES)
    rng.shuffle(clauses)

    for i in range(paras):
        if i % 3 == 2:
            body = render_clause(clauses[i % len(clauses)], rng)
        else:
            body = pool[i % len(pool)]
        w.para(f"{rng.randint(1, 12)}.{i + 1}  {body}")



# ===========================================================================
# Document assembly
# ===========================================================================

def generate_document(rng: random.Random, doc_id: str, use_table: bool) -> Dict[str, Any]:
    spec = build_spec(rng, doc_id, use_table)
    w = PdfWriter()
    qs: List[Dict[str, Any]] = []

    # Title block always first.
    w.heading(spec["authority"])
    w.para(f"Tender Reference: {spec['ref_no']}")
    tender_title = spec["title"]
    w.para(f"Name of Work: {tender_title}")
    w.para("")

    # Content sections in shuffled order, with filler interleaved so that answers
    # sit at different depths across the corpus.
    content = [sec_nit, sec_eligibility, sec_financial, sec_payment, sec_penalties, sec_dispute]
    rng.shuffle(content)

    filler_headings = [
        "Instructions to Bidders", "Scope of Work", "Technical Specifications",
        "General Conditions of Contract", "Annexure — Formats and Declarations",
        "Special Conditions of Contract",
    ]
    rng.shuffle(filler_headings)

    for i, section in enumerate(content):
        if i > 0:
            sec_filler(w, spec, qs, rng, filler_headings[i % len(filler_headings)], rng.randint(26, 38))
        section(w, spec, qs)

    # Tail filler so the last real answer is not always at the very end.
    sec_filler(w, spec, qs, rng, filler_headings[-1], rng.randint(28, 40))

    w.doc.set_metadata({"title": spec["title"], "producer": "TenderAI Synthetic PDF Engine"})
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    page_count = w.doc.page_count
    w.save(pdf_path)

    annotation = {
        "doc_id": doc_id,
        "pdf": f"pdfs/{doc_id}.pdf",
        "source": "synthetic",
        "page_count": page_count,
        "generator_note": (
            "Synthetically generated clean-text PDF. Does not represent the scanned "
            "pages, malformed tables or OCR noise present in real tender documents."
        ),
        "fields": {
            "tender_title": tender_title,
            "submission_deadline": (
                f"{spec['deadline_day']} {spec['deadline_month']} {spec['deadline_year']} "
                f"{spec['deadline_time']} IST"
            ),
            "minimum_turnover": {
                "value": spec["turnover"],
                "currency": "INR",
                "raw": money(spec["turnover"], spec["turnover_style"]),
            },
            "earnest_money_deposit_emd": {
                "value": spec["emd"],
                "currency": "INR",
                "raw": money(spec["emd"], spec["emd_style"]),
            },
            "document_fee": {
                "value": spec["doc_fee"],
                "currency": "INR",
                "raw": money(spec["doc_fee"], "plain"),
            },
            "solvency_certificate_required": spec["solvency_required"],
            "experience_years": spec["experience_years"],
            "contract_duration_months": spec["duration_months"],
            "bid_validity_days": spec["validity_days"],
            "warranty_years": spec["warranty_years"],
            "liquidated_damages_rate": spec["ld_rate"],
            "liquidated_damages_cap": spec["ld_cap"],
            "performance_guarantee_pct": spec["pbg_pct"],
            "liability_cap": spec["liability_cap"],
            "joint_ventures_allowed": spec["jv_allowed"],
            "subcontracting_limit": spec["subcontract_pct"],
            "arbitration_seat": spec["arbitration_seat"],
            "bid_language": spec["bid_language"],
        },
        "questions": qs,
    }

    ANNOTATION_DIR.mkdir(parents=True, exist_ok=True)
    with open(ANNOTATION_DIR / f"{doc_id}.json", "w", encoding="utf-8") as fh:
        json.dump(annotation, fh, indent=2, ensure_ascii=False)

    return annotation


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic tender corpus.")
    parser.add_argument("--seed", type=int, default=42, help="Base RNG seed (default: 42).")
    parser.add_argument("--count", type=int, default=18, help="Number of documents (default: 18).")
    parser.add_argument("--clean", action="store_true", help="Delete existing pdfs/ and annotations/ first.")
    args = parser.parse_args()

    if args.clean:
        shutil.rmtree(PDF_DIR, ignore_errors=True)
        shutil.rmtree(ANNOTATION_DIR, ignore_errors=True)

    print(f"Generating {args.count} synthetic tenders (seed={args.seed}) ...")
    total_q = 0
    total_pages = 0
    for i in range(args.count):
        doc_id = f"synth-{i + 1:03d}"
        rng = random.Random(args.seed * 1000 + i)  # per-document, reproducible
        use_table = i in (2, 9)  # two documents present financials as a table
        ann = generate_document(rng, doc_id, use_table)
        total_q += len(ann["questions"])
        total_pages += ann["page_count"]
        print(f"  {doc_id}: {ann['page_count']:>2} pages, {len(ann['questions']):>2} questions"
              f"{'  [table layout]' if use_table else ''}")

    print(f"\n{args.count} documents, {total_pages} pages, {total_q} questions")
    print(f"  PDFs        -> {PDF_DIR}")
    print(f"  Annotations -> {ANNOTATION_DIR}")
    print("\nNext: python eval_harness.py --mode smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
