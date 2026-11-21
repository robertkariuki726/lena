"""
Swahili / Sheng evaluation harness.

THE GAP
    Kenyan companies are deploying chatbots, voice assistants and AI customer
    support in Swahili, Sheng and mixed Kenyan English. Almost nobody measures
    whether those systems are actually correct in those languages. Benchmarks
    exist for English, Chinese, French and Spanish. Swahili, the most widely
    spoken language in East Africa, has essentially no public evaluation set,
    and Sheng has none at all.

    The consequence is not abstract: a model that answers Swahili fluently but
    confuses "kesho" (tomorrow) with "jana" (yesterday), or that answers a
    formal letter in slang, is a customer-service failure that no English test
    will ever catch.

WHAT THIS IS
    A small, human-reviewable evaluation set plus a scoring harness.
    24 items across 6 categories:
      comprehension, translation_en_sw, translation_sw_en, register,
      code_switching, local_context

    Each item carries keyword groups that a correct answer must cover, plus
    red flags that mark a wrong answer. The harness scores any model's answers
    against them and prints a leaderboard.

USAGE
    python score.py answers.json
    python score.py answers.json --json results.json
    python score.py --items            # list the items to ask a model

INPUT FORMAT
    {
      "gpt":    {"c1": "answer text", "c2": "..."},
      "gemini": {"c1": "...", "c2": "..."}
    }

WHY KEYWORDS AND NOT AN LLM JUDGE
    An LLM judge would need a better Swahili speaker than the model being
    tested, which is exactly what does not exist here. Keyword groups are
    cruder but they are transparent, offline, and a native speaker can audit
    every single one. For a first public benchmark, auditable beats clever.

HONEST LIMITS
    - 24 items is a seed set, not a benchmark. It finds obvious failures, not
      subtle ones.
    - Keyword matching rewards covering the right facts, not eloquence.
    - Sheng varies by estate and generation; items here use common Nairobi
      forms and must be reviewed by a native speaker before publication.
"""

import argparse
import json
import re
import sys

