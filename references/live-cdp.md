# Live CDP entry point

Use `tesco_live.py` rather than a one-off `/tmp/tesco_*.py` mutation script.
The entry point uses `tesco_amendment.Orchestrator`, not a parallel checkout
implementation. `tesco_cdp.py` supplies loopback transport and durable run storage.

## Readiness and limits

This adapter has **offline verification only**. No live browser was contacted
during implementation. There is no verified production Tesco selector map in
this repository. The fixture map is synthetic and must not be passed to a live
run as though it described Tesco. A deployment reviewer must map the actual
rendered page to the evidence contract below, then run read-only preflight.

The current adapter deliberately refuses layouts that cannot expose all required
evidence in one target: the complete active-order list, order/slot/cutoff, and
complete basket must remain inspectable at every stage. It does not navigate a
second tab to refresh My orders, expand collapsed baskets, paginate, scroll,
or infer completeness from a previously observed list. If Tesco does not render
this evidence during checkout, this adapter will stop. Do not replace missing
evidence with constants or relax completeness flags. Extend and test the
extractor before authorizing that layout.

Supported input is a single trusted click per action. Product cards must already
be visible and match exact product ID, title including pack size, price and
current quantity. A quantity action supports a delta of exactly one sellable
unit, through `Add`, `Increase quantity` or `Decrease quantity`. An already
satisfied operation needs no input. Larger changes, product search/text entry,
initial orders, authentication, slot booking, vouchers and payment are not
implemented. The policy library's search fallback is not enabled by this adapter.
Prepare the search results through approved browser tooling before saving the
baseline. Never use a script to fabricate page evidence.

## Exact invocation

Run from the repository root with Python 3.10+ on Linux. Live transport also
requires `websockets>=15,<16`, as listed in `requirements-live.txt`. Websocket
proxy routing is explicitly disabled. `--help`, the policy tests and fake-transport
tests do not require it. The optional DOM-double test uses Node and skips itself
if Node is unavailable.

Prepare two **private runtime files outside Git** using the reviewed live DOM.
The site map is described below. The plan is a JSON array of operations:

```json
[{"product":"222222222","mode":"add","quantity":1}]
```

That product ID is synthetic, not a recommendation or a real order. Replace it
with the exact verified product ID, not a search phrase. `add` adds to the saved
baseline; `set` specifies an absolute quantity. Quantities count sellable packs.
Neither file may contain credentials, cookies, payment data or tokens. The site
map may include delivery-summary text, so keep it private and never paste it in
logs. A new run needs a new empty private state directory. An uncertain existing
run must retain its original directory.

Set these shell variables to private runtime paths and the exact target ID
obtained from approved read-only browser discovery. Do not use a tab index or
an arbitrary Tesco URL as the target. Do not put account information in filenames.

```bash
python3 tesco_live.py \
  --endpoint http://127.0.0.1:9222 \
  --target "$TESCO_TARGET" \
  --site-map "$TESCO_SITE_MAP" \
  --plan "$TESCO_PLAN" \
  --state-dir "$TESCO_RUN_DIR" \
  --dry-run
```

`--dry-run` is optional. Omitting both it and `--execute` has the same read-only
behavior. It sends DOM reads only, creates local private run state, and prints
`READ_ONLY` only after the current complete basket matches the saved baseline or
expected basket. It does not call the mutating orchestrator. This is not a claim
that future checkout stages will be compatible.

After preflight, the user's current existing-order amendment request authorizes
one execution through final confirmation. Preserve the same target, map, plan
and state directory. Do not change these between preflight and execution.

```bash
python3 tesco_live.py \
  --endpoint http://127.0.0.1:9222 \
  --target "$TESCO_TARGET" \
  --site-map "$TESCO_SITE_MAP" \
  --plan "$TESCO_PLAN" \
  --state-dir "$TESCO_RUN_DIR" \
  --execute
```

Both the adapter and CDP transport refuse input without `--execute`. The CLI
requires a previously saved read-only baseline. `--execute` consumes a durable
one-attempt fence before any workflow input, including navigation. A crash,
timeout or stop is **not permission to run it again**. Do not erase the directory,
change the UUID or rediscover a new baseline to bypass the fence.

For an uncertain final submission, use the same command with `--reconcile` in
place of `--execute`. This runs observation-only confirmation checks and refuses
if final submission was never marked pending. Earlier stops need human review.
A changed browser instance or target also needs review, not automatic rebinding.

Only `CONFIRMED` reports a verified amended order. `READ_ONLY` reports inspection
only. `SUMMARY_VERIFIED` is not completion. Halt states exit with code 2. Successful
inspection or confirmed reconciliation exits with code 0. No error traceback,
DOM dump, title, URL, product text, order identifier or address is printed.
Present the fresh delivery and basket summary through the user's private browser
or approved private channel; enum-only console output is not a customer summary.

## Site map contract

`tests/fixtures/site_map.json` documents the shape using invented CSS classes and
synthetic account/product evidence. It is not a production selector recipe.
Selectors must be narrow, reviewed and tied to semantic headings and regions.
Every required single selector must resolve to exactly one rendered element.
Hidden nodes do not count. Duplicate or missing evidence stops execution.

Top-level fields:

