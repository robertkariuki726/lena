"""
Memory auditor for AI assistants.

THE PROBLEM
    Long-lived assistants accumulate memories that contradict each other. A
    preference recorded in March ("never attach the CV") and reversed in June
    ("always attach the CV") both sit in the store, and the assistant picks
    whichever it retrieves first. It then sounds certain while being wrong.
    There is no error message for this. It just quietly gets the person wrong.

WHAT THIS DOES
    Reads a memory export (JSON: list of objects with id, content, kind,
    category) and reports:
      1. DUPLICATES      - two memories saying nearly the same thing
      2. CONTRADICTIONS  - same topic, opposite instruction
      3. SUPERSESSIONS   - a dated memory that appears to override an older one
      4. STALE CLAIMS    - "currently"/"now" claims older than a threshold
      5. ORPHANS         - memories whose category exists nowhere else
    Every finding comes with a suggested action, not just a complaint.

USAGE
    python memory_auditor.py memories.json
    python memory_auditor.py memories.json --json report.json
    python memory_auditor.py --demo

NO DEPENDENCIES. Standard library only, so it runs anywhere.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from itertools import combinations

# --------------------------------------------------------------------------- #
# Polarity: pairs of words that mean opposite things in an instruction.
# --------------------------------------------------------------------------- #
# (word in A, word in B, weak) - weak pairs are common in ordinary prose, so they
# need more shared context before we call them a contradiction.
OPPOSITES = [
    ("always", "never", False),
    ("hourly", "daily", False),
    ("hourly", "weekly", False),
    ("daily", "weekly", False),
    ("remote", "on-site", False),
    ("remote", "onsite", False),
    ("english only", "sheng", False),
    ("no sheng", "sheng", False),
    ("stop", "continue", False),
    ("pause", "activate", False),
    ("activate", "deactivate", False),
    ("include", "exclude", True),
    ("do", "do not", True),
]

STALE_MARKERS = ("currently", "right now", "at the moment", "now ", "presently", "as of")
DATE_RE = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

STOPWORDS = set("""a an the and or but if then than that this these those of to in on for with without
is are was were be been being it its as at by from into over under again more most some such no not only
own same so too very can will just should now i you he she they we my your his her their our me him them
us do does did done have has had having""".split())


def stem(word):
    """Very light stemming so 'mixed'/'mix' and 'roles'/'role' line up."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


SHORT_BUT_MEANINGFUL = {"cv", "ai", "ml", "hr", "id", "db", "us", "uk"}


def tokens(text):
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {stem(w) for w in words
            if w not in STOPWORDS and (len(w) > 2 or w in SHORT_BUT_MEANINGFUL)}