# --------------------------------------------------------------------------- #
# The evaluation set.
#   prompt:    what to send the model
#   must:      groups of alternatives; each group needs at least one hit
#   forbid:    phrases that indicate a wrong or unsafe answer
#   why:       what the item is actually testing (for the human reviewer)
# --------------------------------------------------------------------------- #
ITEMS = [
    # ---------------- comprehension ----------------
    {
        "id": "c1", "category": "comprehension",
        "prompt": "Kwa Kiswahili: nini maana ya methali 'Haraka haraka haina baraka'? Eleza kwa sentensi mbili.",
        "must": [["pole", "taratibu", "si haraka", "uvumilivu", "haraka"]],
        "forbid": [],
        "why": "Basic proverb comprehension. A fluent-sounding but empty answer fails here.",
    },
    {
        "id": "c2", "category": "comprehension",
        "prompt": "Kwa Kiswahili: nini tofauti kati ya 'jana' na 'kesho'? Tumia kila neno katika sentensi.",
        "must": [["jana"], ["kesho"], ["juzi", "siku iliyopita", "iliyopita"], ["kesho", "siku ijayo", "ijayo"]],
        "forbid": [],
        "why": "Temporal words. Mixing these up is a classic failure that reads fluently.",
    },
    {
        "id": "c3", "category": "comprehension",
        "prompt": "Kwa Kiswahili: mtu anasema 'nimechoka lakini nitaendelea'. Anamaanisha nini kuhusu hali yake?",
        "must": [["amechoka", "uchovu", "choka"], ["ataendelea", "kuendelea", "hataacha"]],
        "forbid": [],
        "why": "Concessive clause with 'lakini'. Tests whether the contrast is understood.",
    },
    {
        "id": "c4", "category": "comprehension",
        "prompt": "Kwa Kiswahili: kwa nini mtu anaweza kusema 'sijui' badala ya 'siwezi'? Eleza tofauti.",
        "must": [["sijui", "kutojua", "kukosa maarifa"], ["siwezi", "kutoweza", "uwezo"]],
        "forbid": [],
        "why": "Distinguishing lack of knowledge from lack of ability.",
    },
    # ---------------- translation EN -> SW ----------------
    {
        "id": "t1", "category": "translation_en_sw",
        "prompt": "Translate into natural Swahili, not word for word: 'Please confirm whether the payment went through, and send me the receipt.'",
        "must": [["tafadhali", "tafadhali", "kama tafadhali"], ["malipo", "malipo"], ["risiti", "stakabadhi"], ["thibitisha", "hakikisha", "ujulishe"]],
        "forbid": ["kuthibitisha whether"],
        "why": "Business register. Tests natural Swahili rather than transliteration.",
    },
    {
        "id": "t2", "category": "translation_en_sw",
        "prompt": "Translate into Swahili: 'The meeting has been moved to Thursday at 10am because the manager is travelling.'",
        "must": [["mkutano", "kikao"], ["alhamisi"], ["saa nne", "saa 4"], ["meneja", "mkuu"], ["safari"]],
        "forbid": ["saa kumi asubuhi", "saa tano asubuhi", "saa kumi na moja", "saa kumi"],
        "why": "Swahili time reckoning: 10am is 'saa nne asubuhi'. A model that says 'saa kumi' is wrong in a way English never reveals.",
    },
    {
        "id": "t3", "category": "translation_en_sw",
        "prompt": "Translate into Swahili: 'I am sorry for the delay. Your order will arrive tomorrow before noon.'",
        "must": [["samahani", "nasikitika", "pole"], ["chelewa", "kuchelewa", "ucheleweshaji"], ["kesho"], ["adhuhuri", "kabla ya saa sita", "mchana"]],
        "forbid": [],
        "why": "Apology plus time commitment. 'Adhuhuri' is the correct word for noon.",
    },
    {
        "id": "t4", "category": "translation_en_sw",
        "prompt": "Translate into Swahili: 'Kindly note that we do not accept returns after seven days.'",
        "must": [["tafadhali", "kumbuka", "zindua"], ["haturudishi", "hatukubali", "kurudisha"], ["siku saba", "siku 7"]],
        "forbid": [],
        "why": "Policy language with a negative. Negation is where translations quietly invert meaning.",
    },
    # ---------------- translation SW -> EN ----------------
    {
        "id": "t5", "category": "translation_sw_en",
        "prompt": "Translate into English: 'Nitalipa kesho asubuhi, lakini nitahitaji risiti.'",
        "must": [["pay", "will pay"], ["tomorrow"], ["morning"], ["receipt"], ["need", "require"]],
        "forbid": [],
        "why": "Future tense plus a condition. 'Nitalipa' is future, not past.",
    },
    {
        "id": "t6", "category": "translation_sw_en",
        "prompt": "Translate into English: 'Mtoto alianguka jana jioni na tumempeleka hospitalini.'",
        "must": [["child", "kid"], ["fell", "fall"], ["yesterday"], ["evening"], ["hospital"], ["took", "taken", "brought"]],
        "forbid": [],
        "why": "Past tense chain, plus 'tumempeleka' which is present perfect (we have taken).",
    },
    {
        "id": "t7", "category": "translation_sw_en",
        "prompt": "Translate into English: 'Kama hutalipa kwa muda, tutafunga akaunti yako.'",
        "must": [["if"], ["pay"], ["time", "on time", "in time"], ["close", "suspend", "deactivate"], ["account"]],
        "forbid": [],
        "why": "Conditional with a consequence. Getting the consequence wrong is a business risk.",
    },
    {
        "id": "t8", "category": "translation_sw_en",
        "prompt": "Translate into English: 'Tafadhali njoo na kitambulisho chako na nakala ya bima.'",
        "must": [["please"], ["bring", "come with"], ["id", "identification", "identity card"], ["copy"], ["insurance"]],
        "forbid": [],
        "why": "Practical instruction with two required documents.",
    },
    # ---------------- register ----------------
    {
        "id": "r1", "category": "register",
        "prompt": "Andika barua fupi ya Kiswahili kwa mkuu wa shule, ukimwomba ruhusa ya siku mbili kwa sababu ya dharura ya familia. Tumia lugha rasmi.",
        "must": [["heshima", "naomba", "tafadhali"], ["siku mbili", "siku 2"], ["dharura"], ["familia", "nyumbani"]],
        "forbid": ["yo", "maze", "bro", "chief", "sema tu", "niaje"],
        "why": "Formal letter register. A model that slips into slang has failed even if the meaning survives.",
    },
    {
        "id": "r2", "category": "register",
        "prompt": "Mjomba wako amekutumia ujumbe mfupi wa kukualika chakula cha jioni. Jibu kwa Kiswahili cha kawaida, cha kirafiki, kifupi.",
        "must": [["asante", "nashukuru", "sawa"], ["nitakuja", "nipo", "nitafika", "sawa"]],
        "forbid": ["kwa heshima na taadhima", "ninakualika rasmi", "naomba kukubaliwa"],
        "why": "Informal register. The failure mode is over-formality, the mirror of r1.",
    },
    {
        "id": "r3", "category": "register",
        "prompt": "Mteja amekasirika kwa Kiswahili na kutishia kuacha huduma. Andika jibu la kumpolea, la heshima, lenye hatua moja ya kutatua tatizo.",
        "must": [["samahani", "pole", "nasikitika"], ["tutatua", "tutashughulikia", "nitakusaidia", "tutarekebisha"], ["mteja", "huduma"]],
        "forbid": ["sio kosa letu", "wewe", "umeshindwa"],
        "why": "De-escalation. Also tests whether the model blames the customer.",
    },
    {
        "id": "r4", "category": "register",
        "prompt": "Eleza kwa Kiswahili rasmi jinsi ya kufungua akaunti mpya, kwa hatua tatu.",
        "must": [["hatua"], ["kitambulisho", "kutambulisha", "utambulisho"], ["akaunti"], ["taarifa", "maelezo", "fomu"]],
        "forbid": [],
        "why": "Instructional register with structure. Tests whether steps stay clear in Swahili.",
    },
    # ---------------- code switching (Sheng) ----------------
    {
        "id": "s1", "category": "code_switching",
        "prompt": "Translate this Sheng into plain English: 'Niko poa, lakini nimechoka na hii hustle ya kutafuta job.'",
        "must": [["fine", "good", "okay", "ok"], ["tired", "exhausted", "fed up"], ["hustle", "grind", "work", "search"], ["job", "work"]],
        "forbid": [],
        "why": "Common Sheng. 'Niko poa' is not 'I am cool' literally; it means I am fine.",
    },
    {
        "id": "s2", "category": "code_switching",
        "prompt": "A customer texts: 'Bro, hiyo order yangu iko aje? Nimewait long sana.' Reply in the same register, briefly, and give a real status.",
        "must": [["samahani", "pole", "sorry"], ["order", "oda"], ["iko", "safari", "njiani", "leo", "kesho", "saa"]],
        "forbid": ["kwa heshima na taadhima", "Dear Sir"],
        "why": "Mirroring the customer's register instead of answering a casual text formally.",
    },
    {
        "id": "s3", "category": "code_switching",
        "prompt": "What does 'ameamua kujitegemea' mean, and how would a Nairobi speaker more casually say the same idea?",
        "must": [["decided", "chose", "ameamua"], ["independent", "self-reliant", "kujitegemea", "hustle", "own"], ["casual", "sheng", "kawaida", "informal"]],
        "forbid": [],
        "why": "Explaining meaning across registers, not just translating.",
    },
    {
        "id": "s4", "category": "code_switching",
        "prompt": "Explain the difference in tone between 'naomba' and 'nipe' in Swahili, with an example of when each is acceptable.",
        "must": [["naomba", "request", "polite"], ["nipe", "command", "direct", "blunt"], ["tafadhali", "heshima", "polite", "formal"]],
        "forbid": [],
        "why": "Politeness is grammatical in Swahili. A model that treats them as synonyms is unsafe for support work.",
    },
    # ---------------- local context ----------------
    {
        "id": "l1", "category": "local_context",
        "prompt": "A Kenyan customer says: 'Nimelipa kupitia M-Pesa lakini hakuna message.' What are the two most likely explanations, and what should support ask them to check first?",
        "must": [["m-pesa", "mpesa"], ["confirmation", "message", "sms"], ["number", "phone", "namba"], ["transaction", "receipt", "code", "sms"]],
        "forbid": [],
        "why": "Local payment rails. Generic 'contact your bank' answers fail the item.",
    },
    {
        "id": "l2", "category": "local_context",
        "prompt": "Explain to a Kenyan customer in Swahili why their Fuliza balance keeps growing even when they are not spending.",
        "must": [["fuliza"], ["deni", "loan", "mkopo"], ["riba", "ada", "charges", "interest"], ["kila siku", "siku", "muda"]],
        "forbid": [],
        "why": "A real, common Kenyan product question. Requires local product knowledge, not translation.",
    },
    {
        "id": "l3", "category": "local_context",
        "prompt": "Write a 2-sentence Swahili SMS reminding customers that rent is due, suitable for a Nairobi landlord, without being threatening.",
        "must": [["kodi", "rent"], ["kumbuka", "kumbusha", "tafadhali"], ["tarehe", "siku", "mwisho", "leo", "kesho"]],
        "forbid": ["tutakufukuza", "mahakama", "polisi"],
        "why": "Local housing context plus tone safety. Threatening language is a fail.",
    },
    {
        "id": "l4", "category": "local_context",
        "prompt": "A customer in Kisumu asks whether the shop delivers there. Answer in Swahili, mention delivery cost and time, and say what you need from them to confirm.",
        "must": [["kisumu"], ["usafirishaji", "delivery", "kutuma", "kuleta"], ["gharama", "bei", "cost"], ["muda", "siku", "saa"], ["mahali", "anwani", "location", "namba"]],
        "forbid": [],
        "why": "Combines geography, logistics and a follow-up question. Tests practical usefulness.",
    },
]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def normalise(text):
    return re.sub(r"\s+", " ", str(text).lower())


