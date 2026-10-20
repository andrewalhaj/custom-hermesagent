# Vintage Paper + Claymorphism theme (known-good, verified live 2026-07)

Palette source: 21st.dev @serafim/vintage-paper. Published palette is dark-mode; light-paper derivatives below were derived for documents/decks. Fonts: Libre Baskerville (headings), Lora (body), IBM Plex Mono (chips/numbers) — Google Fonts links with Georgia fallback.

## Tokens

```css
:root {
  --ink: #2d2621;          /* dark bg / body text */
  --dark-card: #3a322c;
  --secondary: #4a4039;
  --accent: #59493e;
  --primary-tan: #c0a080;  /* headline accent on dark */
  --tan-ramp-1: #b3906f;
  --tan-ramp-2: #a67c52;   /* accent text on light; watermark tint */
  --tan-ramp-3: #8d6e4c;
  --tan-ramp-4: #735a3a;   /* shadow hue base; strong accents */
  --rust: #b54a35;         /* negative/warning only */
  --cream: #faf6ef;        /* light slide bg */
  --parchment: #f0e7d8;    /* raised card fill */
  --tanwash: #e8dcc8;      /* chip / banner fill */
  --text-muted: #75604b;   /* NOT #8d6e4c on dark — too low contrast */
}
```

## Clay system — one radius scale, one shadow recipe

Radii: `--r-lg: 28px` (cards/plates/quote), `--r-md: 22px` (rows/pills), `--r-sm: 14px` (chips), `999px` (tags).

```css
/* raised (puffy clay): outer drop + dark inner bottom + light inner top */
--clay-lg: 0 14px 28px rgba(115,90,58,0.16), 0 2px 6px rgba(115,90,58,0.10),
           inset 0 -8px 16px rgba(115,90,58,0.20), inset 0 8px 16px rgba(255,255,255,0.70);
--clay-md: 0 10px 20px rgba(115,90,58,0.13), 0 2px 4px rgba(115,90,58,0.08),
           inset 0 -6px 12px rgba(115,90,58,0.17), inset 0 6px 12px rgba(255,255,255,0.62);
--clay-sm: 0 5px 10px rgba(115,90,58,0.12),
           inset 0 -3px 7px rgba(115,90,58,0.15), inset 0 3px 7px rgba(255,255,255,0.55);
/* debossed (pressed IN): inverted — dark inner TOP, light inner BOTTOM + top-dark gradient fill */
--clay-inset: inset 0 3px 6px rgba(115,90,58,0.45), inset 0 14px 24px rgba(115,90,58,0.22),
              inset 0 -8px 14px rgba(255,255,255,0.90),
              inset 5px 0 14px rgba(115,90,58,0.15), inset -5px 0 14px rgba(115,90,58,0.15),
              0 2px 0 rgba(255,255,255,0.90);
/* dark-slide clay (chips on ink bg) */
--clay-dark: 0 10px 22px rgba(0,0,0,0.50), 0 2px 5px rgba(0,0,0,0.35),
             inset 0 -5px 10px rgba(0,0,0,0.45), inset 0 2px 6px rgba(255,255,255,0.14);
```

Deboss needs more than shadow to read at slide distance: pair with fill
`linear-gradient(180deg, #d9c7a9, #e9ddc6 22%, var(--parchment) 60%)` and
`border: 1px solid rgba(115,90,58,0.28)`. Even then it reads "quiet" at full-slide scale — that's correct; pushing harder goes muddy.

Shadows are always tinted to the brown hue (115,90,58), never black, on light surfaces.

Hover lift: `transform: translateY(-3px)` + slightly larger drop; guard with `@media (prefers-reduced-motion: reduce)`.

Numbered step circles (pressed-clay buttons): `background: var(--tan-ramp-4); color: var(--cream);` +
`box-shadow: 0 3px 6px rgba(115,90,58,0.30), inset 0 2px 3px rgba(255,255,255,0.25), inset 0 -2px 3px rgba(0,0,0,0.20);` IBM Plex Mono numerals.

Paper texture: faint radial gradient on slide bg, e.g. `radial-gradient(ellipse at 15% 0%, rgba(192,160,128,0.09), transparent 50%)`.

## Watermark (adaptive)

Tint white-on-transparent mark to `--tan-ramp-2` (#a67c52); base `filter: brightness(0.82)` on light slides, `brightness(1.35)` class-toggled on dark slides via Reveal `slidechanged`. See SKILL.md watermark section.

## Where it's deployed

- `~/reports/servicenow-loa-presentation-vintage.html` (reveal.js deck, full system)
- `~/reports/ServiceNow-M365-OOO-Delegation-Design-Vintage.docx` + `*-vintage.png` mermaid diagrams
- Mermaid node fills for this theme: zones `#e8dcc8/#f0e7d8/#faf6ef`, strokes `#8d6e4c/#a67c52/#735a3a`, text `#2d2621`, links `#735a3a`. Style EVERY node explicitly — unstyled inner nodes leak Mermaid's default lavender.