- `authenticated`: `{ "selector": "...", "text": "..." }` for a visible
  authenticated-state marker. A password field causes `AUTH_REQUIRED` without
  reading its value.
- `orders`: a visible full active-order list with `root`, `rows`, `order`, `slot`,
  `cutoff`, `count` and `complete`. `count` is a plain integer row count;
  `complete` is a selector/exact-text pair for a real completeness marker.
  The adapter requires exactly one order and compares its identity, slot and
  cutoff to the current view before every input.
- `views`: entries named `orders`, `landing`, `products`, `basket`, `checkout`,
  `offers`, `suggestions`, `summary` or `confirmation`. Each entry has `root`,
  `heading`, `order`, `slot`, `cutoff`, and `basket`. Exactly one view may match.
  `heading` is a selector/exact-text pair. All evidence except the top-level
  orders list and authentication marker is scoped within this order-bound root.
- Each amendment view needs `amendment`, a selector/exact-text pair for the visible
  amendment banner. Orders and confirmation views need not have that banner.
- Every `basket` has `root`, `rows`, `product`, `quantity`, `count`, and `complete`.
  Product selectors resolve to visible Tesco product links. Quantity selectors
  resolve to plain integer text or a visible number input, never arbitrary input
  values. `count` is a plain integer **distinct-line** count. `complete` is a real
  selector/exact-text completeness marker. Duplicate product IDs and partial
  baskets are rejected. The entire basket, not just requested products, is checked.
- `products`: exact product-card mappings with `key`, `root`, `product`, `name`,
  `name_text`, `price`, `price_text` and `quantity`. See the synthetic map. A card
  with an unexpected pack, price or quantity never receives input.
- The summary view needs `summary`, containing selector/exact-text pairs for
  `location`, `slot`, `total`, `delivery_charge`, `savings`, `substitutions` and
  `uncertainty`. These are expectations reviewed for this request, not generic
  nonempty-text tests. Unknown totals or absent fields stop the flow.
- The confirmation view needs `success`, a selector/exact-text pair for explicit
  amended-order success. The same order, slot and complete expected basket are
  also required. A confirmation route alone never establishes success.

`slot` text must identify the full date/time and location, not just a reusable
weekday. Order/slot text is hashed in memory before persistence. `cutoff` currently
requires visible ISO-8601 text with an explicit UTC offset. Non-ISO display formats
need a tested parser extension. Do not substitute a hidden attribute, cached
cutoff, or guessed year/timezone. Exact text comparisons normalize whitespace.

The extractor reads rendered DOM only. It never reads cookies, storage, hidden
React state, credentials or Tesco API responses. It uses no arbitrary JavaScript
from the map. CSS selectors are configuration, not instructions from the page.

## Checkout and dialog handling

The route is explicit: landing/search results, trolley, checkout, Offers,
Suggestions, Order summary, confirmation. `View trolley` or `Trolley` advances
into the trolley. Checkout, Offers and Suggestions each advance at most once;
optional stages can be skipped if the next verified view is already visible.
Both `Continue checkout` and `Continue to checkout` are supported.

Checkout controls come only from the basket region inside the bound order root.
When both `Check out groceries` and `Check out to confirm changes` exist there,
the amendment-specific control wins. Duplicate amendment-specific controls
remain ambiguous. A page-wide ordinary checkout link is never a fallback.

A cancellation response is scoped to the one rendered dialog with the exact
heading `Cancel changes?` and exactly `No` plus `Yes, cancel`. Only its `No`
control can receive the click. Unknown or duplicate dialogs stop the run.

Every click follows a fresh full snapshot and viewport hit test. Hidden, disabled,
offscreen or occluded controls do not receive input. No scroll or click retries
are performed. There remains the usual DOM-to-input race; keep the browser under
one owner's control during an execution.

## Persistence and deadlines

The run directory must be owned by the current user with mode `700`; state,
lock and SQLite files have mode `600`. Linux `flock` enforces one local process
owner. The persisted state contains a random run UUID, typed browser/target
binding, configuration digest, original baseline, expected quantities, numeric
product IDs, opaque order/slot hashes and cutoff. No raw page evidence is saved.
SQLite retains the helper's mutation and final-submission fences and enum-only
journal. The browser instance and target cannot silently change on restart.

CDP HTTP discovery and websocket RPC have finite whole-call deadlines, default
8 seconds and maximum 60. DOM evaluation also has a five-second browser deadline.
Responses are capped at 1 MiB. Discovery accepts only the literal loopback host
`127.0.0.1`; redirects, remote websocket endpoints and non-Tesco pages are refused.
No browser is launched and no profile is copied. A lost input response is never
retried. Final submission is reconciled only through visible confirmation.

## Offline verification

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q tesco_amendment.py tesco_live.py tesco_cdp.py tests
node --check tests/fixtures/dom_contract.cjs
python3 tesco_live.py --help
git diff --check
```

The frozen-peas fixture is a reduced, synthetic replay of the reported flow,
not a captured account or real basket. It exercises one-pack addition, untouched
baseline lines, trolley-scoped competing labels, the checkout stage, both
continuation labels, and verified confirmation. Fake CDP tests cover guards,
timeouts, persistence and drift. The Node test uses a minimal DOM double; it does
not validate real Tesco CSS, layout or accessibility behavior. Separate approved
live acceptance testing and a reviewed production map remain required.
