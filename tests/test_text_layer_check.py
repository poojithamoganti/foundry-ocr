"""Self-check for the layer-1 good/bad text heuristic. Run: python tests/test_text_layer_check.py

Builds minimal raw PDF bytes by hand rather than via a PDF library, since
pypdfium2 is read-oriented and this only needs a valid one-page PDF to read back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import text_layer_check


def _make_pdf(content_stream: str) -> bytes:
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        "/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content_stream)} >>\nstream\n{content_stream}\nendstream",
    ]
    body, offsets = "", []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(f"%PDF-1.4\n") + len(body))
        body += f"{i} 0 obj\n{obj}\nendobj\n"

    header = "%PDF-1.4\n"
    xref_start = len(header) + len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    xref += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"

    return (header + body + xref + trailer).encode("latin-1")


def test_good_text_layer_passes():
    pdf = _make_pdf("BT /F1 12 Tf 72 720 Td (This is a normal digitally-authored page with plenty of text.) Tj ET")
    result = text_layer_check.check(pdf)
    assert result.is_good_quality, result
    assert result.char_count > 0


def test_scanned_pdf_with_no_text_fails():
    pdf = _make_pdf("")  # empty content stream, e.g. an image-only scanned page
    result = text_layer_check.check(pdf)
    assert not result.is_good_quality, result
    assert result.char_count == 0
    assert result.spans == []


def test_spans_carry_normalized_bounding_boxes():
    pdf = _make_pdf("BT /F1 12 Tf 72 720 Td (Hello World) Tj ET")
    result = text_layer_check.check(pdf)
    assert len(result.spans) == 1, result.spans
    span = result.spans[0]
    assert span.page == 0
    assert span.text == "Hello World"
    assert len(span.bbox) == 4
    assert all(0.0 <= coord <= 1.0 for coord in span.bbox), span.bbox
    x0, y0, x1, y1 = span.bbox
    assert x0 < x1 and y0 < y1, span.bbox


if __name__ == "__main__":
    test_good_text_layer_passes()
    test_scanned_pdf_with_no_text_fails()
    test_spans_carry_normalized_bounding_boxes()
    print("ok")
