"""
M-Pesa spending analyser - standard library only, runs anywhere.

THE PROBLEM
    An M-Pesa statement is hundreds of lines of noise. Nobody reads it, so
    nobody knows where their money actually goes. Existing apps want your PIN
    or a subscription; spreadsheets need you to type everything in by hand.

WHAT THIS DOES
    Takes the statement (CSV export, or lines pasted from the SMS/PDF), cleans
    it, categorises every transaction, and answers the questions people
    actually ask: how much came in, how much left, what ate the money, which
    merchants repeat, how much went to betting or Fuliza, and what is left.

    Everything stays on the machine. No upload, no login, no PIN.

USAGE
    python mpesa_analyser.py statement.csv
    python mpesa_analyser.py statement.csv --csv clean.csv
    python mpesa_analyser.py --demo

WHY IT IS BUILT THIS WAY
    No pandas, no pdf libraries, no internet. It runs on a phone in Termux, on
    a Windows laptop with nothing installed, and on a machine with no Python
    packages at all. That is deliberate: the people who need this cannot
    install things.
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Categorisation rules. Ordered: first match wins.
# --------------------------------------------------------------------------- #
RULES = [
    ("Betting", r"\b(sportpesa|betika|odibets|mozzart|1xbet|betway|dafabet|shabiki|bet)\b"),
    ("Loans", r"\b(fuliza|m-shwari|mshwari|kcb mpesa|tala|branch|okash|zenka|shark|loan|overdraft)\b"),
    ("Utilities", r"\b(kplc|kenya power|nairobi water|nwasco|water|token|dstv|gotv|startimes|zuku|safaricom home|faiba|internet)\b"),
    ("Rent", r"\b(rent|landlord|caretaker|plot)\b"),
    ("Transport", r"\b(uber|bolt|little|matatu|bus|fuel|petrol|shell|total|rubis|parking|fare)\b"),
    ("Food", r"\b(glovo|jumia food|uber eats|restaurant|hotel|cafe|butchery|supermarket|naivas|quickmart|carrefour|chandarana|kiosk|mama|food)\b"),
    ("Airtime", r"\b(airtime|safaricom|bundles?|data)\b"),
    ("Health", r"\b(hospital|clinic|pharmacy|chemist|dawa|medical|nhif|sha)\b"),
    ("Education", r"\b(school|college|university|fees|tuition|academy|moringa|sigma|books?|exam)\b"),
    ("Church", r"\b(church|parish|mass|harambee|tithe|offertory|sadaka|mission)\b"),
    ("Shopping", r"\b(jumia|kilimall|shop|store|market|boutique|clothing)\b"),
    ("Savings", r"\b(sacco|mwalimu|investment|chama|savings|deposit to|fixed)\b"),
]

DEBIT_WORDS = ("paid", "sent", "withdraw", "buy", "payment", "charge", "airtime", "paybill", "till")
CREDIT_WORDS = ("received", "deposit", "refund", "reversal", "salary")


def categorise(description):
    """Match the rules, tolerating run-together merchant names like KPLCPREPAID."""
    text = description.lower()
    for name, pattern in RULES:
        if re.search(pattern, text):
            return name
        if re.search(pattern.rstrip("\\b"), text):   # leading boundary only
            return name
    return "Other"


def clean_amount(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("KES", "").replace("Ksh", "").strip()
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-()")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(raw):
    """Accept a bare date or a date with a time, e.g. '01/10/2026 09:12'."""
    text = str(raw or "").strip()
    if not text:
        return None
    candidates = [text]
    if " " in text:
        candidates.append(text.split(" ")[0])          # drop the time
    if "T" in text:
        candidates.append(text.split("T")[0])          # ISO datetime
    for candidate in candidates:
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y",
                    "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(candidate.strip(), fmt)
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
HEADER_HINTS = ("receipt", "date", "details", "amount", "balance", "paid in", "withdrawn")


def parse_csv(path):
    """Parse an M-Pesa CSV export. Column names vary, so we hunt for them."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        all_rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not all_rows:
        return rows

    header_index = None
    for i, row in enumerate(all_rows[:12]):
        joined = " ".join(str(c).lower() for c in row)
        if sum(1 for h in HEADER_HINTS if h in joined) >= 2:
            header_index = i
            break

    if header_index is None:
        return parse_lines("\n".join(",".join(r) for r in all_rows))

    header = [str(c).strip().lower() for c in all_rows[header_index]]

    def find(*names):
        for name in names:
            for idx, col in enumerate(header):
                if name in col:
                    return idx
        return None

    i_date = find("date", "time")
    i_desc = find("details", "description", "narrative", "transaction")
    i_paid_in = find("paid in", "credit", "money in")
    i_withdrawn = find("withdrawn", "debit", "money out")
    i_amount = find("amount")
    i_balance = find("balance")

    for row in all_rows[header_index + 1:]:
        if len(row) < 3:
            continue
        cell = lambda i: row[i] if (i is not None and i < len(row)) else ""
        date = parse_date(cell(i_date))
        desc = str(cell(i_desc)).strip()
        paid_in = clean_amount(cell(i_paid_in))
        withdrawn = clean_amount(cell(i_withdrawn))
        amount = clean_amount(cell(i_amount))
        balance = clean_amount(cell(i_balance))

        if paid_in is None and withdrawn is None and amount is not None:
            if amount < 0:
                withdrawn, paid_in = abs(amount), None
            else:
                paid_in, withdrawn = amount, None
        if date is None or not desc:
            continue
        rows.append({
            "date": date, "description": desc,
            "paid_in": abs(paid_in) if paid_in else 0.0,
            "withdrawn": abs(withdrawn) if withdrawn else 0.0,
            "balance": balance,
        })
    return rows


