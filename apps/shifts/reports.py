"""Small dependency-free XLSX writer for shift reports."""

from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
ET.register_namespace("", MAIN)
ET.register_namespace("r", REL)


@dataclass(frozen=True)
class StyledCell:
    value: object
    style: int = 0


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _worksheet(rows: list[list[object]]) -> bytes:
    root = ET.Element(f"{{{MAIN}}}worksheet")
    sheet_data = ET.SubElement(root, f"{{{MAIN}}}sheetData")
    for row_number, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{MAIN}}}row", {"r": str(row_number)})
        for col_number, value in enumerate(values, start=1):
            if value is None:
                continue
            style = 0
            if isinstance(value, StyledCell):
                style = value.style
                value = value.value
            cell = ET.SubElement(
                row,
                f"{{{MAIN}}}c",
                {"r": f"{_column_name(col_number)}{row_number}"},
            )
            if style:
                cell.set("s", str(style))
            if isinstance(value, (int, float)):
                cell.set("t", "n")
                ET.SubElement(cell, f"{{{MAIN}}}v").text = str(value)
            else:
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{MAIN}}}is")
                text = ET.SubElement(inline, f"{{{MAIN}}}t")
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                text.text = str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _styles() -> bytes:
    root = ET.Element(f"{{{MAIN}}}styleSheet")
    fonts = ET.SubElement(root, f"{{{MAIN}}}fonts", {"count": "2"})
    ET.SubElement(fonts, f"{{{MAIN}}}font")
    font = ET.SubElement(fonts, f"{{{MAIN}}}font")
    ET.SubElement(font, f"{{{MAIN}}}b")
    ET.SubElement(font, f"{{{MAIN}}}color", {"rgb": "FFFFFFFF"})
    fills = ET.SubElement(root, f"{{{MAIN}}}fills", {"count": "6"})
    ET.SubElement(ET.SubElement(fills, f"{{{MAIN}}}fill"), f"{{{MAIN}}}patternFill", {"patternType": "none"})
    ET.SubElement(ET.SubElement(fills, f"{{{MAIN}}}fill"), f"{{{MAIN}}}patternFill", {"patternType": "gray125"})
    for color in ("FF1F4E78", "FFD9EAD3", "FFF4CCCC", "FFD9EAF7"):
        fill = ET.SubElement(fills, f"{{{MAIN}}}fill")
        pattern = ET.SubElement(fill, f"{{{MAIN}}}patternFill", {"patternType": "solid"})
        ET.SubElement(pattern, f"{{{MAIN}}}fgColor", {"rgb": color})
        ET.SubElement(pattern, f"{{{MAIN}}}bgColor", {"indexed": "64"})
    borders = ET.SubElement(root, f"{{{MAIN}}}borders", {"count": "1"})
    ET.SubElement(borders, f"{{{MAIN}}}border")
    base = ET.SubElement(root, f"{{{MAIN}}}cellStyleXfs", {"count": "1"})
    ET.SubElement(base, f"{{{MAIN}}}xf", {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"})
    xfs = ET.SubElement(root, f"{{{MAIN}}}cellXfs", {"count": "5"})
    ET.SubElement(xfs, f"{{{MAIN}}}xf", {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0", "xfId": "0"})
    header = ET.SubElement(xfs, f"{{{MAIN}}}xf", {"numFmtId": "0", "fontId": "1", "fillId": "2", "borderId": "0", "xfId": "0", "applyFont": "1", "applyFill": "1", "applyAlignment": "1"})
    ET.SubElement(header, f"{{{MAIN}}}alignment", {"horizontal": "center", "vertical": "center", "wrapText": "1"})
    for fill_id in (3, 4, 5):
        xf = ET.SubElement(xfs, f"{{{MAIN}}}xf", {"numFmtId": "0", "fontId": "0", "fillId": str(fill_id), "borderId": "0", "xfId": "0", "applyFill": "1", "applyAlignment": "1"})
        ET.SubElement(xf, f"{{{MAIN}}}alignment", {"vertical": "center", "wrapText": "1"})
    styles = ET.SubElement(root, f"{{{MAIN}}}cellStyles", {"count": "1"})
    ET.SubElement(styles, f"{{{MAIN}}}cellStyle", {"name": "Normal", "xfId": "0", "builtinId": "0"})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def workbook_bytes(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    """Build a minimal valid XLSX workbook from named sheet row data."""
    if not sheets:
        raise ValueError("En az bir Excel sayfası gereklidir.")
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        content_types = ET.Element(f"{{{CONTENT}}}Types")
        ET.SubElement(
            content_types,
            f"{{{CONTENT}}}Default",
            {
                "Extension": "rels",
                "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
            },
        )
        ET.SubElement(content_types, f"{{{CONTENT}}}Override", {"PartName": "/xl/styles.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"})
        ET.SubElement(content_types, f"{{{CONTENT}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
        ET.SubElement(
            content_types,
            f"{{{CONTENT}}}Override",
            {
                "PartName": "/xl/workbook.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            },
        )
        for index in range(1, len(sheets) + 1):
            ET.SubElement(
                content_types,
                f"{{{CONTENT}}}Override",
                {
                    "PartName": f"/xl/worksheets/sheet{index}.xml",
                    "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
                },
            )
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(sheets[index - 1][1]))
        archive.writestr("[Content_Types].xml", ET.tostring(content_types, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/styles.xml", _styles())

        package_rels = ET.Element(f"{{{PKG_REL}}}Relationships")
        ET.SubElement(
            package_rels,
            f"{{{PKG_REL}}}Relationship",
            {"Id": "rId1", "Type": f"{REL}/officeDocument", "Target": "xl/workbook.xml"},
        )
        archive.writestr("_rels/.rels", ET.tostring(package_rels, encoding="utf-8", xml_declaration=True))

        workbook = ET.Element(f"{{{MAIN}}}workbook")
        workbook_sheets = ET.SubElement(workbook, f"{{{MAIN}}}sheets")
        workbook_rels = ET.Element(f"{{{PKG_REL}}}Relationships")
        for index, (name, _) in enumerate(sheets, start=1):
            ET.SubElement(
                workbook_sheets,
                f"{{{MAIN}}}sheet",
                {"name": name[:31], "sheetId": str(index), f"{{{REL}}}id": f"rId{index}"},
            )
            ET.SubElement(
                workbook_rels,
                f"{{{PKG_REL}}}Relationship",
                {
                    "Id": f"rId{index}",
                    "Type": f"{REL}/worksheet",
                    "Target": f"worksheets/sheet{index}.xml",
                },
            )
        ET.SubElement(workbook_rels, f"{{{PKG_REL}}}Relationship", {"Id": f"rId{len(sheets) + 1}", "Type": f"{REL}/styles", "Target": "styles.xml"})
        archive.writestr("xl/workbook.xml", ET.tostring(workbook, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(workbook_rels, encoding="utf-8", xml_declaration=True))
    return stream.getvalue()
