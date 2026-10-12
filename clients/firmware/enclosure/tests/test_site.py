"""The build page must show every part of HX-1, each in a step, each named."""

from __future__ import annotations

import pytest
from huxcase import site
from huxcase.concepts import hx1


def test_every_part_is_in_a_build_step():
    names = [p.name for p in hx1.build().parts + site.face_art_parts()]
    assigned = site.assign_steps(names)
    missing = sorted(set(names) - set(assigned) - site.HIDDEN)
    assert missing == [], f"add these to a step in site.STEPS: {missing}"
    assert {s["id"] for s in site.STEPS} == set(assigned.values()), "a step has no parts"
    for name in assigned:
        site._label(name)  # raises StopIteration when a part has no display name


def test_step_copy_has_no_em_dashes():
    for s in site.STEPS:
        assert "—" not in s["title"] + s["text"]


def test_the_build_page_talks_to_any_maker():
    """The page is public (the website's /hx-1/build): no one's name, no one's printer."""
    copy = (site.SITE_SRC / "index.html").read_text()
    copy += "".join(s["title"] + s["text"] for s in site.STEPS)
    for phrase in ("Mario", "your Bambu", "your measured"):
        assert phrase not in copy


def test_the_websites_copy_loads_its_glb_and_links_back():
    page = site._page({"model": "/hx1/hx1.glb", "home": "/hx-1", "x": "</script>"})
    assert "__MANIFEST__" not in page
    assert '"model": "/hx1/hx1.glb"' in page
    assert '</script>"' not in page  # inlined data can't close its own tag
    assert "M.model" in page and "M.home" in page  # and the page reads both
    assert "M.downloads" in page and 'id="downloads"' in page


def test_the_print_files_pack_the_same_every_time(tmp_path):
    """The website keeps the zips in git: an unchanged set must pack to the
    same bytes, each with PRINT.txt, the coupon with the STLs."""
    import zipfile

    src = tmp_path / "out" / "print"
    (src / "projects").mkdir(parents=True)
    (src / "hx1_01_faceplate.stl").write_bytes(b"solid a")
    (src / "PRINT.txt").write_text("how to print")
    (src / "projects" / "1_white.3mf").write_bytes(b"3mf")
    (src.parent / "coupon_plate.stl").write_bytes(b"solid c")
    first = site.pack_print_files(tmp_path / "a", src)
    site.pack_print_files(tmp_path / "b", src)
    for d in first:
        name = d["href"].rsplit("/", 1)[1]
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    names = {
        d["href"]: zipfile.ZipFile(tmp_path / "a" / d["href"].rsplit("/", 1)[1]).namelist()
        for d in first
    }
    assert sorted(names["/hx1/hx1-stl.zip"]) == [
        "PRINT.txt",
        "coupon_plate.stl",
        "hx1_01_faceplate.stl",
    ]
    assert sorted(names["/hx1/hx1-bambu-p2s.zip"]) == ["PRINT.txt", "projects/1_white.3mf"]


def test_the_print_files_must_exist(tmp_path):
    with pytest.raises(SystemExit, match="slice"):
        (tmp_path / "print").mkdir()
        site.pack_print_files(tmp_path / "web", tmp_path / "print")