LINE_RE = re.compile(
    r"(?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}).*?"
    r"(?P<amount>-?[\d,]+\.\d{2})"
)


def parse_lines(text):
    """Fallback: pull date + amount out of pasted statement lines."""
    rows = []
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        date = parse_date(m.group("date"))
        amount = clean_amount(m.group("amount"))
        if date is None or amount is None:
            continue
        desc = line[:m.start()].strip(" ,|\t") or line
        low = desc.lower()
        if amount < 0:
            is_out = True
        elif any(w in low for w in CREDIT_WORDS) and not any(w in low for w in DEBIT_WORDS):
            is_out = False
        else:
            is_out = True   # in a statement line, an unsigned amount is almost always a payment
        if is_out:
            rows.append({"date": date, "description": desc, "paid_in": 0.0,
                         "withdrawn": abs(amount), "balance": None})
        else:
            rows.append({"date": date, "description": desc, "paid_in": amount,
                         "withdrawn": 0.0, "balance": None})
    return rows


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def analyse(rows):
    if not rows:
        return None
    for r in rows:
        r["category"] = categorise(r["description"])
        r["direction"] = "in" if r["paid_in"] > 0 else "out"

    total_in = sum(r["paid_in"] for r in rows)
    total_out = sum(r["withdrawn"] for r in rows)

    by_category = defaultdict(float)
    for r in rows:
        if r["withdrawn"]:
            by_category[r["category"]] += r["withdrawn"]

    by_month = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    for r in rows:
        key = r["date"].strftime("%Y-%m")
        by_month[key]["in"] += r["paid_in"]
        by_month[key]["out"] += r["withdrawn"]
        by_month[key]["count"] += 1

    merchants = Counter()
    merchant_value = defaultdict(float)
    for r in rows:
        if not r["withdrawn"]:
            continue
        name = re.sub(r"\s+", " ", r["description"])[:44].strip()
        merchants[name] += 1
        merchant_value[name] += r["withdrawn"]

    out_rows = [r for r in rows if r["withdrawn"]]
    biggest = sorted(out_rows, key=lambda r: r["withdrawn"], reverse=True)[:5]
    recurring = [(m, c, merchant_value[m]) for m, c in merchants.items() if c >= 3]
    recurring.sort(key=lambda t: t[2], reverse=True)

    return {
        "count": len(rows),
        "first": min(r["date"] for r in rows),
        "last": max(r["date"] for r in rows),
        "total_in": total_in,
        "total_out": total_out,
        "net": total_in - total_out,
        "avg_out": total_out / len(out_rows) if out_rows else 0.0,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)),
        "by_month": dict(sorted(by_month.items())),
        "biggest": biggest,
        "recurring": recurring[:6],
        "betting": by_category.get("Betting", 0.0),
        "loans": by_category.get("Loans", 0.0),
    }


def money(value):
    return f"{value:,.0f}"


def report(result, top=6):
    if not result:
        print("No transactions found. Check the file is an M-Pesa statement export.")
        return
    span = f"{result['first']:%d %b %Y} to {result['last']:%d %b %Y}"
    print("=" * 62)
    print("M-PESA SPENDING REPORT".center(62))
    print(span.center(62))
    print("=" * 62)
    print(f"Transactions analysed : {result['count']}")
    print(f"Money in              : KES {money(result['total_in'])}")
    print(f"Money out             : KES {money(result['total_out'])}")
    print(f"Net                   : KES {money(result['net'])}")
    print(f"Average spend per txn : KES {money(result['avg_out'])}")

    print("\nWHERE IT WENT")
    total = result["total_out"] or 1
    for name, value in list(result["by_category"].items())[:top]:
        share = value / total * 100
        bar = "#" * max(1, int(share / 2.5))
        print(f"  {name:<12} KES {money(value):>10}  {share:4.1f}%  {bar}")

    print("\nMONTH BY MONTH")
    for month, v in result["by_month"].items():
        print(f"  {month}   in KES {money(v['in']):>10}   out KES {money(v['out']):>10}   {v['count']:>3} txns")

    if result["biggest"]:
        print("\nBIGGEST SINGLE PAYMENTS")
        for r in result["biggest"]:
            print(f"  {r['date']:%d %b}  KES {money(r['withdrawn']):>9}  {r['description'][:44]}")

    if result["recurring"]:
        print("\nREPEATING PAYMENTS (3 or more times)")
        for name, count, value in result["recurring"]:
            print(f"  {count}x  KES {money(value):>10}  {name[:44]}")

    flags = []
    if result["betting"]:
        flags.append(f"KES {money(result['betting'])} went to betting")
    if result["loans"]:
        flags.append(f"KES {money(result['loans'])} went to loans or Fuliza")
    if result["net"] < 0:
        flags.append("more left the account than came in over this period")
    if flags:
        print("\nWORTH LOOKING AT")
        for f in flags:
            print(f"  - {f}")

    print("\n" + "=" * 62)
    print("Calculated on this machine. Nothing was uploaded.")


