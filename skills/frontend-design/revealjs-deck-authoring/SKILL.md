---
name: revealjs-deck-authoring
description: "Use when building or theming a single-file reveal.js deck"
---

# reveal.js Deck Authoring & Verification

Applies to any work on self-contained reveal.js HTML presentations (inline CSS/JS, base64-embedded images) — building, restyling, theming (e.g. claymorphism, custom palettes), or improving an existing deck. Andrew's decks live in `~/reports/` and are served from a `python3 -m http.server` on the Tailscale IP (check `ss -tlnp` before starting a new one — it's often already running from a prior session).

## Editing workflow (proven)

1. **Backup first** (WRITE-GATE style even for report files): `cp deck.html deck.html.bak-$(date +%Y%m%d%H%M%S)`.
2. **Strip base64 to read.** These files are 400KB+ mostly image payload. Make a readable working copy:
   `re.sub(r'(data:image/[a-z]+;base64,)[A-Za-z0-9+/=]{100,}', r'\1<B64>', src)` → read THAT, never the raw file.
3. **Apply edits as ONE Python script of `replace()` calls with count-asserts.** Each replacement asserts expected occurrence count and collects failures instead of silently no-oping. End the script with a full-body audit (grep for banned strings) and `sys.exit(1)` on any failure. This makes 30+ edits atomic and self-verifying. Never hand-patch a 400KB single-file deck with sed or individual patch calls.
4. **Audit scope includes speaker notes and HTML comments.** Em-dash/AI-tell cleanup applied only to visible markup will fail the audit on `<aside class="notes">` content and `<!-- -->` comments. Clean those via regex over `aside`/comment blocks (` — ` → `, `).

## Base64-adjacent corruption (recurring defect)

Patching near an embedded image can eat the img tag's trailing attributes and the following closing tags, causing **speaker-note text to render as visible slide content**. This happened twice on the same deck and a "verified" pass missed it once. Always run the leak check below after any edit near an `<img src="data:...">`.

## Verification checklist (live browser, not source grep)

Serve the file and check in a real browser (Playwright MCP works when the Hermes browser service is flaky):

0. **Per-slide overflow + centering audit (catches what "verified" workers miss):** loop `Reveal.slide(i)`; for each `.present` section compare child bounding boxes vs the section box (see "Layout inside reveal.js sections" below for the numeric method and the inline-`display:block` root cause + fix). Delegated deck builds routinely pass their own text-level verification while slides overflow — this DOM audit is the orchestrator's job.
1. **Structure counts:** `slides`, `aside.notes` (should equal slide count), `svg` icon count, every `img.naturalWidth > 100` (catches truncated base64).
2. **Note-leak check (the one that catches corruption):** per slide, clone the section, remove `aside.notes`, and assert distinctive note phrases do NOT appear in `innerText`.
3. **Fragment walk:** `Reveal.slide(0,0,0)` then loop `Reveal.next()` until `isLastSlide() && !availableFragments().next`, with a step cap. Confirms every fragment and slide is reachable and JS is healthy.
4. **Computed styles**, not source: `getComputedStyle` on themed elements (radius, box-shadow, font-family) proves CSS actually applied.
5. **Console errors:** must be zero.
6. **Fragment counting pitfall:** source count of `class="fragment` misses compound classes (`class="fragment ladder-step"` etc. counts fine, but `class="fragment"` vs `fragment fade-up` orderings differ). Use regex `class="[^"]*\bfragment\b[^"]*"` on the body, or DOM `querySelectorAll('.fragment').length`. Also: "navigation steps walked" ≠ fragment element count (a slide transition is a step too) — don't compare the two numbers across sessions.

## Vision-model verification pitfalls