def similarity(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_date(text):
    m = ISO_RE.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = DATE_RE.search(text)
    if m:
        months = ["jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec"]
        try:
            return datetime(int(m.group(3)), months.index(m.group(2).lower()[:3]) + 1, int(m.group(1)))
        except (ValueError, IndexError):
            return None
    return None


def polarity_conflict(text_a, text_b):
    """Return the opposing word pair if two texts sit on opposite sides of it."""
    a, b = text_a.lower(), text_b.lower()
    for left, right, weak in OPPOSITES:
        if (left in a and right in b) or (right in a and left in b):
            # ignore the case where both texts contain both words (not a conflict)
            if not (left in a and right in a and left in b and right in b):
                return (left, right, weak)
    return None


def topic_overlap(text_a, text_b, threshold=0.30):
    return similarity(text_a, text_b) >= threshold


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def audit(memories, dup_threshold=0.78, overlap_threshold=0.30, stale_days=120, today=None):
    today = today or datetime.now()
    findings = {"duplicates": [], "contradictions": [], "supersessions": [],
                "stale": [], "orphans": []}

    for a, b in combinations(memories, 2):
        ta, tb = str(a.get("content", "")), str(b.get("content", ""))
        sim = similarity(ta, tb)

        # Polarity first: two memories can look almost identical and still mean
        # the opposite thing. That is the failure mode that matters, so it must
        # not be swallowed by duplicate detection.
        pair = polarity_conflict(ta, tb)
        same_category = (a.get("category") and a.get("category") == b.get("category"))
        shared = len(tokens(ta) & tokens(tb))
        weak = pair[2] if pair else False
        need = 3 if weak else 2
        same_topic = topic_overlap(ta, tb, overlap_threshold) or (same_category and shared >= need)
        if pair and same_topic:
            date_a, date_b = find_date(ta), find_date(tb)
            newer = None
            if date_a and date_b and date_a != date_b:
                newer = a.get("id") if date_a > date_b else b.get("id")
            findings["contradictions"].append({
                "a": a.get("id"), "b": b.get("id"),
                "opposites": (pair[0], pair[1]),
                "overlap": round(sim, 2),
                "newer": newer,
                "suggested_action": ("keep the newer one and replace the older with it"
                                     if newer else "decide which one is true, then forget the other"),
                "preview_a": ta[:110], "preview_b": tb[:110],
            })
            continue

        if sim >= dup_threshold:
            findings["duplicates"].append({
                "a": a.get("id"), "b": b.get("id"),
                "similarity": round(sim, 2),
                "category": a.get("category") or b.get("category"),
                "suggested_action": "merge into one memory, then forget the weaker one",
                "preview_a": ta[:110], "preview_b": tb[:110],
            })

    for m in memories:
        text = str(m.get("content", ""))
        lower = text.lower()
        d = find_date(text)
        if d:
            age = (today - d).days
            if age > stale_days:
                findings["supersessions"].append({
                    "id": m.get("id"), "dated": d.strftime("%Y-%m-%d"),
                    "age_days": age,
                    "suggested_action": "check whether a later memory overrides this, then replace it",
                    "preview": text[:110],
                })
        if any(marker in lower for marker in STALE_MARKERS) and not d:
            findings["stale"].append({
                "id": m.get("id"),
                "suggested_action": "undated 'currently' claim: add the date or re-verify it",
                "preview": text[:110],
            })

    counts = defaultdict(int)
    for m in memories:
        counts[m.get("category") or "uncategorised"] += 1
    for m in memories:
        if counts[m.get("category") or "uncategorised"] == 1:
            findings["orphans"].append({
                "id": m.get("id"), "category": m.get("category"),
                "suggested_action": "single-memory category: merge into a broader one",
                "preview": str(m.get("content", ""))[:110],
            })

    findings["summary"] = {
        "memories": len(memories),
        "duplicates": len(findings["duplicates"]),
        "contradictions": len(findings["contradictions"]),
        "supersessions": len(findings["supersessions"]),
        "stale": len(findings["stale"]),
        "orphans": len(findings["orphans"]),
        "health": health_score(findings, len(memories)),
    }
    return findings


def health_score(findings, total):
    if not total:
        return 0
    penalties = (len(findings["contradictions"]) * 12
                 + len(findings["duplicates"]) * 6
                 + len(findings["supersessions"]) * 3
                 + len(findings["stale"]) * 4)
    return max(0, min(100, 100 - penalties))


def report(findings):
    s = findings["summary"]
    print("=" * 66)
    print("MEMORY AUDIT".center(66))
    print("=" * 66)
    print(f"Memories read     : {s['memories']}")
    print(f"Health score      : {s['health']}/100")
    print(f"Contradictions    : {s['contradictions']}   <- the dangerous ones")
    print(f"Duplicates        : {s['duplicates']}")
    print(f"Old dated claims  : {s['supersessions']}")
    print(f"Undated 'currently': {s['stale']}")
    print(f"Single-use tags   : {s['orphans']}")

    if findings["contradictions"]:
        print("\nCONTRADICTIONS - the assistant will get the person wrong here")
        for f in findings["contradictions"][:8]:
            print(f"\n  [{f['opposites'][0]} vs {f['opposites'][1]}]  overlap {f['overlap']}")
            print(f"    A: {f['preview_a']}")
            print(f"    B: {f['preview_b']}")
            print(f"    -> {f['suggested_action']}")

    if findings["duplicates"]:
        print("\nDUPLICATES")
        for f in findings["duplicates"][:6]:
            print(f"\n  similarity {f['similarity']}  ({f['category']})")
            print(f"    A: {f['preview_a']}")
            print(f"    B: {f['preview_b']}")
            print(f"    -> {f['suggested_action']}")

    if findings["supersessions"]:
        print("\nOLD DATED CLAIMS (over 120 days)")
        for f in findings["supersessions"][:6]:
            print(f"  {f['dated']} ({f['age_days']}d): {f['preview']}")

    if findings["stale"]:
        print("\nUNDATED 'CURRENTLY' CLAIMS")
        for f in findings["stale"][:6]:
            print(f"  {f['preview']}")

    print("\n" + "=" * 66)


# --------------------------------------------------------------------------- #
# Demo: the kind of contradictions real assistants actually accumulate
# --------------------------------------------------------------------------- #
def demo_memories():
    return [
        {"id": "m1", "category": "Job Strategy", "kind": "instruction",
         "content": "2 Nov 2026: Always attach the CV as a PDF to every job application."},
        {"id": "m2", "category": "Job Strategy", "kind": "instruction",
         "content": "5 Oct 2026: Never attach a CV or portfolio link to automated applications."},
        {"id": "m3", "category": "Job Strategy", "kind": "instruction",
         "content": "Applications must run hourly, local Kenya and remote roles included."},
        {"id": "m4", "category": "Job Strategy", "kind": "instruction",
         "content": "Applications run daily at 9am, local Kenya and remote roles included."},
        {"id": "m5", "category": "Job Strategy", "kind": "preference",
         "content": "Remote roles are the primary focus of the job search."},
        {"id": "m6", "category": "Job Strategy", "kind": "preference",
         "content": "Nairobi on-site roles are the primary focus of the job search."},
        {"id": "m7", "category": "Style", "kind": "preference",
         "content": "Speak English only in chat, no Sheng or Swahili mixed in."},
        {"id": "m8", "category": "Style", "kind": "preference",
         "content": "Sound like a modern Kenyan youth and mix in some Sheng."},
        {"id": "m9", "category": "Style", "kind": "preference",
         "content": "Keep replies short and warm, avoid lists in ordinary chat."},
        {"id": "m10", "category": "Style", "kind": "preference",
         "content": "Keep replies short and warm; avoid numbered lists in ordinary chat."},
        {"id": "m11", "category": "Vocation", "kind": "decision",
         "content": "1 Oct 2026: The priestly vocation is the highest priority above the job search."},
        {"id": "m12", "category": "Personal Finance", "kind": "fact",
         "content": "Currently saving 20 percent of every payment into a SACCO account."},
        {"id": "m13", "category": "Voice App", "kind": "fact",
         "content": "The desktop voice app has never run successfully on his laptop."},
    ]


def main():
    ap = argparse.ArgumentParser(description="Audit an assistant's memory store for contradictions.")
    ap.add_argument("memories", nargs="?", help="JSON file: list of {id, content, kind, category}")
    ap.add_argument("--json", dest="out", help="write the full findings as JSON here")
    ap.add_argument("--demo", action="store_true", help="audit sample memories")
    ap.add_argument("--stale-days", type=int, default=120)
    args = ap.parse_args()

    if args.demo:
        memories = demo_memories()
    elif args.memories:
        with open(args.memories, encoding="utf-8") as fh:
            memories = json.load(fh)
        if isinstance(memories, dict):
            memories = memories.get("memories", [])
    else:
        ap.print_help()
        return

    findings = audit(memories, stale_days=args.stale_days)
    report(findings)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(findings, fh, indent=2)
        print(f"Full report written to {args.out}")


if __name__ == "__main__":
    main()
