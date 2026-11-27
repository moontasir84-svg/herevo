"""HerEvo's tools for the moments where a woman's rights are actually decided.

  Paper Lens   she photographs a paper; HerEvo reads it aloud, pulls out dates and amounts, and warns her
               before she signs away her rights (a gift deed, a "no claim" paper, blank spaces).
  Rehearsal    she practises the hard conversation (the brother-in-law, the land office clerk, the salish)
               with the AI playing the other side, and a coach beside her after every line.
  Counter      at the real desk, she tells HerEvo what the official just said; it whispers back what is true
               and what to say.
  Witness      her private, sealed diary of what happened, turned into a dated statement she can take to the
               Women Affairs Officer or legal aid.

Every tool has a saved answer for when the AI is slow or down, so the woman (and a live demo) always gets
something correct. The AI may only use the facts written here (FACTS) for laws, fees and numbers.
"""
from __future__ import annotations

import logging
import re

from .llm import LANGUAGE_NAMES, get_llm
from .safety import screen

log = logging.getLogger("herevo.tools")

# ---------------------------------------------------------------------------------------------------------
# Facts the AI may cite. Nothing else: no invented laws, fees or numbers.
FACTS = {
    "BD": """Bangladesh (checked facts; cite only these):
- Inheritance (Muslim law): a wife gets 1/8 of her husband's property if he left children, 1/4 if not. A daughter gets
  half of a son's share. Brothers-in-law get nothing while the deceased has a son.
- Land mutation (namjari) puts heirs' names in the record (khatian). Apply online at land.gov.bd or at the Union
  Digital Centre. Official fee Tk 1,170 in total (application Tk 20, notice Tk 50, record Tk 1,100), paid online
  with a receipt. Usually 28 to 45 days. Land service hotline 16122. Anyone asking for more money is asking for a bribe.
- A heba (gift deed) or sale deed signed and registered gives the land away permanently. A "no claim" or
  relinquishment paper (na-dabi) gives up her share. Never sign a paper you have not had read to you in full.
- Dower (denmohor) written in the kabinnama is her money. The prompt part (mu'ajjal) is owed whenever she asks; the
  deferred part (mu'wajjal) is owed at divorce or the husband's death. Unpaid dower stays owed. Column 18 of the
  kabinnama says whether the husband delegated the right of divorce to her (talaq-e-tafweez).
- Maintenance: the Union Parishad Arbitration Council can hear it (Muslim Family Laws Ordinance 1961). The Family
  Court hears dower and maintenance cases (Family Courts Act 2023), court fee Tk 200.
- Demanding dowry is a crime (Dowry Prohibition Act 2018).
- Domestic Violence (Prevention and Protection) Act 2010: she can apply for a protection order to the Judicial
  Magistrate, directly or through the Upazila Women Affairs Officer.
- Free government legal aid: 16430. Violence against women helpline: 109. Emergency: 999.""",
    "KE": """Kenya (checked facts; cite only these):
- Law of Succession Act (Cap 160): a widow inherits from her husband's estate; where there are children she keeps
  the household and a life interest in the rest. Taking or selling a deceased person's property without a court
  grant is "intermeddling", an offence (section 45).
- Matrimonial Property Act 2013: property bought during the marriage belongs to both spouses.
- The chief and the court cannot give the family home to in-laws without a succession case.
- FIDA Kenya gives women free legal advice. Gender-based violence helpline: 1195 (free). Emergency: 999 or 112.""",
    "MX": """Mexico (checked facts; cite only these):
- Ley Agraria art. 17: an ejidatario names who inherits his rights in a list registered with the RAN (Registro
  Agrario Nacional). Art. 18: with no list, the rights go first to the spouse (then the concubina, then one child).
- The comisariado ejidal and the asamblea cannot hand the parcel to someone else against the law.
- The Procuraduria Agraria gives free advice and represents campesinas. Emergency: 911.""",
}

SAFETY_NUMBERS = {"BD": "999 / 109", "KE": "999 / 1195", "MX": "911"}

def facts_for(country: str) -> str:
    return FACTS.get((country or "BD").upper(), FACTS["BD"])


def lang_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, "English")


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------------------------------------------------
# Paper Lens

LENS_SCHEMA = {
    "type": "object",
    "properties": {
        "is_document": {"type": "boolean", "description": "false if the photo is not a paper or document"},
        "doc_type": {"type": "string", "description": "short kind of paper, in her language, e.g. 'Hearing notice'"},
        "title": {"type": "string", "description": "one line naming the paper and who it is from, her language"},
        "summary": {"type": "string", "description": "2-4 plain sentences in her language: what this paper means for HER"},
        "say": {"type": "string", "description": "what to read aloud to her (under 500 characters), her language"},
        "verdict": {"type": "string", "enum": ["do_not_sign", "check_first", "keep_safe", "unclear"],
                    "description": "do_not_sign: signing gives away a right or money; check_first: needs legal aid "
                                   "before signing; keep_safe: an informational paper to keep; unclear: unreadable"},
        "facts": {"type": "array", "description": "up to 6 key facts read from the paper",
                  "items": {"type": "object", "properties": {
                      "label": {"type": "string", "description": "her language, 1-3 words, e.g. 'Hearing date'"},
                      "value": {"type": "string", "description": "exactly as written, or translated if needed"},
                      "box": {"type": "array", "items": {"type": "integer"},
                              "description": "[ymin, xmin, ymax, xmax] of where it is written, 0-1000 scale"}},
                      "required": ["label", "value"]}},
        "flags": {"type": "array", "description": "warnings and protections, most important first, up to 4",
                  "items": {"type": "object", "properties": {
                      "level": {"type": "string", "enum": ["danger", "warn", "good"]},
                      "text": {"type": "string", "description": "one short sentence in her language"},
                      "box": {"type": "array", "items": {"type": "integer"},
                              "description": "[ymin, xmin, ymax, xmax] of the clause, 0-1000 scale"}},
                      "required": ["level", "text"]}},
        "steps": {"type": "array", "items": {"type": "string"},
                  "description": "up to 4 short next steps in her language, using only the checked facts"},
        "dates": {"type": "array", "description": "dates she must act on (attend, pay, reply), not the issue date",
                  "items": {"type": "object", "properties": {
                      "what": {"type": "string", "description": "her language, e.g. 'Mutation hearing at the land office'"},
                      "date": {"type": "string", "description": "YYYY-MM-DD"},
                      "time": {"type": "string", "description": "HH:MM 24-hour, if written"}},
                      "required": ["what", "date"]}},
    },
    "required": ["is_document", "doc_type", "title", "summary", "say", "verdict", "facts", "flags", "steps"],
}

LENS_SYSTEM = """You are HerEvo's Paper Lens. Many women you help cannot read, or cannot read official language, and \
lose land and money by signing papers others explain to them falsely. You read the paper in the photo FOR her and \
protect her.

Rules:
1. Write every field in {lang}, in short, warm, spoken sentences. Use the paper's own numbers and dates.
2. Read only what is really written. If a part is unreadable, say so. Never invent names, amounts or dates. Label \
each date by what it is: the date the paper was issued is not the date she must attend or pay. Do not list the same \
fact twice.
3. Protect her: flag (level=danger) anything that gives away land, a share, dower or money, a power of attorney, a \
"no claim" or relinquishment, blank spaces to be filled later, or an amount different from the law. Flag deadlines \
and dates to attend (warn). Flag rights the paper gives her (good).
4. verdict: do_not_sign when signing would give away a right; check_first when she should have legal aid read it \
first; keep_safe for notices, certificates and records she should keep; unclear if you cannot read it.
5. For each fact and flag, give `box` = [ymin, xmin, ymax, xmax] on a 0-1000 scale for where it is written on the \
image, when you can see it. Leave it out if unsure.
6. Next steps and any law, fee, office or number must come only from these checked facts:
{facts}
7. If the photo is not a paper, set is_document=false, verdict=unclear, and kindly ask for a clearer photo of the paper."""


def _box(b):
    try:
        b = [int(x) for x in b]
    except (TypeError, ValueError):
        return None
    if len(b) != 4 or not all(0 <= x <= 1000 for x in b) or b[2] <= b[0] or b[3] <= b[1]:
        return None
    return b