- **Default Playwright viewport screenshots the deck at 960×540** — a 1200×700 deck downscaled. Subtle effects (inset shadows, deboss, soft clay highlights) are crushed and the vision model will report them absent even when present. Resize viewport to 1920×1080 before judging fine visual detail.
- **Before iterating CSS on a "not visible" verdict, crop + 2x upscale the region with PIL and re-check.** If the effect exists pixel-wise but not at slide distance, that's a design-intensity decision, not a bug — strengthen once or twice max, then stop (over-pushing inset shadows goes muddy). Report the residual honestly.
- Emoji-range regex scans (`\u2600-\u27BF`) flag intentional ✓ U+2713 / ✗ U+2717 dingbats — list the actual matched codepoints before treating it as a failure.

## Theming conventions (Andrew's decks)

Full Vintage Paper palette tokens + clay shadow recipes (the known-good CSS): `references/vintage-clay-theme.md`.

- Palette tokens as CSS custom properties in `:root`; claymorphism = one radius scale (28/22/14px) + shared shadow recipes as variables (`--clay-lg/md/sm` raised, `--clay-inset` debossed) rather than per-element shadow soup.
- Raised vs debossed gives the material two states: cards/rows raised, quotes/tags/chips pressed-in.
- **Icons: Andrew WANTS emoji, not stroke SVGs** (explicit reversal, 2026-07-09: "bring back those emojis, they added a lot of fun character"). The anti-slop "no emoji as icons" rule does NOT apply to his internal decks. Pattern: emoji inside the same soft clay chips via a `.emo` class (`font-size` tuned per context: cards ~24px, warning banners ~28px, rails ~21px; `font-style:normal` to survive italic parents; `flex-shrink:0`). Keep distinct emoji per card (✅📋🔔📅) rather than repeating one.
- Google Fonts `<link>` with system serif fallbacks (Libre Baskerville/Lora/IBM Plex Mono → Georgia).
- Apply the anti-slop rules (em-dash ban, no decorative dots, no quoted product names, kickers over eyebrow-spam) — they transfer to decks. EXCEPT the emoji ban (see above).
- Keep `prefers-reduced-motion` handling for hover transitions.
- **Host anonymization (explicit, 2026-08-14): shareable decks use LH01 (= Mac Mini) / LH02 (= Mac Studio) — never "Mac", "Mini", or "Studio" anywhere: visible text, speaker notes, HTML comments, AND inside the embedded diagram SVG.** Enforce with a banned-string audit over the full file plus the rendered `document.body.innerText` and notes. When the diagram labels change, the Mermaid source must be re-rendered and re-embedded, not just the HTML text edited.
- **Every deck ships with a speaker script** at `<deck-name>-script.md`: one block per slide, a bold "landing line" per block, ~10-12 min total. In-deck `<aside class="notes">` should be short summaries that match the script. Andrew's polish bar for these is "needs to impress" — run the vision pass per major slide and fix what it flags before delivering.

## Building a NEW deck via delegated Claude Code (proven 2026-08-14)

Write a full spec file (`~/reports/<deck>-spec.md`: theme tokens inline, per-slide content, a verification python one-liner at the end) and delegate the build with `claude -p ... --max-turns 20-25`.

**Launch flags when running as root (verified 2026-10-12):** `--dangerously-skip-permissions` is refused outright under root/sudo ("cannot be used with root/sudo privileges for security reasons") — the worker exits 1 before doing anything. Use a scoped allow-list instead: `--allowedTools "Read" "Write" "Edit" "Bash(python3:*)" "Bash(ls:*)"`. This ran a full deck+script build to completion with no permission prompts.

**Spec content for text-only decks (no diagram), 2026-10-12:** bake the two recurring worker defects into the spec itself as verbatim requirements — the `html/body { background: var(--paper) }` rule and `align-self: flex-start` on chips — plus the flex-fix CSS and a python verification block with required/banned strings. The dtg-findings build shipped clean on the first run with all of these specced; the earlier same-day build without them shipped both defects.

