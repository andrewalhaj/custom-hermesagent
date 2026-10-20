#!/usr/bin/env python3
"""Extended retrieval benchmarks: Recall@10 + Multi-hop recall + Graph traversal.

Usage:
  python3 ~/.hermes/scripts/benchmark_extended.py              # all benchmarks
  python3 ~/.hermes/scripts/benchmark_extended.py --json        # machine-readable
  python3 ~/.hermes/scripts/benchmark_extended.py --skip-recall # multi-hop only
  python3 ~/.hermes/scripts/benchmark_extended.py --skip-graph  # recall only

Requires NEO4J_URI / NEO4J_PASSWORD env vars.
See knowledge-store skill for full documentation.
"""
# This is a symlink target — the canonical script lives at ~/.hermes/scripts/benchmark_extended.py
# See: skill_view('knowledge-store') → v3.5 section for benchmark results and design rationale.
