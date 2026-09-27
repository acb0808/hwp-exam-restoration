from __future__ import annotations
from hwp_units import pt_to_hwp
from callout_validation import validate_report_callout

import io
import re
import shutil
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path
from typing import Sequence
try:
    from equation_converter import EquationConverter, LatexConversionError
except ImportError:
    from equation_converter import EquationConverter, LatexConversionError

_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _escape_xml(text: str) -> str:
    cleaned = _ILLEGAL_XML_CHARS.sub("", text)
    return saxutils.escape(cleaned, entities={'"': "&quot;", "'": "&apos;"})


def _text_xml(text: str) -> str:
    """OWPML soft breaks are children of hp:t, not siblings of hp:t."""
    return '<hp:t>' + '<hp:lineBreak/>'.join(_escape_xml(part) for part in text.split('\n')) + '</hp:t>'


def _normalize_new_document_styles(members: dict[str, bytes]) -> dict[str, bytes]:
    """Make newly authored style tables ordinal-compatible with native Hancom.

    Builder templates may use sparse convenient style IDs. Remap IDs and every
    reference together at serialization; never apply this to an original edited
    through HwpxModifier. Keep the reusable template and builder elements intact.
    """
    from xml_spans import XmlDocument, HH
    header_key = 'Contents/header.xml'
    if header_key not in members:
        raise ValueError('Builder requires a header with character and paragraph definitions')
    header = XmlDocument(members[header_key])
    mappings = {}
    definition_ids = {}
    container_counts = {}
    for kind, container, reference in [('charPr', 'charProperties', 'charPrIDRef'), ('paraPr', 'paraProperties', 'paraPrIDRef')]:
        containers = header.find(container, HH)
        if len(containers) != 1:
            raise ValueError('Missing or ambiguous new-document style container: ' + container)
        entries = [n for n in containers[0].children if n.local == kind and n.uri == HH]
        old_ids = [n.attrs.get('id') for n in entries]
        if not entries or any(value is None or not value.isdecimal() for value in old_ids) or len(set(old_ids)) != len(old_ids):
            raise ValueError('Invalid or duplicate new-document style IDs: ' + kind)
        mappings[reference] = {old: str(index) for index, old in enumerate(old_ids)}
        definition_ids.update({n.path: str(index) for index, n in enumerate(entries)})
        container_counts[containers[0].path] = str(len(entries))
    result = dict(members)
    for name, data in members.items():
        if not name.endswith('.xml'):
            continue
        doc = XmlDocument(data)
        edits = []
        for node in doc.nodes:
            updates = {}
            if name == header_key:
                if node.path in definition_ids: updates['id'] = definition_ids[node.path]
                if node.path in container_counts: updates['itemCnt'] = container_counts[node.path]
            for reference, mapping in mappings.items():
                if reference not in node.attrs: continue
                old = node.attrs[reference]
                # Native numbering definitions use UINT_MAX as "inherit".
                if name == header_key and old == '4294967295': continue
                if old not in mapping: raise ValueError('Unknown new-document style reference: ' + reference + '=' + old)
                updates[reference] = mapping[old]
            if not updates: continue
            opening = doc.data[node.start:node.open_end]
            for key, value in updates.items():
                attribute = re.compile(rb'(?<![\w:.-])' + key.encode() + rb'\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27)')
                replacement = key.encode() + b'="' + value.encode() + b'"'
                opening, count = attribute.subn(lambda match: replacement, opening, count=1)
                if not count:
                    offset = len(opening) - (2 if opening.endswith(b'/>') else 1)
                    opening = opening[:offset] + b' ' + replacement + opening[offset:]
            edits.append((node.start, node.open_end, opening))
        if edits: result[name] = doc.replace(edits)
    return result


def estimate_equation_size(hwpeqn: str, font_size_pt: float = 10.0) -> tuple[int, int]:
    """
    Estimate tight (width, height) in HWP units matching native Hancom Office 2022 metrics.
    Prevents huge blank gaps caused by hardcoded 10000 HWPUNIT widths.
    """
    scale = font_size_pt / 10.0
    has_fraction = " over " in hwpeqn or "over" in hwpeqn
    has_tall = any(k in hwpeqn for k in ["sqrt", "sum", "int", "matrix", "cases", "^", "_"])

    if has_fraction:
        height = int(2400 * scale)
    elif has_tall:
        height = int(1150 * scale)
    else:
        height = int(980 * scale)

    if "over" in hwpeqn:
        prefix_part, frac_part = (hwpeqn.split("=", 1)) if "=" in hwpeqn and hwpeqn.find("=") < hwpeqn.find("over") else ("", hwpeqn)
        prefix_len = len(re.sub(r"[ `\^_{}]", "", prefix_part))
        parts = frac_part.split("over", 1)
        top_len = len(re.sub(r"[ `\^_{}]", "", parts[0]))
        bot_len = len(re.sub(r"[ `\^_{}]", "", parts[1]))
        effective_len = prefix_len + max(top_len, bot_len) + 1.2
    else:
        # 1. Subscripts and superscripts are 65% size
        sub_text = "".join(re.findall(r"[_^]\{([^{}]+)\}", hwpeqn))
        sub_len = len(re.sub(r"\\[a-zA-Z]+|[ `]", "", sub_text)) * 0.65

        # 2. Strip sub/superscripts to evaluate main expression
        main_expr = re.sub(r"[_^]\{[^{}]+\}|[_^][a-zA-Z0-9]", "", hwpeqn)

        # 3. Collapse Greek letters, symbols, and functions (\alpha, \beta, \cdot, \sin, \cos, etc.) to 1 char
        collapsed = re.sub(r"\\[a-zA-Z]+", "X", main_expr)
        collapsed = re.sub(r"#\s*[a-zA-Z]+", "X", collapsed)
        clean = re.sub(r"[ `{}()]", "", collapsed)
        effective_len = len(clean) + sub_len if (clean or sub_len) else 1

    op_count = sum(hwpeqn.count(op) for op in ["=", "+", "-", "<", ">", ":"])
    est_w = int((effective_len * 460 + op_count * 200 + 50) * scale)
    width = max(int(450 * scale), est_w)
    return width, height


