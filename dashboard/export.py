"""Export ledger records as CSV and as an auditor-facing PDF report.

The PDF is the artifact that leaves the building. Someone who has never heard of
this system should be able to read it cold and come away knowing what happened,
who authorised it, and why they should believe the record. So it leads with the
integrity result and a plain-language narrative, and puts the structured tables
after -- the opposite order from how the data is stored.

Everything in the report is derived from the ledger at generation time. There is
no separate reporting store to drift out of sync with the records.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

INK = colors.HexColor("#14171f")
MUTED = colors.HexColor("#5a6474")
DIM = colors.HexColor("#8a94a4")
LINE = colors.HexColor("#d9dee6")
BG = colors.HexColor("#f4f6f9")
OK = colors.HexColor("#0f8a4d")
BAD = colors.HexColor("#c62828")
WARN = colors.HexColor("#a9701a")
ACCENT = colors.HexColor("#5b3fd9")


# ----------------------------------------------------------------------- CSV


CSV_COLUMNS = [
    "seq", "record_id", "incident_id", "timestamp", "action_type", "summary",
    "actor_agent_name", "actor_agent_version", "actor_platform", "actor_model",
    "invocation_id", "resource_type", "resource_id", "environment",
    "policy_id", "allowed_by", "policy_digest",
    "approver_id", "approver_type", "approved_at", "approval_channel", "decision_note",
    "change_type", "reversible", "blast_radius", "command_or_diff",
    "outcome_status", "outcome_details", "duration_ms",
    "evidence_count", "evidence_types",
    "in_response_to", "prev_record_hash", "record_hash",
]


def _row(r: dict[str, Any]) -> dict[str, Any]:
    approver = r.get("approver") or {}
    pa = r.get("proposed_action") or {}
    evidence = r.get("evidence") or []
    return {
        "seq": r.get("seq"),
        "record_id": r["record_id"],
        "incident_id": r["incident_id"],
        "timestamp": r["timestamp"],
        "action_type": r["action_type"],
        "summary": r["summary"],
        "actor_agent_name": r["actor"]["agent_name"],
        "actor_agent_version": r["actor"]["agent_version"],
        "actor_platform": r["actor"]["tool_or_platform"],
        "actor_model": r["actor"].get("model", ""),
        "invocation_id": r["actor"]["invocation_id"],
        "resource_type": r["target"]["resource_type"],
        "resource_id": r["target"]["resource_id"],
        "environment": r["target"]["environment"],
        "policy_id": r["policy_reference"]["policy_id"],
        "allowed_by": r["policy_reference"]["allowed_by"],
        "policy_digest": r["policy_reference"].get("policy_digest", ""),
        "approver_id": approver.get("approver_id", ""),
        "approver_type": approver.get("approver_type", ""),
        "approved_at": approver.get("approved_at", ""),
        "approval_channel": approver.get("approval_channel", ""),
        "decision_note": approver.get("decision_note", ""),
        "change_type": pa.get("change_type", ""),
        "reversible": pa.get("reversible", ""),
        "blast_radius": pa.get("blast_radius", ""),
        "command_or_diff": pa.get("command_or_diff", ""),
        "outcome_status": r["outcome"]["status"],
        "outcome_details": r["outcome"]["details"],
        "duration_ms": r["outcome"].get("duration_ms", ""),
        "evidence_count": len(evidence),
        "evidence_types": "|".join(sorted({e["evidence_type"] for e in evidence})),
        "in_response_to": r.get("in_response_to", ""),
        "prev_record_hash": r["prev_record_hash"],
        "record_hash": r["record_hash"],
    }


def records_csv(records: Iterable[dict[str, Any]], out: io.StringIO) -> None:
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        writer.writerow(_row(r))


# ----------------------------------------------------------------------- PDF


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT,
                                spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontSize=10.5, leading=15,
                                   textColor=MUTED, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=17, textColor=INK,
                             spaceBefore=16, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName="Helvetica-Bold",
                             fontSize=10, leading=14, textColor=INK,
                             spaceBefore=10, spaceAfter=3),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9.5, leading=14,
                               textColor=INK, spaceAfter=5),
        "muted": ParagraphStyle("m", parent=base["Normal"], fontSize=8.5, leading=12,
                                textColor=MUTED, spaceAfter=4),
        "mono": ParagraphStyle("mo", parent=base["Normal"], fontName="Courier",
                               fontSize=7.5, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=7.8, leading=10.5,
                               textColor=INK),
        "cellmono": ParagraphStyle("cm", parent=base["Normal"], fontName="Courier",
                                   fontSize=6.8, leading=9, textColor=MUTED),
    }


def _esc(text: Any, limit: int = 4000) -> str:
    s = "" if text is None else str(text)
    s = s[:limit]
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Doc(BaseDocTemplate):
    """Adds the running footer, including the chain head on every page."""

    def __init__(self, buf, head_hash: str, generated: str, **kw):
        super().__init__(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                         topMargin=16 * mm, bottomMargin=18 * mm, **kw)
        self.head_hash = head_hash
        self.generated = generated
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=self._footer)])

    def _footer(self, canvas, doc):
        canvas.saveState()
        y = 11 * mm
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, y + 6 * mm, doc.leftMargin + doc.width, y + 6 * mm)
        canvas.setFont("Helvetica", 6.8)
        canvas.setFillColor(DIM)
        canvas.drawString(doc.leftMargin, y,
                          f"SwarmOps audit ledger · generated {self.generated}")
        canvas.drawRightString(doc.leftMargin + doc.width, y, f"Page {doc.page}")
        canvas.setFont("Courier", 6.2)
        canvas.drawCentredString(doc.leftMargin + doc.width / 2, y,
                                 f"chain head {self.head_hash[:32]}…")
        canvas.restoreState()


def _verdict_banner(verification: dict, s: dict) -> Table:
    ok = verification["ok"]
    headline = (
        f"VERIFIED — {verification['verified_records']}/{verification['total_records']} "
        f"records verified, no gaps detected"
        if ok else
        f"INTEGRITY FAILURE — {len(verification['findings'])} problem(s) detected"
    )
    explain = (
        "Every record's hash was recomputed from its stored content, every link to the "
        "preceding record was followed, and every referenced evidence blob was re-hashed "
        "against the address the record cites. Any edit, deletion or reordering of the "
        "records in this report would have broken one of those checks."
        if ok else
        "This ledger does not verify. The records in this report must not be relied upon "
        "until the findings listed below are explained. Details follow in the Findings section."
    )
    cell = [
        Paragraph(f"<b>{headline}</b>",
                  ParagraphStyle("v", fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                                 textColor=OK if ok else BAD)),
        Spacer(1, 3),
        Paragraph(explain, s["muted"]),
    ]
    t = Table([[cell]], colWidths=[174 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eefaf3") if ok else colors.HexColor("#fdecec")),
        ("BOX", (0, 0), (-1, -1), 0.8, OK if ok else BAD),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t


def _kv_table(rows: list[tuple[str, Any]], s: dict, width: float = 174 * mm) -> Table:
    data = [[Paragraph(f"<b>{_esc(k)}</b>", s["cell"]), Paragraph(_esc(v), s["cell"])]
            for k, v in rows]
    t = Table(data, colWidths=[42 * mm, width - 42 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _incident_narrative(chain: list[dict], s: dict) -> list:
    """The six questions, answered in prose, for one incident."""
    by: dict[str, list[dict]] = {}
    for r in chain:
        by.setdefault(r["action_type"], []).append(r)

    # See dashboard/app.py:_incident_summary -- prefer the record carrying the
    # agent's reasoning, fall back to the last investigation step.
    investigations = by.get("investigate", [])
    conclusion = next(
        (r for r in reversed(investigations)
         if any(e["evidence_type"] == "agent_reasoning" for e in r.get("evidence", []))),
        investigations[-1] if investigations else None,
    )
    proposal = (by.get("propose_fix") or [None])[-1]
    approval = (by.get("approve") or [None])[-1]
    rejection = (by.get("reject") or [None])[-1]
    execution = (by.get("execute") or [None])[-1]
    steps = len(by.get("investigate", []))
    evidence_total = sum(len(r.get("evidence", [])) for r in chain)

    decision = approval or rejection
    flow: list[tuple[str, Any]] = [
        ("What broke",
         conclusion["outcome"]["details"].split("\n")[0] if conclusion
         else "No investigation conclusion was recorded."),
        ("What the agent examined",
         f"{evidence_total} evidence artifact(s) gathered over {steps} diagnostic step(s). "
         f"Each command and its output is recorded in the chain below."),
        ("What it proposed",
         proposal["proposed_action"]["description"] if proposal else "No remediation was proposed."),
        ("Exact change requested",
         proposal["proposed_action"]["command_or_diff"] if proposal else "—"),
        ("Who authorised it",
         (f"{decision['approver']['approver_id']} "
          f"({decision['approver']['approver_type']}) "
          f"{'approved' if approval else 'rejected'} it on "
          f"{decision['approver']['approved_at']} via "
          f"{decision['approver']['approval_channel']}.")
         if decision else "No decision has been recorded; nothing was applied."),
        ("Reason given",
         (decision["approver"].get("decision_note") or "—") if decision else "—"),
        ("When it executed",
         f"{execution['timestamp']} ({execution['outcome'].get('duration_ms', '?')} ms)"
         if execution else "Never executed."),
        ("Did it work",
         (("Yes. " if execution["outcome"]["status"] == "success" else "No. ")
          + execution["outcome"]["details"].replace("\n", " "))
         if execution else "Not applicable."),
        ("Policy applied",
         f"{chain[0]['policy_reference']['policy_id']} · "
         + ", ".join(sorted({r["policy_reference"]["allowed_by"] for r in chain}))),
    ]

    out = [Paragraph("Incident summary", s["h2"]), _kv_table(flow, s)]

    unattended = [
        r for r in chain
        if (r.get("approver") or {}).get("approval_channel") == "trial_harness_unattended"
    ]
    if unattended:
        out += [
            Spacer(1, 6),
            Paragraph(
                "<b>Note on approval:</b> the approval recorded for this incident was made by "
                "an automated trial harness operating under standing pre-authorisation, not by "
                "a human reviewing this specific incident. It is labelled "
                "<font face='Courier'>trial_harness_unattended</font> in the ledger so that "
                "attended and unattended approvals remain distinguishable.",
                s["muted"],
            ),
        ]
    return out


def audit_pdf(
    *,
    records: list[dict[str, Any]],
    filters: dict[str, Any],
    verification: dict[str, Any],
    incident_chain: list[dict[str, Any]] | None = None,
) -> bytes:
    s = _styles()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    buf = io.BytesIO()
    doc = _Doc(buf, head_hash=verification.get("head_hash") or "-", generated=generated)

    story: list = []
    scope = f"Incident {filters['incident_id']}" if filters.get("incident_id") else "Ledger extract"
    story.append(Paragraph("AI Agent Action Audit Report", s["title"]))
    story.append(Paragraph(
        f"{scope} · generated {generated} · {len(records)} record(s)", s["subtitle"]))

    story.append(_verdict_banner(verification, s))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Scope of this report", s["h2"]))
    applied = [(k.replace("_", " ").title(), v) for k, v in filters.items() if v]
    story.append(_kv_table(
        (applied or [("Filters", "None — the full ledger is included.")]) + [
            ("Records included", len(records)),
            ("Ledger size", f"{verification['total_records']} record(s), "
                            f"seq {verification['seq_range'][0]}–{verification['seq_range'][1]}"),
            ("Evidence artifacts re-hashed", verification["evidence_artifacts_checked"]),
            ("Chain head hash", verification.get("head_hash") or "—"),
        ], s))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "This report is generated directly from the append-only ledger. Records cannot be "
        "modified or removed after they are written: the application role holds only INSERT "
        "and SELECT privileges, database triggers reject UPDATE, DELETE and TRUNCATE for all "
        "roles including the table owner, and each record commits to the hash of its "
        "predecessor so that any removal or reordering is detectable. The verification result "
        "above was computed at generation time by re-deriving every hash from stored content.",
        s["muted"]))

    if not verification["ok"]:
        story.append(Paragraph("Integrity findings", s["h2"]))
        for f in verification["findings"][:40]:
            story.append(Paragraph(
                f"<b>{_esc(f['kind'])}</b> at seq {f['seq']} "
                f"({_esc(f['record_id'])}): {_esc(f['detail'])}", s["body"]))

    if incident_chain:
        story.append(Spacer(1, 4))
        story.extend(_incident_narrative(incident_chain, s))

    # ----------------------------------------------------------- record table
    story.append(PageBreak())
    story.append(Paragraph("Records", s["h2"]))
    story.append(Paragraph(
        "One row per recorded action, newest first. Full detail for each record, including "
        "its evidence, follows in the appendix.", s["muted"]))
    story.append(Spacer(1, 4))

    header = ["#", "Time (UTC)", "Action", "Summary", "Target", "Policy rule", "Approver", "Outcome"]
    data = [[Paragraph(f"<b>{h}</b>", s["cell"]) for h in header]]
    for r in records:
        approver = (r.get("approver") or {}).get("approver_id", "—")
        data.append([
            Paragraph(str(r.get("seq", "")), s["cellmono"]),
            Paragraph(_esc(r["timestamp"].replace("T", " ").replace("Z", "")), s["cellmono"]),
            Paragraph(_esc(r["action_type"]), s["cell"]),
            Paragraph(_esc(r["summary"], 220), s["cell"]),
            Paragraph(_esc(f"{r['target']['environment']} / {r['target']['resource_id']}"), s["cell"]),
            Paragraph(_esc(r["policy_reference"]["allowed_by"]), s["cellmono"]),
            Paragraph(_esc(approver, 60), s["cell"]),
            Paragraph(_esc(r["outcome"]["status"]), s["cell"]),
        ])

    table = Table(data, repeatRows=1, colWidths=[
        8 * mm, 25 * mm, 20 * mm, 47 * mm, 27 * mm, 22 * mm, 15 * mm, 10 * mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(records, start=1):
        status = r["outcome"]["status"]
        if status == "failure":
            style.append(("TEXTCOLOR", (7, i), (7, i), BAD))
        elif status == "pending":
            style.append(("TEXTCOLOR", (7, i), (7, i), WARN))
        else:
            style.append(("TEXTCOLOR", (7, i), (7, i), OK))
    table.setStyle(TableStyle(style))
    story.append(table)

    # --------------------------------------------------------------- appendix
    detail_records = incident_chain or records[: min(len(records), 25)]
    story.append(PageBreak())
    story.append(Paragraph("Appendix — record detail", s["h2"]))
    story.append(Paragraph(
        f"Full detail for {len(detail_records)} record(s), including the evidence each action "
        f"rested on and the hashes that make it tamper-evident.", s["muted"]))

    for r in detail_records:
        block: list = [Paragraph(
            f"{r['action_type']} · {_esc(r['summary'], 160)}", s["h3"])]
        approver = r.get("approver") or {}
        pa = r.get("proposed_action") or {}
        rows: list[tuple[str, Any]] = [
            ("Record ID", r["record_id"]),
            ("Incident", r["incident_id"]),
            ("Timestamp", r["timestamp"]),
            ("Actor", f"{r['actor']['agent_name']} v{r['actor']['agent_version']} "
                      f"({r['actor']['tool_or_platform']})"
                      + (f", model {r['actor']['model']}" if r["actor"].get("model") else "")),
            ("Target", f"{r['target']['environment']} / {r['target']['resource_id']} "
                       f"({r['target']['resource_type']})"),
            ("Policy rule", f"{r['policy_reference']['allowed_by']} "
                            f"in {r['policy_reference']['policy_id']}"),
            ("Policy text", r["policy_reference"]["policy_description"]),
        ]
        if pa:
            rows += [
                ("Proposed change", pa["description"]),
                ("Command", pa["command_or_diff"]),
                ("Reversible", pa["reversible"]),
                ("Blast radius", pa.get("blast_radius", "—")),
            ]
        if approver:
            rows += [
                ("Approved by", f"{approver['approver_id']} ({approver['approver_type']})"),
                ("Approved at", f"{approver['approved_at']} via {approver['approval_channel']}"),
                ("Reason given", approver.get("decision_note", "—")),
            ]
        rows += [
            ("Outcome", f"{r['outcome']['status']} — {r['outcome']['details']}"),
            ("record_hash", r["record_hash"]),
            ("prev_record_hash", r["prev_record_hash"]),
        ]
        block.append(_kv_table(rows, s))

        evidence = r.get("evidence") or []
        if evidence:
            block.append(Spacer(1, 4))
            block.append(Paragraph(f"Evidence ({len(evidence)})", s["h3"]))
            for e in evidence:
                ref = e["snapshot_ref_or_inline"]
                if ref["kind"] == "inline":
                    body, addr = ref["content"], "stored inline (covered by record_hash)"
                else:
                    body, addr = ref.get("preview", ""), ref["sha256"]
                block.append(Paragraph(
                    f"<b>{_esc(e['evidence_type'])}</b> — "
                    f"<font face='Courier' size='7'>{_esc(e['source'], 200)}</font>"
                    + ("  [redacted]" if e.get("redacted") else ""), s["muted"]))
                excerpt = Table(
                    [[Paragraph(_esc(body, 1800).replace("\n", "<br/>"), s["mono"])]],
                    colWidths=[174 * mm])
                excerpt.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), BG),
                    ("BOX", (0, 0), (-1, -1), 0.3, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]))
                block.append(excerpt)
                block.append(Paragraph(f"retrieved {_esc(e['retrieved_at'])} · {_esc(addr)}", s["mono"]))
                block.append(Spacer(1, 4))

        story.append(KeepTogether(block) if len(evidence) <= 1 else block[0])
        if len(evidence) > 1:
            story.extend(block[1:])
        story.append(Spacer(1, 10))

    doc.build(story)
    return buf.getvalue()
