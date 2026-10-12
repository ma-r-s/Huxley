"""The fit coupon must print as one clean solid and bracket the fits it tunes."""

from __future__ import annotations

from huxcase import coupon, fits


def test_coupon_builds_as_single_valid_solids():
    plate, peg = coupon.plate(), coupon.peg()
    assert plate.is_valid and len(plate.solids()) == 1
    assert peg.is_valid and len(peg.solids()) == 1


def test_coupon_ladder_brackets_the_fits_it_calibrates():
    lo, hi = min(coupon.LADDER), max(coupon.LADDER)
    for value in (fits.SNUG, fits.SLIDE, fits.SLIDE_HBORE):
        assert lo <= value <= hi


def test_coupon_horizontal_bores_keep_a_printable_roof():
    roof = coupon.PLATE[2] - coupon.HBORE_Z - (coupon.PEG_D / 2 + max(coupon.LADDER))
    assert roof >= fits.MIN_WALL


def test_pin_block_brackets_the_lever_pin_fit():
    from huxcase.concepts import hx1

    seat = round((hx1.PIN_BORE - hx1.PIN_D) / 2, 6)
    assert min(coupon.PIN_LADDER) <= seat <= max(coupon.PIN_LADDER)
    block = coupon.pin_block()
    assert block.is_valid and len(block.solids()) == 1
    assert coupon.pin().is_valid
