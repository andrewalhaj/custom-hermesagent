#!/usr/bin/env python3
"""Backfill: MERGE all Supabase rows into Neo4j with idempotent MERGE.
Pipes Supabase rows via stdin (JSON array from REST API).
Reads Neo4j credentials from ~/.hermes/.env.
source = 'supabase-backfill' to distinguish from webhook-created nodes.
"""
import os, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()

def read_env(path=HERMES_HOME / ".env"):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env

cfg = read_env()
NEO4J_URI = cfg.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = cfg.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = cfg.get("NEO4J_PASSWORD", "")
# Aura Free: database name = instance ID (NOT "neo4j")
NEO4J_DB = cfg.get("NEO4J_DATABASE") or cfg.get("NEO4J_USERNAME", "neo4j")

from neo4j import GraphDatabase

rows = json.load(sys.stdin)
print(f"Loaded {len(rows)} rows from Supabase", file=sys.stderr)
print(f"Neo4j: {NEO4J_URI} db={NEO4J_DB}", file=sys.stderr)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
now = datetime.now(timezone.utc).isoformat()
created, existed = 0, 0

with driver.session(database=NEO4J_DB) as session:
    for i, row in enumerate(rows):
        text = row["text"]
        nid = hashlib.sha256(text.encode()).hexdigest()[:16]
        priority = row.get("priority", "normal")

        result = session.run(
            """
            MERGE (f:Fact {id: $id})
            ON CREATE SET f.text = $text, f.source = 'supabase-backfill',
                          f.priority = $priority, f.stored_at = $stored_at
            ON MATCH SET f.priority = $priority
            RETURN f.source as source
            """,
            id=nid, text=text, priority=priority, stored_at=now,
        )
        src = result.single()["source"]
        if src == "supabase-backfill":
            created += 1
        else:
            existed += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(rows)}  created={created} existed={existed}", file=sys.stderr)

driver.close()
print(json.dumps({"created": created, "existed": existed, "total": len(rows)}))
