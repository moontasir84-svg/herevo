"""Score HerEvo against the evaluation set.

    python scripts/run_eval.py ../eval/bd_questions.yaml            # offline: safety + retrieval + rule fallback
    ANTHROPIC_API_KEY=... python scripts/run_eval.py ../eval/bd_questions.yaml   # adds Claude intent accuracy

Writes a JSON report next to the question file. Uses a throwaway user so no real data is touched."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app import alerts, db, llm  # noqa: E402
from app.packs import ingest  # noqa: E402
from app.pipeline import fallback_intent, fallback_sale, handle  # noqa: E402
from app.retrieval import retrieve  # noqa: E402
from app.safety import screen  # noqa: E402


def main(path: str) -> dict:
    qs = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]
    db.init_schema()
    ingest("BD")
    alerts.set_notifier(alerts.LogNotifier())  # never alert real helpers during evaluation
    use_claude = llm.get_llm() is not None
    uid = db.user_id_for("eval", "eval-run")
    db.upsert_user(uid, "eval", "BD", "bn")

    rows, tp, fn, fp, tn = [], 0, 0, 0, 0
    hit1 = hit3 = n_ret = 0
    amt_ok = n_amt = 0
    intent_ok = n_intent = 0
    for q in qs:
        exp = q["expect"]
        esc = screen(q["text"]).escalate
        if exp["escalate"]:
            tp, fn = tp + esc, fn + (not esc)
        else:
            fp, tn = fp + esc, tn + (not esc)
        row = {"id": q["id"], "escalated": esc}
        if exp.get("top") and not esc:
            ids = [h["id"].split("#")[0] for h in retrieve("BD", q["text"], 3)]
            n_ret += 1
            hit1 += bool(ids) and ids[0] == exp["top"]
            hit3 += exp["top"] in ids
            row["retrieved"] = ids
        if use_claude and not esc:
            res = handle(uid=uid, channel="eval", text=q["text"], country="BD")
            n_intent += 1
            intent_ok += res.intent == exp["intent"]
            row.update(intent=res.intent, answered_by=res.answered_by, answer_en=res.reply_en,
                       cited=[c.id for c in res.citations])
            sale = res.sale
        else:
            row["fallback_intent"] = fallback_intent(q["text"])
            sale = fallback_sale(q["text"], "BDT") if row["fallback_intent"] == "EARN" else None
        if exp.get("amount"):
            n_amt += 1
            amt_ok += bool(sale) and abs(float(sale["amount"]) - exp["amount"]) < 0.01
            row["sale"] = sale
        rows.append(row)

    db.delete_user(uid)
    report = {
        "questions": len(qs),
        "mode": "claude" if use_claude else "offline",
        "safety_recall": round(tp / max(1, tp + fn), 3),
        "false_escalation_rate": round(fp / max(1, fp + tn), 3),
        "retrieval_hit@1": round(hit1 / max(1, n_ret), 3),
        "retrieval_hit@3": round(hit3 / max(1, n_ret), 3),
        "sale_amount_accuracy": round(amt_ok / max(1, n_amt), 3),
        "intent_accuracy": round(intent_ok / n_intent, 3) if n_intent else None,
        "missed_escalations": [r["id"] for r, q in zip(rows, qs) if q["expect"]["escalate"] and not r["escalated"]],
        "false_escalations": [r["id"] for r, q in zip(rows, qs) if not q["expect"]["escalate"] and r["escalated"]],
        "retrieval_misses": [r["id"] for r, q in zip(rows, qs)
                             if q["expect"].get("top") and "retrieved" in r and q["expect"]["top"] not in r["retrieved"]],
        "details": rows,
    }
    out = Path(path).with_suffix(".report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    db.close()
    return report


if __name__ == "__main__":
    r = main(sys.argv[1] if len(sys.argv) > 1 else "../eval/bd_questions.yaml")
    print(json.dumps({k: v for k, v in r.items() if k != "details"}, ensure_ascii=False, indent=2))
