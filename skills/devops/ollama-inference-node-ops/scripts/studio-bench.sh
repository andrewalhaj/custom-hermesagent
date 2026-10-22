#!/bin/bash
# Standard Mac Studio inference probe: cold load + 3 warm runs.
# Reports eval t/s (real work), prompt t/s (ingestion), load time.
# Usage: ./studio-bench.sh [model] [num_predict]
#   ./studio-bench.sh qwen2.5-32b-32k 120
# IMPORTANT for fair comparison: on a freshly-built model, run ONCE first as a
# throwaway warm-up (this script's "cold-load" run does that), then read runs 2-3.
# Compare warm-vs-warm against references/performance-baseline-*.md — never warm-vs-cold.

API="${OLLAMA_API:-http://${STUDIO_OLLAMA_HOST:?set STUDIO_OLLAMA_HOST (host:port) or OLLAMA_API}}"
MODEL="${1:-qwen2.5-32b-32k}"
NP="${2:-120}"
PROMPT="Explain the tradeoff between context window size and parallel inference slots in two sentences."

echo "=== $MODEL (API=$API, num_predict=$NP) ==="
for i in 1 2 3; do
  label="warm"; [ "$i" -eq 1 ] && label="cold-or-firstwarm"
  resp=$(curl -sf -m 120 "$API/api/generate" \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"$PROMPT\",\"stream\":false,\"options\":{\"num_predict\":$NP}}")
  echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
ec=d.get('eval_count',0); ed=d.get('eval_duration',1)/1e9
pc=d.get('prompt_eval_count',0); pd=d.get('prompt_eval_duration',1)/1e9
ld=d.get('load_duration',0)/1e9
print(f'Run $i ($label): eval={ec/ed:.1f} t/s | prompt={pc/pd:.1f} t/s | load={ld:.2f}s | eval_tokens={ec}')
"
done

echo ""
echo "=== VRAM residency (size_vram per model, vs 56GB cap) ==="
curl -sf -m 5 "$API/api/ps" | python3 -c "
import sys,json
d=json.load(sys.stdin); tot=0
for m in d.get('models',[]):
    gb=m.get('size_vram',0)/1e9; tot+=gb
    print(f\"  {m['name']:<24} {gb:.1f}GB | ctx={m.get('context_length','?')}\")
print(f'  {\"TOTAL\":<24} {tot:.1f}GB / 56GB cap ({tot/56*100:.0f}%) | headroom {56-tot:.1f}GB')
"