def clean_lens(raw: dict) -> dict:
    out = {
        "is_document": bool(raw.get("is_document", True)),
        "doc_type": _clip(raw.get("doc_type"), 60),
        "title": _clip(raw.get("title"), 140),
        "summary": _clip(raw.get("summary"), 700),
        "say": _clip(raw.get("say") or raw.get("summary"), 600),
        "verdict": raw.get("verdict") if raw.get("verdict") in ("do_not_sign", "check_first", "keep_safe", "unclear")
        else "unclear",
        "facts": [], "flags": [], "steps": [],
    }
    for f in (raw.get("facts") or [])[:6]:
        if isinstance(f, dict) and f.get("label") and f.get("value"):
            out["facts"].append({"label": _clip(f["label"], 40), "value": _clip(f["value"], 120), "box": _box(f.get("box"))})
    for f in (raw.get("flags") or [])[:4]:
        if isinstance(f, dict) and f.get("text"):
            lvl = f.get("level") if f.get("level") in ("danger", "warn", "good") else "warn"
            out["flags"].append({"level": lvl, "text": _clip(f["text"], 220), "box": _box(f.get("box"))})
    out["steps"] = [_clip(s, 200) for s in (raw.get("steps") or [])[:4] if str(s).strip()]
    out["dates"] = []
    for d in (raw.get("dates") or [])[:3]:
        if isinstance(d, dict) and re.fullmatch(r"20\d\d-[01]\d-[0-3]\d", str(d.get("date", ""))):
            tm = str(d.get("time") or "")
            out["dates"].append({"what": _clip(d.get("what"), 100), "date": d["date"],
                                 "time": tm if re.fullmatch(r"[0-2]\d:[0-5]\d", tm) else None})
    return out


def read_paper(image_b64: str, mime: str, lang: str, country: str, sample: str | None = None) -> dict:
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        try:
            raw = llm.generate(LENS_SYSTEM.format(lang=lang_name(lang), facts=facts_for(country)),
                               [{"image": image_b64, "mime": mime},
                                {"text": f"Read this paper for her. Answer in {lang_name(lang)}."}],
                               "read_paper", LENS_SCHEMA, seconds=40, temperature=0.2)
            out = clean_lens(raw)
            if out["summary"]:
                return {**out, "source": "ai"}
            reason = "empty answer"
        except Exception as exc:
            reason = f"{type(exc).__name__}"
            log.warning("paper lens AI failed: %s", exc)
    saved = SAMPLE_READINGS.get(sample or "", {}).get(lang if lang == "bn" else "en")
    if saved:
        return {**saved, "source": "saved", "note": reason}
    msg = {"bn": "এখন কাগজটি পড়তে পারছি না। একটু পরে আবার ছবি পাঠান। তার আগে কোনো কাগজে সই করবেন না।",
           "en": "I can't read the paper right now. Please try again in a minute. Don't sign anything before then."}
    m = msg["bn" if lang == "bn" else "en"]
    return {"is_document": True, "doc_type": "", "title": "", "summary": m, "say": m, "verdict": "unclear",
            "facts": [], "flags": [], "steps": [], "source": "unavailable", "note": reason}


# Saved readings of the three sample papers (made-up people, marked SAMPLE), used if the AI is down.
# Boxes match web/samples/*.jpg, measured when the samples were drawn.
SAMPLE_READINGS: dict[str, dict] = {}  # filled from samples.json at the end of this module


# ---------------------------------------------------------------------------------------------------------
# Rehearsal room

