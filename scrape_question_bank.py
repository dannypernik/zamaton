#!/usr/bin/env python3
"""
Scrape the College Board SAT Suite Educator Question Bank
(https://satsuiteeducatorquestionbank.collegeboard.org/digital/results)
into a content/meta JSON pair per section (see CONTENT_FIELDS/META_FIELDS).

Each section writes two files: a *content* file with just what's needed to
serve and score a question during a practice test, and a *meta* file with the
taxonomy and the explanation, neither of which is wanted until after a test is
submitted. generate.py merges the pair back into one record per question.

Reverse-engineered from the site's own JS bundle - it's a public,
unauthenticated JSON API (qbank-api.collegeboard.org) with no login wall,
CAPTCHA, or rate-limit headers observed. This script mirrors exactly the
requests the site's own frontend makes.

About a quarter of questions predate the digital item bank ("legacy" items,
identified by having an ibn but no external_id) and get a different detail
response shape, handled separately by build_record_legacy(). For legacy SPR
questions the API exposes no structured "accepted answers" list (unlike
modern items), so the correct value is best-effort scraped from the
rationale's "The correct answer is ..." sentence - it may miss equivalent
forms (e.g. decimal equivalents of a fraction) that the modern API would
include. A warning is printed for any such item.

Usage:
  python3 scrape_question_bank.py --section math   # → math-question-bank-{content,meta}.json
  python3 scrape_question_bank.py --section rw     # → reading-question-bank-{content,meta}.json
  python3 scrape_question_bank.py --section math --domains Algebra,"Advanced Math" --limit 20
  python3 scrape_question_bank.py --section math --ids ac472881,3f5a3602
  python3 scrape_question_bank.py --section math --ids-file wanted_ids.txt
"""

import argparse
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API_BASE = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank"
LOOKUP_URL = f"{API_BASE}/lookup"
GET_QUESTIONS_URL = f"{API_BASE}/digital/get-questions"
GET_QUESTION_URL = f"{API_BASE}/digital/get-question"

ASSESSMENT_IDS = {
    "sat": 99,
    "psat_nmsqt_10": 100,
    "psat_8_9": 102,
}

SECTION_TO_TEST_ID = {
    "rw": 1,
    "math": 2,
}
SECTION_TO_LOOKUP_KEY = {
    "rw": "R&W",
    "math": "Math",
}

DIFFICULTY_MAP = {"E": "Easy", "M": "Medium", "H": "Hard"}

# About a quarter of questions predate the digital item bank ("legacy" items).
# They have no external_id, only an ibn (item bank number). The detail
# endpoint still serves them if you pass the ibn in the external_id field,
# but the response shape - and field names - are completely different.
LEGACY_TYPE_MAP = {"Multiple Choice": "mcq", "SPR": "spr"}

_FRACTION_ALT_RE = re.compile(r"the fraction (-?[\d.]+) over (-?[\d.]+)", re.IGNORECASE)
_IMG_ALT_RE = re.compile(r'<img[^>]*\balt="([^"]*)"[^>]*>')
_TAG_RE = re.compile(r"<[^>]+>")
_CORRECT_ANSWER_RE = re.compile(r"correct answer is\s+(.*?)(?:\.\s|\.$)", re.IGNORECASE | re.DOTALL)

_NUM_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_DENOM_WORDS = {
    "half": 2, "halves": 2, "third": 3, "thirds": 3, "quarter": 4, "quarters": 4,
    "fourth": 4, "fourths": 4, "fifth": 5, "fifths": 5, "sixth": 6, "sixths": 6,
    "seventh": 7, "sevenths": 7, "eighth": 8, "eighths": 8, "ninth": 9, "ninths": 9,
    "tenth": 10, "tenths": 10,
}
_SPELLED_FRACTION_RE = re.compile(r"^(?P<num>[a-z]+)[\s-]+(?P<denom>[a-z]+)$", re.IGNORECASE)


def _spelled_fraction_to_text(alt):
    match = _SPELLED_FRACTION_RE.match(alt.strip())
    if not match:
        return None
    num = _NUM_WORDS.get(match.group("num").lower())
    denom = _DENOM_WORDS.get(match.group("denom").lower())
    if num is None or denom is None:
        return None
    return f"{num}/{denom}"


