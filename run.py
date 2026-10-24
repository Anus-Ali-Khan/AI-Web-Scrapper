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
ap.add_argument("--proxy", help='e.g. "http://1.2.3.4:8080"')
ap.add_argument("--out", default="results")
args = ap.parse_args()

intent = parse_prompt(args.prompt, use_llm=not args.no_llm)
intent.strict = intent.strict or args.strict
print("Understood request:", intent.to_dict())

results, stats = run_search(intent, headless=not args.visible, use_llm=not args.no_llm,
                            out_dir=args.out, proxy=args.proxy,
                            verify_country=not args.no_verify_country,
                            find_country=args.find_country)
print(f"\n{len(results)} profiles -> {stats['csv']}")
print("Stats:", stats)