SCENARIOS: dict[str, dict] = {
    "inlaw_land": {
        "country": "BD", "tone": "land",
        "title": {"bn": "দেবর জমির ভাগ দিচ্ছে না", "en": "Brother-in-law refuses my land share"},
        "role": {"bn": "করিম, আপনার দেবর", "en": "Karim, your brother-in-law"},
        "setting": {"bn": "উঠানে, পরিবারের সামনে", "en": "In the courtyard, in front of the family"},
        "goal": {"bn": "স্বামীর জমিতে আপনার ন্যায্য ভাগ চাওয়া এবং নামজারির কথা বলা",
                 "en": "Claim your lawful share of your late husband's land and ask for mutation"},
        "opening": {"bn": "ভাবি, ভাইয়ের জমি তো বংশের জমি। মেয়েমানুষ জমি দিয়ে কী করবে? পাঁচ শতাংশ নিয়ে চুপ থাকেন।",
                    "en": "Bhabi, my brother's land is family land. What will a woman do with land? Take five decimals and keep quiet."},
        "brief": "You are Karim, the younger brother of her husband who died last year. He left her, two sons and one "
                 "daughter, and 100 decimals of land. You want to keep the land. Your tactics: custom ('family land', "
                 "'women don't take land'), shame ('people will talk'), offering a small amount (5 decimals), delay "
                 "('after the harvest'), and claiming you paid for the funeral. You soften when she calmly states her "
                 "1/8 share (12.5 decimals), says her children's shares are theirs, mentions mutation at land.gov.bd "
                 "or free legal aid 16430.",
        "fallback": {"bn": ["সবাই কী বলবে? বিধবা হয়ে জমি নিয়ে ঝগড়া করছেন!",
                            "জানাজার খরচ তো আমি দিয়েছি। সেটার হিসাব কে দেবে?",
                            "আচ্ছা, ধান কাটার পরে বসা যাবে। এখন তাড়া কিসের?",
                            "ঠিক আছে ভাবি, আইনে যা আছে তা-ই হবে। কাগজপত্র নিয়ে বসি।"],
                     "en": ["What will people say? A widow fighting over land!",
                            "I paid for the funeral. Who will pay me back for that?",
                            "Fine, we can sit after the harvest. Why the hurry now?",
                            "All right, Bhabi. What the law says will happen. Let's sit with the papers."]},
        "tips": {"bn": ["আইনে স্ত্রী পান আট ভাগের এক ভাগ। ১০০ শতাংশে আমার ভাগ সাড়ে বারো শতাংশ।",
                        "আমার ছেলেমেয়ের ভাগও তাদের। আপনি ভাই হিসেবে কিছু পান না।",
                        "নামজারির আবেদন আমি land.gov.bd-তে করব। ফি ১,১৭০ টাকা।",
                        "রাজি না হলে আমি বিনামূল্যে আইনি সহায়তা ১৬৪৩০-এ যাব।"],
                 "en": ["By law a wife gets one-eighth. Of 100 decimals, my share is twelve and a half.",
                        "My children's shares are theirs. As a brother, you get nothing while he has sons.",
                        "I will apply for mutation at land.gov.bd. The fee is 1,170 taka.",
                        "If you don't agree, I will go to free legal aid, 16430."]},
        "law": {"bn": "স্ত্রী: সন্তান থাকলে ১/৮ · ছেলে থাকলে ভাই কিছু পান না (মুসলিম উত্তরাধিকার আইন)",
                "en": "Wife: 1/8 with children; a brother gets nothing while there is a son (Muslim inheritance law)"},
    },
    "land_office": {
        "country": "BD", "tone": "money",
        "title": {"bn": "ভূমি অফিসে ঘুষ চাইছে", "en": "Land office clerk asks for a bribe"},
        "role": {"bn": "ভূমি অফিসের কেরানি", "en": "Clerk at the land office"},
        "setting": {"bn": "এসি ল্যান্ড অফিসের ডেস্কে", "en": "At the AC Land office desk"},
        "goal": {"bn": "সরকারি ফি জেনে রসিদ চাওয়া, বাড়তি টাকা না দেওয়া",
                 "en": "Pay only the official fee with a receipt, refuse the extra money"},
        "opening": {"bn": "নামজারি? পাঁচ হাজার টাকা লাগবে, নইলে ছয় মাসেও ফাইল নড়বে না।",
                    "en": "Mutation? That will be five thousand taka, or the file won't move for six months."},
        "brief": "You are a clerk at the Assistant Commissioner (Land) office. You ask for Tk 5,000 'speed money' for "
                 "a mutation. Tactics: delay threats, 'everyone pays', 'the online system doesn't work', 'your papers "
                 "are incomplete'. You back down when she says the official fee is Tk 1,170 paid online with a receipt, "
                 "that she can apply at land.gov.bd herself, and that she will call the land hotline 16122.",
        "fallback": {"bn": ["সবাই দেয়, আপা। না দিলে ফাইল পড়ে থাকবে।",
                            "অনলাইনে হয় না, সার্ভার নষ্ট। আমার কাছেই দিতে হবে।",
                            "আপনার কাগজে ভুল আছে। ঠিক করাতে খরচ আছে।",
                            "আচ্ছা আচ্ছা, অনলাইনে জমা দেন, রসিদ দিয়ে দিব।"],
                     "en": ["Everyone pays, sister. If you don't, the file will just sit.",
                            "Online doesn't work, the server is down. You have to pay me.",
                            "Your papers have a mistake. Fixing that costs money.",
                            "All right, all right. Submit online, you'll get a receipt."]},
        "tips": {"bn": ["সরকারি ফি মোট ১,১৭০ টাকা। আমি শুধু রসিদ দিয়ে টাকা দেব।",
                        "আমি land.gov.bd-তে নিজেই আবেদন করব।",
                        "কাগজে কী ভুল, লিখে দিন। আমি ঠিক করে আনব।",
                        "না হলে আমি ভূমি সেবা হটলাইন ১৬১২২-এ জানাব।"],
                 "en": ["The official fee is 1,170 taka in total. I will only pay with a receipt.",
                        "I will apply myself at land.gov.bd.",
                        "Write down what is wrong with my papers. I will fix it.",
                        "Otherwise I will report it to the land service hotline, 16122."]},
        "law": {"bn": "নামজারি ফি মোট ১,১৭০ টাকা, অনলাইনে রসিদসহ · ভূমি সেবা হটলাইন ১৬১২২",
                "en": "Mutation fee Tk 1,170 total, paid online with a receipt · Land hotline 16122"},
    },
    "salish": {
        "country": "BD", "tone": "rights",
        "title": {"bn": "সালিশে খোরপোষ ও দেনমোহর", "en": "Maintenance and dower at the salish"},
        "role": {"bn": "ইউনিয়ন পরিষদের চেয়ারম্যান", "en": "The Union Parishad chairman"},
        "setting": {"bn": "ইউনিয়ন পরিষদের সালিশে, স্বামীর পরিবার উপস্থিত",
                    "en": "At the Union Parishad salish, with your husband's family present"},
        "goal": {"bn": "তাৎক্ষণিক দেনমোহর ২০,০০০ টাকা ও মাসিক খোরপোষ চাওয়া",
                 "en": "Ask for your prompt dower of 20,000 taka and monthly maintenance"},
        "opening": {"bn": "মা, সংসারে ঝগড়া হয়। মানিয়ে নাও। টাকার কথা তুললে সংসার টিকবে না।",
                    "en": "Daughter, every home has quarrels. Adjust. If you talk about money, the marriage won't last."},
        "brief": "You are the Union Parishad chairman leading an informal salish. Her husband stopped giving her and "
                 "their child money four months ago; the kabinnama shows dower Tk 50,000 (Tk 20,000 prompt, "
                 "Tk 30,000 deferred), none paid. Your tactics: "
                 "'adjust', protecting family honour, suggesting a small one-time payment, saying dower is only paid "
                 "at divorce. You become fair when she says the prompt dower of Tk 20,000 is owed when she asks, asks for "
                 "maintenance through the Arbitration Council, or says she can go to the Family Court.",
        "fallback": {"bn": ["দেনমোহর তো তালাক হলে দেয়। এখন চাওয়ার কী আছে?",
                            "ঠিক আছে, ও এককালীন দুই হাজার টাকা দিক। মিটে গেল।",
                            "তুমি কি সংসার ভাঙতে চাও?",
                            "ঠিক আছে, সালিশি পরিষদে লিখিতভাবে খোরপোষের হিসাব হবে।"],
                     "en": ["Dower is paid at divorce. Why ask now?",
                            "Fine, let him pay two thousand taka once. Settled.",
                            "Do you want to break the marriage?",
                            "All right. The Arbitration Council will settle maintenance in writing."]},
        "tips": {"bn": ["কাবিননামায় দেনমোহর ৫০,০০০ টাকা, তার মধ্যে মু'আজ্জল ২০,০০০ টাকা চাইলেই পাওনা।",
                        "চার মাস খোরপোষ দেয়নি। আমি মাসিক খোরপোষ লিখিতভাবে চাই।",
                        "আমি সংসার ভাঙতে আসিনি, আমার পাওনা চাইতে এসেছি।",
                        "এখানে না হলে আমি পারিবারিক আদালতে যাব, ফি মাত্র ২০০ টাকা।"],
                 "en": ["My kabinnama says dower 50,000 taka, and the prompt part, 20,000, is owed when I ask.",
                        "He has not paid maintenance for four months. I want monthly maintenance in writing.",
                        "I did not come to break my marriage. I came for what I am owed.",
                        "If not here, I will go to the Family Court. The fee is only 200 taka."]},
        "law": {"bn": "মু'আজ্জল দেনমোহর চাইলেই পাওনা · সালিশি পরিষদ (মুসলিম পারিবারিক আইন ১৯৬১) · পারিবারিক আদালত ফি ২০০ টাকা",
                "en": "Prompt dower is owed on demand · Arbitration Council (Muslim Family Laws Ordinance 1961) · Family Court fee Tk 200"},
    },
    "dowry": {
        "country": "BD", "tone": "safety",
        "title": {"bn": "শাশুড়ি যৌতুক চাইছেন", "en": "Mother-in-law demands dowry"},
        "role": {"bn": "আপনার শাশুড়ি", "en": "Your mother-in-law"},
        "setting": {"bn": "শ্বশুরবাড়িতে, রান্নাঘরে", "en": "At your in-laws' house, in the kitchen"},
        "goal": {"bn": "শান্তভাবে না বলা, আইন জানানো, নিজের নিরাপত্তা ঠিক রাখা",
                 "en": "Say no calmly, know the law, and keep yourself safe"},
        "opening": {"bn": "তোমার বাপের বাড়ি থেকে একটা মোটরসাইকেল আনতে হবে। না আনলে এখানে থাকা কঠিন হবে।",
                    "en": "You must bring a motorcycle from your father's house. If not, living here will be hard."},
        "brief": "You are her mother-in-law demanding a motorcycle as dowry. Tactics: 'it is custom', comparing her "
                 "to other brides, pressure through her husband. Never threaten physical violence in this practice. "
                 "You retreat when she calmly refuses, says demanding dowry is a crime under the Dowry Prohibition Act "
                 "2018, and that she knows the helpline 109.",
        "fallback": {"bn": ["সবাই তো দেয়। পাশের বাড়ির বউ টিভি এনেছে।",
                            "তোমার স্বামীও তো চায়। তুমি কি ওর কথা শুনবে না?",
                            "এসব আইন-কানুন আমাদের ঘরে চলে না।",
                            "আচ্ছা, থাক। এ নিয়ে আর কথা বলব না।"],
                     "en": ["Everyone gives. The bride next door brought a TV.",
                            "Your husband wants it too. Won't you listen to him?",
                            "Those laws don't apply in our house.",
                            "Fine, leave it. We won't talk about it again."]},
        "tips": {"bn": ["আমি যৌতুক আনব না। যৌতুক চাওয়া আইনত অপরাধ।",
                        "যৌতুক নিরোধ আইন ২০১৮ অনুযায়ী যৌতুক চাওয়া দণ্ডনীয়।",
                        "আমার কোনো ক্ষতি হলে আমি ১০৯ নম্বরে জানাব।",
                        "চলুন, আমার বাবা-মাকে সঙ্গে নিয়ে সবাই শান্তভাবে কথা বলি।"],
                 "en": ["I will not bring dowry. Demanding dowry is a crime.",
                        "Under the Dowry Prohibition Act 2018, demanding dowry is punishable.",
                        "If I am harmed, I will call 109.",
                        "Let's all talk calmly, with my parents present."]},
        "law": {"bn": "যৌতুক চাওয়া অপরাধ (যৌতুক নিরোধ আইন ২০১৮) · নারী নির্যাতন হেল্পলাইন ১০৯ · জরুরি ৯৯৯",
                "en": "Demanding dowry is a crime (Dowry Prohibition Act 2018) · Helpline 109 · Emergency 999"},
    },
    "ke_home": {
        "country": "KE", "tone": "land",
        "title": {"bn": "কেনিয়া: শ্বশুরপক্ষ বাড়ি দখল করতে চায়", "en": "Kenya: in-laws want to take the family home"},
        "role": {"bn": "আপনার ভাসুর, জোসেফ", "en": "Joseph, your husband's elder brother"},
        "setting": {"bn": "আপনার বাড়ির সামনে, গ্রামের চিফ উপস্থিত", "en": "Outside your home, the village chief present"},
        "goal": {"bn": "বাড়িতে থাকার অধিকার বলা, বিক্রি বন্ধ করা", "en": "Keep your home and stop the sale"},
        "opening": {"bn": "আমার ভাই মারা গেছে, এই জমি এখন বংশের। তুমি তোমার বাপের বাড়ি ফিরে যাও।",
                    "en": "My brother is dead. This land belongs to the clan now. Go back to your father's home."},
        "brief": "You are Joseph, the elder brother of her late husband in rural Kenya. You want to sell the land. "
                 "Tactics: clan custom, the chief's authority, saying she has no title deed. You back down when she says "
                 "the Law of Succession Act protects a widow, that selling a dead person's property without a court "
                 "grant is intermeddling (an offence), and that she will go to FIDA Kenya.",
        "fallback": {"en": ["The chief agrees with me. Custom is custom.", "Your name is not on the title deed.",
                            "We already have a buyer.", "Fine. We will wait for the succession case."]},
        "tips": {"en": ["The Law of Succession Act protects me as his widow.",
                        "Selling his property without a court grant is intermeddling. That is an offence.",
                        "We bought this home together. The Matrimonial Property Act says it is mine too.",
                        "I am going to FIDA Kenya for free legal advice."]},
        "law": {"en": "Law of Succession Act (Cap 160), s.45 intermeddling · Matrimonial Property Act 2013 · FIDA Kenya"},
    },
    "mx_ejido": {
        "country": "MX", "tone": "land",
        "title": {"bn": "মেক্সিকো: এহিদোর জমি ছেলেকে দিতে চায়", "en": "Mexico: the ejido wants to give my parcel to my son"},
        "role": {"bn": "কমিসারিয়াদো এহিদাল", "en": "The comisariado ejidal"},
        "setting": {"bn": "এহিদোর সভায়", "en": "At the ejido assembly"},
        "goal": {"bn": "স্বামীর এহিদো অধিকার নিজের নামে চাওয়া", "en": "Claim your late husband's ejido rights"},
        "opening": {"bn": "ম্যাডাম, জমি পুরুষের হাতে থাকাই নিয়ম। অধিকার আপনার ছেলের নামে দিচ্ছি।",
                    "en": "Señora, land stays with the men. We will put the rights in your son's name."},
        "brief": "You are the comisariado ejidal. Her husband, an ejidatario, died without a registered successor list. "
                 "You want the rights to go to her son. You back down when she cites Ley Agraria art. 18 (with no list, "
                 "the spouse inherits first) and says the Procuraduria Agraria will advise her.",
        "fallback": {"en": ["That's how we have always done it here.", "Your son can work the land better.",
                            "The assembly has already decided.", "Very well. We will check the law with the Procuraduría."]},
        "tips": {"en": ["Ley Agraria article 18: with no list, the rights go first to the spouse.",
                        "Did my husband register a successor list with the RAN? If not, I inherit.",
                        "The assembly cannot decide against the law.",
                        "I will ask the Procuraduría Agraria for free advice."]},
        "law": {"en": "Ley Agraria art. 17-18: with no successor list, the spouse inherits first · Procuraduría Agraria"},
    },
}


