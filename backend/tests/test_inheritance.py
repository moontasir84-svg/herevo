"""Known cases of Muslim (Hanafi) inheritance as applied in Bangladesh. Each expected share was worked out by
hand from the rules in app/inheritance.py; the calculator must match them exactly."""
from fractions import Fraction as F

from app.inheritance import Family, compute, describe


def shares(**kw):
    res, why = compute(Family(**kw))
    assert res is not None, why
    return {s.heir: (s.each, s.count) for s in res.shares}


def test_widow_with_two_sons_and_a_daughter():
    s = shares(deceased="husband", wives=1, sons=2, daughters=1)
    assert s == {"wife": (F(1, 8), 1), "son": (F(7, 20), 2), "daughter": (F(7, 40), 1)}


def test_widow_and_one_daughter_remainder_returns_to_the_daughter_not_the_wife():
    s = shares(deceased="husband", wives=1, daughters=1)
    assert s == {"wife": (F(1, 8), 1), "daughter": (F(7, 8), 1)}


def test_shares_over_the_whole_are_reduced_in_proportion():
    s = shares(deceased="husband", wives=1, daughters=2, father=True, mother=True)
    assert s == {"wife": (F(1, 9), 1), "daughter": (F(8, 27), 2), "mother": (F(4, 27), 1), "father": (F(4, 27), 1)}


def test_two_wives_share_one_eighth():
    s = shares(deceased="husband", wives=2, sons=1)
    assert s["wife"] == (F(1, 16), 2) and s["son"] == (F(7, 8), 1)


def test_husband_and_a_son():
    assert shares(deceased="wife", husband=True, sons=1) == {"husband": (F(1, 4), 1), "son": (F(3, 4), 1)}


def test_no_children_wife_and_both_parents():
    s = shares(deceased="husband", wives=1, father=True, mother=True, siblings=0)
    assert s == {"wife": (F(1, 4), 1), "mother": (F(1, 4), 1), "father": (F(1, 2), 1)}


def test_wife_mother_and_daughter_without_father():
    s = shares(deceased="husband", wives=1, mother=True, daughters=1)
    assert s == {"wife": (F(1, 8), 1), "daughter": (F(21, 32), 1), "mother": (F(7, 32), 1)}


def test_daughter_and_father_father_takes_the_rest():
    s = shares(deceased="husband", wives=1, daughters=1, father=True)
    assert s == {"wife": (F(1, 8), 1), "daughter": (F(1, 2), 1), "father": (F(9, 24), 1)}


def test_land_is_divided_too():
    res, _ = compute(Family(deceased="husband", wives=1, sons=2, daughters=1, land=100, unit="decimal"))
    land = {s.heir: s.land_each for s in res.shares}
    assert land == {"wife": 12.5, "son": 35.0, "daughter": 17.5}


def test_cases_that_need_a_lawyer_are_refused():
    assert compute(Family(deceased="husband", wives=1))[0] is None             # siblings may inherit
    assert compute(Family(deceased="husband", wives=1, father=True, mother=True))[0] is None  # siblings unknown
    assert compute(Family(deceased="husband", husband=True, sons=1))[0] is None  # inconsistent


def test_description_in_bangla_and_english():
    res, _ = compute(Family(deceased="husband", wives=1, sons=2, daughters=1, land=100, unit="শতাংশ"))
    bn = describe(res, "bn", "শতাংশ")
    assert "স্ত্রী: ১/৮ (১২.৫%) = ১২.৫ শতাংশ" in bn and "প্রত্যেক ছেলে: ৭/২০ (৩৫%)" in bn and "১৬৪৩০" in bn
    en = describe(res, "en", "decimals")
    assert "Each son: 7/20 (35%) = 35 decimals" in en