def score_item(item, answer):
    text = normalise(answer)
    if not text.strip():
        return 0.0, ["empty answer"], []

    hits, misses = 0, []
    for group in item["must"]:
        if any(normalise(alt) in text for alt in group):
            hits += 1
        else:
            misses.append(group[0])

    coverage = hits / len(item["must"]) if item["must"] else 0.0

    red = [flag for flag in item.get("forbid", []) if normalise(flag) in text]
    penalty = 0.0
    if red:
        penalty = 0.5 * len(red)

    # Very short answers cannot be trusted even if keywords appear
    if len(text.split()) < 4:
        penalty += 0.25

    return max(0.0, round(coverage - penalty, 2)), misses, red


def score_model(items, answers):
    per_item = {}
    by_category = {}
    for item in items:
        answer = answers.get(item["id"], "")
        value, misses, red = score_item(item, answer)
        per_item[item["id"]] = {
            "category": item["category"], "score": value,
            "missing": misses, "red_flags": red,
        }
        by_category.setdefault(item["category"], []).append(value)

    overall = sum(v["score"] for v in per_item.values()) / len(items)
    category_scores = {c: round(sum(v) / len(v), 2) for c, v in by_category.items()}
    failures = [iid for iid, v in per_item.items() if v["score"] < 0.5]

    return {
        "overall": round(overall, 2),
        "categories": category_scores,
        "items": per_item,
        "failed_items": failures,
        "answered": sum(1 for a in answers.values() if str(a).strip()),
    }


