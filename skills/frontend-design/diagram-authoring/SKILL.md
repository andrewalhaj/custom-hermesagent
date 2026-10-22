---
name: diagram-authoring
description: "Use when drawing or adapting architecture diagrams."
sector_path: ["user", "writing", "diagrams"]
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [diagrams, mermaid, kroki, architecture, azure, html, png]
    created_by: agent
load_when:
  - "user asks for a diagram, flowchart, or architecture picture"
  - "user sends a vendor reference diagram and asks to adapt it to his environment"
  - "user asks to make a diagram prettier / more professional"
  - "a report or deck needs a rendered PNG/SVG of a Mermaid block"
---

# Diagram Authoring (Andrew's conventions)

## When to Use

Any request for a diagram or architecture picture: drawing one from scratch, rendering a Mermaid block to PNG/SVG for a report, adapting a vendor reference diagram (Microsoft CAF, Well-Architected) to one of his environments, or upgrading an existing diagram when he says "more professional / prettier". Deck-embedded diagrams additionally follow `revealjs-deck-authoring`.

## Output tiers

Two output tiers. Pick by what he asked for; he escalates from one to the other in the same thread.

| Tier | When | How |
|---|---|---|
| **Mermaid** | Default for any diagram in a `.md`, quick visuals, anything he may edit himself | Mermaid block in the report + Kroki PNG |
| **Styled HTML → PNG** | "More professional / prettier", vendor-style slides (Microsoft CAF look), anything going in front of leadership | Single HTML file with CSS cards, rendered via `browser_exec` CDP screenshot at 2× (`references/html-diagram-recipe.md`) |

Standing rules (all tiers):
- Mermaid only, never ASCII art. Never hand him a text-box drawing.
- Shareable artifacts strip real IPs/hostnames (LH01 = Mini, LH02 = Studio) in labels, notes AND the diagram source.
- Every rendered image gets a `vision_analyze` pass before delivery, asked as 2–3 yes/no questions (rows horizontal? any clipping? any line crossing text?). The model over-describes; constrain the question or it narrates instead of checking.
- Deliver the PNG inline (`MEDIA:` path) plus the source path so he can edit it. Source lives next to the PNG under `~/reports/`.

## Adapting a vendor reference diagram (CAF, Well-Architected, etc.)

1. **Ask which environment** when he has more than one in play (the work tenant vs. the Hermes homelab) — the diagram's vendor branding does not settle it. One `clarify` call with both questions: which environment, and what is actually in it.
2. **Inventory from memory first, then him.** `memory_search` the environment; take his answer as the inventory; mark everything else **unknown** in the artifact rather than filling in plausible components. He prefers a sparse honest diagram over a complete invented one.
3. **Right-size before mapping.** A reference diagram drawn for an enterprise maps onto a small tenant as slots that are "already exists", "turn on, don't build", or "skip until a trigger". Say which per slot (table in the `.md`), with the trigger that would un-skip it.
4. **Write the `.md` first** (architecture → `.md`), with the Mermaid block, a slot-by-slot table, an ordered build sequence, open questions, and a one-paragraph mechanism explanation (he learns by restating the mechanism; give him one to restate).
5. Keep the vendor's own annotation sentences on connectors when he asked for a derived version — he recognises them and they anchor the adaptation.
6. When he then asks for a cropped/edited version ("replace X with Y, remove Z"), produce exactly those edits on the same layout — no new boxes, no re-ordering, no added commentary in the picture.
7. When he asks "what software can I use to do this myself", separate the two readings before answering: the *provisioning* (portal/accelerator/Bicep/Terraform) vs. the *drawing* (draw.io with the Azure shape library, Visio under M365, mermaid.live for the existing block). Answer the one he meant; if he says "I mean the diagram part", the drawing list is: draw.io (free, Azure/M365 icon sets built in), Visio (check M365 SKU), mermaid.live to edit the block already in the report, official Azure Architecture Icons SVG pack for any of them.

## Mermaid → PNG via Kroki

- `curl -s -A "Mozilla/5.0 hermes" -H 'Content-Type: text/plain' --data-binary @file.mmd https://kroki.io/mermaid/png` — the User-Agent is required (bare urllib/curl gets 403).
- **Retry 3× with a 3 s sleep before touching the diagram.** Kroki returns intermittent 500s on an identical payload; a 500 that flips to 200 on retry was never a syntax error. Only bisect the source (drop `style` lines, then `classDef`/`:::`, then `~~~`) after three consecutive failures. Fallback renderer: mermaid.ink pako route via `revealjs-deck-authoring/scripts/render-mermaid.py`.
- Check the output is actually a PNG (`file x.png`); on error Kroki writes the error text into the output path.
- **Horizontal rows inside a `flowchart TB` subgraph:** `direction LR` holds only when the subgraph's nodes have no edges to outside nodes. If a row stacks vertically, chain the row with invisible links (`A ~~~ B ~~~ C`) — but remove that chain again if the same nodes also have dashed edges to a node below, because the two constraints fight and the row stacks again. Test one subgraph at a time.
- Emoji render as tofu in Kroki PNGs; use words.

## Styled HTML → PNG (the "professional" tier)

Full recipe and known-good CSS in `references/html-diagram-recipe.md`. Non-negotiables:
- Fixed-width canvas (1120 px), `Emulation.setDeviceMetricsOverride(deviceScaleFactor=2)`, `Page.captureScreenshot` with a clip of the canvas height. `file://` URLs work in `browser_exec`.
- Any geometry computed by page JS (connector arrows from DOM positions) must be recomputed **after** the device-metrics override — expose it as a global `drawArrows()` and call it via `js()` right before the capture; arrows drawn on first load are measured against the wrong viewport.
- Connector lines start at a box's **edge**, never at an inner element (a pill inside a card) — the segment from the inner element to the edge crosses the card's own title text.
- Hand-drawn SVG glyphs are placeholders; say so in the reply and point at the official icon pack (Microsoft Azure Architecture Icons, M365 icons) for customer-facing use.
- Equal-height cards in a flex row: make each card `display:flex; flex-direction:column` and push the last sub-label down with `margin-top:auto`; otherwise a card with one extra text line breaks the row.
