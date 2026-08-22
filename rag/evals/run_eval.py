"""Run deterministic retrieval recall@5 against the seeded golden set."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from plotline_rag.config import CACHE_DIR, PROJECT_ROOT
from plotline_rag.schema import SearchRequest
from plotline_rag.search.hybrid import HybridSearch


def run(golden_path: Path, root: Path = CACHE_DIR) -> float:
    cases = yaml.safe_load(Path(golden_path).read_text(encoding="utf-8"))
    engine = HybridSearch(root=root)
    hits = 0
    print("#  hit  expected       top result")
    print("-- ---  -------------  -------------")
    for number, case in enumerate(cases, start=1):
        results = engine.search(SearchRequest(query=case["query"], k=5))
        returned = [result.source_id for result in results]
        expected = set(case["expected"])
        hit = bool(expected.intersection(returned))
        hits += int(hit)
        top = returned[0] if returned else "<none>"
        print(f"{number:>2} {'yes' if hit else 'NO ':>3}  {case['expected'][0]:<13}  {top}")
    recall = hits / len(cases) if cases else 0.0
    print(f"\nrecall@5: {hits}/{len(cases)} = {recall:.1%}")
    return recall


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=PROJECT_ROOT / "evals" / "golden.yaml")
    parser.add_argument("--root", type=Path, default=CACHE_DIR)
    args = parser.parse_args()
    if run(args.golden, args.root) < 0.8:
        raise SystemExit("recall@5 is below the required 8/10 threshold")


if __name__ == "__main__":
    main()