def scenario_list(lang: str) -> list[dict]:
    out = []
    for sid, s in SCENARIOS.items():
        pick = lambda d: d.get(lang) or d.get("en") or ""  # noqa: E731
        out.append({"id": sid, "country": s["country"], "tone": s["tone"], "title": pick(s["title"]),
                    "role": pick(s["role"]), "setting": pick(s["setting"]), "goal": pick(s["goal"]),
                    "opening": pick(s["opening"]), "law": pick(s["law"])})
    return out


REHEARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "the character's next line, in her language, 1-2 short spoken sentences"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "how clear, calm and firm her LAST line was, and whether it used the right fact"},
        "good": {"type": "string", "description": "one short sentence, her language: what she did well"},
        "try_saying": {"type": "string", "description": "one exact sentence she could say next, her language, first person"},
        "law": {"type": "string", "description": "one short checked fact that helps her now, with its source, her language"},
        "done": {"type": "boolean", "description": "true when the character has agreed to her goal or the next step"},
        "real_danger": {"type": "boolean", "description": "true only if she says she is really in danger now, not in the role-play"},
        "remember": {"type": "array", "items": {"type": "string"},
                     "description": "when done: 3 short sentences in her language to remember for the real day"},
    },
    "required": ["reply", "score", "good", "try_saying", "law", "done", "real_danger"],
}

REHEARSE_SYSTEM = """You run HerEvo's rehearsal room. A woman is practising a hard real-life conversation so that on \
the real day she is calm, firm and knows the law. You do two jobs in every turn.

1. PLAY the other person, in {lang}: {brief}
   Stay in character. Push back realistically with the tactics listed, one at a time, in 1-2 short spoken \
sentences. Become more reasonable each time she makes a calm, firm, correct point. After she has made about three \
strong points, agree to her goal or the next step and set done=true. Never use slurs, never threaten violence, \
never mention that you are an AI.

2. COACH her, in {lang}: score her LAST line (0-100) for clarity, calm, firmness and using the right fact. In `good` \
say briefly what worked (or, gently, what to fix). In `try_saying` give one exact first-person sentence she could \
say next. In `law` give one short checked fact with its source.

Laws, fees, numbers and offices may come only from these checked facts:
{facts}
{context}
If she says she is really in danger now (outside the practice), set real_danger=true and in `good` tell her to call \
{sos} or get to safety. When done=true, fill `remember` with 3 short sentences to remember for the real day."""


def rehearse(scenario: str, history: list[dict], said: str, lang: str, context: str | None = None,
             finish: bool = False) -> dict:
    s = SCENARIOS.get(scenario)
    if not s:
        raise KeyError(scenario)
    turn = sum(1 for h in history if h.get("who") == "her")  # lines she said before this one
    danger = screen(said)
    if danger.escalate and danger.kind == "danger":
        sos = SAFETY_NUMBERS.get(s["country"], "999")
        msg = {"bn": f"এটা কি সত্যিই এখন ঘটছে? আপনি বিপদে থাকলে এখনই {sos} নম্বরে ফোন করুন বা নিরাপদ জায়গায় যান। "
                     "উপরের লাল বোতাম চাপলে একজন সহায়তাকর্মী পাবেন।",
               "en": f"Is this really happening now? If you are in danger, call {sos} now or get to a safe place. "
                     "The red button at the top reaches a trained helper."}
        m = msg["bn" if lang == "bn" else "en"]
        return {"reply": "", "score": 0, "good": m, "try_saying": "", "law": "", "done": False, "real_danger": True,
                "remember": [], "source": "safety"}
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        convo = "\n".join(f"{'HER' if h.get('who') == 'her' else 'YOU'}: {_clip(h.get('text'), 400)}"
                          for h in history[-12:])
        ask = (f"Conversation so far (YOU = the character):\nYOU: {s['opening'].get(lang) or s['opening']['en']}\n"
               f"{convo}\nHER (her last line, score this one): {said}\n")
        if finish:
            ask += "She wants to finish now: set done=true and fill `remember`.\n"
        try:
            raw = llm.generate(REHEARSE_SYSTEM.format(lang=lang_name(lang), brief=s["brief"],
                                                      facts=facts_for(s["country"]),
                                                      context=f"About her case: {context}\n" if context else "",
                                                      sos=SAFETY_NUMBERS.get(s["country"], "999")),
                               [{"text": ask}], "rehearse", REHEARSE_SCHEMA, seconds=25, temperature=0.7)
            if raw.get("reply") or raw.get("done"):
                score = raw.get("score")
                out = {"reply": _clip(raw.get("reply"), 300),
                       "score": max(0, min(100, int(score))) if isinstance(score, (int, float)) else 50,
                       "good": _clip(raw.get("good"), 240), "try_saying": _clip(raw.get("try_saying"), 240),
                       "law": _clip(raw.get("law"), 240), "done": bool(raw.get("done")) or finish,
                       "real_danger": bool(raw.get("real_danger")),
                       "remember": [_clip(x, 200) for x in (raw.get("remember") or [])[:3]], "source": "ai"}
                if out["done"] and not out["remember"]:
                    out["remember"] = _saved_remember(s, lang)
                return out
            reason = "empty answer"
        except Exception as exc:
            reason = type(exc).__name__
            log.warning("rehearsal AI failed: %s", exc)
    return _saved_turn(s, lang, turn, said, finish, reason)