from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DocumentTheme:
    """Document styling theme with print-friendly palettes and OWPML style mappings."""
    name: str
    primary: str
    secondary: str
    accent: str
    surface: str
    text: str
    border: str
    h1_char_pr: int
    callout_border_fill: int
    table_header_border_fill: int
    table_zebra_border_fill: int


DOCUMENT_THEMES: dict[str, DocumentTheme] = {
    "corporate_navy": DocumentTheme(
        name="corporate_navy",
        primary="#34495E",
        secondary="#526171",
        accent="#34495E",
        surface="#F7F7F6",
        text="#222222",
        border="#D8DADD",
        h1_char_pr=50,
        callout_border_fill=10,
        table_header_border_fill=14,
        table_zebra_border_fill=15,
    ),
    "modern_slate": DocumentTheme(
        name="modern_slate",
        primary="#18181B",
        secondary="#475569",
        accent="#991B1B",
        surface="#F4F4F5",
        text="#18181B",
        border="#E4E4E7",
        h1_char_pr=56,
        callout_border_fill=11,
        table_header_border_fill=8,
        table_zebra_border_fill=16,
    ),
    "forest_emerald": DocumentTheme(
        name="forest_emerald",
        primary="#064E3B",
        secondary="#047857",
        accent="#D97706",
        surface="#F0FDF4",
        text="#064E3B",
        border="#A7F3D0",
        h1_char_pr=57,
        callout_border_fill=12,
        table_header_border_fill=8,
        table_zebra_border_fill=17,
    ),
    "clean_mono": DocumentTheme(
        name="clean_mono",
        primary="#111827",
        secondary="#374151",
        accent="#4B5563",
        surface="#F3F4F6",
        text="#000000",
        border="#000000",
        h1_char_pr=58,
        callout_border_fill=13,
        table_header_border_fill=8,
        table_zebra_border_fill=16,
    ),
}


