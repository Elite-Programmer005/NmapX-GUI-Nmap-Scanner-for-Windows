from __future__ import annotations

import csv
from datetime import datetime
from typing import IO, Any, Dict, List, Union

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


FileOrPath = Union[str, IO[Any]]


def export_csv(results: List[Dict], file: FileOrPath) -> bool:
    try:
        if isinstance(file, str):
            csv_file: IO[Any]
            csv_file = open(file, "w", newline="", encoding="utf-8")
            close_after = True
        else:
            csv_file = file
            close_after = False

        writer = csv.DictWriter(
            csv_file,
            fieldnames=["host", "port", "protocol", "state", "service", "version"],
        )
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "host": row.get("host", ""),
                    "port": row.get("port", ""),
                    "protocol": row.get("protocol", ""),
                    "state": row.get("state", ""),
                    "service": row.get("service", ""),
                    "version": row.get("version", ""),
                }
            )
        if close_after:
            csv_file.close()
        return True
    except Exception:
        return False


def export_pdf(results: List[Dict], target: str, profile: str, file: FileOrPath) -> bool:
    try:
        document = SimpleDocTemplate(file, pagesize=A4)
        styles = getSampleStyleSheet()
        elements = []

        elements.append(Paragraph("<b>NmapX</b>", styles["Title"]))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f"Target: {target}", styles["Normal"]))
        elements.append(Paragraph(f"Profile: {profile}", styles["Normal"]))
        elements.append(
            Paragraph(
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 12))

        table_data = [["Host", "Port", "State", "Service", "Version"]]
        for row in results:
            table_data.append(
                [
                    str(row.get("host", "")),
                    str(row.get("port", "")),
                    str(row.get("state", "")),
                    str(row.get("service", "")),
                    str(row.get("version", "")),
                ]
            )

        table = Table(table_data, repeatRows=1)
        style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#160d2e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#e0d8f0")),
                ("FONTNAME", (0, 0), (-1, -1), "Courier"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bf5fff")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8f8f8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )

        for idx, row in enumerate(results, start=1):
            state = str(row.get("state", "")).lower()
            if state == "open":
                style.add("TEXTCOLOR", (2, idx), (2, idx), colors.HexColor("#008000"))
            elif state == "filtered":
                style.add("TEXTCOLOR", (2, idx), (2, idx), colors.HexColor("#ff8c00"))
            elif state == "closed":
                style.add("TEXTCOLOR", (2, idx), (2, idx), colors.HexColor("#cc0000"))

        table.setStyle(style)
        elements.append(table)

        document.build(elements)
        return True
    except Exception:
        return False