def _pick(d: dict, lang: str):
    return d.get(lang) or d.get("en")


def _saved_remember(s: dict, lang: str) -> list[str]:
    tips = _pick(s["tips"], lang)
    return [tips[0], tips[2 % len(tips)], tips[-1]]


def _score_line(said: str, s: dict, lang: str) -> int:
    """Without the AI: a line is stronger when it is calm (no insults) and uses one of the checked facts."""
    text = said.lower()
    digits = re.findall(r"[0-9০-৯]+", text)
    keys = ["১/৮", "1/8", "আট ভাগ", "eighth", "১২", "12", "১১৭০", "১,১৭০", "1,170", "1170", "রসিদ", "receipt",
            "১৬৪৩০", "16430", "১৬১২২", "16122", "১০৯", "109", "আইন", "law", "আদালত", "court", "দেনমোহর", "dower",
            "নামজারি", "mutation", "land.gov.bd", "succession", "intermeddling", "fida", "artículo", "article 18",
            "procuradur", "কাবিন", "kabin", "অপরাধ", "crime"]
    hits = sum(1 for k in keys if k in text)
    score = 45 + min(35, hits * 15) + (5 if digits else 0) + (10 if len(said) > 40 else 0)
    return max(20, min(95, score))


def _saved_turn(s: dict, lang: str, turn: int, said: str, finish: bool, reason: str) -> dict:
    lines = _pick(s["fallback"], lang)
    tips = _pick(s["tips"], lang)
    done = finish or turn >= len(lines) - 1
    score = _score_line(said, s, lang)
    good = {"bn": ("ভালো, শান্ত আর পরিষ্কার বলেছেন।" if score >= 70 else
                   "ভালো শুরু। এবার আইনের কথা আর সংখ্যাটা বলুন, তাতে কথা জোরালো হবে।"),
            "en": ("Good: calm and clear." if score >= 70 else
                   "Good start. Now say the law and the number; that makes it stronger.")}[("bn" if lang == "bn" else "en")]
    return {"reply": lines[min(turn, len(lines) - 1)], "score": score, "good": good,
            "try_saying": tips[min(turn, len(tips) - 1)], "law": _pick(s["law"], lang), "done": done,
            "real_danger": False, "remember": _saved_remember(s, lang) if done else [], "source": "saved",
            "note": reason}


# ---------------------------------------------------------------------------------------------------------
# Counter: whisper at the real desk

PLACES = {
    "land_office": {"bn": "ভূমি অফিস", "en": "Land office"},
    "union_parishad": {"bn": "ইউনিয়ন পরিষদ", "en": "Union Parishad"},
    "police": {"bn": "থানা", "en": "Police station"},
    "clinic": {"bn": "হাসপাতাল / ক্লিনিক", "en": "Hospital / clinic"},
    "bank": {"bn": "ব্যাংক / এনজিও ঋণ", "en": "Bank / loan office"},
}

COUNTER_SCHEMA = {
    "type": "object",
    "properties": {
        "whisper": {"type": "string", "description": "one short first-person sentence she can say right now, her language"},
        "truth": {"type": "string", "description": "one short sentence: what is actually true, from the checked facts, her language"},
        "red_flag": {"type": "boolean", "description": "true if the official is asking for a bribe, lying about the law, "
                                                       "or pushing her to sign or give up something"},
        "ask_for": {"type": "string", "description": "what to ask for in writing (receipt, written reason, copy), her language; may be empty"},
    },
    "required": ["whisper", "truth", "red_flag", "ask_for"],
}

COUNTER_SYSTEM = """You are HerEvo whispering to a woman at a real office desk ({place}). She tells you what the \
official just said. Answer instantly and briefly in {lang}: one sentence she can say now (polite, firm), the truth \
from the checked facts, whether it is a red flag (bribe, false law, pressure to sign or give up a right), and what \
to ask for in writing. Use only these checked facts for laws, fees and numbers; if they do not cover it, tell her \
to ask for the rule in writing and call free legal aid:
{facts}"""


def counter(place: str, heard: str, lang: str, country: str) -> dict:
    pname = _pick(PLACES.get(place, {"en": place}), lang)
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        try:
            raw = llm.generate(COUNTER_SYSTEM.format(place=pname, lang=lang_name(lang), facts=facts_for(country)),
                               [{"text": f"The official said: {heard}"}], "whisper", COUNTER_SCHEMA, seconds=15,
                               temperature=0.3)
            if raw.get("whisper"):
                return {"whisper": _clip(raw["whisper"], 220), "truth": _clip(raw.get("truth"), 260),
                        "red_flag": bool(raw.get("red_flag")), "ask_for": _clip(raw.get("ask_for"), 160),
                        "source": "ai"}
            reason = "empty answer"
        except Exception as exc:
            reason = type(exc).__name__
            log.warning("counter AI failed: %s", exc)
    return {**_saved_whisper(heard, lang), "source": "saved", "note": reason}


def _saved_whisper(heard: str, lang: str) -> dict:
    t = heard.lower()
    bn = lang == "bn"
    money = any(k in t for k in ("টাকা", "taka", "money", "pay", "fee", "খরচ", "হাজার", "thousand", "দিতে হবে"))
    sign = any(k in t for k in ("সই", "sign", "টিপ", "thumb", "দস্তখত"))
    if sign:
        return {"whisper": "আগে পুরো কাগজটা পড়ে শোনান। না বুঝে আমি সই করব না।" if bn else
                "Please read the whole paper to me first. I won't sign what I don't understand.",
                "truth": "সই করা কাগজে জমি বা অধিকার চিরতরে চলে যেতে পারে।" if bn else
                "A signed paper can give away land or rights for ever.",
                "red_flag": True, "ask_for": "কাগজের একটা কপি" if bn else "A copy of the paper"}
    if money:
        return {"whisper": "সরকারি ফি কত, লিখে দিন। আমি রসিদ ছাড়া টাকা দেব না।" if bn else
                "Please write down the official fee. I won't pay without a receipt.",
                "truth": "নামজারির সরকারি ফি মোট ১,১৭০ টাকা, অনলাইনে রসিদসহ।" if bn else
                "The official mutation fee is Tk 1,170 in total, paid online with a receipt.",
                "red_flag": True, "ask_for": "সরকারি রসিদ" if bn else "An official receipt"}
    return {"whisper": "এই নিয়মটা কোথায় লেখা আছে, একটু দেখাবেন?" if bn else "Could you show me where this rule is written?",
            "truth": "নিয়ম লিখিতভাবে চাইতে পারেন। সন্দেহ হলে বিনামূল্যে আইনি সহায়তা ১৬৪৩০-এ ফোন করুন।" if bn else
            "You can ask for the rule in writing. If unsure, call free legal aid on 16430.",
            "red_flag": False, "ask_for": "লিখিত কারণ" if bn else "The reason in writing"}


# ---------------------------------------------------------------------------------------------------------
# Witness: her sealed diary, turned into a statement

STATEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "statement": {"type": "string", "description": "first-person statement in her language, dated paragraphs, only what she wrote"},
        "statement_en": {"type": "string", "description": "the same statement in English"},
        "timeline": {"type": "array", "items": {"type": "object", "properties": {
            "date": {"type": "string"}, "what": {"type": "string", "description": "one line, her language"}},
            "required": ["date", "what"]}},
        "missing": {"type": "array", "items": {"type": "string"},
                    "description": "up to 4 questions, her language, whose answers would make the statement stronger "
                                   "(witnesses, injuries, medical visit, threats, children present)"},
        "where": {"type": "string", "description": "where to take it, from the checked facts, her language"},
    },
    "required": ["statement", "statement_en", "timeline", "missing", "where"],
}