class HwpxBuilder:
    """
    Constructs 100% compliant HWPX (KS X 6101 / OWPML) documents with official fontfaces,
    styles, and paper layout, ensuring zero encoding/rendering corruption in Hancom Office.
    """

    def __init__(
        self,
        converter: EquationConverter | None = None,
        title: str = "문서",
        theme: str | DocumentTheme = "corporate_navy",
    ) -> None:
        self.converter = converter or EquationConverter()
        self.title = title
        if isinstance(theme, str):
            self.theme = DOCUMENT_THEMES.get(theme, DOCUMENT_THEMES["corporate_navy"])
        else:
            self.theme = theme
        self.elements: list[str] = []
        self.images: list[dict[str, Any]] = []
        self._next_para_id = 1000
        self._next_ctrl_id = 5000

        # Discover template base directory
        candidates = [
            Path(__file__).resolve().parent / "template_base",
            Path(__file__).resolve().parent.parent / "template_base",
        ]
        self.template_base = next((c for c in candidates if c.is_dir()), None)

    def add_paragraph(
        self,
        text: str,
        para_pr_id: int = 0,
        char_pr_id: int = 0,
        page_break: bool = False,
        underline: bool = False,
    ) -> None:
        """
        Add a normal text paragraph with paragraph style and font style, supporting soft line breaks.
        If underline=True, applies standard OWPML bottom underline styling.
        """
        # Anti-pattern detection: Warn if agent used repeated hyphens/underscores as a divider
        if re.match(r"^\s*[-=_~]{3,}\s*$", text.strip()):
            import warnings
            warnings.warn(
                f"Detected plain text divider '{text.strip()}'. Use add_horizontal_divider() instead "
                "to prevent line wrap distortion and ugly broken characters in Korean documents."
            )

        if underline and char_pr_id == 0:
            char_pr_id = 60  # Standard bottom underline style

        pb = "1" if page_break else "0"
        inner_xml = _text_xml(text)

        p_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="{para_pr_id}" styleIDRef="0" pageBreak="{pb}" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char_pr_id}">'
            f'{inner_xml}'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(p_xml)
        self._next_para_id += 1

    def add_horizontal_divider(
        self,
        thickness_pt: float = 0.7,
        color: str | None = None,
        margin_top_pt: float = 4.0,
        margin_bottom_pt: float = 6.0,
    ) -> None:
        """
        Add a professional, full-width vector divider line across the page margins.
        Prevents ugly broken line wraps caused by typing repeated hyphens (e.g. '-----').
        """
        total_width = 41954
        tbl_id = self._next_ctrl_id
        self._next_ctrl_id += 1
        
        bf_id = 19 if thickness_pt >= 1.0 else 18
        margin_top_hwp = pt_to_hwp(margin_top_pt)
        margin_bottom_hwp = pt_to_hwp(margin_bottom_pt)

        inner_p = (
            f'<hp:p id="{self._next_para_id + 1}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0"><hp:t/></hp:run>'
            f'</hp:p>'
        )

        tbl_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            f'<hp:tbl id="{tbl_id}" zOrder="1" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="1" colCnt="1" cellSpacing="0" borderFillIDRef="{bf_id}" noAdjust="0">'
            f'<hp:sz width="{total_width}" widthRelTo="ABSOLUTE" height="100" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="{margin_top_hwp}" bottom="{margin_bottom_hwp}"/>'
            f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:tr>'
            f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bf_id}">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'{inner_p}'
            f'</hp:subList>'
            f'<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{total_width}" height="100"/><hp:cellMargin left="0" right="0" top="0" bottom="0"/>'
            f'</hp:tc>'
            f'</hp:tr>'
            f'</hp:tbl>'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(tbl_xml)
        self._next_para_id += 3

    def add_equation(
        self,
        latex: str = "",
        font_size_pt: float = 11.0,
        inline: bool = True,
        raw_hwpeqn: str | None = None,
    ) -> None:
        """Convert LaTeX to hwpeqn (or use raw_hwpeqn) and add a fully compliant equation object."""
        if raw_hwpeqn:
            hwpeqn = raw_hwpeqn
        else:
            conversion = self.converter.convert(latex)
            if not conversion.ok or not conversion.hwpeqn:
                raise LatexConversionError(f"LaTeX conversion failed: {conversion.error}")
            hwpeqn = conversion.hwpeqn
        escaped_script = _escape_xml(hwpeqn)
        base_unit = pt_to_hwp(font_size_pt)
        
        # Hancom OWPML Standard: width="0", height="0", baseLine="0" triggers
        # native auto-recalculation of exact sub-pixel font glyph boundaries and baseline.
        eq_id = self._next_ctrl_id
        self._next_ctrl_id += 1

        eq_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="20" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            f'<hp:equation id="{eq_id}" zOrder="0" numberingType="EQUATION" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'version="Equation Version 60" baseLine="0" textColor="#000000" baseUnit="{base_unit}" lineMode="CHAR" font="HancomEQN">'
            f'<hp:sz width="0" widthRelTo="ABSOLUTE" height="0" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
            f'vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
            f'<hp:shapeComment>수식입니다.</hp:shapeComment>'
            f'<hp:script>{escaped_script}</hp:script>'
            f'</hp:equation>'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(eq_xml)
        self._next_para_id += 1

    def add_complex_paragraph(
        self,
        runs: Sequence[tuple[str, int]],
        para_pr_id: int = 0,
        page_break: bool = False,
    ) -> None:
        """Add a paragraph consisting of multiple runs with distinct character styles."""
        pb = "1" if page_break else "0"
        run_xmls = []
        for text, char_pr_id in runs:
            escaped = _escape_xml(text)
            run_xmls.append(f'<hp:run charPrIDRef="{char_pr_id}"><hp:t>{escaped}</hp:t></hp:run>')
        runs_joined = "".join(run_xmls)
        p_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="{para_pr_id}" styleIDRef="0" pageBreak="{pb}" columnBreak="0" merged="0">'
            f'{runs_joined}'
            f'</hp:p>'
        )
        self.elements.append(p_xml)
        self._next_para_id += 1

    def add_math_paragraph(
        self,
        text: str,
        font_size_pt: float = 10.0,
        para_pr_id: int = 0,
        char_pr_id: int = 0,
        page_break: bool = False,
    ) -> None:
        """
        Add a paragraph containing mixed text and inline LaTeX math delimited by $...$.
        Accurately sizes each inline equation to avoid huge blank gaps in Hancom Office.
        Example: "중심이 $A(4, 2)$ 이고 반지름의 길이가 $r$ ($r > 0$)인 원"
        """
        pb = "1" if page_break else "0"
        tokens = re.split(r"(\$.*?\$)", text)
        run_xmls = []
        base_unit = pt_to_hwp(font_size_pt)

        # Batch convert all inline formulas in this paragraph in one shot
        latex_tokens: list[str] = []
        for token in tokens:
            if token and token.startswith("$") and token.endswith("$") and len(token) >= 2:
                ltx = token[1:-1].strip()
                if ltx:
                    latex_tokens.append(ltx)

        conversion_dict: dict[str, str] = {}
        if latex_tokens:
            conversions = self.converter.convert_many(latex_tokens)
            if len(conversions) != len(latex_tokens):
                raise LatexConversionError("Inline LaTeX conversion returned an incomplete batch")
            for source, conv in zip(latex_tokens, conversions):
                if conv.latex != source or not conv.ok or not conv.hwpeqn:
                    raise LatexConversionError(f"Inline LaTeX conversion failed: {conv.error}")
                conversion_dict[source] = conv.hwpeqn

        for token in tokens:
            if not token:
                continue
            if token.startswith("$") and token.endswith("$") and len(token) >= 2:
                latex = token[1:-1].strip()
                if not latex:
                    continue
                hwpeqn = conversion_dict[latex]

                escaped_script = _escape_xml(hwpeqn)
                eq_id = self._next_ctrl_id
                self._next_ctrl_id += 1

                # Hancom OWPML Standard: width="0", height="0", baseLine="0" triggers
                # native auto-recalculation of exact sub-pixel font glyph boundaries and baseline.
                eq_run = (
                    f'<hp:run charPrIDRef="{char_pr_id}">'
                    f'<hp:equation id="{eq_id}" zOrder="0" numberingType="EQUATION" '
                    f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
                    f'version="Equation Version 60" baseLine="0" textColor="#000000" baseUnit="{base_unit}" lineMode="CHAR" font="HancomEQN">'
                    f'<hp:sz width="0" widthRelTo="ABSOLUTE" height="0" heightRelTo="ABSOLUTE" protect="0"/>'
                    f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
                    f'vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
                    f'<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
                    f'<hp:shapeComment>수식입니다.</hp:shapeComment>'
                    f'<hp:script>{escaped_script}</hp:script>'
                    f'</hp:equation>'
                    f'</hp:run>'
                )
                run_xmls.append(eq_run)
            else:
                inner_xml = _text_xml(token)
                run_xmls.append(f'<hp:run charPrIDRef="{char_pr_id}">{inner_xml}</hp:run>')

        runs_joined = "".join(run_xmls)
        p_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="{para_pr_id}" styleIDRef="0" pageBreak="{pb}" columnBreak="0" merged="0">'
            f'{runs_joined}'
            f'</hp:p>'
        )
        self.elements.append(p_xml)
        self._next_para_id += 1

    def add_numbered_equation(
        self,
        latex: str = "",
        eq_num: str = "",
        font_size_pt: float = 10.5,
        eq_para_pr_id: int = 22,
        num_para_pr_id: int = 23,
        raw_hwpeqn: str | None = None,
    ) -> None:
        """Add an equation with right-aligned numbering using a borderless table layout."""
        if raw_hwpeqn:
            hwpeqn = raw_hwpeqn
        else:
            conversion = self.converter.convert(latex)
            if not conversion.ok or not conversion.hwpeqn:
                raise LatexConversionError(f"LaTeX conversion failed: {conversion.error}")
            hwpeqn = conversion.hwpeqn
        escaped_script = _escape_xml(hwpeqn)
        base_unit = pt_to_hwp(font_size_pt)
        eq_id = self._next_ctrl_id
        self._next_ctrl_id += 1
        tbl_id = self._next_ctrl_id
        self._next_ctrl_id += 1

        total_width = 41954
        w0 = 37500
        w1 = total_width - w0

        # Hancom OWPML Standard: width="0", height="0", baseLine="0" triggers
        # native auto-recalculation of exact sub-pixel font glyph boundaries and baseline.
        eq_xml_run = (
            f'<hp:equation id="{eq_id}" zOrder="0" numberingType="EQUATION" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'version="Equation Version 60" baseLine="0" textColor="#000000" baseUnit="{base_unit}" lineMode="CHAR" font="HancomEQN">'
            f'<hp:sz width="0" widthRelTo="ABSOLUTE" height="0" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
            f'vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
            f'<hp:shapeComment>수식입니다.</hp:shapeComment>'
            f'<hp:script>{escaped_script}</hp:script>'
            f'</hp:equation>'
        )

        tbl_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            f'<hp:tbl id="{tbl_id}" zOrder="1" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="1" colCnt="2" cellSpacing="0" borderFillIDRef="7" noAdjust="0">'
            f'<hp:sz width="{total_width}" widthRelTo="ABSOLUTE" height="1200" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="100" bottom="100"/>'
            f'<hp:inMargin left="100" right="100" top="50" bottom="50"/>'
            f'<hp:tr>'
            f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="7">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'<hp:p id="{self._next_para_id + 1}" paraPrIDRef="{eq_para_pr_id}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{eq_xml_run}</hp:run>'
            f'</hp:p>'
            f'</hp:subList>'
            f'<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{w0}" height="1200"/><hp:cellMargin left="100" right="100" top="50" bottom="50"/>'
            f'</hp:tc>'
            f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="7">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'<hp:p id="{self._next_para_id + 2}" paraPrIDRef="{num_para_pr_id}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0"><hp:t>{_escape_xml(eq_num)}</hp:t></hp:run>'
            f'</hp:p>'
            f'</hp:subList>'
            f'<hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{w1}" height="1200"/><hp:cellMargin left="100" right="100" top="50" bottom="50"/>'
            f'</hp:tc>'
            f'</hp:tr>'
            f'</hp:tbl>'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(tbl_xml)
        self._next_para_id += 5

    def add_table(
        self,
        rows: int,
        cols: int,
        data: Sequence[Sequence[str]],
        col_widths: Sequence[int] | None = None,
        col_aligns: Sequence[int] | None = None,
        header_shaded: bool = True,
        booktabs: bool = False,
    ) -> None:
        """Add a table element with accurate cell sizes, solid borders, and alignments."""
        if col_widths is None or len(col_widths) != cols:
            total_width = 41954
            base_w = total_width // cols
            computed_widths = [base_w] * cols
            computed_widths[-1] = total_width - (base_w * (cols - 1))
        else:
            computed_widths = list(col_widths)
            total_width = sum(computed_widths)

        # Default alignments: Left (0) for col 0, Right (23) for others
        if col_aligns is None or len(col_aligns) != cols:
            computed_aligns = [0] + [23] * (cols - 1)
        else:
            computed_aligns = list(col_aligns)

        row_h = 1000
        total_h = rows * row_h
        tbl_id = self._next_ctrl_id
        self._next_ctrl_id += 1

        tbl_border_fill = 7 if booktabs else 3
        tbl_xml_parts = [
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">',
            f'<hp:run charPrIDRef="0">',
            f'<hp:tbl id="{tbl_id}" zOrder="1" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{rows}" colCnt="{cols}" cellSpacing="0" borderFillIDRef="{tbl_border_fill}" noAdjust="0">',
            f'<hp:sz width="{total_width}" widthRelTo="ABSOLUTE" height="{total_h}" heightRelTo="ABSOLUTE" protect="0"/>',
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>',
            f'<hp:outMargin left="283" right="283" top="283" bottom="283"/>',
            f'<hp:inMargin left="510" right="510" top="141" bottom="141"/>',
        ]

        for r_idx in range(rows):
            tbl_xml_parts.append('<hp:tr>')
            is_header_row = (r_idx == 0)
            is_last_row = (r_idx == rows - 1)

            if booktabs:
                if is_header_row:
                    cell_border_fill = 8  # Top 0.3mm SOLID, Bottom 0.12mm SOLID
                    default_char_pr = 43   # 9pt Bold
                    default_para_pr = computed_aligns[0] if cols == 1 else None
                elif is_last_row:
                    cell_border_fill = 9  # Bottom 0.3mm SOLID
                    default_char_pr = 42   # 9pt Regular
                    default_para_pr = None
                else:
                    cell_border_fill = 7  # Transparent
                    default_char_pr = 42   # 9pt Regular
                    default_para_pr = None
            else:
                cell_border_fill = 4 if (is_header_row and header_shaded) else 3
                default_char_pr = 10 if is_header_row else 0
                default_para_pr = 22 if is_header_row else None

            for c_idx in range(cols):
                raw_cell = ""
                if r_idx < len(data) and c_idx < len(data[r_idx]):
                    raw_cell = str(data[r_idx][c_idx])

                lines = raw_cell.split("\n")
                char_pr = default_char_pr
                if default_para_pr is not None:
                    para_pr = default_para_pr
                else:
                    para_pr = computed_aligns[c_idx]

                p_runs = []
                for l_i, line_text in enumerate(lines):
                    escaped_l = _escape_xml(line_text)
                    pid = self._next_para_id + 2000 + r_idx * 50 + c_idx * 5 + l_i
                    p_runs.append(
                        f'<hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                        f'<hp:run charPrIDRef="{char_pr}"><hp:t>{escaped_l}</hp:t></hp:run>'
                        f'</hp:p>'
                    )
                p_joined = "".join(p_runs)

                tbl_xml_parts.append(
                    f'<hp:tc name="" header="{1 if is_header_row else 0}" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{cell_border_fill}">'
                    f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f'{p_joined}'
                    f'</hp:subList>'
                    f'<hp:cellAddr colAddr="{c_idx}" rowAddr="{r_idx}"/>'
                    f'<hp:cellSpan colSpan="1" rowSpan="1"/>'
                    f'<hp:cellSz width="{computed_widths[c_idx]}" height="{row_h}"/>'
                    f'<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                    f'</hp:tc>'
                )
            tbl_xml_parts.append('</hp:tr>')

        tbl_xml_parts.extend([
            f'</hp:tbl>',
            f'</hp:run>',
            f'</hp:p>'
        ])

        self.elements.append("".join(tbl_xml_parts))
        self._next_para_id += 1

    def add_heading(
        self,
        level: int,
        text: str,
        numbering_prefix: str | None = None,
        page_break: bool = False,
    ) -> None:
        """
        Add a standardized Korean document heading with proper typographic scale, margins, and bullet.
        - Level 1 (장/대주제): 16pt Bold theme color, ■ prefix, top 16pt / bottom 6pt margin
        - Level 2 (절/중주제): 13pt Bold dark slate, ○ prefix, top 12pt / bottom 4pt margin
        - Level 3 (항/소주제): 11pt Bold slate, ― prefix, top 8pt / bottom 2pt margin
        - Level 4 (세부항목): 10pt Regular/Bold, · prefix
        """
        level = max(1, min(level, 4))
        if level == 1:
            para_pr_id = 50
            char_pr_id = self.theme.h1_char_pr
            default_bullet = "■ "
        elif level == 2:
            para_pr_id = 51
            char_pr_id = 51
            default_bullet = "○ "
        elif level == 3:
            para_pr_id = 52
            char_pr_id = 52
            default_bullet = "― "
        else:
            para_pr_id = 0
            char_pr_id = 52
            default_bullet = "· "

        prefix = (numbering_prefix + " ") if numbering_prefix is not None else default_bullet
        if text.startswith(prefix.strip()) or any(text.startswith(b) for b in ["■", "○", "―", "·", "□", "◆", "▶"]):
            full_text = text
        else:
            full_text = f"{prefix}{text}"

        self.add_paragraph(full_text, para_pr_id=para_pr_id, char_pr_id=char_pr_id, page_break=page_break)

    def add_callout_box(
        self,
        text: str,
        title: str | None = None,
        style: str = "summary",
    ) -> None:
        """
        Add a plain-text report summary. Exam labels and math require Studio box blocks.
        """
        validate_report_callout(text, title, style)
        total_width = 41954
        tbl_id = self._next_ctrl_id
        self._next_ctrl_id += 1
        border_fill_id = self.theme.callout_border_fill

        inner_paras = []
        if title:
            escaped_title = _escape_xml(title)
            inner_paras.append(
                f'<hp:p id="{self._next_para_id + 1}" paraPrIDRef="56" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="54"><hp:t>{escaped_title}</hp:t></hp:run>'
                f'</hp:p>'
            )

        lines = text.split("\n") if "\n" in text else [text]
        for idx, line in enumerate(lines):
            if not line.strip() and idx > 0:
                continue
            escaped_line = _escape_xml(line)
            pid = self._next_para_id + 2 + idx
            inner_paras.append(
                f'<hp:p id="{pid}" paraPrIDRef="56" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="53"><hp:t>{escaped_line}</hp:t></hp:run>'
                f'</hp:p>'
            )

        joined_inner = "".join(inner_paras)
        tbl_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            f'<hp:tbl id="{tbl_id}" zOrder="1" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="1" colCnt="1" cellSpacing="0" borderFillIDRef="{border_fill_id}" noAdjust="0">'
            f'<hp:sz width="{total_width}" widthRelTo="ABSOLUTE" height="1500" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="200" bottom="200"/>'
            f'<hp:inMargin left="300" right="200" top="150" bottom="150"/>'
            f'<hp:tr>'
            f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{border_fill_id}">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'{joined_inner}'
            f'</hp:subList>'
            f'<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{total_width}" height="1500"/><hp:cellMargin left="300" right="200" top="150" bottom="150"/>'
            f'</hp:tc>'
            f'</hp:tr>'
            f'</hp:tbl>'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(tbl_xml)
        self._next_para_id += len(lines) + 5

    def add_bullet_item(
        self,
        level: int,
        text: str,
        bold_prefix: str | None = None,
    ) -> None:
        """
        Add a bullet item with hanging indent (내어쓰기) conforming to Korean document standards.
        - Level 1: Left margin 2000, intent -1000 HWPUNIT (1 char hanging indent), paraPrIDRef 53
        - Level 2: Left margin 3000, intent -1000 HWPUNIT, paraPrIDRef 54
        - Level 3: Left margin 4000, intent -1000 HWPUNIT, paraPrIDRef 55
        """
        level = max(1, min(level, 3))
        para_pr_id = 52 + level  # 53 for L1, 54 for L2, 55 for L3
        default_symbols = {1: "■ ", 2: "○ ", 3: "― "}
        sym = default_symbols[level]

        if not any(text.startswith(s) for s in ["■", "○", "―", "·", "□", "◆", "-", "*", "1.", "가."]):
            full_text = f"{sym}{text}"
        else:
            full_text = text

        if bold_prefix:
            if full_text.startswith(sym):
                bullet_part = sym
                body_part = full_text[len(sym):]
            else:
                bullet_part = ""
                body_part = full_text

            if body_part.startswith(bold_prefix):
                remainder = body_part[len(bold_prefix):]
                runs = [
                    (f"{bullet_part}{bold_prefix}", 10),
                    (remainder, 53),
                ]
                self.add_complex_paragraph(runs, para_pr_id=para_pr_id)
                return

        self.add_paragraph(full_text, para_pr_id=para_pr_id, char_pr_id=53)

    def add_styled_table(
        self,
        rows: int,
        cols: int,
        data: Sequence[Sequence[str]],
        col_widths: Sequence[int] | None = None,
        col_aligns: Sequence[int] | None = None,
        zebra: bool = False,
        booktabs: bool = True,
        body_char_pr: int = 53,
        header_char_pr: int = 55,
        cell_margin_lr: int = 510,
    ) -> None:
        """
        Add a high-quality data table with theme styling, zebra striping, and smart alignments.
        - Numerical columns right-aligned (23)
        - Short labels / code columns centered (22)
        - Text columns left-aligned (11)
        - Header row centered with theme tint
        """
        if col_widths is None or len(col_widths) != cols:
            total_width = 41954
            base_w = total_width // cols
            computed_widths = [base_w] * cols
            computed_widths[-1] = total_width - (base_w * (cols - 1))
        else:
            computed_widths = list(col_widths)
            total_width = sum(computed_widths)

        # Smart column alignments if not provided
        if col_aligns is None or len(col_aligns) != cols:
            computed_aligns = []
            for c in range(cols):
                values = [str(data[r][c]).strip() for r in range(1, len(data)) if c < len(data[r])]
                if not values:
                    computed_aligns.append(11)
                    continue
                is_numeric = all(
                    re.match(r"^[\$₩€]?\s*-?\d[\d,\.]*\s*[%점원억달러배]?$", v)
                    for v in values if v
                )
                if is_numeric and len(values) > 0:
                    computed_aligns.append(23)  # Right
                elif all(len(v) <= 4 for v in values):
                    computed_aligns.append(22)  # Center
                else:
                    computed_aligns.append(11)  # Left (align="LEFT", breakNonLatinWord="BREAK_WORD")
        else:
            computed_aligns = list(col_aligns)

        row_h = 1000
        total_h = rows * row_h
        tbl_id = self._next_ctrl_id
        self._next_ctrl_id += 1

        tbl_border_fill = 7 if booktabs else 3
        tbl_xml_parts = [
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">',
            f'<hp:run charPrIDRef="0">',
            f'<hp:tbl id="{tbl_id}" zOrder="1" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{rows}" colCnt="{cols}" cellSpacing="0" borderFillIDRef="{tbl_border_fill}" noAdjust="0">',
            f'<hp:sz width="{total_width}" widthRelTo="ABSOLUTE" height="{total_h}" heightRelTo="ABSOLUTE" protect="0"/>',
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>',
            f'<hp:outMargin left="283" right="283" top="283" bottom="283"/>',
            f'<hp:inMargin left="{cell_margin_lr}" right="{cell_margin_lr}" top="141" bottom="141"/>',
        ]

        for r_idx in range(rows):
            tbl_xml_parts.append('<hp:tr>')
            is_header_row = (r_idx == 0)
            is_last_row = (r_idx == rows - 1)

            if is_header_row:
                cell_border_fill = self.theme.table_header_border_fill
                default_char_pr = header_char_pr
                default_para_pr = 22  # Centered
            elif zebra and (r_idx % 2 == 0):
                cell_border_fill = self.theme.table_zebra_border_fill
                default_char_pr = body_char_pr
                default_para_pr = None
            elif booktabs and is_last_row:
                cell_border_fill = 9   # Bottom thick rule
                default_char_pr = body_char_pr
                default_para_pr = None
            else:
                cell_border_fill = 7 if booktabs else 3
                default_char_pr = body_char_pr
                default_para_pr = None

            for c_idx in range(cols):
                raw_cell = ""
                if r_idx < len(data) and c_idx < len(data[r_idx]):
                    raw_cell = str(data[r_idx][c_idx])

                lines = raw_cell.split("\n")
                char_pr = default_char_pr
                para_pr = default_para_pr if default_para_pr is not None else computed_aligns[c_idx]

                p_runs = []
                for l_i, line_text in enumerate(lines):
                    escaped_l = _escape_xml(line_text)
                    pid = self._next_para_id + 2000 + r_idx * 50 + c_idx * 5 + l_i
                    p_runs.append(
                        f'<hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                        f'<hp:run charPrIDRef="{char_pr}"><hp:t>{escaped_l}</hp:t></hp:run>'
                        f'</hp:p>'
                    )
                p_joined = "".join(p_runs)

                tbl_xml_parts.append(
                    f'<hp:tc name="" header="{1 if is_header_row else 0}" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{cell_border_fill}">'
                    f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f'{p_joined}'
                    f'</hp:subList>'
                    f'<hp:cellAddr colAddr="{c_idx}" rowAddr="{r_idx}"/>'
                    f'<hp:cellSpan colSpan="1" rowSpan="1"/>'
                    f'<hp:cellSz width="{computed_widths[c_idx]}" height="{row_h}"/>'
                    f'<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                    f'</hp:tc>'
                )
            tbl_xml_parts.append('</hp:tr>')

        tbl_xml_parts.extend([
            f'</hp:tbl>',
            f'</hp:run>',
            f'</hp:p>'
        ])

        self.elements.append("".join(tbl_xml_parts))
        self._next_para_id += 1

    def add_picture(
        self,
        image_path: Path | str,
        width_hwpunit: int = 41954,
        height_hwpunit: int = 19928,
        caption: str = "",
        para_pr_id: int = 20,
    ) -> None:
        """Add an embedded picture into the document."""
        img_p = Path(image_path).resolve()
        if not img_p.exists():
            raise FileNotFoundError(f"Image not found: {img_p}")

        image_index = len(self.images) + 1
        item_id = f"image{image_index}"
        
        self.images.append({
            "id": item_id,
            "path": img_p,
            "filename": f"{item_id}.png",
            "bytes": img_p.read_bytes(),
        })

        pic_id = self._next_ctrl_id
        self._next_ctrl_id += 1

        pic_xml = (
            f'<hp:p id="{self._next_para_id}" paraPrIDRef="{para_pr_id}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            f'<hp:pic id="{pic_id}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="112077639" reverse="0">'
            f'<hp:offset x="0" y="0"/>'
            f'<hp:orgSz width="160000" height="76000"/>'
            f'<hp:curSz width="0" height="0"/>'
            f'<hp:flip horizontal="0" vertical="0"/>'
            f'<hp:rotationInfo angle="0" centerX="80000" centerY="38000" rotateimage="1"/>'
            f'<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/><hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/><hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
            f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
            f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="160000" y="0"/><hc:pt2 x="160000" y="76000"/><hc:pt3 x="0" y="76000"/></hp:imgRect>'
            f'<hp:imgClip left="0" right="160000" top="0" bottom="76000"/>'
            f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:imgDim dimwidth="160000" dimheight="76000"/>'
            f'<hp:effects/>'
            f'<hp:sz width="{width_hwpunit}" widthRelTo="ABSOLUTE" height="{height_hwpunit}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:shapeComment>{_escape_xml(caption or "그림입니다.")}</hp:shapeComment>'
            f'</hp:pic>'
            f'</hp:run>'
            f'</hp:p>'
        )
        self.elements.append(pic_xml)
        self._next_para_id += 1

    def _get_secpr(self) -> str:
        # Hancom Office 2022 standard A4 section properties
        return (
            '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="1" textVerticalWidthHead="0" masterPageCnt="0">'
            '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
            '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
            '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
            '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
            '<hp:pagePr landscape="WIDELY" width="59528" height="84186" gutterType="LEFT_ONLY"><hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504" top="5668" bottom="4252"/></hp:pagePr>'
            '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/><hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/><hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/><hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
            '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/><hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/><hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/><hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
            '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            '<hp:pageBorderFill type="EVEN" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            '<hp:pageBorderFill type="ODD" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
            '</hp:secPr>'
            '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/></hp:ctrl>'
        )

    def _render_section_xml(self) -> str:
        secpr = self._get_secpr()
        first_para_content = ""
        other_elements = []

        if self.elements:
            first_elem = self.elements[0]
            # Wrap secPr inside the first paragraph's initial run
            secpr_run = f'<hp:run charPrIDRef="0">{secpr}</hp:run>'
            
            # Find insertion point after opening <hp:p ...>
            gt_pos = first_elem.find(">")
            if gt_pos != -1:
                first_elem_modified = first_elem[:gt_pos+1] + secpr_run + first_elem[gt_pos+1:]
            else:
                first_elem_modified = f'<hp:p id="1" paraPrIDRef="0" styleIDRef="0">{secpr_run}</hp:p>\n' + first_elem
            other_elements = [first_elem_modified] + self.elements[1:]
        else:
            other_elements = [f'<hp:p id="1" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">{secpr}</hp:run></hp:p>']

        body_content = "\n".join(other_elements)

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<hs:sec xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
            'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
            'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
            'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
            'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
            'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
            'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf/" '
            'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
            'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
            'xmlns:epub="http://www.idpf.org/2007/ops" '
            'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">\n'
            f'{body_content}\n'
            '</hs:sec>'
        )

    def _render_content_hpf(self) -> str:
        manifest_items = [
            '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>',
            '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>',
        ]
        for img in self.images:
            manifest_items.append(
                f'<opf:item id="{img["id"]}" href="BinData/{img["filename"]}" media-type="image/png" isEmbeded="1"/>'
            )
        manifest_str = "\n".join(manifest_items)

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="2.0" unique-identifier="BookId">\n'
            '<opf:metadata>\n'
            f'<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">{_escape_xml(self.title)}</dc:title>\n'
            '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">ko</dc:language>\n'
            '</opf:metadata>\n'
            '<opf:manifest>\n'
            f'{manifest_str}\n'
            '</opf:manifest>\n'
            '<opf:spine>\n'
            '<opf:itemref idref="header"/>\n'
            '<opf:itemref idref="section0"/>\n'
            '</opf:spine>\n'
            '</opf:package>'
        )

    def save(self, filepath: Path | str) -> Path:
        out_path = Path(filepath).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        members = {}
        if self.template_base and self.template_base.is_dir():
            for file_p in self.template_base.rglob('*'):
                if file_p.is_file():
                    members[file_p.relative_to(self.template_base).as_posix()] = file_p.read_bytes()
        members['Contents/section0.xml'] = self._render_section_xml().encode('utf-8')
        members['Contents/content.hpf'] = self._render_content_hpf().encode('utf-8')
        for img in self.images:
            members[f"BinData/{img['filename']}"] = img['bytes']
        members = _normalize_new_document_styles(members)
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED)
        return out_path
