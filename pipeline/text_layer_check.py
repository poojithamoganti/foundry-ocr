"""Layer 1: use the PDF's own embedded text layer when it's good enough, skipping
OCR entirely. pypdfium2 gives both the text and, per contiguous run, a bounding
box — the only real work here is the good/bad quality heuristic and normalizing
those boxes to fractional [0,1] coordinates (top-left origin, y-down) so they line
up with the bboxes paddle_service.py produces from rendered page images.
"""
import string
from dataclasses import dataclass

import pypdfium2 as pdfium

from . import config

_PRINTABLE = set(string.printable)


@dataclass
class Span:
    page: int
    text: str
    bbox: list  # [x0, y0, x1, y1], normalized to [0, 1], top-left origin


@dataclass
class TextLayerResult:
    text: str
    is_good_quality: bool
    char_count: int
    printable_ratio: float
    spans: list  # list[Span]


def _normalize_rect(rect: tuple, page_width: float, page_height: float) -> list:
    left, bottom, right, top = rect
    return [
        left / page_width,
        (page_height - top) / page_height,
        right / page_width,
        (page_height - bottom) / page_height,
    ]


def check(pdf_bytes: bytes) -> TextLayerResult:
    doc = pdfium.PdfDocument(pdf_bytes)
    pages_text = []
    spans: list[Span] = []

    for page_index, page in enumerate(doc):
        page_width, page_height = page.get_size()
        textpage = page.get_textpage()
        pages_text.append(textpage.get_text_range())

        # ponytail: count_rects()/get_rect() returns one box per contiguous text run
        # (roughly a line), not per word — enough granularity to locate a field's
        # value on the page; split further only if a consumer needs word-level boxes.
        for i in range(textpage.count_rects()):
            rect = textpage.get_rect(i)
            run_text = textpage.get_text_bounded(*rect).strip()
            if run_text:
                spans.append(Span(page=page_index, text=run_text, bbox=_normalize_rect(rect, page_width, page_height)))

        textpage.close()
        page.close()

    num_pages = len(doc)
    doc.close()
    text = "\n".join(pages_text)

    char_count = len(text.strip())
    printable_ratio = (
        sum(1 for c in text if c in _PRINTABLE) / len(text) if text else 0.0
    )
    chars_per_page = char_count / max(num_pages, 1)

    is_good = (
        chars_per_page >= config.MIN_CHARS_PER_PAGE
        and printable_ratio >= config.MIN_PRINTABLE_RATIO
    )
    return TextLayerResult(
        text=text,
        is_good_quality=is_good,
        char_count=char_count,
        printable_ratio=printable_ratio,
        spans=spans,
    )