STATEMENT_SYSTEM = """You help a woman turn her private diary notes about violence or abuse into a clear, dated, \
first-person statement she can take to the Women Affairs Officer, legal aid or a magistrate. Write in {lang} (and \
the same in English). Use ONLY what her notes say: never add events, injuries, names or words she did not write. \
Keep her dates. Neutral, factual sentences. Do not include her name or address (she fills those in by hand: write \
"[name]" and "[address]"). Where to take it must come from these checked facts:
{facts}"""


def statement(entries: list[dict], lang: str, country: str) -> dict:
    notes = "\n".join(f"- {_clip(e.get('at'), 30)}: {_clip(e.get('text'), 1200)}" for e in entries[-40:])
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        try:
            raw = llm.generate(STATEMENT_SYSTEM.format(lang=lang_name(lang), facts=facts_for(country)),
                               [{"text": "Her diary notes:\n" + notes}], "statement", STATEMENT_SCHEMA, seconds=40,
                               temperature=0.2)
            if raw.get("statement"):
                return {"statement": str(raw["statement"])[:6000], "statement_en": str(raw.get("statement_en") or "")[:6000],
                        "timeline": [{"date": _clip(t.get("date"), 40), "what": _clip(t.get("what"), 200)}
                                     for t in (raw.get("timeline") or [])[:40] if isinstance(t, dict)],
                        "missing": [_clip(m, 200) for m in (raw.get("missing") or [])[:4]],
                        "where": _clip(raw.get("where"), 400), "source": "ai"}
            reason = "empty answer"
        except Exception as exc:
            reason = type(exc).__name__
            log.warning("statement AI failed: %s", exc)
    return {**_saved_statement(entries, lang, country), "source": "saved", "note": reason}


def _saved_statement(entries: list[dict], lang: str, country: str) -> dict:
    bn = lang == "bn"
    head = ("আমি, [নাম], [ঠিকানা], নিচের ঘটনাগুলো সত্য বলে জানাচ্ছি:" if bn else
            "I, [name], of [address], state that the following events are true:")
    body = "\n\n".join(f"{_clip(e.get('at'), 30)}: {e.get('text', '').strip()}" for e in entries)
    where = ({"BD": "উপজেলা মহিলা বিষয়ক কর্মকর্তা বা বিনামূল্যে আইনি সহায়তা (১৬৪৩০)। পারিবারিক সহিংসতা আইন ২০১০ অনুযায়ী "
                    "সুরক্ষা আদেশের আবেদন করা যায়।"}.get(country.upper(), "বিনামূল্যে আইনি সহায়তা।") if bn else
             {"BD": "The Upazila Women Affairs Officer or free legal aid (16430). You can apply for a protection order "
                    "under the Domestic Violence Act 2010.",
              "KE": "FIDA Kenya (free legal advice) or the police gender desk. Helpline 1195.",
              "MX": "The local Women's Institute or the public prosecutor (Ministerio Público). Emergency 911."}
             .get(country.upper(), "Free legal aid."))
    return {"statement": head + "\n\n" + body, "statement_en": "",
            "timeline": [{"date": _clip(e.get("at"), 40), "what": _clip(e.get("text"), 120)} for e in entries],
            "missing": (["কেউ কি ঘটনা দেখেছেন? তাঁর নাম লিখে রাখুন।", "আঘাত পেয়ে থাকলে ডাক্তার দেখিয়েছিলেন? কাগজ রেখে দিন।"]
                        if bn else ["Did anyone see it happen? Note their name.",
                                    "If you were hurt, did you see a doctor? Keep the paper."]),
            "where": where}


# ---------------------------------------------------------------------------------------------------------
# Just tell me: she describes what is happening in her own words; HerEvo builds her path through the tools.

TOOLS_FOR_PLAN = ("share", "lens", "rehearse", "counter", "witness", "call", "chat")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "understood": {"type": "string", "description": "one short sentence in her language: what is happening to her, as you understood it"},
        "say": {"type": "string", "description": "what to read aloud to her: warm, 2-3 short sentences in her language, ending with what to do first"},
        "danger": {"type": "boolean", "description": "true only if she may be in danger now (violence, threats, being locked in, forced)"},
        "family": {"type": "object", "description": "ONLY if a death and inheritance of land or property is involved; only facts she said",
                   "properties": {
                       "deceased": {"type": "string", "enum": ["husband", "father", "mother"]},
                       "wives": {"type": "integer"}, "sons": {"type": "integer"}, "daughters": {"type": "integer"},
                       "father_alive": {"type": "boolean"}, "mother_alive": {"type": "boolean"},
                       "land": {"type": "number", "description": "total land in decimals (shotangsho) if she said it"},
                       "offered": {"type": "number", "description": "decimals the family is offering her, if she said it"}}},
        "steps": {"type": "array", "description": "2 to 5 steps in the order she should do them",
                  "items": {"type": "object", "properties": {
                      "tool": {"type": "string", "enum": list(TOOLS_FOR_PLAN)},
                      "title": {"type": "string", "description": "an action in her language, at most 8 words"},
                      "why": {"type": "string", "description": "one short sentence in her language"},
                      "scenario": {"type": "string", "enum": list(SCENARIOS), "description": "for tool=rehearse: the scenario id"},
                      "place": {"type": "string", "enum": list(PLACES), "description": "for tool=counter: the place id"},
                      "paper": {"type": "string", "description": "for tool=lens: which paper to photograph, her language"},
                      "number": {"type": "string", "description": "for tool=call: the number, from the checked facts only"}},
                      "required": ["tool", "title", "why"]}},
        "ask": {"type": "string", "description": "one short question in her language if a key fact is missing, else empty"},
    },
    "required": ["understood", "say", "danger", "steps"],
}

PLAN_SYSTEM = """You are HerEvo, an AI ally for rural women. She has just told you, in her own words (often spoken, \
maybe messy), what is happening to her. Understand her situation and build her a short, practical path through \
HerEvo's tools. Write in {lang}, warm and plain, for a woman who may not read well.

HerEvo's tools:
- share: works out her exact lawful share of land after a death, under Muslim law as applied in Bangladesh ONLY \
(needs who died and who survived). Never use it for Kenya or Mexico.
- lens: she photographs a paper (deed, notice, kabinnama, loan paper) and HerEvo reads it aloud and warns her before \
signing. Say which paper in `paper`.
- rehearse: she practises a hard conversation with the AI playing the other person. Scenario ids: {scenarios}.
- counter: HerEvo whispers the truth to her at a real office desk. Place ids: {places}.
- witness: a private, locked diary of violence or abuse that becomes a dated statement for a protection order.
- call: a helpline, only numbers from the checked facts.
- chat: anything else (farming, health, money, other questions).

Rules: 2 to 5 steps, in the order she should do them, each a concrete action. Use the tools that truly fit. If she \
may be in danger now, set danger=true and make the first step call with the emergency number. Fill `family` only \
with facts she actually said; never guess numbers. If one key fact is missing, ask for it in `ask`. Laws, fees and \
numbers only from these checked facts:
{facts}"""


def _norm_digits(text: str) -> str:
    return text.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))


def _guess_family(text: str) -> dict | None:
    """Without the AI: pull the obvious facts out of her words ('2 ছেলে', '1 daughter', '80 শতাংশ')."""
    t = _norm_digits(text.lower())
    deceased = None
    if re.search(r"স্বামী|husband|mume|esposo|marido", t) and re.search(r"মারা|died|dead|passed|mrehemu|alifariki|murió|falleció", t):
        deceased = "husband"
    elif re.search(r"বাবা|father|baba|padre|papá", t) and re.search(r"মারা|died|dead|passed|murió|falleció", t):
        deceased = "father"
    elif re.search(r"\bমা\b|mother|madre|mamá", t) and re.search(r"মারা|died|dead|passed|murió|falleció", t):
        deceased = "mother"
    if not deceased:
        return None
    words = {"এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

    def count(pattern: str) -> int | None:
        m = re.search(r"(\d+|" + "|".join(words) + r")\s*(?:জন\s*|টি\s*)?(?:" + pattern + ")", t)
        if not m:
            return None
        return int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]

    fam: dict = {"deceased": deceased}
    sons, daughters = count("ছেলে|পুত্র|sons?"), count("মেয়ে|কন্যা|daughters?")
    if sons is not None:
        fam["sons"] = sons
    if daughters is not None:
        fam["daughters"] = daughters
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:শতাংশ|শতক|decimals?)", t)
    if m:
        fam["land"] = float(m.group(1))
    return fam