def write_clean_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "description", "category", "direction", "paid_in", "withdrawn", "balance"])
        for r in sorted(rows, key=lambda x: x["date"]):
            writer.writerow([
                r["date"].strftime("%Y-%m-%d"), r["description"], r["category"],
                r["direction"], f"{r['paid_in']:.2f}", f"{r['withdrawn']:.2f}",
                "" if r["balance"] is None else f"{r['balance']:.2f}",
            ])
    return path


# --------------------------------------------------------------------------- #
# Demo data - so the tool can be shown without anyone's real statement
# --------------------------------------------------------------------------- #
def demo_rows():
    raw = [
        ("01/10/2026", "Funds received from JOHN KAMAU", 12000, 0),
        ("02/10/2026", "Pay Bill to KPLC prepaid token", 0, 1000),
        ("02/10/2026", "Buy goods NAIVAS SUPERMARKET", 0, 2450),
        ("03/10/2026", "Customer Transfer to BOLT ride", 0, 320),
        ("04/10/2026", "Buy goods SPORTPESA", 0, 500),
        ("05/10/2026", "Pay Bill to RENT landlord Mwangi", 0, 9000),
        ("06/10/2026", "Airtime purchase Safaricom", 0, 100),
        ("07/10/2026", "Buy goods NAIVAS SUPERMARKET", 0, 1980),
        ("08/10/2026", "Fuliza repayment", 0, 450),
        ("09/10/2026", "Funds received from MARY WANJIKU", 5000, 0),
        ("10/10/2026", "Buy goods SPORTPESA", 0, 750),
        ("11/10/2026", "Pay Bill to KPLC prepaid token", 0, 500),
        ("12/10/2026", "Customer Transfer to BOLT ride", 0, 280),
        ("13/10/2026", "Buy goods NAIVAS SUPERMARKET", 0, 3100),
        ("14/10/2026", "Church harambee contribution", 0, 1000),
        ("15/10/2026", "Funds received from EMPLOYER salary", 25000, 0),
        ("16/10/2026", "Buy goods SPORTPESA", 0, 1000),
        ("17/10/2026", "Pay Bill to ZUKU internet", 0, 3000),
        ("18/10/2026", "Customer Transfer to BOLT ride", 0, 350),
        ("19/10/2026", "Buy goods QUICKMART", 0, 1600),
        ("20/10/2026", "M-Shwari loan disbursement received", 2000, 0),
        ("21/10/2026", "Buy goods SPORTPESA", 0, 400),
        ("22/10/2026", "Airtime purchase Safaricom", 0, 200),
        ("23/10/2026", "Customer Transfer to BOLT ride", 0, 300),
    ]
    return [{"date": parse_date(d), "description": desc, "paid_in": float(i),
             "withdrawn": float(o), "balance": None} for d, desc, i, o in raw]


def main():
    ap = argparse.ArgumentParser(description="Clean and analyse an M-Pesa statement, offline.")
    ap.add_argument("statement", nargs="?", help="CSV export of the M-Pesa statement")
    ap.add_argument("--csv", dest="out_csv", help="also write a clean CSV here")
    ap.add_argument("--demo", action="store_true", help="run on sample data to see the output")
    ap.add_argument("--paste", action="store_true", help="read statement lines from the keyboard (Ctrl+D to finish)")
    args = ap.parse_args()

    if args.demo:
        rows = demo_rows()
    elif args.paste:
        rows = parse_lines(sys.stdin.read())
    elif args.statement:
        path = Path(args.statement)
        if not path.exists():
            sys.exit(f"No such file: {path}")
        rows = parse_csv(path) if path.suffix.lower() in (".csv", ".txt") else parse_lines(path.read_text(errors="replace"))
    else:
        ap.print_help()
        return

    result = analyse(rows)
    report(result)
    if args.out_csv and rows:
        print(f"\nClean CSV written to {write_clean_csv(rows, args.out_csv)}")


if __name__ == "__main__":
    main()
