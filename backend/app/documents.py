"""Papers HerEvo writes for her: applications and letters filled from the conversation, with her name, address
and phone number always left blank for her to write by hand (HerEvo never asks for or stores them)."""
from __future__ import annotations

from collections import defaultdict

from . import db

BLANK = "______________"
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
INTRO = {
    "bn": "আপনার কাগজটি নিচে লিখে দিয়েছি। নাম, ঠিকানা ও স্বাক্ষরের ঘর নিজে পূরণ করে ছাপিয়ে বা দেখিয়ে নিয়ে যান।",
    "en": "I have written your paper below. Fill in your name, address and signature by hand, then print it or show it at the office.",
}


def _fill(template: str, fields: dict[str, str]) -> str:
    values = defaultdict(lambda: BLANK, {k: v for k, v in fields.items() if v and v.strip()})
    return template.format_map(values).strip()


def _income_fields(uid: str, lang: str) -> dict[str, str]:
    rows = db.ledger(uid)
    if not rows:
        return {"period": BLANK, "count": "0", "total": "0", "lines": "—"}
    cur = rows[0]["currency"]
    total = sum(float(r["amount"]) for r in rows)
    lines = [f"• {r['created_at']:%d %b %Y}: {r['product'] or '—'}, {float(r['amount']):,.0f} {r['currency']}" for r in rows]
    out = {"period": f"{rows[0]['created_at']:%d %b %Y} – {rows[-1]['created_at']:%d %b %Y}", "count": str(len(rows)),
           "total": f"{total:,.0f} {cur}", "lines": "\n".join(lines)}
    if lang == "bn":
        out = {k: v.translate(BN_DIGITS) for k, v in out.items()}
    return out


def render(pack: dict, doc_type: str, fields: dict[str, str], lang: str, uid: str | None = None) -> dict | None:
    """{type, title, text, title_en, text_en} in her language and English, or None if the pack has no such paper."""
    tpl = (pack.get("documents") or {}).get(doc_type)
    if not tpl:
        return None
    local = lang if lang in tpl else "en"
    f_local, f_en = dict(fields), dict(fields)
    if doc_type == "income_statement" and uid:
        f_local, f_en = _income_fields(uid, local), _income_fields(uid, "en")
    return {"type": doc_type, "title": tpl["title"].get(local) or tpl["title"]["en"], "text": _fill(tpl[local], f_local),
            "title_en": tpl["title"]["en"], "text_en": _fill(tpl["en"], f_en), "language": local}