**Note-leak check false positive:** a worker may legitimately render the same sentence as BOTH a visible pressed callout and the `aside.notes` summary. The leak check (note phrase found in visible innerText) fires on this. Before treating it as base64-adjacent corruption, inspect the section: if the visible copy is a properly-structured element (own div, ASIDE still last child, markup intact), it's duplicate content by design, not corruption — keep or edit editorially, don't "fix."

Two pitfalls that each cost a full run:

1. **Pre-render the diagram SVG YOURSELF before delegating.** A worker told to "fetch from Kroki" burned all 25 turns fighting render errors and wrote zero output. Render the SVG first, put it on disk, and spec "the SVG already exists at <path>; base64-embed it, do not fetch anything from the network."
2. **A worker's "verified, passes the check" covers only the source-grep assertions you gave it — never layout.** Both delegated builds this session shipped slides that overflowed or mis-laid-out in the live browser (reveal.js inline-style conflicts, below) despite green verification. Always run the live overflow audit + vision pass yourself after any delegated build.

## Local screenshot & verification quirks (verified 2026-10-12)

- **Snap chromium sandboxes /tmp** — `chromium-browser --headless --screenshot=/tmp/x.png` reports "bytes written" but the file lands in snap's private namespace, NOT /tmp; `vision_analyze` then fails "media file not found". Write screenshots to a home-owned path (e.g. `~/reports/`).
- **Per-slide screenshots without a browser session:** `chromium-browser --headless --disable-gpu --no-sandbox --screenshot=sN.png --window-size=1920,1080 --hide-scrollbars --virtual-time-budget=5000 "<url>#/N"` renders slide N directly. Cache-bust with `?v=N` after each edit round.
- **Playwright MCP (mac) schema gotchas:** `browser_evaluate` requires `function`, `browser_console_messages` requires `level`, `browser_take_screenshot` requires `type`. Its screenshots save on the MCP host (the Mac), NOT this box — use local headless chromium when the file must be vision-analyzed here. Sessions can die mid-audit ("Session terminated"); re-navigate and continue.
- **Two worker defects shipped 2026-10-12 despite green source verification — check both on every delegated build:** (a) white strips at viewport edges: theme background set only on `.reveal`; fix = `html { background: var(--paper); } body { background: var(--paper) !important; }`. (b) the `.present{display:flex}` fix makes block-level chips/banners stretch full width; any chip/badge/priority label needs `align-self: flex-start`.
- **Don't park a CSS rule by renaming its selector** (e.g. `body` → `body-orig`): its other properties (font, color) silently orphan. Merge properties into the surviving rule.
- **Count-assert failures are data:** when a replace counts 0, grep the live file before rewriting — usual causes are entity encoding (`&times;` vs `×`, `&mdash;` vs `—`) and casing the worker changed.
- **Mid-build respecs: kill the worker first.** When the user changes framing while a delegated build runs (e.g. "no timelines"), `process(action='kill')` the claude -p run, rewrite the spec, add the new constraint to the spec's banned-strings verification block, relaunch. Cheaper than post-hoc editing a wrong deck.

## reveal.js layout traps (verified live 2026-08-14)

- **reveal.js sets inline `display: block` on every section at init — plain CSS `display:flex` on sections silently loses.** Symptom: a flex column layout measures as `display:block` in computed style and content overflows/top-anchors. Fix: `!important` on the flex declaration, scoped to `.present` (only visible slides): `.reveal .slides section.present { display:flex !important; flex-direction:column; justify-content:center; }`. This is also the clean way to vertically center content on every slide when sections are `height:100%` — `Reveal.initialize({center:true})` does NOTHING for full-height sections (center works by offsetting short slides; a 100%-height section has no slack).
- **Per-slide overflow audit (run after every content change):** loop `Reveal.slide(i)`, take the `.present` section, compare `max(child bottoms)` (excluding `aside`) to the section rect bottom; any positive delta = overflow. This caught +24px/+61px overflows the delegated worker's own check missed. Also verify centering: top gap ≈ bottom gap. Script: `scripts/overflow_audit.js` (paste into browser evaluate).\n- **`document.body.innerText` HIDES non-present slides** — auditing banned strings/terms via innerText false-negatives on content in other slides. Use `section.textContent` per slide, or the source file (with base64 stripped) for whole-deck audits.

