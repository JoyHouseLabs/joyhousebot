"""Structure-aware WordprocessingML extraction for private documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

from .chunking import chunk_text, normalize_whitespace
from .models import Chunk


@dataclass(frozen=True)
class _DocxBlock:
    text: str
    block_type: str
    section_path: tuple[str, ...]
    page: int


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _attribute(element: ElementTree.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return ""


def _direct_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((item for item in element if _local_name(item.tag) == name), None)


def _direct_children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [item for item in element if _local_name(item.tag) == name]


def _outline_level(element: ElementTree.Element | None) -> int | None:
    if element is None:
        return None
    value = _attribute(element, "val")
    if not value.isdigit():
        return None
    resolved = int(value)
    return resolved + 1 if 0 <= resolved <= 8 else None


def _inferred_heading_level(value: str) -> int | None:
    normalized = re.sub(r"[\s_-]+", "", value).casefold()
    match = re.search(r"(?:heading|标题|小标题)([1-9])$", normalized)
    return int(match.group(1)) if match else None


def _style_heading_levels(
    styles_root: ElementTree.Element | None,
) -> dict[str, int]:
    if styles_root is None:
        return {}
    raw: dict[str, tuple[int | None, str]] = {}
    for style in styles_root.iter():
        if _local_name(style.tag) != "style" or _attribute(style, "type") != "paragraph":
            continue
        style_id = _attribute(style, "styleId")
        if not style_id:
            continue
        properties = _direct_child(style, "pPr")
        outline = _direct_child(properties, "outlineLvl") if properties is not None else None
        name = _direct_child(style, "name")
        based_on = _direct_child(style, "basedOn")
        raw[style_id] = (
            _outline_level(outline)
            or _inferred_heading_level(_attribute(name, "val"))
            or _inferred_heading_level(style_id),
            _attribute(based_on, "val") if based_on is not None else "",
        )

    resolved: dict[str, int] = {}

    def resolve(style_id: str, visiting: set[str]) -> int | None:
        if style_id in resolved:
            return resolved[style_id]
        if style_id in visiting or style_id not in raw:
            return None
        visiting.add(style_id)
        level, parent = raw[style_id]
        level = level or (resolve(parent, visiting) if parent else None)
        visiting.remove(style_id)
        if level is not None:
            resolved[style_id] = level
        return level

    for style_id in raw:
        resolve(style_id, set())
    return resolved


def _paragraph_properties(paragraph: ElementTree.Element) -> ElementTree.Element | None:
    return _direct_child(paragraph, "pPr")


def _paragraph_heading_level(
    paragraph: ElementTree.Element, style_levels: dict[str, int]
) -> int | None:
    properties = _paragraph_properties(paragraph)
    if properties is None:
        return None
    direct = _outline_level(_direct_child(properties, "outlineLvl"))
    if direct is not None:
        return direct
    style = _direct_child(properties, "pStyle")
    style_id = _attribute(style, "val") if style is not None else ""
    return style_levels.get(style_id) or _inferred_heading_level(style_id)


def _is_enabled(element: ElementTree.Element | None) -> bool:
    if element is None:
        return False
    return _attribute(element, "val").casefold() not in {"0", "false", "off"}


def _paragraph_break_before(paragraph: ElementTree.Element) -> bool:
    properties = _paragraph_properties(paragraph)
    return _is_enabled(
        _direct_child(properties, "pageBreakBefore") if properties is not None else None
    )


def _paragraph_is_list_item(paragraph: ElementTree.Element) -> bool:
    properties = _paragraph_properties(paragraph)
    return properties is not None and _direct_child(properties, "numPr") is not None


def _paragraph_segments(paragraph: ElementTree.Element) -> tuple[list[str], int]:
    """Join adjacent Word runs while retaining explicit pagination markers."""

    segments: list[list[str]] = [[]]
    page_breaks = 0
    for element in paragraph.iter():
        name = _local_name(element.tag)
        if name == "t":
            segments[-1].append(element.text or "")
        elif name == "tab":
            segments[-1].append("\t")
        elif name in {"cr", "br"}:
            if name == "br" and _attribute(element, "type") == "page":
                segments.append([])
                page_breaks += 1
            else:
                segments[-1].append("\n")
        elif name == "lastRenderedPageBreak":
            segments.append([])
            page_breaks += 1
    return [normalize_whitespace("".join(item)) for item in segments], page_breaks


def _table_row(row: ElementTree.Element) -> tuple[str, int]:
    values: list[str] = []
    page_breaks = 0
    for cell in _direct_children(row, "tc"):
        paragraphs: list[str] = []
        for paragraph in (item for item in cell.iter() if _local_name(item.tag) == "p"):
            segments, breaks = _paragraph_segments(paragraph)
            page_breaks = max(page_breaks, breaks)
            value = " ".join(item for item in segments if item)
            if value:
                paragraphs.append(value)
        values.append(" ".join(paragraphs))
    return " | ".join(values).strip(" |"), page_breaks


def _append_paragraph_blocks(
    blocks: list[_DocxBlock],
    paragraph: ElementTree.Element,
    *,
    style_levels: dict[str, int],
    headings: list[str],
    page: int,
) -> tuple[int, bool]:
    page_marked = False
    if _paragraph_break_before(paragraph):
        page += 1
        page_marked = True
    segments, breaks = _paragraph_segments(paragraph)
    page_marked = page_marked or breaks > 0
    heading_level = _paragraph_heading_level(paragraph, style_levels)
    block_type = "list_item" if _paragraph_is_list_item(paragraph) else "paragraph"
    heading_recorded = False
    for index, text in enumerate(segments):
        if index:
            page += 1
        if not text:
            continue
        if heading_level is not None and not heading_recorded:
            del headings[max(0, heading_level - 1) :]
            while len(headings) < heading_level - 1:
                headings.append("")
            headings.append(text)
            section_path = tuple(item for item in headings if item)
            blocks.append(_DocxBlock(text, "heading", section_path, page))
            heading_recorded = True
        else:
            blocks.append(
                _DocxBlock(text, block_type, tuple(item for item in headings if item), page)
            )
    return page, page_marked


def parse_docx(
    document_root: ElementTree.Element,
    styles_root: ElementTree.Element | None = None,
) -> list[Chunk]:
    """Extract paragraph, heading, list, table-row, and explicit page evidence."""

    style_levels = _style_heading_levels(styles_root)
    body = next(
        (item for item in document_root.iter() if _local_name(item.tag) == "body"),
        None,
    )
    if body is None:
        return []
    blocks: list[_DocxBlock] = []
    headings: list[str] = []
    page = 1
    has_explicit_pages = False
    for child in body:
        name = _local_name(child.tag)
        if name == "p":
            page, marked = _append_paragraph_blocks(
                blocks,
                child,
                style_levels=style_levels,
                headings=headings,
                page=page,
            )
            has_explicit_pages = has_explicit_pages or marked
        elif name == "tbl":
            for row in _direct_children(child, "tr"):
                text, breaks = _table_row(row)
                if text:
                    blocks.append(
                        _DocxBlock(
                            text,
                            "table_row",
                            tuple(item for item in headings if item),
                            page,
                        )
                    )
                if breaks:
                    page += breaks
                    has_explicit_pages = True

    chunks: list[Chunk] = []
    cursor = 0
    for block in blocks:
        text = normalize_whitespace(block.text)
        if not text:
            continue
        for item in chunk_text(
            text,
            chunk_size=1200,
            overlap=200,
            page=block.page if has_explicit_pages else None,
        ):
            chunks.append(
                Chunk(
                    text=item.text,
                    start_offset=cursor + item.start_offset,
                    end_offset=cursor + item.end_offset,
                    page=item.page,
                    meta={
                        "section_path": list(block.section_path),
                        "block_type": block.block_type,
                    },
                )
            )
        cursor += len(text) + 2
    return chunks


__all__ = ["parse_docx"]
