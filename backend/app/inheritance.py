"""Inheritance shares under Muslim personal law as applied in Bangladesh (Hanafi), for the common family
cases HerEvo meets: a spouse, sons, daughters, father and mother. Exact fractions, no floating point.

The AI never does this arithmetic: it only collects the facts (who survived) and HerEvo computes here.
Anything outside these cases (grandchildren through a son who died earlier, brothers and sisters inheriting,
grandparents, non-Muslim families) returns `None` with a reason, and the answer sends her to legal aid.

Rules used
  - Wife or wives together: 1/8 if the deceased left children, else 1/4. Husband: 1/4 with children, else 1/2.
  - With children: mother 1/6, father 1/6.
  - Sons and daughters: the remainder, a son taking twice a daughter's part.
  - Daughters without sons: one 1/2, two or more 2/3 together; the father also takes what is left.
  - No children, father alive: mother 1/3 (1/6 if the deceased had two or more brothers or sisters), but only
    a third of what is left after the spouse's share (Umariyyatain); the father takes the rest.
  - Shares over the whole are reduced in proportion ('awl); a remainder with no one to take it returns to the
    blood relatives in proportion to their shares (radd), never to the spouse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction as F

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


@dataclass
class Family:
    deceased: str                 # "husband" | "wife" | "father" | "mother": who died, as she would say it
    wives: int = 0                # surviving wives (if a man died)
    husband: bool = False         # surviving husband (if a woman died)
    sons: int = 0
    daughters: int = 0
    father: bool = False          # the deceased's father is alive
    mother: bool = False          # the deceased's mother is alive
    siblings: int | None = None   # the deceased's brothers and sisters; only needed with no children
    land: float | None = None     # total land left, in `unit`
    unit: str | None = None


@dataclass
class Share:
    heir: str        # "wife", "son", ...
    count: int       # how many people share this line
    each: F          # fraction of the whole estate for each person
    land_each: float | None = None


@dataclass
class Result:
    shares: list[Share] = field(default_factory=list)
    note: str = ""


def compute(f: Family) -> tuple[Result | None, str]:
    """Returns (result, "") or (None, reason) when the case needs a lawyer."""
    male_deceased = f.deceased in ("husband", "father", "son", "brother", "man")
    if male_deceased and f.husband:
        return None, "inconsistent: a man who died cannot leave a husband"
    if not male_deceased and f.wives:
        return None, "inconsistent: a woman who died cannot leave wives"
    if f.wives > 4 or min(f.sons, f.daughters, f.wives) < 0:
        return None, "unusual family: ask legal aid"
    children = f.sons + f.daughters > 0
    if not children and not f.father:
        return None, "no children and no father: brothers, sisters or other relatives may inherit"
    if not children and f.mother and f.siblings is None:
        return None, "need: how many brothers and sisters the deceased had"

    shares: dict[str, F] = {}            # fixed shares (total for each line)
    spouse = "wife" if f.wives else ("husband" if f.husband else None)
    if spouse == "wife":
        shares["wife"] = F(1, 8) if children else F(1, 4)
    elif spouse == "husband":
        shares["husband"] = F(1, 4) if children else F(1, 2)

    residue_to: str | None = None        # who takes what is left
    if children:
        if f.mother:
            shares["mother"] = F(1, 6)
        if f.father:
            shares["father"] = F(1, 6)
        if f.sons:
            residue_to = "children"
        else:
            shares["daughter"] = F(1, 2) if f.daughters == 1 else F(2, 3)
            residue_to = "father" if f.father else None
    else:  # no children, father alive
        if f.mother:
            third = F(1, 6) if (f.siblings or 0) >= 2 else F(1, 3)
            left_after_spouse = 1 - shares.get(spouse, F(0)) if spouse else F(1)
            shares["mother"] = third * left_after_spouse if (spouse and third == F(1, 3)) else third
        residue_to = "father"

    total = sum(shares.values(), F(0))
    if total > 1:  # 'awl: everyone's share shrinks in proportion
        shares = {k: v / total for k, v in shares.items()}
        total = F(1)
    rest = 1 - total

    lines: dict[str, F] = dict(shares)
    if rest and residue_to == "children":
        units = 2 * f.sons + f.daughters
        lines["son"] = rest * 2 * f.sons / units
        if f.daughters:
            lines["daughter"] = rest * f.daughters / units
    elif rest and residue_to == "father":
        lines["father"] = lines.get("father", F(0)) + rest
    elif rest:  # radd: back to blood relatives with fixed shares, in proportion, never the spouse
        blood = {k: v for k, v in shares.items() if k not in ("wife", "husband")}
        btotal = sum(blood.values(), F(0))
        if not btotal:
            return None, "remainder with no heir to take it: ask legal aid"
        for k, v in blood.items():
            lines[k] = v + rest * v / btotal

    counts = {"wife": f.wives, "husband": 1, "son": f.sons, "daughter": f.daughters, "father": 1, "mother": 1}
    order = ["wife", "husband", "son", "daughter", "mother", "father"]
    out = Result()
    for heir in order:
        if heir in lines and lines[heir]:
            n = counts[heir] or 1
            each = lines[heir] / n
            out.shares.append(Share(heir, n, each, round(each * f.land, 2) if f.land else None))
    assert sum((s.each * s.count for s in out.shares), F(0)) == 1, "shares must add up to the whole estate"
    return out, ""


NAMES = {
    "en": {"wife": ("wife", "wives"), "husband": ("husband", "husband"), "son": ("son", "sons"),
           "daughter": ("daughter", "daughters"), "mother": ("mother", "mother"), "father": ("father", "father")},
    "bn": {"wife": ("স্ত্রী", "স্ত্রী"), "husband": ("স্বামী", "স্বামী"), "son": ("ছেলে", "ছেলে"),
           "daughter": ("মেয়ে", "মেয়ে"), "mother": ("মা", "মা"), "father": ("বাবা", "বাবা")},
}
DISCLAIMER = {
    "en": ("This is the general calculation under Muslim law as applied in Bangladesh. Check it with free legal aid "
           "(16430) before acting. Hindu, Christian and Buddhist families follow different rules."),
    "bn": ("এটি বাংলাদেশে প্রযোজ্য মুসলিম আইনের সাধারণ হিসাব। কোনো পদক্ষেপ নেওয়ার আগে বিনামূল্যে আইনি সহায়তা (১৬৪৩০) "
           "থেকে মিলিয়ে নিন। হিন্দু, খ্রিষ্টান ও বৌদ্ধ পরিবারের নিয়ম আলাদা।"),
}


def describe(result: Result, lang: str, unit: str | None = None) -> str:
    """Plain lines she can read or hear, e.g. 'Each son: 7/20 (35%) — 35 decimals'."""
    lang = lang if lang in NAMES else "en"
    names = NAMES[lang]
    lines = []
    for s in result.shares:
        one, many = names[s.heir]
        pct = f"{float(s.each) * 100:.1f}".rstrip("0").rstrip(".")
        frac = f"{s.each.numerator}/{s.each.denominator}"
        land = ""
        if s.land_each is not None:
            amount = f"{s.land_each:g}"
            land = f" = {amount} {unit or ''}".rstrip()
        if lang == "bn":
            who = f"প্রত্যেক {one}" if s.count > 1 else one
            line = f"{who}: {frac} ({pct}%){land}".translate(BN_DIGITS)
        else:
            who = f"Each {one}" if s.count > 1 else one.capitalize()
            line = f"{who}: {frac} ({pct}%){land}"
        lines.append("• " + line)
    head = "সম্পত্তির ভাগ:" if lang == "bn" else "Shares of the estate:"
    return head + "\n" + "\n".join(lines) + "\n\n" + DISCLAIMER[lang]
