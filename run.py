"""Command line entry point:  python run.py "find 500 tech creators from Pakistan" """
import argparse

from intent import parse_prompt
from pipeline import run_search

ap = argparse.ArgumentParser()
ap.add_argument("prompt", help='e.g. "find 200 female AI creators from India over 10k followers #aitools"')
ap.add_argument("--visible", action="store_true", help="show the browser window (headless is the default)")
ap.add_argument("--no-llm", action="store_true", help="skip LLM analysis (country/gender can't be verified)")
ap.add_argument("--strict", action="store_true", help="keep only profiles whose country/gender is verified")
ap.add_argument("--no-verify-country", action="store_true",
                help="skip the extra per-profile country search (faster, less accurate)")
ap.add_argument("--find-country", action="store_true",
                help="web-search each profile to fill the country column (slower; always on for single profiles)")
ap.add_argument("--from-file", help="check usernames from this text/CSV file instead of searching for them")
ap.add_argument("--max-cooldowns", type=int, default=6, help="how many times to wait out an Instagram block")
ap.add_argument("--cooldown", type=int, default=300, help="seconds to wait after a block")
ap.add_argument("--discover-only", action="store_true",
                help="only collect usernames (needs SERPER_API_KEY) into a file; check profiles later with --from-file")
ap.add_argument("--max-api-calls", type=int, help="stop after this many Serper requests (cost cap)")
ap.add_argument("--proxy", help='e.g. "http://1.2.3.4:8080"')
ap.add_argument("--out", default="results")
args = ap.parse_args()

intent = parse_prompt(args.prompt, use_llm=not args.no_llm)
intent.strict = intent.strict or args.strict
print("Understood request:", intent.to_dict())

if args.max_api_calls:
    import discover
    discover.API_CALL_LIMIT = args.max_api_calls

if args.discover_only:
    import os
    from pathlib import Path
    import discover
    from pipeline import _slug
    if not os.environ.get("SERPER_API_KEY"):
        raise SystemExit("Set SERPER_API_KEY first (see serper.dev), then run again.")
    target = intent.count * 2          # roughly half of the usernames fail the filters / are unreadable
    names, seen, ctx = [], set(), {}
    for u in discover.iter_candidates(lambda: None, intent, seen, ctx):
        names.append(u)
        seen.add(u.lower())
        if len(names) % 200 == 0:
            print(f"[discover-only] {len(names)} usernames, {discover.API_CALLS} API calls so far")
        if len(names) >= target:
            break
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{_slug(intent)}-usernames.txt"
    path.write_text("\n".join(names), encoding="utf-8")
    print(f"\n{len(names)} usernames -> {path}   ({discover.API_CALLS} Serper requests used)")
    print(f'Next: python run.py "{args.prompt}" --from-file {path} --strict')
    raise SystemExit(0)

results, stats = run_search(intent, headless=not args.visible, use_llm=not args.no_llm,
                            out_dir=args.out, proxy=args.proxy,
                            verify_country=not args.no_verify_country,
                            find_country=args.find_country, usernames_file=args.from_file,
                            max_cooldowns=args.max_cooldowns, cooldown_seconds=args.cooldown)
print(f"\n{len(results)} profiles -> {stats['csv']}")
print("Stats:", stats)