"""provenance(): label rules R1-R7 on fixture rows (design note 20261019 section 5)."""
import json, os, sys

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge.provenance import provenance, rank

SID = "20261012_093015_ab12cd34"


@pytest.mark.parametrize("row,label", [
    ({"source": "session-distill"}, "digest"),                              # R1 source
    ({"tags": ["session-digest", "active-session"]}, "digest"),             # R1 tag
    ({"text": "Session: dispatcher fix | 2026-10-12 decided X"}, "digest"),  # R1 shape
    ({"tags": ["decision", "session-heartbeat", "cli"], "source": SID}, "extracted"),  # R2 tag
    ({"source": SID}, "extracted"),                                         # R2 session-id source
    ({"source": "memory_tool"}, "explicit"),                                # R3
    ({"source": "memory-offload"}, "imported"),                             # R4
    ({"source": "backfill-20260708"}, "imported"),
    ({"source": "github-webhook"}, "imported"),
    ({"source": "e2e-test"}, "imported"),
    ({"source": "knowledge-maintenance-restore"}, "unknown"),               # R5
    ({"tags": ["session-end"]}, "unknown"),                                 # R6
    ({"tags": json.dumps(["offload"])}, "unknown"),                         # R6, jsonb as string
    ({}, "unknown"),                                                        # R7
    ({"tags": None, "source": None, "text": None}, "unknown"),
    ({"tags": ["prov:explicit", "session-heartbeat"]}, "explicit"),         # stamped tag wins
    ({"tags": ["prov:bogus"]}, "unknown"),                                  # invalid stamp ignored
])
def test_rules(row, label):
    assert provenance(row) == label


def test_rank_order():
    assert rank({"source": "memory_tool"}) == rank({"source": "e2e-test"}) > rank({"source": "session-distill"}) \
        == rank({"source": SID}) > rank({})
