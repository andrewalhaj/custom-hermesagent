#!/usr/bin/env python3
"""CLI: build centroids.pkl + anchors.pkl from taxonomy.json, optionally
calibrate the reject thresholds and run the retrieval eval.

    python3 build_centroids.py [--taxonomy taxonomy.json] [--output-dir .]
                               [--calibrate] [--eval-facts]

Run from the knowledge/ directory (or anywhere — paths default relative to this
file so the generated artifacts always land in knowledge/).
"""
import os
import sys
import argparse

# Allow running as `python3 knowledge/build_centroids.py` from scripts root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_ROOT = os.path.dirname(_HERE)
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from knowledge import classifier


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--taxonomy', default=os.path.join(_HERE, 'taxonomy.json'),
                    help='path to taxonomy.json (default: knowledge/taxonomy.json)')
    ap.add_argument('--output-dir', default=_HERE,
                    help='where to write centroids.pkl / anchors.pkl (default: knowledge/)')
    ap.add_argument('--calibrate', action='store_true',
                    help='run the risk-coverage calibration sweep over labeled facts')
    ap.add_argument('--eval-facts', action='store_true',
                    help='run eval_benchmark() and print P@5 after building')
    args = ap.parse_args()

    taxonomy = args.taxonomy
    if not os.path.isabs(taxonomy) and not os.path.exists(taxonomy):
        taxonomy = os.path.join(_HERE, os.path.basename(taxonomy))
    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    print(f"== build_centroids ==")
    print(f"  taxonomy : {taxonomy}")
    print(f"  output   : {out_dir}")
    print(f"  calibrate: {args.calibrate}")

    classifier.build_centroids(taxonomy, out_dir, calibrate=args.calibrate)

    if args.eval_facts:
        print("\n== eval_benchmark ==")
        try:
            from knowledge.eval import eval_benchmark
            res = eval_benchmark()
            if res.get('error'):
                print(f"  eval skipped: {res['error']} ({res.get('path')})")
            else:
                print(f"  P@5={res['p@5']}  MRR={res['MRR']}  "
                      f"({res['passed']}/{res['total']} queries)")
        except Exception as e:
            print(f"  eval failed: {e}")

    print("\nDone.")


if __name__ == '__main__':
    main()