def _fill_share(fam: dict | None) -> dict | None:
    """Compute her share when the facts are enough (the AI never does this arithmetic)."""
    from .inheritance import Family, compute
    if not fam or fam.get("deceased") not in ("husband", "father", "mother"):
        return None
    if fam.get("sons") is None and fam.get("daughters") is None:
        return None
    d = fam["deceased"]
    sons, daughters = max(0, int(fam.get("sons") or 0)), max(0, int(fam.get("daughters") or 0))
    if d != "husband":
        daughters = max(1, daughters)  # she is one of the daughters
    f = Family(deceased=d, wives=max(1, int(fam.get("wives") or 1)) if d in ("husband", "father") else 0,
               husband=False, sons=sons, daughters=daughters, father=bool(fam.get("father_alive")),
               mother=bool(fam.get("mother_alive")), land=float(fam["land"]) if fam.get("land") else None)
    if d == "father" and not fam.get("wives"):
        f.wives = 0
    result, _ = compute(f)
    if not result:
        return None
    her = "wife" if d == "husband" else "daughter"
    mine = next((x for x in result.shares if x.heir == her), None)
    if not mine:
        return None
    return {"deceased": d, "wives": f.wives, "sons": sons, "daughters": daughters, "father": f.father,
            "mother": f.mother, "land": f.land, "offered": fam.get("offered"), "heir": her,
            "num": mine.each.numerator, "den": mine.each.denominator, "her_land": mine.land_each,
            "pct": round(float(mine.each) * 100, 2)}


def _scenario_for(text: str, country: str) -> str:
    """The rehearsal that fits her words best, when the AI did not pick one."""
    if country == "KE":
        return "ke_home"
    if country == "MX":
        return "mx_ejido"
    t = text.lower()
    if any(k in t for k in ("যৌতুক", "dowry")):
        return "dowry"
    if any(k in t for k in ("ঘুষ", "bribe", "অফিস", "office", "নামজারি", "mutation")):
        return "land_office"
    if any(k in t for k in ("দেনমোহর", "মোহর", "খোরপোষ", "সালিশ", "dower", "maintenance", "salish")):
        return "salish"
    return "inlaw_land"


def _clean_steps(steps, country: str, text: str = "") -> list[dict]:
    out = []
    for st in (steps or [])[:5]:
        if not isinstance(st, dict) or st.get("tool") not in TOOLS_FOR_PLAN or not st.get("title"):
            continue
        if st["tool"] == "share" and country != "BD":
            continue  # the share calculator is Bangladesh Muslim law only
        item = {"tool": st["tool"], "title": _clip(st["title"], 80), "why": _clip(st.get("why"), 200)}
        if st["tool"] == "rehearse":
            sc = st.get("scenario")
            item["scenario"] = sc if sc in SCENARIOS and SCENARIOS[sc]["country"] == country else _scenario_for(text, country)
        if st["tool"] == "counter":
            item["place"] = st.get("place") if st.get("place") in PLACES else "land_office"
        if st["tool"] == "lens":
            item["paper"] = _clip(st.get("paper"), 80)
        if st["tool"] == "call":
            num = re.sub(r"[^0-9]", "", _norm_digits(str(st.get("number") or "")))
            allowed = re.findall(r"\b\d{3,5}\b", facts_for(country))
            item["number"] = num if num in allowed else SAFETY_NUMBERS.get(country, "999").split(" / ")[0]
        out.append(item)
    return out


def plan(text: str, lang: str, country: str) -> dict:
    country = (country or "BD").upper()
    danger = screen(text)
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        scen = ", ".join(f"{k} ({v['title']['en']})" for k, v in SCENARIOS.items() if v["country"] == country) or \
            ", ".join(SCENARIOS)
        try:
            raw = llm.generate(PLAN_SYSTEM.format(lang=lang_name(lang), scenarios=scen, places=", ".join(PLACES),
                                                  facts=facts_for(country)),
                               [{"text": "What she said:\n" + text}], "make_path", PLAN_SCHEMA, seconds=25,
                               temperature=0.3)
            steps = _clean_steps(raw.get("steps"), country, text)
            if steps:
                fam = raw.get("family") if isinstance(raw.get("family"), dict) else None
                is_danger = bool(raw.get("danger")) or (danger.escalate and danger.kind == "danger")
                if is_danger and steps[0]["tool"] != "call":
                    steps.insert(0, _call_step(lang, country))
                return {"understood": _clip(raw.get("understood"), 240), "say": _clip(raw.get("say"), 500),
                        "danger": is_danger, "share": _fill_share(fam) if country == "BD" else None, "steps": steps[:5],
                        "ask": _clip(raw.get("ask"), 200), "source": "ai"}
            reason = "no steps"
        except Exception as exc:
            reason = type(exc).__name__
            log.warning("plan AI failed: %s", exc)
    return {**_saved_plan(text, lang, country, danger.escalate and danger.kind == "danger"), "source": "saved",
            "note": reason}


def _call_step(lang: str, country: str) -> dict:
    num = SAFETY_NUMBERS.get(country, "999").split(" / ")[0]
    bn = lang == "bn"
    bn_num = num.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
    return {"tool": "call", "number": num, "title": f"এখনই {bn_num}-এ ফোন করুন" if bn else f"Call {num} now",
            "why": "আপনি বিপদে থাকলে আগে নিজেকে নিরাপদ রাখুন।" if bn else "If you are in danger, get safe first."}


