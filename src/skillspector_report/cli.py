import argparse
import json
import re
import subprocess
import shutil
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
)
from reportlab.lib.utils import ImageReader


def sanitize_text(text):
    if not isinstance(text, str):
        return text

    # Hide macOS temporary extraction paths created during zip scans.
    text = re.sub(
        r"/var/folders/[^\n`\"]+/skillspector_[^\n`\"]+/extracted",
        "[temporary extraction directory]",
        text,
    )

    # Hide local user paths from raw JSON and report output.
    text = re.sub(
        r"/Users/[^\n`\"]+",
        "[local scan source]",
        text,
    )

    # Hide temporary scan paths from raw JSON and report output.
    text = re.sub(
        r"/private/tmp/[^\n`\"]+",
        "[temporary scan source]",
        text,
    )

    return text

def sanitize_json(obj):
    if isinstance(obj, dict):
        return {key: sanitize_json(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json(item) for item in obj]
    if isinstance(obj, str):
        return sanitize_text(obj)
    return obj


def clean_text_for_pdf(text):
    text = sanitize_text(str(text))
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def p(text, style):
    return Paragraph(clean_text_for_pdf(text), style)


def run_skillspector(skill_path):
    skill_path = Path(skill_path).expanduser()

    if not skill_path.exists():
        raise FileNotFoundError(f"Scan target not found: {skill_path}")

    skillspector_path = shutil.which("skillspector")
    if not skillspector_path:
        raise RuntimeError(
            "SkillSpector CLI was not found on PATH. "
            "Install it first with: "
            "uv tool install 'skillspector[mcp] @ git+https://github.com/NVIDIA/skillspector.git'"
        )

    command = [
        skillspector_path,
        "scan",
        str(skill_path),
        "--no-llm",
        "--format",
        "json",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        shell=False,
    )

    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr or result.stdout)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SkillSpector did not return valid JSON output.") from exc


def yes_no(value):
    return "Yes" if value else "No"


def get_scan_mode(scan_data):
    metadata = scan_data.get("metadata", {})
    completeness = scan_data.get("analysis_completeness", {})

    llm_requested = metadata.get("llm_requested")
    llm_analysis = completeness.get("llm_analysis")

    if llm_requested or llm_analysis not in (None, "skipped"):
        return "Static + LLM semantic analysis"

    return "Static only (--no-llm)"


def get_banner(severity, recommendation):
    severity = str(severity or "").upper()
    recommendation = str(recommendation or "").upper()

    if severity in ("HIGH", "CRITICAL") or "DO NOT" in recommendation:
        return (
            "DO NOT INSTALL - High-risk findings require remediation. "
            "See raw findings at the end of this report.",
            colors.HexColor("#991B1B"),
        )

    if severity == "MEDIUM" or recommendation == "CAUTION":
        return (
            "CAUTION - Manual review required before approval. "
            "See raw findings at the end of this report.",
            colors.HexColor("#92400E"),
        )

    if severity == "LOW" or recommendation == "SAFE":
        return (
            "LOW RISK - Manual review recommended. "
            "See raw findings at the end of this report.",
            colors.HexColor("#166534"),
        )

    return (
        "REVIEW REQUIRED - See raw findings at the end of this report.",
        colors.HexColor("#374151"),
    )


def add_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6B7280"))

    footer = (
        "Generated locally by SkillSpector Report, an independent companion tool "
        "for NVIDIA SkillSpector. Not affiliated with or endorsed by NVIDIA."
    )

    max_width = A4[0] - (1.3 * inch)
    words = footer.split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        if canvas.stringWidth(test, "Helvetica", 8) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    y = 0.45 * inch
    for line in lines:
        canvas.drawString(0.65 * inch, y, line)
        y -= 0.12 * inch

    canvas.restoreState()


def logo_image(logo_path, max_width=1.1 * inch, max_height=0.65 * inch):
    if not logo_path:
        return ""

    path = Path(logo_path).expanduser()
    if not path.exists():
        return ""

    reader = ImageReader(str(path))
    width, height = reader.getSize()

    scale = min(max_width / width, max_height / height)
    img = Image(str(path), width=width * scale, height=height * scale)
    return img



def issue_location(issue):
    location = issue.get("location") or {}
    file_name = location.get("file", "Unknown")
    start_line = location.get("start_line")

    if start_line:
        return f"{file_name}:{start_line}"

    return file_name


def issue_title(issue):
    issue_id = issue.get("id") or "Unknown"
    category = issue.get("category") or "Uncategorized"
    pattern = issue.get("pattern") or "No pattern name"

    return f"{issue_id} - {category}: {pattern}"


