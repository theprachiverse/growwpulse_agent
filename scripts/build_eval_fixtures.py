"""Builds the frozen Phase 6 eval fixtures under `evals/`.

    uv run python scripts/build_eval_fixtures.py [--week 2026-W42]

Outputs (all already PII-scrubbed except the synthetic injection set):
  evals/fixtures/reviews_sample.jsonl   ~500 CleanReview rows from a real run (stratified, frozen)
  evals/fixtures/pii_injection.jsonl    synthetic PII cases, tier A (must pass) and tier B (tracked)
  evals/fixtures/pii_negatives.jsonl    text that must NOT be scrubbed
  evals/golden/theme_labels_v1.csv      DRAFT labels from keyword rules, to be confirmed by people

The golden CSV is a starting point: `labeller=heuristic-draft` rows count as unverified until a
person edits the label and sets `labeller` to their name. See Docs/eval.md section 3.2.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

GOLDEN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "customer_support",
        (
            "customer care",
            "customer support",
            "support",
            "agent",
            "bot",
            "ticket",
            "helpline",
            "no one respond",
            "ai chat",
        ),
    ),
    (
        "charges_brokerage",
        (
            "brokerage",
            "charges",
            "charge",
            "hidden fee",
            "dp charge",
            "fees",
            "commission",
            "deducted",
        ),
    ),
    (
        "payments_withdrawals",
        (
            "upi",
            "withdraw",
            "add money",
            "deposit",
            "payment",
            "refund",
            "bank account",
            "money not",
        ),
    ),
    (
        "mf_onboarding_kyc",
        (
            "mutual fund",
            "sip",
            "kyc",
            "account opening",
            "onboard",
            "verification",
            "demat account",
        ),
    ),
    (
        "app_stability_login",
        (
            "crash",
            "hang",
            "freez",
            "login",
            "log in",
            "otp",
            "face id",
            "fingerprint",
            "biometric",
            "slow app",
            "not opening",
            "loading",
        ),
    ),
    (
        "trading_execution",
        (
            "order",
            "f&o",
            "option",
            "stop loss",
            "stoploss",
            "trade",
            "trading",
            "chart",
            "price",
            "execution",
            "gtt",
            "square off",
            "stock",
        ),
    ),
]


def label_review(text: str) -> str:
    low = text.lower()
    best, best_hits = "other", 0
    for label, words in GOLDEN_KEYWORDS:
        hits = sum(low.count(w) for w in words)
        if hits > best_hits:
            best, best_hits = label, hits
    return best


# ---------------------------------------------------------------- PII cases
PHONES = ["+91 98765 43210", "09876543210", "(9876543210)", "98765-43210", "+919876543210"]
PANS = ["ABCDE1234F", "abcde1234f", "PQRSX9876L"]
AADHAARS = ["1234 5678 9012", "1234-5678-9012", "987654321098"]
EMAILS = ["a.b+groww@gmail.com", "rahul.sharma92@yahoo.co.in", "support_help@outlook.com"]
UPIS = ["rahul@okaxis", "9876543210@ybl", "priya.k@paytm"]
IFSCS = ["HDFC0001234", "SBIN0004567", "ICIC0000123"]
ACCOUNTS = ["1208160012345678", "50100123456789"]
URLS = ["https://wa.me/919876543210", "http://example.com/refer?id=abc123"]
IDS = ["GRW2410AB93XZ"]
FRAMES = [
    "Please call {v} and fix my pending withdrawal, nobody responds in the app.",
    "{v} is what I gave support but they still did not reply to my complaint.",
    "My problem is not solved yet, contact details are {v}.",
    "Mera issue solve nahi hua, number {v} hai, please call back jaldi.",
    "After the update the app keeps crashing; reach me on {v}",
]

TIER_A = {
    "PHONE": PHONES,
    "PAN": PANS,
    "AADHAAR": AADHAARS,
    "EMAIL": EMAILS,
    "UPI": UPIS,
    "IFSC": IFSCS,
    "ACCOUNT_NUMBER": ACCOUNTS,
    "URL": URLS,
    "ID": IDS,
}
TIER_B = [
    ("PERSON", "my name is Rahul Sharma and my withdrawal is stuck", "Rahul Sharma"),
    ("PERSON", "agent Priya Nair was rude on the call", "Priya Nair"),
    ("OBFUSCATED_DIGITS", "my number is 9 8 7 6 5 4 3 2 1 0 call me", "9 8 7 6 5 4 3 2 1 0"),
    (
        "OBFUSCATED_DIGITS",
        "nine eight seven six five four three two one zero is my phone",
        "nine eight seven six five four three two one zero",
    ),
]

NEGATIVES = [
    "The brokerage was ₹1,500 on my last trade which is too high.",
    "I paid Rs. 20 per order and it is still deducted twice.",
    "Version 5.12.3 crashes on every launch since the update.",
    "Updated on 12/10/2026 and nothing works after that.",
    "Nifty 24500 CE strike price jumped suddenly at 9:15 am.",
    "I lost 25,000 rupees because the stop loss did not trigger.",
    "App rating is 4.2 on the store but it is 1 star for me.",
    "Order was placed at 10:32 and rejected within 3 seconds.",
    "Used Android 14 and iOS 18.1 both, same problem on each.",
    "SIP of ₹500 per month failed for the third time in a row.",
    "The chart refresh takes 15 to 20 seconds every single time.",
    "Margin shown is 1.5x but the order says insufficient funds.",
    "Waited 45 minutes in the queue for customer care to pick up.",
    "Price of the stock moved from 1,234.50 to 1,198.00 before execution.",
    "Support ticket closed in 2 days without any resolution at all.",
]


def build_pii_cases() -> list[dict]:
    rng = random.Random(7)
    cases: list[dict] = []
    n = 0
    for ptype, values in TIER_A.items():
        for value in values:
            frames = rng.sample(FRAMES, 2)
            for frame in frames:
                n += 1
                cases.append(
                    {
                        "id": f"A{n:03d}",
                        "tier": "A",
                        "category": ptype,
                        "text": frame.format(v=value),
                        "values": [value],
                        "expected_types": [ptype],
                    }
                )
    # Hidden by zero-width characters (cleaner strips them before scrubbing)
    cases.append(
        {
            "id": "A900",
            "tier": "A",
            "category": "PHONE",
            "text": "call me on 98765​43210 about my withdrawal please",
            "values": ["9876543210"],
            "expected_types": ["PHONE"],
        }
    )
    for j, (ptype, text, value) in enumerate(TIER_B, start=1):
        cases.append(
            {
                "id": f"B{j:03d}",
                "tier": "B",
                "category": ptype,
                "text": text,
                "values": [value],
                "expected_types": [ptype],
            }
        )
    return cases


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default="2026-W42")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--golden", type=int, default=200)
    args = ap.parse_args()

    src = ROOT / "data" / "runs" / args.week / "reviews_clean.jsonl"
    rows = [
        json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    rng = random.Random(42)

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    app = by_source.get("app_store", [])
    n_app = min(len(app), int(args.sample * 0.15))
    sample = rng.sample(app, n_app) + rng.sample(by_source["play_store"], args.sample - n_app)
    sample.sort(key=lambda r: r["rid"])
    out = ROOT / "evals" / "fixtures"
    write_jsonl(out / "reviews_sample.jsonl", sample)

    write_jsonl(out / "pii_injection.jsonl", build_pii_cases())
    write_jsonl(
        out / "pii_negatives.jsonl",
        [{"id": f"N{i:03d}", "text": t} for i, t in enumerate(NEGATIVES, 1)],
    )

    golden_rows = rng.sample(sample, args.golden)
    gpath = ROOT / "evals" / "golden" / "theme_labels_v1.csv"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    with gpath.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rid", "text", "golden_theme", "labeller", "notes"])
        for r in golden_rows:
            w.writerow(
                [r["rid"], r["clean_text"], label_review(r["clean_text"]), "heuristic-draft", ""]
            )

    print(
        f"reviews_sample: {len(sample)}  pii cases: {len(build_pii_cases())}  golden: {len(golden_rows)}"
    )


if __name__ == "__main__":
    main()
