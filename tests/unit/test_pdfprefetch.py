"""The byte ranges pdf.js needs to open a PDF and show its first pages, computed on the server so the browser can
fetch them in one round trip instead of one per xref section / object (a 1192-page book saved incrementally 32
times took ~45 serial range requests at ~1 s RTT each)."""
import re
import shutil

import pymupdf
import pytest

from aidoc.pdfprefetch import open_ranges

CHUNK = 1024                  # small, so the fixtures span many chunks


def incremental_pdf(src, dst, saves=6):
    shutil.copy(src, dst)
    for i in range(saves):
        d = pymupdf.open(dst)
        d.set_metadata({"title": f"rev {i}" + "x" * 3000})
        d.saveIncr()
        d.close()
    return dst


def xref_chain(path):
    """Every xref section offset by following startxref and /Prev (classic tables or xref streams)."""
    data = path.read_bytes()
    pos = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    out = []
    while pos and pos not in out:
        out.append(pos)
        m = re.search(rb"/Prev\s+(\d+)", data[pos:pos + 200000])
        pos = int(m.group(1)) if m else 0
    return out


def covered(ranges, offset):
    return any(b <= offset < e for b, e in ranges)


def obj_offset(path, xref):
    return list(re.finditer(rb"(?<![0-9])%d 0 obj" % xref, path.read_bytes()))[-1].start()


def check_shape(ranges, size, chunk):
    assert ranges and ranges == sorted(ranges)
    for (b, e), nxt in zip(ranges, ranges[1:] + [(None, None)]):
        assert b % chunk == 0 and (e % chunk == 0 or e == size) and b < e <= size
        assert nxt[0] is None or e < nxt[0]                       # merged: no touching or overlapping ranges
    assert ranges[0][0] == 0 and ranges[-1][1] == size            # the header chunk and the trailer chunk


def test_every_xref_section_of_an_incremental_pdf_is_covered(tmp_path, fixtures):
    pdf = incremental_pdf(fixtures / "big.pdf", tmp_path / "inc.pdf")
    chain = xref_chain(pdf)
    assert len(chain) >= 5
    size = pdf.stat().st_size
    ranges = open_ranges(pdf, CHUNK)
    check_shape(ranges, size, CHUNK)
    for off in chain:
        assert covered(ranges, off), off


def test_first_pages_objects_are_covered(tmp_path, fixtures):
    pdf = incremental_pdf(fixtures / "big.pdf", tmp_path / "inc.pdf", saves=1)
    ranges = open_ranges(pdf, CHUNK, pages=2)
    d = pymupdf.open(pdf)
    want = [d.pdf_catalog(), int(d.xref_get_key(d.pdf_catalog(), "Pages")[1].split()[0])]
    for n in range(3):                        # page dicts of pages 1-3 (their sizes), contents of pages 1-2
        want.append(d[n].xref)
    for n in range(2):
        want += d[n].get_contents()
    for x in want:
        assert covered(ranges, obj_offset(pdf, x)), x


def test_xref_stream_pdf(tmp_path, fixtures):
    d = pymupdf.open(fixtures / "big.pdf")
    out = tmp_path / "objstm.pdf"
    d.save(out, use_objstms=True)
    size = out.stat().st_size
    ranges = open_ranges(out, CHUNK)
    check_shape(ranges, size, CHUNK)
    for off in xref_chain(out):
        assert covered(ranges, off)


def test_total_is_capped(tmp_path, fixtures):
    pdf = incremental_pdf(fixtures / "big.pdf", tmp_path / "inc.pdf")
    ranges = open_ranges(pdf, CHUNK, max_bytes=2 * CHUNK)
    assert sum(e - b for b, e in ranges) <= 2 * CHUNK and ranges[0][0] == 0


@pytest.mark.parametrize("content", [b"", b"not a pdf at all" * 100, b"%PDF-1.4\n garbage without xref"])
def test_unparseable_files_still_get_head_and_tail(tmp_path, content):
    f = tmp_path / "x.pdf"
    f.write_bytes(content)
    ranges = open_ranges(f, CHUNK)
    if not content:
        assert ranges == []
    else:
        assert ranges == [(0, len(content))]