def issue_confidence(issue):
    confidence = issue.get("confidence")

    if isinstance(confidence, (int, float)):
        return f"{round(confidence * 100)}%"

    return "Not specified"


def issue_guidance(issue):
    explanation = issue.get("explanation")
    remediation = issue.get("remediation")

    if explanation and remediation:
        return f"{explanation} Remediation: {remediation}"

    if remediation:
        return remediation

    if explanation:
        return explanation

    return "No remediation guidance was provided by SkillSpector."


def component_executable(component):
    return "Yes" if component.get("executable") else "No"


def legal_notice_text():
    return (
        "This report is provided for informational and review-support purposes only. "
        "It is generated from the output of the installed NVIDIA SkillSpector CLI and "
        "does not guarantee that a skill, package, repository, or file is secure, safe, "
        "compliant, or free from malicious behavior. Security scanning tools may produce "
        "false positives or false negatives. Results may vary depending on the installed "
        "SkillSpector version, scan mode, configuration, dependencies, network availability, "
        "and the contents scanned. Users remain responsible for independent review, testing, "
        "approval decisions, and compliance with their own internal policies. SkillSpector "
        "Report is an independent companion tool and is not affiliated with, endorsed by, "
        "or sponsored by NVIDIA."
    )


def build_pdf(scan_data, args):
    scan_data = sanitize_json(scan_data)

    risk = scan_data.get("risk_assessment", {})
    metadata = scan_data.get("metadata", {})
    completeness = scan_data.get("analysis_completeness", {})
    issues = scan_data.get("issues", [])
    components = scan_data.get("components", [])

    score = risk.get("score", "Not found")
    severity = risk.get("severity", "Not found")
    recommendation = risk.get("recommendation", "Not found")
    skillspector_version = metadata.get("skillspector_version", "Not found")
    executable_scripts = yes_no(metadata.get("has_executable_scripts", False))
    scan_mode = get_scan_mode(scan_data)
    coverage = completeness.get("coverage_percent", "Not found")

    scanned_item = Path(args.skill_path).name
    scan_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.85 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=7,
            leading=9,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            name="Legal",
            parent=styles["Normal"],
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#6B7280"),
        )
    )

    styles.add(
        ParagraphStyle(
            name="TableLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableValue",
            parent=styles["Normal"],
            fontSize=9,
            leading=11,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            name="Banner",
            parent=styles["Normal"],
            fontSize=10,
            leading=13,
            textColor=colors.white,
            alignment=1,
        )
    )

    story = []

    logo = logo_image(args.logo)
    title = Paragraph("<b>SkillSpector Security Report</b>", styles["Title"])

    header_table = Table([[logo, title]], colWidths=[1.45 * inch, 5.15 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "LEFT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(header_table)

    privacy_text = (
        "This report generator does not upload scan inputs or reports. "
        "SkillSpector was run in static-only mode (--no-llm). "
        "Depending on dependencies and network availability, SkillSpector may query OSV.dev."
    )

    metadata_rows = [
        ["Company", args.company],
        ["Report Generated By", args.generated_by],
        ["Email", args.email],
        ["Scanned Item", scanned_item],
        ["Scan Date", scan_date],
        ["Scan Mode", scan_mode],
        ["SkillSpector Version", str(skillspector_version)],
        ["Coverage", f"{coverage}%"],
        ["Privacy", privacy_text],
    ]

    metadata_table = Table(
        [[p(label, styles["TableLabel"]), p(value, styles["TableValue"])] for label, value in metadata_rows],
        colWidths=[1.85 * inch, 4.75 * inch],
        repeatRows=0,
    )
    metadata_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F4F6")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(metadata_table)
    story.append(Spacer(1, 12))

    banner_text, banner_color = get_banner(severity, recommendation)
    banner_table = Table(
        [[Paragraph(f"<b>{clean_text_for_pdf(banner_text)}</b>", styles["Banner"])]],
        colWidths=[6.6 * inch],
    )
    banner_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), banner_color),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(banner_table)

    story.append(Spacer(1, 8))
    story.append(Paragraph("<b>Executive Summary</b>", styles["Heading2"]))

    summary_rows = [
        ["Risk Score", str(score)],
        ["Severity", str(severity)],
        ["Recommendation", str(recommendation)],
        ["Issues Found", str(len(issues))],
        ["Components Scanned", str(len(components))],
        ["Executable Scripts", executable_scripts],
    ]

    summary_table = Table(
        [[p(label, styles["TableLabel"]), p(value, styles["TableValue"])] for label, value in summary_rows],
        colWidths=[2.1 * inch, 4.5 * inch],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F9FAFB")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 10))

    story.append(
        Paragraph(
            "Risk guidance: LOW usually means the skill may be acceptable after normal review. "
            "MEDIUM means caution and manual review are recommended. HIGH or CRITICAL should not "
            "be approved or installed unless the finding is fully understood and remediated.",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 12))
    story.append(Paragraph("<b>Review Guidance</b>", styles["Heading2"]))
    story.append(
        Paragraph(
            "This report is informational and does not guarantee that the scanned item is safe. "
            "Use the executive summary, findings detail, scanned components, and raw findings "
            "to support manual review and remediation decisions according to your internal policy.",
            styles["Normal"],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("<b>Findings Detail</b>", styles["Heading2"]))

    if issues:
        story.append(
            Paragraph(
                f"SkillSpector reported {len(issues)} issue(s). The table below summarizes each finding, "
                "where it was found, confidence, and the remediation guidance returned by SkillSpector.",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 8))

        findings_rows = [
            [
                p("Severity", styles["TableLabel"]),
                p("Finding", styles["TableLabel"]),
                p("Location", styles["TableLabel"]),
                p("Conf.", styles["TableLabel"]),
                p("Guidance", styles["TableLabel"]),
            ]
        ]

        for issue in issues:
            findings_rows.append(
                [
                    p(issue.get("severity", "Unknown"), styles["TableValue"]),
                    p(issue_title(issue), styles["TableValue"]),
                    p(issue_location(issue), styles["TableValue"]),
                    p(issue_confidence(issue), styles["TableValue"]),
                    p(issue_guidance(issue), styles["TableValue"]),
                ]
            )

        findings_table = Table(
            findings_rows,
            colWidths=[0.75 * inch, 1.45 * inch, 1.45 * inch, 0.75 * inch, 2.2 * inch],
            repeatRows=1,
        )
        findings_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(findings_table)
    else:
        story.append(
            Paragraph(
                "No issues were reported by SkillSpector in this scan. Manual review is still recommended before installation or use.",
                styles["Normal"],
            )
        )

    story.append(Spacer(1, 14))
    story.append(PageBreak())
    story.append(Paragraph("<b>Scanned Components</b>", styles["Heading2"]))

    if components:
        story.append(
            Paragraph(
                f"SkillSpector scanned {len(components)} component(s). The table below shows what was included in the scan.",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 8))

        component_rows = [
            [
                p("File", styles["TableLabel"]),
                p("Type", styles["TableLabel"]),
                p("Lines", styles["TableLabel"]),
                p("Executable", styles["TableLabel"]),
            ]
        ]

        for component in components:
            component_rows.append(
                [
                    p(component.get("path", "Unknown"), styles["TableValue"]),
                    p(component.get("type", "Unknown"), styles["TableValue"]),
                    p(str(component.get("lines", "Unknown")), styles["TableValue"]),
                    p(component_executable(component), styles["TableValue"]),
                ]
            )

        components_table = Table(
            component_rows,
            colWidths=[3.9 * inch, 1.0 * inch, 0.75 * inch, 0.95 * inch],
            repeatRows=1,
        )
        components_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(components_table)
    else:
        story.append(
            Paragraph(
                "No scanned components were returned by SkillSpector.",
                styles["Normal"],
            )
        )

    story.append(PageBreak())
    story.append(Paragraph("<b>Raw SkillSpector JSON Findings</b>", styles["Heading2"]))
    story.append(
        Paragraph(
            "The following section contains the structured SkillSpector JSON output for audit evidence.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 8))

    raw_json = json.dumps(scan_data, indent=2, ensure_ascii=False)
    for line in raw_json.splitlines():
        story.append(Paragraph(clean_text_for_pdf(line), styles["Small"]))

    story.append(Spacer(1, 14))
    story.append(Paragraph("<b>Legal Notice and Limitations</b>", styles["Small"]))
    story.append(Paragraph(legal_notice_text(), styles["Legal"]))

    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a clean local PDF report from a NVIDIA SkillSpector scan."
    )
    parser.add_argument("skill_path", help="Path to a skill, zip, directory, repository, or file.")
    parser.add_argument("--generated-by", default="Not specified", help="Name of the person generating the report.")
    parser.add_argument("--reviewer", dest="generated_by", help=argparse.SUPPRESS)
    parser.add_argument("--email", default="Not specified", help="Email address to show in the report.")
    parser.add_argument("--company", default="Not specified", help="Company name to show in the report.")
    parser.add_argument("--logo", default=None, help="Optional path to a logo image.")
    parser.add_argument("--output", default="skillspector-report.pdf", help="Output PDF path.")

    args = parser.parse_args()

    scan_data = run_skillspector(args.skill_path)
    output_path = build_pdf(scan_data, args)

    print(f"Report generated: {output_path}")


if __name__ == "__main__":
    main()