## Mermaid rendering: Kroki can die mid-session — mermaid.ink pako is the fallback (2026-08-14)

Kroki went 400 → 500 → full timeouts on the SAME diagram within one session (trivial payloads timing out too), and a delegated `claude -p` build burned its whole 25-turn budget fighting it. The working fallback chain:

1. **mermaid.ink pako endpoint** — this is the one that works: `https://mermaid.ink/svg/pako:<b64>` where payload is `base64.urlsafe_b64encode(zlib.compress(json.dumps({'code': src, 'mermaid': {'theme':'neutral'}}).encode(), 9)).decode().rstrip('=')`.
2. **mermaid.ink plain-base64 (`/svg/base64:<b64>`) 400s even on trivial valid diagrams** — do not use it to conclude your syntax is broken; it will fail on `flowchart TB\n A-->B`. Only the pako route is trustworthy.
3. Still inject the foreignObject overflow fix into whichever SVG you get (same snippet as the Kroki section below).

**Pre-bake diagrams before delegating deck builds.** Render the SVG yourself, write it to disk, and tell the worker "the SVG already exists at <path>, base64-embed it, do not fetch anything" — a worker looping on a flaky render service is the #1 way a deck build exhausts max-turns with zero files written.

## reveal.js kills section-level flex — scope layout CSS to `.present` with `!important` (2026-08-14)

reveal.js sets **inline `display: block`** on every `<section>`, so any `display:flex` you put on sections in a stylesheet silently loses, and `center: true` in `Reveal.initialize` does nothing when sections have explicit `height: 100%`. Symptoms: content top-anchored with dead space below, or a flex-column diagram slide overflowing the bottom. The fix that works:

```css
.reveal .slides section.present { display: flex !important; flex-direction: column; justify-content: center; }
```

This vertically centers every slide (verify: per-slide top gap == bottom gap). For a single special slide (e.g. a diagram plate that must fill), the same pattern on its class: `.slide-diagram { display: flex !important; ... }`.