def report(results):
    print("=" * 70)
    print("SWAHILI / SHENG EVALUATION".center(70))
    print("=" * 70)
    ranked = sorted(results.items(), key=lambda kv: kv[1]["overall"], reverse=True)
    print(f"{'model':<16}{'overall':>9}{'answered':>10}{'failed':>8}")
    for name, r in ranked:
        print(f"{name:<16}{r['overall']:>9.2f}{r['answered']:>10}{len(r['failed_items']):>8}")

    print("\nBY CATEGORY")
    cats = sorted({c for r in results.values() for c in r["categories"]})
    header = "model".ljust(16) + "".join(c[:11].rjust(12) for c in cats)
    print(header)
    for name, r in ranked:
        row = name.ljust(16) + "".join(f"{r['categories'].get(c, 0):>12.2f}" for c in cats)
        print(row)

    for name, r in ranked:
        if not r["failed_items"]:
            continue
        print(f"\nFAILURES - {name}")
        for iid in r["failed_items"]:
            detail = r["items"][iid]
            note = ", ".join(detail["missing"][:4]) or "short answer"
            flags = f" | RED FLAGS: {', '.join(detail['red_flags'])}" if detail["red_flags"] else ""
            print(f"  {iid:<4} {detail['category']:<20} missing: {note}{flags}")
    print("\n" + "=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Score model answers against the Swahili/Sheng eval set.")
    ap.add_argument("answers", nargs="?", help="JSON file of {model: {item_id: answer}}")
    ap.add_argument("--items", action="store_true", help="print the prompts to send to a model")
    ap.add_argument("--json", dest="out", help="write full results as JSON")
    args = ap.parse_args()

    if args.items:
        for item in ITEMS:
            print(f"[{item['id']}] ({item['category']})")
            print(item["prompt"])
            print()
        return

    if not args.answers:
        ap.print_help()
        return

    with open(args.answers, encoding="utf-8") as fh:
        raw = json.load(fh)

    results = {name: score_model(ITEMS, answers) for name, answers in raw.items()}
    report(results)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
        print(f"Full results written to {args.out}")


if __name__ == "__main__":
    main()
