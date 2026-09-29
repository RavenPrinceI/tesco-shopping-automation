# Order-bound policy helper

`tesco_amendment.py` is a Python 3.10+ stdlib library, not a Tesco client. It
contains no browser connection, network request, selector implementation, or
credential handling. The tests use synthetic snapshots and a scripted fake
adapter. Passing them proves policy behavior under those fixtures, not that the
current website works with a particular driver.

## Public API

- `Operation(product, QuantityMode.ADD | QuantityMode.SET, quantity)` expresses
  quantities in verified sellable units. Adding one pack to two existing packs
  expects three packs. Setting one expects one. Setting zero removes the item.
  Duplicate product operations and negative or non-integer quantities are errors.
- `discover(snapshot, operations, now=utc_epoch_seconds)` returns an
  `AmendmentContext` or raises `Halt` with a safe `State`. Discovery requires one
  complete active-order list, one eligible target, a live cutoff, and the complete
  baseline basket. A detached trolley is not an eligible amendment target.
- `Orchestrator(adapter, context, journal, run_id, authorized=False).run()` returns
  a `State`. `CONFIRMED` alone means verified success. `SUMMARY_VERIFIED` means
  the final click has not happened. Halt states need review.
- `Journal(path)` stores a durable submission fence, mutation-attempt markers,
  and enum-only events in SQLite. Call `close()` when finished.

Integration outline, with the adapter and private storage supplied by the caller:

```python
import time
from uuid import uuid4
from tesco_amendment import (
    Journal, Operation, Orchestrator, QuantityMode, discover,
)


def amend(adapter, operations, private_journal_path, save_context, *, authorized):
    # The caller resolves exact products and pack sizes before constructing operations.
    context = discover(adapter.observe(), tuple(operations), now=time.time())
    run_id = str(uuid4())
    # Save these BEFORE any browser input. Recovery must reuse both.
    save_context(run_id, context)
    journal = Journal(private_journal_path)
    try:
        engine = Orchestrator(adapter, context, journal, run_id,
                              authorized=authorized)
        return engine.run(), context
    finally:
        journal.close()
```

For the included CLI, `tesco_cdp.RunStore` supplies private persisted context,
binding and a whole-run execution fence. See [live CDP](live-cdp.md). Other
integrations must implement `save_context`. Keep the context and opaque keys in private storage
outside this repository. Do not print them. Do not generate a new run identity
or rediscover a new baseline to recover an uncertain attempt. If either the
original context or journal is lost, stop for manual reconciliation.

`authorized=True` means the caller has checked that the user's current request
covers completion of this existing-order amendment. It does not authorize a new
slot, payment input, voucher redemption, or a new order. With the default
`False`, the helper may stage requested edits but stops at the verified summary.
This is a conservative integration default, not a request to ask the user twice.

## Adapter contract

Implement `BrowserAdapter.observe() -> Snapshot` and
`BrowserAdapter.act(action: Action, page: Page) -> None`.

`observe` must collect fresh visible evidence and return detached snapshots.
Never mutate a snapshot after returning it. Each observation must establish:

- All active/upcoming orders, including pagination, and whether the list is
  complete. Never use an old order list merely because the checkout page loaded.
- The browser instance, target, order and slot through stable opaque local keys.
  The adapter owns the mapping to real visible identities. Do not place raw order
  identifiers, delivery addresses, account data, or sensitive URLs in snapshots.
- A finite UTC cutoff parsed from visible text with a known timezone. Unknown,
  changed, or expired cutoffs are not permission to proceed.
- A `View` classified from rendered headings and page structure, not URL alone.
  `making_changes=True` requires the visible amendment banner and matching order
  delivery information. A route parameter is only supporting evidence.
- `basket_complete=True` only when all basket lines and sellable-unit quantities
  have been inspected. Use positive integer quantities and omit removed items.
  This requirement applies on product, checkout, and confirmation views too.
- `verified_products` only for unambiguous, currently rendered product controls
  whose exact identity, pack size, price, and current quantity were checked.
- `controls` containing only visible enabled controls. Resolve duplicate matching
  controls by semantic relationships or fail closed, never choose the first.
- Any blocking dialog's exact visible heading and controls. Do not hide dialogs
  behind the underlying page's state. The only automatic response is `No` to an
  exact `Cancel changes?` dialog with exactly `No` and `Yes, cancel` controls,
  before final submission. Everything else stops.
- `summary_complete=True` only after the fresh rendered summary has been checked
  for item quantities, delivery location and slot, total, delivery charge,
  savings, substitutions and uncertainty. Present that summary through a private
  user channel, not the journal. This library does not calculate Tesco prices.