**Per-slide overflow audit (run after ANY delegated deck edit — a worker's "verified" deck shipped slides overflowing 24-61px):**
```js
for (let i = 0; i < n; i++) { Reveal.slide(i);
  const s = document.querySelector('section.present');
  const bots = [...s.children].filter(c => c.tagName !== 'ASIDE').map(k => k.getBoundingClientRect().bottom);
  const over = Math.max(...bots) - s.getBoundingClientRect().bottom; /* >0 = overflow */ }
```
Fix overflows by tightening row padding / gaps / font-size a few px at a time and re-auditing; residual 1-5px overflows usually need one more small nudge (e.g. body font 15→14px).

## Companion speaker script (deliverable pattern, 2026-08-14)

Decks for Andrew often ship with a script md (`<deck>-script.md`): one block per slide, narration prose, a **bolded landing line** per slide, and — when the audience is mixed-technical — a **"Terms on this slide"** glossary under each block defining every technical term in speaking order (he explicitly asked not to assume P@5/MRR/RAG are known). Keep script slide numbers in lockstep with the deck: renumber descending (16→17 before 15→16) to avoid replace collisions, and assert the final number sequence. Content-depth is iterated (plain-language pass, then technical pass) — do edits as the one-python-script-with-count-asserts pattern above, and audit banned strings (censored hostnames like real machine names when Andrew aliases hosts, IPs when he asks for removal) across visible text AND notes AND the diagram SVG.

## Diagrams inside decks (SVG, focal-point sizing)

When a diagram slide is "hard to read," the root cause is usually **aspect-ratio mismatch, not pixel size** — a 1420×190 one-row flowchart can never fill a 1200×700 slide. Fix the diagram's shape first (re-author as 2 rows / TB in Mermaid — see the `diagram-authoring` skill for the subgraph/`~~~` row technique, Kroki retry rule, and the styled-HTML→PNG tier for non-deck diagrams), then size it.

- **Embed diagrams as SVG, not PNG**: `data:image/svg+xml;base64,...`. Infinitely sharp when projected, and far smaller (this deck went 415KB→255KB). Render via Kroki `/mermaid/svg`. Kroki needs a User-Agent (`curl -A …`): bare `urllib` gets a 403.
- **Kroki fallback = mermaid.ink PAKO endpoint (proven 2026-08-14).** Kroki can go flaky mid-session (400s, then 500s, then timeouts even on trivial payloads). The reliable alternate is `https://mermaid.ink/svg/pako:<b64>` where payload = `base64.urlsafe_b64encode(zlib.compress(json.dumps({'code': mmd_src, 'mermaid': {'theme':'neutral'}}).encode(), 9)).decode().rstrip('=')`. CRITICAL: mermaid.ink's plain-base64 route (`/svg/base64:<b64>`) 400s even on a trivial one-edge flowchart — a 400 there does NOT mean your diagram syntax is bad; only the pako route is trustworthy. Inject the same foreignObject `<style>` fix after render.
- **Mermaid SVG label clipping**: Kroki's Mermaid SVGs use `foreignObject` for labels and the last character can clip (e.g. "moving" → "movin"). Before embedding, inject into the SVG right after the opening tag:
  `<style>foreignObject{overflow:visible!important}foreignObject div,foreignObject span{font-family:"Trebuchet MS",Verdana,Arial,sans-serif!important}</style>`
  Then verify a suspect label character-by-character via PIL crop + vision.
- **Kroki does not honor scale options** (JSON API returns 403; scale header ignored) — SVG output is the hi-res route, not scaled PNG.
- **Kroki flakiness fallback: mermaid.ink PAKO endpoint (verified 2026-08-14).** Kroki can degrade mid-session (400s → 500s → timeouts even on trivial diagrams). Fallback renderer: `https://mermaid.ink/svg/pako:<b64>` where the payload is zlib-compressed `{"code": <mermaid-src>, "mermaid": {"theme": "neutral"}}`, urlsafe-base64, `=` padding stripped. CRITICAL: mermaid.ink's plain `svg/base64:` route 400s even on trivial valid flowcharts — a 400 there does NOT mean your syntax is broken; try pako before debugging the diagram. Use `scripts/render-mermaid.py` (renders via pako, injects the foreignObject fix, writes the SVG).
- **Pre-render the diagram SVG BEFORE delegating a deck build.** A `claude -p` worker told to "fetch the diagram from Kroki" burned its entire 25-turn budget retrying a flaky render and wrote zero deck HTML. Render the SVG yourself first, put it on disk, and make the brief say "SVG already exists at <path>, base64-embed it, do NOT fetch anything from the network." External-service retries inside a turn-capped worker are pure budget burn; keep network-dependent steps on the orchestrator side.
- Focal-point layout: shrink the slide header (h2 ~30px, tight margins), let the clay plate `flex:1 1 auto` when the image should fill, or `flex:0 0 auto` to hug a shorter diagram (prevents dead space above/below inside the plate).

## Layout inside reveal.js sections (inline-style trap)

- **Reveal.js sets inline `display: block` on every `<section>` at init, silently killing any custom `display: flex` stylesheet rule on sections (verified 2026-08-14).** Symptom: flex column layout computes as `block`, content top-anchors and plates/images overflow the slide bottom while the CSS reads correct in source. Fix: `!important` on the section-level display rule. Verify with `getComputedStyle(section).display`, never by reading the stylesheet.
- **Vertical centering that actually works with full-height themed sections:** `Reveal.initialize({center:true})` does nothing when sections have `height:100%` (Reveal can't offset a full-height slide). Use instead `.reveal .slides section.present { display:flex !important; flex-direction:column; justify-content:center; }` with `center:false`. Scoping to `.present` keeps hidden slides untouched.
- **Numeric centering/overflow audit (run per slide):** for each slide, compare min child `top` minus section top vs section bottom minus max child `bottom` (excluding `aside`). Centered = the two gaps are equal; overflow = max bottom exceeds section bottom. Catches both dead-space and clipped-diagram defects that source grep and a single screenshot miss.

## Structural edits (add/remove slides)

- **String-anchor collision with reveal.js internals:** `src.count('<div class="reveal">')` returns 2 — the second is inside reveal.js's own minified error-message string (`'Unable to find presentation root (<div class="reveal">)'`). Anchor structural inserts on `'<body>\n<div class="reveal">'` instead. Same caution for any short markup string: assert-count against the full file WILL hit the inlined JS.
- Removing a slide: find `<!-- SLIDE N` comment → next `</section>\n\n</div>` (the slides-container close), assert the removed chunk contains the expected title and exactly one `<section`. Then re-walk fragments in the browser — total step count drops by that slide's fragments + 1 transition.

## Brand watermark (tasteful, all slides)

Proven pattern (JD logo, 2026-07-09):

1. **Source the official mark from the company site.** The homepage often has NO logo img (CSS/JS-injected); check secondary pages (`who_we_are.html` had `jdlogo-round-white.png` when index had nothing). Guessing asset paths 404s — grep real page HTML for `src=` instead.
2. **White-on-transparent PNGs are invisible to vision tools** — composite onto a dark background with PIL (`Image.alpha_composite` over the deck's ink color) before `vision_analyze`, or you'll wrongly conclude the file is blank.
3. **Tint to the theme palette, preserve alpha:** iterate pixels, replace RGB with the theme tan, keep A.
4. **Static single-tint has a visibility ceiling (superseded 2026-07-09).** Andrew asked for "slightly more visible" and the static mark plateaued at 4-4.5/7: brightening for dark slides washes it out on light ones and vice versa. Use the ADAPTIVE pattern:
   - Tint to mid-ramp tan (`#a67c52`), ~78px tall, `opacity:0.88`.
   - Base CSS (light slides): `filter: brightness(0.82) drop-shadow(0 2px 5px rgba(115,90,58,0.35)); transition: filter 0.3s ease;`
   - Dark-slide class: `.jd-watermark.on-dark { filter: brightness(1.35) drop-shadow(0 2px 5px rgba(0,0,0,0.45)); }`
   - Toggle via Reveal hooks (before `Reveal.initialize`): `Reveal.on('ready'|'slidechanged', () => wm.classList.toggle('on-dark', Reveal.getCurrentSlide().classList.contains('dark-slide')))`.
5. **CSS:** `position:fixed` (outside `.reveal` scaling), bottom-left, `pointer-events:none`, `user-select:none`, `aria-hidden="true"`, z-index above slides but below controls. Place opposite corner from the slide counter. Insert the `<img>` right after `<body>`, NOT inside `.reveal`.
6. **Tune with a numeric vision loop:** ask the vision model for a 1-7 visibility rating per background (target ~5 = noticed in 2-3s, clearly intentional, not competing). Iterate opacity/size/filter until BOTH light and dark slides score ~5. Verify: `getBoundingClientRect` for position/size, `naturalWidth > 0` for load, class-toggle across title → light → divider transitions.
7. Tinted JD asset lives at `~/reports/jdlogo-tan.png`.

## Companion interactive demos (proven 2026-07-09)

Decks often ship with a separate single-file interactive simulation (e.g. `ooo-sync-demo.html`) that a slide points to ("Let's watch it work"). Reviewing/upgrading one:

1. **Verify against the deck's promises, not just "does it run."** List every behavior the deck slides claim ("coverage expires on its own", "manual delegations always win", "manager fallback") and exercise each. This found two real logic bugs a smoke-click missed: auto-expiry never implemented (status stuck "Out of office" after return date), and manual-override checked `starts <= clock <= ends` instead of window overlap (`r.starts <= oooEnd && r.ends >= oooStart`), so the sync duplicated coverage the manual record already provided.
2. **Drive the state machine programmatically.** The demo's functions are globals — call them directly from `page.evaluate` (`resetDemo(); toggle.checked=true; onOOOToggle(); saveOOO(); advanceHour(); fastForwardReturn();`) and assert on `document.body.innerText`, table rows, and `state.*`. A full scenario matrix (happy path, each dropdown branch, early return, manual override, guard rails, reset, idempotent re-syncs, no duplicate notifications) runs in 2-3 evaluate calls — far faster and more complete than clicking.
3. **`<select>` option values must be read, not guessed** — `[...select.options].map(o => o.value)` first (this demo used `manager-ooo`, not `managerooo`).
4. **http.server caching trap:** after editing the file, the browser can serve the STALE copy (disk verified correct, live behavior old). Cache-bust with `?v=N` on the URL before re-verifying, or you'll wrongly conclude the fix didn't land.
5. **Theming a demo ≠ theming the deck:** apply the palette to page chrome (background, cards, buttons' radius, table, watermark) but KEEP functional/brand colors — panel headers (Outlook blue `#0078d4`, ServiceNow green `#2e7d32`), semantic button colors (red=reset, orange=time controls), and the dark terminal look (just warm its background to the theme ink `#2d2621`).
6. **Verify the worker's logic fix yourself with the full matrix** — the delegated worker fixed the specced bug but the matrix exposed the second (unspecced) one in my verification pass, not theirs.

## Speaker scripts (Andrew's format, 2026-08-14)

Decks ship with a companion script at `<deck>-script.md`. Format he converged on:
- One block per slide, narration prose, ending in a **bolded landing line** (the one point to make before advancing).
- **Audience-calibration is iterative — expect whiplash.** This session went plain-language ("assume non-technical", metaphors: sticky notes / filing cabinet / corkboard) then reversed to "more technical". The stable resolution: TECHNICAL deck text + a **"Terms on this slide"** glossary under each script block defining every term in speaking order (P@5, MRR, RRF, cosine similarity, embeddings, pgvector, tailnet, TTL...), 1-2 spoken-style sentences each, nothing assumed. Keep the metaphors as one-line seasoning inside speaker notes, not as the slide content. When renumbering slides after an insert, renumber script blocks in DESCENDING order so replaces don't collide.
- Content sourcing: pull architecture claims from live skill references (`fibonacci-sector-retrieval` references for memory/RAG), never invent. Anonymization requests ("no mention of X") get a banned-strings list enforced in the edit script's audit AND re-checked in the live DOM per-slide (innerText hides non-present slides).

## Delivering the deck as a download (2026-10-12)

When Andrew asks for a download link (TG MEDIA: file delivery is broken for him): **zip the deck + script first** — a bare `.html` link renders in the browser instead of downloading, and tmpfiles.org rejects `.html` outright ("Invalid file type") while accepting the zip. Working chain when litterbox 500s: `curl -F "file=@deck.zip" https://tmpfiles.org/api/v1/upload` → convert the returned URL to the direct-download form `tmpfiles.org/dl/<id>/<name>` → verify with `curl -sI` before sending. Warn that tmpfiles expires in ~60 min (vs litterbox 72h). 0x0.st has uploads disabled (anti-bot). Always also give the permanent paths: `~/reports/...` + the tailnet :8000 URL.

## Reporting

Andrew wants: what changed (table), what verification proved (concrete counts/values), defects found in prior work named plainly, backup path, live URL, and honest disclosure of anything that didn't fully land.