def _saved_plan(text: str, lang: str, country: str, danger: bool) -> dict:
    bn = lang == "bn"
    t = text.lower()
    L = lambda b, e: b if bn else e  # noqa: E731
    steps: list[dict] = []
    has = lambda *ks: any(k in t for k in ks)  # noqa: E731
    fam = _guess_family(text)
    share = _fill_share(fam) if country == "BD" else None
    if danger:
        steps.append(_call_step(lang, country))
    if has("মারে", "মারধর", "ধাক্কা", "নির্যাতন", "beat", "hit", "push", "abuse", "violence", "threat", "হুমকি"):
        steps.append({"tool": "witness", "title": L("যা ঘটেছে, গোপনে লিখে রাখুন", "Write down what happened, privately"),
                      "why": L("তারিখসহ লেখা পরে সুরক্ষা আদেশে কাজে লাগবে।", "Dated notes help you get a protection order.")})
    if has("যৌতুক", "dowry"):
        steps.append({"tool": "rehearse", "scenario": "dowry", "title": L("শান্তভাবে না বলার মহড়া দিন", "Practise saying no calmly"),
                      "why": L("যৌতুক চাওয়া অপরাধ। আগে থেকে কথা গুছিয়ে রাখুন।", "Demanding dowry is a crime. Prepare your words.")})
    if fam or has("জমি", "land", "inherit", "ভাগ", "শামবা", "shamba", "ejido", "parcela", "tierra"):
        if country == "BD":
            steps.append({"tool": "share", "title": L("জমিতে আমার ভাগ দেখুন", "See my share of the land"),
                          "why": L("আইনে আপনার কতটা, আগে নিশ্চিত হোন।", "Know exactly what the law gives you first.")})
        if has("সই", "টিপ", "দলিল", "কাগজ", "sign", "paper", "deed", "thumb"):
            steps.append({"tool": "lens", "paper": L("যে কাগজে সই চাইছে", "The paper they want you to sign"),
                          "title": L("সই করার আগে কাগজটা পড়িয়ে নিন", "Have the paper read before you sign"),
                          "why": L("হেবা বা না-দাবি কাগজে জমি চিরতরে চলে যায়।", "A gift or no-claim paper gives the land away for ever.")})
        sc = {"KE": "ke_home", "MX": "mx_ejido"}.get(country, "inlaw_land")
        steps.append({"tool": "rehearse", "scenario": sc, "title": L("ভাগ চাওয়ার মহড়া দিন", "Practise claiming your share"),
                      "why": L("আইন আর সংখ্যা মুখস্থ থাকলে কেউ ভয় দেখাতে পারবে না।", "With the law and the number ready, no one can scare you.")})
        if country == "BD":
            steps.append({"tool": "counter", "place": "land_office", "title": L("নামজারির দিন অফিসে পাশে থাকব", "I'll be with you at the land office"),
                          "why": L("সরকারি ফি মোট ১,১৭০ টাকা। বাড়তি চাইলে ধরিয়ে দেব।", "The official fee is Tk 1,170. I'll catch any extra demand.")})
    if has("ঘুষ", "bribe", "অফিস", "office", "টাকা চাই", "fee"):
        steps.append({"tool": "counter", "place": "land_office", "title": L("অফিসের ডেস্কে পাশে থাকব", "I'll be with you at the desk"),
                      "why": L("ওরা কী বলছে শোনান, সত্যিটা কানে বলব।", "Let me hear them; I'll whisper the truth.")})
    if has("দেনমোহর", "মোহর", "খোরপোষ", "কাবিন", "dower", "maintenance", "kabin"):
        steps.append({"tool": "lens", "paper": L("কাবিননামা", "Your kabinnama"), "title": L("কাবিননামার ছবি তুলুন", "Photograph your kabinnama"),
                      "why": L("দেনমোহর কত লেখা আছে, শুনে নিন।", "Hear exactly what dower it promises.")})
        steps.append({"tool": "rehearse", "scenario": "salish", "title": L("সালিশে কথা বলার মহড়া দিন", "Practise for the salish"),
                      "why": L("চাইলেই পাওনা দেনমোহর আপনার অধিকার।", "The prompt dower is yours on demand.")})
    if not steps or (danger and len(steps) == 1):
        steps.append({"tool": "chat", "title": L("HerEvo-কে খুলে বলুন", "Tell HerEvo more"),
                      "why": L("কৃষি, টাকা, স্বাস্থ্য বা অন্য যেকোনো কথা।", "Farming, money, health or anything else.")})
    seen, uniq = set(), []
    for st in steps:
        key = (st["tool"], st.get("scenario"), st.get("place"))
        if key not in seen:
            seen.add(key)
            uniq.append(st)
    first = uniq[0]["title"]
    topics = []
    if danger or has("মারে", "মারধর", "ধাক্কা", "নির্যাতন", "beat", "hit", "push", "abuse", "violence", "threat", "হুমকি"):
        topics.append(L("আপনার সঙ্গে সহিংসতা হচ্ছে", "you are facing violence"))
    if has("যৌতুক", "dowry"):
        topics.append(L("যৌতুক চাওয়া হচ্ছে", "dowry is being demanded"))
    if fam or has("জমি", "land", "inherit", "শামবা", "shamba", "ejido", "parcela", "tierra"):
        topics.append(L("জমির ন্যায্য ভাগ নিয়ে বাধা আসছে", "you are being blocked from your share of the land"))
    if has("সই", "টিপ", "sign", "thumb"):
        topics.append(L("না বুঝে কাগজে সই করানোর চাপ আছে", "you are being pushed to sign a paper"))
    if has("ঘুষ", "bribe", "হাজার টাকা", "thousand taka"):
        topics.append(L("অফিসে বাড়তি টাকা চাওয়া হচ্ছে", "an office is asking you for extra money"))
    if has("দেনমোহর", "মোহর", "খোরপোষ", "কাবিন", "dower", "maintenance", "kabin"):
        topics.append(L("দেনমোহর আর খোরপোষ পাচ্ছেন না", "you are not getting your dower and maintenance"))
    if topics:
        joined = ", ".join(topics[:-1]) + (L(" এবং ", " and ") if len(topics) > 1 else "") + topics[-1]
        understood = L(f"বুঝেছি: {joined}।", f"I understand: {joined}.")
    else:
        understood = L("আপনার কথা শুনেছি। চলুন, ধাপে ধাপে এগোই।", "I heard you. Let's go step by step.")
    return {"understood": understood,
            "say": L(f"আপনার কথা শুনেছি। আপনি একা নন। প্রথমে: {first}।", f"I heard you. You are not alone. First: {first}."),
            "danger": danger, "share": share, "steps": uniq[:5],
            "ask": "" if share or not fam else L("কতজন ছেলে ও মেয়ে আছে, আর মোট জমি কত শতাংশ?", "How many sons and daughters, and how much land in total?")}


# ---------------------------------------------------------------------------------------------------------
# Listen mode: HerEvo heard a whole meeting; write down who promised what, so nobody can deny it later.

MINUTES_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 plain sentences in her language: what happened in the meeting"},
        "promises": {"type": "array", "description": "every promise or decision heard, up to 6",
                     "items": {"type": "object", "properties": {
                         "who": {"type": "string", "description": "who said it, as heard (e.g. 'the chairman'), her language"},
                         "what": {"type": "string", "description": "what was promised or decided, her language"},
                         "when": {"type": "string", "description": "by when, if said; else empty"}},
                         "required": ["who", "what"]}},
        "warnings": {"type": "array", "items": {"type": "string"},
                     "description": "up to 3: bribes, false rules, pressure, in her language"},
        "next": {"type": "array", "items": {"type": "string"}, "description": "up to 3 next steps, her language"},
    },
    "required": ["summary", "promises", "warnings", "next"],
}

MINUTES_SYSTEM = """A woman used HerEvo during a real meeting at {place}. Below are the lines HerEvo heard, in \
order. Write the record she will keep, in {lang}: a short summary, every promise or decision (who, what, by when), \
warnings (a bribe, a false rule, pressure to sign or give up a right) and next steps. Use ONLY what the lines say; \
never invent names, amounts or dates. Laws, fees and numbers in warnings and next steps only from these checked facts:
{facts}"""


def minutes(place: str, lines: list[str], lang: str, country: str) -> dict:
    pname = _pick(PLACES.get(place, {"en": place}), lang)
    heard = "\n".join(f"{i}. {_clip(x, 500)}" for i, x in enumerate(lines[-80:], 1))
    llm = get_llm()
    reason = "no AI configured"
    if llm is not None and hasattr(llm, "generate"):
        try:
            raw = llm.generate(MINUTES_SYSTEM.format(place=pname, lang=lang_name(lang), facts=facts_for(country)),
                               [{"text": "Lines heard:\n" + heard}], "meeting_record", MINUTES_SCHEMA, seconds=30,
                               temperature=0.2)
            if raw.get("summary"):
                return {"summary": _clip(raw["summary"], 600),
                        "promises": [{"who": _clip(p.get("who"), 60), "what": _clip(p.get("what"), 200),
                                      "when": _clip(p.get("when"), 60)}
                                     for p in (raw.get("promises") or [])[:6] if isinstance(p, dict) and p.get("what")],
                        "warnings": [_clip(w, 200) for w in (raw.get("warnings") or [])[:3]],
                        "next": [_clip(n, 200) for n in (raw.get("next") or [])[:3]], "source": "ai"}
            reason = "empty answer"
        except Exception as exc:
            reason = type(exc).__name__
            log.warning("minutes AI failed: %s", exc)
    bn = lang == "bn"
    warn = [_saved_whisper(x, lang)["truth"] for x in lines if _saved_whisper(x, lang)["red_flag"]][:3]
    return {"summary": (f"{pname}-এ সভায় {len(lines)}টি কথা শোনা গেছে। নিচে সব লেখা আছে।" if bn else
                        f"{len(lines)} things were said at the {pname}. All are written below."),
            "promises": [], "warnings": list(dict.fromkeys(warn)), "next": [], "lines": lines[-80:],
            "source": "saved", "note": reason}

# ---------------------------------------------------------------------------------------------------------

def _load_samples() -> None:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "web" / "samples" / "samples.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        SAMPLE_READINGS.update({k: v["reading"] for k, v in data.items() if "reading" in v})
    except (OSError, ValueError, KeyError) as exc:
        log.info("no saved sample readings: %s", exc)


_load_samples()
