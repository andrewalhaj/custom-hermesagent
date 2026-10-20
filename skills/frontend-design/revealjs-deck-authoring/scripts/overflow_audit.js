// reveal.js per-slide overflow + centering audit
// Paste into browser evaluate (Playwright page.evaluate or browser_console expression).
// Returns slides whose content overflows the section box, plus centering gaps.
// Catches what source-grep verification cannot: reveal.js inline-style conflicts,
// oversized diagrams, dead space. Run after EVERY content/CSS change.
() => {
  const out = [];
  const n = document.querySelectorAll('.reveal .slides > section').length;
  for (let i = 0; i < n; i++) {
    Reveal.slide(i);
    const s = document.querySelector('.reveal .slides > section.present');
    const kids = [...s.children].filter(c => c.tagName !== 'ASIDE');
    if (!kids.length) continue;
    const bots = kids.map(k => k.getBoundingClientRect().bottom);
    const tops = kids.map(k => k.getBoundingClientRect().top);
    const sr = s.getBoundingClientRect();
    out.push({
      i,
      h2: (s.querySelector('h2,h1') || {}).textContent || 'title',
      overflowPx: Math.round(Math.max(...bots) - sr.bottom),   // >0 = content cut off
      topGap: Math.round(Math.min(...tops) - sr.top),          // compare to botGap for centering
      botGap: Math.round(sr.bottom - Math.max(...bots)),
      display: getComputedStyle(s).display                     // 'block' = your flex CSS lost to reveal's inline style
    });
  }
  return {
    slideCount: n,
    overflows: out.filter(o => o.overflowPx > 0),   // must be []
    all: out
  };
}