def _img_alt_to_text(match):
    alt = match.group(1)
    frac = _FRACTION_ALT_RE.fullmatch(alt.strip())
    if frac:
        return f"{frac.group(1)}/{frac.group(2)}"
    spelled = _spelled_fraction_to_text(alt)
    if spelled:
        return spelled
    return alt


def _rationale_to_plain_text(rationale_html):
    text = _IMG_ALT_RE.sub(_img_alt_to_text, rationale_html)
    text = _TAG_RE.sub("", text)
    return html.unescape(text)


def extract_legacy_spr_correct(rationale_html):
    """Best-effort extraction of the accepted answer(s) from a legacy SPR
    item's rationale text, e.g. "The correct answer is 180." or "...is
    either 10/3, 15/4, or 25/6.". Unlike the modern API, the legacy endpoint
    doesn't expose a structured list of equivalent accepted forms (e.g.
    decimal equivalents of a fraction) - only what's spelled out in the
    rationale's opening sentence.
    """
    text = _rationale_to_plain_text(rationale_html)
    match = _CORRECT_ANSWER_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.sub(r"^either\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r",?\s+or\s+", " or ", value)
    value = re.sub(r",\s+", " or ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (scrape_question_bank.py; educational use)",
}


def fetch_lookup(session):
    resp = session.get(LOOKUP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()["lookupData"]


def fetch_question_list(session, asmt_event_id, test_id, domain_codes):
    body = {
        "asmtEventId": asmt_event_id,
        "test": test_id,
        "domain": ",".join(domain_codes),
    }
    resp = session.post(GET_QUESTIONS_URL, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_question_detail(session, lookup_id, retries=3):
    # The API uses one field, external_id, for both modern external ids and
    # legacy ibns - which one it is just changes the shape of the response.
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.post(
                GET_QUESTION_URL,
                headers=HEADERS,
                json={"external_id": lookup_id},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def build_record(meta, detail):
    answer_options = detail.get("answerOptions") or []
    answers = {
        chr(65 + i): f"<div>{opt.get('content', '')}</div>"
        for i, opt in enumerate(answer_options)
    }

    correct_answer = detail.get("correct_answer")
    keys = detail.get("keys")
    if correct_answer:
        correct = correct_answer[0]
    elif keys:
        correct = keys[0]
    else:
        correct = None

    difficulty_cd = meta.get("difficulty", "")
    return {
        "type": detail.get("type"),
        "domain": meta.get("primary_class_cd_desc"),
        "skill": meta.get("skill_desc"),
        "difficulty": DIFFICULTY_MAP.get(difficulty_cd, difficulty_cd),
        "prompt": f"<div>{detail.get('stimulus') or ''}</div>",
        "question": f"<div>{detail.get('stem') or ''}</div>",
        "answers": answers,
        "correct": correct,
        "explanation": f"<div>{detail.get('rationale') or ''}</div>",
    }


def build_record_legacy(meta, detail):
    answer = detail.get("answer") or {}
    style = answer.get("style")
    record_type = LEGACY_TYPE_MAP.get(style)
    if record_type is None:
        print(f"WARNING: unknown legacy answer style {style!r} for {meta['questionId']} (ibn={meta.get('ibn')})", file=sys.stderr)
        record_type = (style or "").lower()

    choices = answer.get("choices") or {}
    # choices is a dict keyed "a","b","c","d" - sort so option order is preserved
    answers = {
        letter.upper(): f"<div>{body.get('body', '')}</div>"
        for letter, body in sorted(choices.items())
    }

    if record_type == "mcq":
        correct_choice = answer.get("correct_choice")
        correct = correct_choice.upper() if correct_choice else None
    elif record_type == "spr":
        correct = extract_legacy_spr_correct(answer.get("rationale") or "")
        if correct is None:
            print(f"WARNING: could not extract correct answer for legacy SPR {meta['questionId']} (ibn={meta.get('ibn')})", file=sys.stderr)
    else:
        correct = None

    difficulty_cd = meta.get("difficulty", "")
    return {
        "type": record_type,
        "domain": meta.get("primary_class_cd_desc"),
        "skill": meta.get("skill_desc"),
        "difficulty": DIFFICULTY_MAP.get(difficulty_cd, difficulty_cd),
        "prompt": f"<div>{detail.get('body') or ''}</div>",
        "question": f"<div>{detail.get('prompt') or ''}</div>",
        "answers": answers,
        "correct": correct,
        "explanation": f"<div>{answer.get('rationale') or ''}</div>",
    }


def resolve_domain_codes(lookup_data, section, requested_domains):
    domains = lookup_data["domain"][SECTION_TO_LOOKUP_KEY[section]]
    if not requested_domains:
        return [d["primaryClassCd"] for d in domains]

    by_name = {d["text"].lower(): d["primaryClassCd"] for d in domains}
    codes = []
    for name in requested_domains:
        code = by_name.get(name.lower())
        if not code:
            available = ", ".join(d["text"] for d in domains)
            raise SystemExit(f"Unknown domain {name!r}. Available: {available}")
        codes.append(code)
    return codes


def load_requested_ids(ids_arg, ids_file_arg):
    ids = set()
    if ids_arg:
        ids.update(part.strip() for part in ids_arg.split(",") if part.strip())
    if ids_file_arg:
        with open(ids_file_arg, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ids.add(line)
    return ids or None


def load_existing(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── Output split ──────────────────────────────────────────────────────────────
# Each section is written as two files. CONTENT_FIELDS is everything needed to
# put a question in front of a student and score it, so a practice test can be
# served from that file alone; META_FIELDS is everything else — the taxonomy
# used for browsing/reporting, plus the explanation, which is only wanted after
# a test is submitted. Explanation is 24% of the math bytes and 46% of the RW
# bytes, so keeping it out of the serving file is most of the win.
# Moving a field between these two tuples is all it takes to redraw the line;
# generate.py merges the pair back together and doesn't care where a field sits.
CONTENT_FIELDS = ("type", "prompt", "question", "answers", "correct")
META_FIELDS    = ("domain", "skill", "difficulty", "explanation")

# --section is the API's own name for the section; the bank files have always
# used "reading" rather than "rw", so keep the filenames the repo expects.
SECTION_TO_BANK_PREFIX = {"rw": "reading", "math": "math"}


def split_record(record):
    """Partition one scraped record into its (content, meta) halves.

    A field absent from the record is omitted rather than written as null, so
    the two files stay a clean partition of whatever the API actually returned.
    """
    content = {k: record[k] for k in CONTENT_FIELDS if k in record}
    meta    = {k: record[k] for k in META_FIELDS    if k in record}
    return content, meta


def write_split(results, content_path, meta_path):
    """Write the {qid: record} map out as the content/meta file pair."""
    content_out, meta_out = {}, {}
    for qid, record in results.items():
        content_out[qid], meta_out[qid] = split_record(record)
    for path, data in ((content_path, content_out), (meta_path, meta_out)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assessment", default="sat", choices=sorted(ASSESSMENT_IDS), help="Assessment to scrape (default: sat)")
    parser.add_argument("--section", default="math", choices=sorted(SECTION_TO_TEST_ID), help="Section to scrape (default: math)")
    parser.add_argument("--domains", default=None, help="Comma-separated domain names to restrict to (default: all domains in the section)")
    parser.add_argument("--ids", default=None, help="Comma-separated question IDs to scrape (the short hex IDs used as keys in the output JSON, e.g. ac472881). Skips everything else in the section.")
    parser.add_argument("--ids-file", default=None, help="Path to a file with one question ID per line (blank lines and #-comments ignored)")
    parser.add_argument("--out", default=None, help=f"Content JSON path — the practice-test serving file, holding {', '.join(CONTENT_FIELDS)} (default: <bank>-question-bank-content.json)")
    parser.add_argument("--out-meta", default=None, help=f"Meta JSON path, holding {', '.join(META_FIELDS)} (default: alongside --out as <bank>-question-bank-meta.json)")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent detail-fetch workers (default: 8)")
    parser.add_argument("--limit", type=int, default=None, help="Only scrape the first N questions (for testing)")
    parser.add_argument("--resume", action="store_true", help="Skip question IDs already present in --out")
    parser.add_argument("--checkpoint-every", type=int, default=50, help="Write output to disk every N completed questions (default: 50)")
    args = parser.parse_args()

    bank = SECTION_TO_BANK_PREFIX[args.section]
    content_path = args.out or f"{bank}-question-bank-content.json"
    if args.out_meta:
        meta_path = args.out_meta
    elif args.out:
        # Derive from --out so an explicit path still yields a matching pair:
        # foo-content.json → foo-meta.json, otherwise foo.json → foo-meta.json.
        stem, ext = os.path.splitext(args.out)
        stem = stem[: -len("-content")] if stem.endswith("-content") else stem
        meta_path = f"{stem}-meta{ext or '.json'}"
    else:
        meta_path = f"{bank}-question-bank-meta.json"
    requested_domains = [d.strip() for d in args.domains.split(",")] if args.domains else None
    requested_ids = load_requested_ids(args.ids, args.ids_file)

    session = requests.Session()

    print("Fetching lookup data (assessments/sections/domains/skills)...", file=sys.stderr)
    lookup_data = fetch_lookup(session)

    asmt_event_id = ASSESSMENT_IDS[args.assessment]
    test_id = SECTION_TO_TEST_ID[args.section]
    domain_codes = resolve_domain_codes(lookup_data, args.section, requested_domains)
    print(f"Assessment={args.assessment} ({asmt_event_id}), section={args.section} ({test_id}), domains={domain_codes}", file=sys.stderr)

    print("Fetching question list...", file=sys.stderr)
    question_list = fetch_question_list(session, asmt_event_id, test_id, domain_codes)

    # dedupe by questionId, keep first occurrence
    seen = {}
    for meta in question_list:
        qid = meta["questionId"]
        if qid not in seen:
            seen[qid] = meta
    question_list = list(seen.values())
    print(f"Found {len(question_list)} unique questions.", file=sys.stderr)

    if requested_ids:
        question_list = [m for m in question_list if m["questionId"] in requested_ids]
        found_ids = {m["questionId"] for m in question_list}
        missing = requested_ids - found_ids
        if missing:
            print(f"WARNING: {len(missing)} requested id(s) not found in {args.section}/{args.assessment} domains={domain_codes}: {sorted(missing)}", file=sys.stderr)
        print(f"Matched {len(question_list)}/{len(requested_ids)} requested ids.", file=sys.stderr)

    if args.limit:
        question_list = question_list[: args.limit]

    results = {}
    if args.resume:
        # Recombine the pair so a resumed run rewrites both halves intact —
        # reading only the content file would blank the meta of finished ids.
        existing_content = load_existing(content_path)
        existing_meta    = load_existing(meta_path)
        for qid in existing_content.keys() | existing_meta.keys():
            results[qid] = {**existing_content.get(qid, {}), **existing_meta.get(qid, {})}
        before = len(question_list)
        question_list = [m for m in question_list if m["questionId"] not in results]
        print(f"Resuming: {before - len(question_list)} already done, {len(question_list)} remaining.", file=sys.stderr)

    completed = 0
    total = len(question_list)

    def worker(meta):
        is_legacy = not meta.get("external_id")
        lookup_id = meta["ibn"] if is_legacy else meta["external_id"]
        detail = fetch_question_detail(session, lookup_id)
        record = build_record_legacy(meta, detail) if is_legacy else build_record(meta, detail)
        return meta["questionId"], record

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(worker, meta): meta for meta in question_list}
        for future in as_completed(futures):
            meta = futures[future]
            try:
                qid, record = future.result()
                results[qid] = record
            except Exception as e:
                print(f"FAILED {meta['questionId']}: {e}", file=sys.stderr)
            completed += 1
            if completed % 25 == 0 or completed == total:
                print(f"{completed}/{total}", file=sys.stderr)
            if completed % args.checkpoint_every == 0:
                write_split(results, content_path, meta_path)

    write_split(results, content_path, meta_path)

    print(f"Wrote {len(results)} questions to {content_path} "
          f"({', '.join(CONTENT_FIELDS)}) and {meta_path} "
          f"({', '.join(META_FIELDS)})", file=sys.stderr)


if __name__ == "__main__":
    main()
