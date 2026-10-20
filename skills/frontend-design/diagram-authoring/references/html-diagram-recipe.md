# Styled HTML → PNG diagram recipe (Microsoft CAF card look)

Use when Mermaid is not pretty enough. One self-contained HTML file in `~/reports/`, rendered with `browser_exec`.

## Page skeleton

- `.canvas{width:1120px;padding:36px 44px 40px;background:#fff}` — fixed width so the screenshot is deterministic.
- Font stack: `"Segoe UI",Inter,-apple-system,Helvetica,Arial,sans-serif` (matches Microsoft slides).
- Zones = rounded containers with a bold zone label; Azure zone `background:#e8f1fb;border:1px solid #9dc3ea`, neutral zones `#f6f6f6` with `#c8c8c8` border.
- Cards = white, `border:1px solid #c8c8c8; border-radius:8px`, title row with a 24 px inline SVG glyph, italic muted sub-label, optional coloured pill (`Foundry IQ` purple `#5b2d8e`, `Work IQ` navy `#1f3a68`, Claude orange `#d97757`) or outlined chip.
- Vertical dashed connector between zones: a `0×34px` div with `border-left:1.5px dashed #777` and a CSS-triangle `:after`, followed by the italic annotation sentence.
- Footer line: attribution left ("Adapted from Microsoft Cloud Adoption Framework …"), org + month right.

## Arrows computed from the DOM

Put an empty `<svg id="arrows">` inside the container that holds both ends, make that container `position:relative`, and draw after layout:

```js
function drawArrows(){
  const svg=document.getElementById('arrows'), inner=svg.parentElement;
  const ib=inner.getBoundingClientRect();
  const src=document.querySelector('.foundry').getBoundingClientRect();   // start at the BOX edge
  const row=document.querySelector('.inner .row').getBoundingClientRect();
  const sx=/* x of the element you want the line to rise from */, sy=src.top-ib.top;
  const midY=(row.bottom+src.top)/2-ib.top;
  svg.style.cssText='position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;overflow:visible';
  let d='<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0 0 8 4 0 8Z" fill="#5b2d8e"/></marker></defs>';
  document.querySelectorAll('.inner .row .card').forEach(c=>{const cb=c.getBoundingClientRect();
    d+=`<path d="M${sx} ${sy} V${midY} H${cb.left+cb.width/2-ib.left} V${cb.bottom-ib.top+6}" fill="none" stroke="#5b2d8e" stroke-width="1.6" stroke-dasharray="5 4" marker-end="url(#ah)"/>`;});
  svg.innerHTML=d;
}
drawArrows(); addEventListener('resize',drawArrows); addEventListener('load',drawArrows);
```

Leave ~46 px between the source box and the target row so the horizontal bus has room.

## Render

```python
import base64, time
goto_url("file://~/reports/<name>.html"); wait_for_load()
h = int(js("document.querySelector('.canvas').getBoundingClientRect().height"))
cdp("Emulation.setDeviceMetricsOverride", width=1120, height=h+2, deviceScaleFactor=2, mobile=False)
time.sleep(0.5); js("drawArrows()")            # geometry must be re-measured after the override
r = cdp("Page.captureScreenshot", format="png", captureBeyondViewport=True,
        clip={"x":0,"y":0,"width":1120,"height":h+1,"scale":2})
open(os.path.expanduser("~/reports/<name>.png"),"wb").write(base64.b64decode(r["data"]))
```

Then `vision_analyze` with 2–3 yes/no checks. If the vision model claims a line crosses text, verify from the DOM first (print the path `d` strings and the text box rect) — it misreads crops and will send you chasing a non-defect.

## Checklist before sending

- Cards in a row equal height (flex column + `margin-top:auto` on the last line).
- No annotation text floating outside a card; put notes inside the box with a left border divider.
- Chips/pills consistent: filled = vendor "IQ"-style capability, outlined = category label. Don't mix three styles.
- Say in the reply that icons are approximations if official marks were not used, and name the official icon pack.