- `visible_success=True` only for explicit rendered amended-order success tied
  to the same order, slot and complete requested basket. A confirmation URL, an
  order number alone, or a previous order's confirmation is insufficient.

`act` must revalidate the passed snapshot immediately before input, including
fresh authentication, active-order count, order/slot/cutoff, stage, basket and
control or product identity. Refuse stale evidence. The abstract library cannot
make a browser action atomic with an earlier observation. Use normal trusted
browser input, never hidden APIs or internal application-state writes.

Action semantics:

| Action | Adapter behavior |
| --- | --- |
| `ENTER_AMENDMENT` | Click the selected order card's `Make changes` control once. |
| `SEARCH_ENTER` | Populate the visible search field for the exact product and press Enter once. |
| `SEARCH_SUBMIT` | Submit the still-visible populated search form once through normal browser input. No second Enter attempt. |
| `SET_QUANTITY` | Set the verified product's absolute quantity once. Do not translate a desired total into that many Add clicks. If unsupported, refuse. |
| `CHECKOUT` | Click the currently rendered supported checkout label once. |
| `OPEN_TROLLEY` | Open the trolley from the order-bound landing/search view, then verify the complete expected basket. |
| `CONTINUE` | Click the supported checkout continuation once. |
| `KEEP_CHANGES` | Choose `No` only in the classified cancellation dialog. |
| `CONFIRM` | Click the final summary's `Confirm order` once. Never retry internally. |

All adapter calls must have finite I/O timeouts and must not log raw browser
payloads or exception messages. The helper bounds observation counts with
`max_polls` and uses `poll_interval` between polls. It cannot interrupt a hung
synchronous adapter call. An input timeout means outcome uncertain, not that the
input failed. The helper reconciles quantity and final-click timeouts by reading
visible state without repeating the input.

## State and recovery rules

Normal progress is `ORDER_DISCOVERY`, `AMENDING`, `PRODUCT_SELECTION`,
`BASKET_VERIFIED`, `CHECKOUT`, `SUMMARY_VERIFIED`, `SUBMISSION_PENDING`,
`CONFIRMED`. Product selection is skipped for already-satisfied operations.
Landing and product views may first open the trolley. Checkout, Offers and
Suggestions may be absent. Both `Continue checkout` and `Continue to checkout`
are continuation aliases. A delayed view or control is polled within
a fixed bound; intermediate stages are each advanced at most once per run.

`AUTH_REQUIRED`, `SLOT_DRIFT`, `AMBIGUOUS_TARGET`, `UNEXPECTED_DIALOG`, and
`OUTCOME_UNKNOWN` stop normal progress. Do not schedule automatic whole-run
retries. The helper neither logs in nor selects replacement slots.

A missing target during a bounded wait triggers read-only rediscovery across
snapshots. A replacement must be uniquely bound to the same browser, order and
slot with appropriate visible amendment or confirmation evidence. A payment
page never receives input. If no trustworthy confirmation appears, report the
outcome as unknown, not failed or complete.

Before final input, SQLite commits `pending` using an atomic conditional update.
A crash immediately before the click is indistinguishable from a crash just after
it. On restart, construct the engine using the original context, original run
UUID and same database. A pending or confirmed run performs read-only
reconciliation; it never clicks Confirm again. Pending remains pending even when
the current state is an authentication or unknown-outcome stop.

The database stores a random run UUID, a context-binding digest, enum states,
and numeric mutation positions. It does not serialize page text, product keys,
order keys, slot keys, quantities, URLs, labels or exceptions. This is an allowlist,
not regex redaction. Store it in a private directory on durable local storage.
Use a single orchestration owner per run. The final fence is atomic across
connections, but this is not a distributed lock for the whole browser workflow.

Mutation markers prevent repeating a quantity input after a crash. The saved
baseline determines absolute expected totals. The helper deliberately stops if a
multi-operation restart cannot match a known basket state. Human review is safer
than reconstructing intent from a partially changed basket.

## Offline verification

From the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q tesco_amendment.py tesco_live.py tesco_cdp.py tests
git diff --check
```

No installation, pytest, network, cookies, credentials, or browser is needed.
The fake adapter in `tests/test_orchestration.py` supplies typed fixtures and
records actions so tests can assert absence of duplicate input.

The repository now includes a conservative visible-DOM adapter, private context
storage and bounded CDP transport. Production selector mapping, private user
summary delivery and separately approved live acceptance testing remain
deployment work. Read the [live adapter limitations](live-cdp.md) before use.
