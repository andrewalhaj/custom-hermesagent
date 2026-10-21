# Amazon listing verification (browser_exec recipe)

Why a browser: `web_search` is often empty on this box and Amazon's `/dp/` pages serve a
"Sorry! Something went wrong" bot wall to direct URL hits. The search-results page via the
homepage search box renders reliably and already contains everything needed.

## Steps
1. `new_tab("https://www.amazon.com/")`, `wait_for_load()`, sleep 2.
2. `fill_input("#twotabsearchtextbox", "<query>")`, click `#nav-search-submit-button`, wait + sleep 3.
3. Extract cards — the card `innerText` carries the full title (chip, RAM, SSD, colour), price,
   rating and "sold by" hints; `h2` alone often returns just "Apple":
   ```js
   (() => { const out = [];
     document.querySelectorAll('div[data-asin][data-component-type="s-search-result"]').forEach(d => {
       out.push({asin: d.getAttribute('data-asin'),
                 txt: d.innerText.replace(/\s+/g,' ').trim().slice(0,260),
                 price: d.querySelector('.a-price .a-offscreen')?.innerText}); });
     return out; })()
   ```
4. For the 1–3 closest matches, open `https://www.amazon.com/dp/<ASIN>` and read
   `#productTitle`, `#corePrice_feature_div .a-offscreen`, `#merchantInfoFeature_feature_div`
   (seller), `#availability`, and the variant list (`#twister .a-button-text`). If the page
   comes back empty/titled just "Amazon.com", it's bot-walled — answer from the step-3 card,
   don't loop retries.

## Reading the results
- Seller line `Shipper / Seller Amazon.com` = Amazon-stocked; anything else (Adorama,
  DataVision, B&H) is a marketplace listing — fine from reputable resellers, but returns go
  through them.
- Titles with "CTO" or non-standard RAM/SSD combos are build-to-order units — only third-party
  sellers carry them; compare against the vendor's own configurator before calling it cheaper.
- Check display variants (nano-texture/matte vs glossy) and colour in the title; they're
  separate ASINs at different prices.
- Deliver as: exact-spec link + price + caveat, one cheaper fallback, the Amazon-sold option
  if any, and the vendor-direct link as the baseline.
