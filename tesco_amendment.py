"""Order-bound amendment policy. No browser transport or Tesco API calls.

All observations must come from freshly rendered, complete visible state.
Opaque keys are adapter-local references, never raw account identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Mapping, Protocol, Callable
from pathlib import Path
from uuid import UUID
import sqlite3
import time
import math


class State(str, Enum):
    ORDER_DISCOVERY = 'ORDER_DISCOVERY'
    AMENDING = 'AMENDING'
    PRODUCT_SELECTION = 'PRODUCT_SELECTION'
    BASKET_VERIFIED = 'BASKET_VERIFIED'
    CHECKOUT = 'CHECKOUT'
    SUMMARY_VERIFIED = 'SUMMARY_VERIFIED'
    SUBMISSION_PENDING = 'SUBMISSION_PENDING'
    CONFIRMED = 'CONFIRMED'
    AUTH_REQUIRED = 'AUTH_REQUIRED'
    SLOT_DRIFT = 'SLOT_DRIFT'
    AMBIGUOUS_TARGET = 'AMBIGUOUS_TARGET'
    UNEXPECTED_DIALOG = 'UNEXPECTED_DIALOG'
    OUTCOME_UNKNOWN = 'OUTCOME_UNKNOWN'


class View(str, Enum):
    ORDERS = 'orders'
    BASKET = 'basket'
    PRODUCTS = 'products'
    LANDING = 'landing'
    CHECKOUT = 'checkout'
    OFFERS = 'offers'
    SUGGESTIONS = 'suggestions'
    SUMMARY = 'summary'
    PAYMENT = 'payment'
    CONFIRMATION = 'confirmation'
    LOADING = 'loading'


class Submission(str, Enum):
    NOT_STARTED = 'not_started'
    PENDING = 'pending'
    CONFIRMED = 'confirmed'


class QuantityMode(str, Enum):
    ADD = 'add'
    SET = 'set'


@dataclass(frozen=True)
class Operation:
    product: str  # exact verified product/pack key, not a free-text search
    mode: QuantityMode
    quantity: int


def expected_quantities(baseline: Mapping[str, int],
                        operations: tuple[Operation, ...]) -> dict[str, int]:
    result = dict(baseline)
    seen = set()
    for operation in operations:
        if (operation.product in seen or not operation.product or
                type(operation.quantity) is not int or operation.quantity < 0 or
                not isinstance(operation.mode, QuantityMode)):
            raise ValueError('invalid or duplicate operation')
        seen.add(operation.product)
        result[operation.product] = (result.get(operation.product, 0) + operation.quantity
                                     if operation.mode == QuantityMode.ADD
                                     else operation.quantity)
    if any(type(q) is not int or q < 0 for q in result.values()):
        raise ValueError('invalid basket quantities')
    return {p: q for p, q in result.items() if q}


@dataclass(frozen=True)
class Order:
    key: str
    slot: str
    cutoff: float  # UTC epoch seconds, parsed from the visible cutoff


@dataclass(frozen=True)
class Dialog:
    heading: str
    controls: tuple[str, ...]


@dataclass(frozen=True)
class Page:
    target: str
    browser: str
    order: str | None
    slot: str | None
    view: View
    making_changes: bool = False
    quantities: Mapping[str, int] = field(default_factory=dict)
    controls: tuple[str, ...] = ()  # visible, enabled accessible names only
    basket_complete: bool = False
    summary_complete: bool = False
    visible_success: bool = False
    verified_products: tuple[str, ...] = ()
    dialog: Dialog | None = None
    route_hint: str = ""  # optional sanitized route; never success evidence or logged


@dataclass(frozen=True)
class Snapshot:
    orders: tuple[Order, ...]
    pages: tuple[Page, ...]
    authenticated: bool = True
    orders_complete: bool = True


@dataclass
class AmendmentContext:
    target: str
    browser: str
    order: str
    slot: str
    cutoff: float
    requested_operations: tuple[Operation, ...]
    baseline_quantities: dict[str, int]
    expected_quantities: dict[str, int]
    basket_fingerprint: str
    stage: State = State.ORDER_DISCOVERY
    submission_status: Submission = Submission.NOT_STARTED


class MissingTarget(Exception):
    """Transient observation failure; only bounded read-only polling is allowed."""


class Halt(Exception):
    """Safe public exception: never contains page text or raw adapter errors."""
    def __init__(self, state: State):
        self.state = state
        super().__init__(state.value)


def fingerprint(quantities: Mapping[str, int]) -> str:
    return sha256(json.dumps(dict(quantities), sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def discover(snapshot: Snapshot, operations: tuple[Operation, ...], *, now: float) -> AmendmentContext:
    if not snapshot.authenticated:
        raise Halt(State.AUTH_REQUIRED)
    if not snapshot.orders_complete or len(snapshot.orders) != 1:
        raise Halt(State.AMBIGUOUS_TARGET)
    order = snapshot.orders[0]
    if not math.isfinite(order.cutoff) or not math.isfinite(now) or now >= order.cutoff:
        raise Halt(State.SLOT_DRIFT)
    pages = [p for p in snapshot.pages if p.order == order.key and
             (p.making_changes or p.view == View.ORDERS)]
    if len(pages) != 1:
        raise Halt(State.AMBIGUOUS_TARGET)
    page = pages[0]
    if page.slot != order.slot:
        raise Halt(State.SLOT_DRIFT)
    if not page.basket_complete:
        raise Halt(State.OUTCOME_UNKNOWN)
    baseline = expected_quantities(page.quantities, ())
    expected = expected_quantities(baseline, operations)
    return AmendmentContext(page.target, page.browser, order.key, order.slot,
                            order.cutoff, operations, baseline, expected,
                            fingerprint(expected), State.AMENDING if page.making_changes
                            else State.ORDER_DISCOVERY)



LABEL_ALIASES = {
    'checkout': frozenset({'Checkout to confirm changes', 'Check out to confirm changes',
                           'Check out groceries'}),
    'continue': frozenset({'Continue to checkout', 'Continue checkout'}),
    'open_trolley': frozenset({'View trolley', 'Trolley'}),
    'search': frozenset({'Search'}),
    'confirm': frozenset({'Confirm order'}),
    'enter_amendment': frozenset({'Make changes'}),
    'keep_changes': frozenset({'No'}),
}
CHECKOUT_LABELS = LABEL_ALIASES['checkout']
CANCEL_DIALOG_HEADING = 'Cancel changes?'
CANCEL_DIALOG_CONTROLS = frozenset({'No', 'Yes, cancel'})


def visible_label(page: Page, action: str) -> str:
    matches = LABEL_ALIASES[action].intersection(page.controls)
    if len(matches) != 1:
        raise Halt(State.OUTCOME_UNKNOWN)
    return next(iter(matches))


class ActionKind(str, Enum):
    ENTER_AMENDMENT = 'enter_amendment'
    KEEP_CHANGES = 'keep_changes'
    CONTINUE = 'continue'
    SEARCH_ENTER = 'search_enter'
    SEARCH_SUBMIT = 'search_submit'
    SET_QUANTITY = 'set_quantity'
    CHECKOUT = 'checkout'
    OPEN_TROLLEY = 'open_trolley'
    CONFIRM = 'confirm'


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    label: str
    product: str | None = None
    quantity: int | None = None


class BrowserAdapter(Protocol):
    """Implement with visible browser UI only. All calls need bounded I/O timeouts.

    observe must refresh the complete active-order list and visible target state.
    act must revalidate the passed page's browser/order/slot and enabled control
    immediately before trusted input. Never retry an action internally.
    """
    def observe(self) -> Snapshot: ...
    def act(self, action: Action, page: Page) -> None: ...


class Journal:
    """SQLite write-ahead submission fence and enum-only event log.

    Use a private local directory, one durable database and stable random run UUID
    across restarts. No snapshots, identifiers, labels or exception text are stored.
    """
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs
                (run TEXT PRIMARY KEY, binding TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mutations
                (run TEXT NOT NULL, step INTEGER NOT NULL, PRIMARY KEY(run, step));
            CREATE TABLE IF NOT EXISTS events
                (sequence INTEGER PRIMARY KEY, run TEXT NOT NULL, state TEXT NOT NULL);
        """)

    @staticmethod
    def _validate_run(run: str) -> None:
        if str(UUID(run)) != run:
            raise ValueError('run must be a canonical UUID')

    def bind(self, run: str, binding: str) -> Submission:
        self._validate_run(run)
        if len(binding) != 64 or any(c not in '0123456789abcdef' for c in binding):
            raise ValueError('binding must be a SHA-256 digest')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO runs VALUES (?, ?, ?)',
                            (run, binding, Submission.NOT_STARTED.value))
            row = self.db.execute('SELECT binding, status FROM runs WHERE run=?',
                                  (run,)).fetchone()
            if row[0] != binding:
                raise Halt(State.OUTCOME_UNKNOWN)
        return Submission(row[1])

    def claim(self, run: str) -> bool:
        self._validate_run(run)
        with self.db:
            result = self.db.execute('UPDATE runs SET status=? WHERE run=? AND status=?',
                (Submission.PENDING.value, run, Submission.NOT_STARTED.value))
        return result.rowcount == 1

    def claim_mutation(self, run: str, step: int) -> bool:
        self._validate_run(run)
        if type(step) is not int or step < 0:
            raise ValueError("invalid mutation step")
        with self.db:
            result = self.db.execute('INSERT OR IGNORE INTO mutations VALUES (?, ?)',
                                     (run, step))
        return result.rowcount == 1

    def event(self, run: str, state: State) -> None:
        self._validate_run(run)
        if not isinstance(state, State):
            raise ValueError('events accept only State values')
        with self.db:
            self.db.execute('INSERT INTO events(run, state) VALUES (?, ?)', (run, state.value))
            if state == State.CONFIRMED:
                self.db.execute('UPDATE runs SET status=? WHERE run=?',
                                (Submission.CONFIRMED.value, run))

    def events(self) -> list[str]:
        return [row[0] for row in self.db.execute('SELECT state FROM events ORDER BY sequence')]

    def close(self) -> None:
        self.db.close()


class Orchestrator:
    def __init__(self, adapter: BrowserAdapter, context: AmendmentContext,
                 journal: Journal, run_id: str, *, authorized: bool = False,
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 max_polls: int = 10, poll_interval: float = 0.2):
        if max_polls < 1 or poll_interval < 0:
            raise ValueError('invalid wait bounds')
        if (context.basket_fingerprint != fingerprint(context.expected_quantities) or
                context.expected_quantities != expected_quantities(
                    context.baseline_quantities, context.requested_operations)):
            raise Halt(State.OUTCOME_UNKNOWN)
        self.adapter, self.context, self.journal = adapter, context, journal
        self.run_id, self.authorized = run_id, authorized
        self.now, self.sleep = now, sleep
        self.max_polls, self.poll_interval = max_polls, poll_interval
        binding = sha256(json.dumps([context.browser, context.order, context.slot,
            context.cutoff, context.baseline_quantities, context.expected_quantities,
            [(op.product, op.mode.value, op.quantity) for op in context.requested_operations]],
            sort_keys=True).encode()).hexdigest()
        context.submission_status = journal.bind(run_id, binding)

    def _state(self, state: State) -> State:
        self.context.stage = state
        self.journal.event(self.run_id, state)
        return state

    def _observe(self, *, allow_dialog: bool = True) -> Page:
        snapshot = self.adapter.observe()
        ctx = self.context
        if not snapshot.authenticated:
            raise Halt(State.AUTH_REQUIRED)
        if (not snapshot.orders_complete or len(snapshot.orders) != 1 or
                snapshot.orders[0].key != ctx.order):
            raise Halt(State.AMBIGUOUS_TARGET)
        order = snapshot.orders[0]
        if (order.slot != ctx.slot or order.cutoff != ctx.cutoff or
                (ctx.submission_status == Submission.NOT_STARTED and self.now() >= ctx.cutoff)):
            raise Halt(State.SLOT_DRIFT)
        candidates = [p for p in snapshot.pages if p.browser == ctx.browser and
                      p.order == ctx.order and
                      (p.making_changes or p.view == View.CONFIRMATION or
                       (ctx.stage == State.ORDER_DISCOVERY and p.view == View.ORDERS))]
        if not candidates:
            raise MissingTarget()
        if len(candidates) != 1:
            raise Halt(State.AMBIGUOUS_TARGET)
        page = candidates[0]
        if page.slot != ctx.slot:
            raise Halt(State.SLOT_DRIFT)
        ctx.target = page.target
        if page.dialog is not None:
            if (not allow_dialog or page.dialog.heading != CANCEL_DIALOG_HEADING or
                    set(page.dialog.controls) != CANCEL_DIALOG_CONTROLS or
                    ctx.submission_status != Submission.NOT_STARTED):
                raise Halt(State.UNEXPECTED_DIALOG)
            self.adapter.act(Action(ActionKind.KEEP_CHANGES, next(iter(LABEL_ALIASES['keep_changes']))), page)
            return self._observe(allow_dialog=False)
        return page

    def _poll(self, predicate: Callable[[Page], bool]) -> Page | None:
        for _ in range(self.max_polls):
            try:
                page = self._observe()
            except MissingTarget:
                self.sleep(self.poll_interval)
                continue
            if predicate(page):
                return page
            self.sleep(self.poll_interval)
        return None

    def _wait(self, predicate: Callable[[Page], bool]) -> Page:
        page = self._poll(predicate)
        if page is None:
            raise Halt(State.OUTCOME_UNKNOWN)
        return page

    def _enter(self) -> None:
        page = self._observe()
        if self.context.stage == State.ORDER_DISCOVERY:
            if page.view != View.ORDERS:
                raise Halt(State.OUTCOME_UNKNOWN)
            self.adapter.act(Action(ActionKind.ENTER_AMENDMENT, visible_label(page, 'enter_amendment')), page)
            self._wait(lambda p: p.making_changes and p.basket_complete and
                       dict(p.quantities) == self.context.baseline_quantities)
            self._state(State.AMENDING)

    def _checkout(self) -> Page:
        seen = set()
        for _ in range(4):  # checkout, offers, suggestions, summary
            page = self._wait(lambda p: p.view != View.LOADING and
                              (p.view not in (View.CHECKOUT, View.OFFERS, View.SUGGESTIONS) or
                               bool(LABEL_ALIASES['continue'].intersection(p.controls))))
            if page.view == View.SUMMARY:
                return self._wait(lambda p: p.view == View.SUMMARY and p.summary_complete)
            if (page.view not in (View.CHECKOUT, View.OFFERS, View.SUGGESTIONS) or page.view in seen or
                    not self._quantities_match(page)):
                raise Halt(State.OUTCOME_UNKNOWN)
            seen.add(page.view)
            self.adapter.act(Action(ActionKind.CONTINUE, visible_label(page, 'continue')), page)
            self._wait(lambda p: p.view != page.view)
        raise Halt(State.OUTCOME_UNKNOWN)

    def _mutate(self) -> None:
        expected_so_far = self.context.baseline_quantities.copy()
        for index, operation in enumerate(self.context.requested_operations):
            before = expected_so_far.copy()
            desired = self.context.expected_quantities.get(operation.product, 0)
            expected_so_far[operation.product] = desired
            expected_so_far = {p: q for p, q in expected_so_far.items() if q}
            current = self._observe()
            if current.view not in (View.BASKET, View.PRODUCTS, View.LANDING):
                raise Halt(State.OUTCOME_UNKNOWN)
            if current.basket_complete and dict(current.quantities) == expected_so_far:
                continue
            if not current.basket_complete or dict(current.quantities) != before:
                raise Halt(State.OUTCOME_UNKNOWN)
            self._state(State.PRODUCT_SELECTION)
            if operation.product not in current.verified_products:
                if not LABEL_ALIASES['search'].intersection(current.controls):
                    raise Halt(State.OUTCOME_UNKNOWN)
                self.adapter.act(Action(ActionKind.SEARCH_ENTER, visible_label(current, 'search'), operation.product), current)
                selected = self._poll(lambda p: operation.product in p.verified_products)
                if selected is None:
                    current = self._observe()
                    # Only a still-present unchanged search form permits fallback.
                    if (not LABEL_ALIASES['search'].intersection(current.controls) or current.view not in
                            (View.BASKET, View.PRODUCTS, View.LANDING) or
                            dict(current.quantities) != before):
                        raise Halt(State.OUTCOME_UNKNOWN)
                    self.adapter.act(Action(ActionKind.SEARCH_SUBMIT, visible_label(current, 'search'), operation.product), current)
                    selected = self._wait(lambda p: operation.product in p.verified_products)
                current = selected
            if (current.view not in (View.BASKET, View.PRODUCTS, View.LANDING) or
                    not current.basket_complete or dict(current.quantities) != before):
                raise Halt(State.OUTCOME_UNKNOWN)
            if self.journal.claim_mutation(self.run_id, index):
                try:
                    self.adapter.act(Action(ActionKind.SET_QUANTITY, '', operation.product, desired), current)
                except Exception:
                    pass  # Lost response: read visible quantities; never repeat input.
            self._wait(lambda p: p.basket_complete and dict(p.quantities) == expected_so_far)

    def _quantities_match(self, page: Page) -> bool:
        return page.basket_complete and dict(page.quantities) == self.context.expected_quantities

    def _reconcile(self) -> State:
        self._wait(lambda p: p.view == View.CONFIRMATION and p.visible_success
                   and self._quantities_match(p))
        self.context.submission_status = Submission.CONFIRMED
        return self._state(State.CONFIRMED)

    def run(self) -> State:
        """Run bounded policy steps. A halted run needs human review, not a blind rerun."""
        try:
            if self.context.submission_status != Submission.NOT_STARTED:
                return self._reconcile()
            self._enter()
            self._mutate()
            page = self._observe()
            if not self._quantities_match(page):
                raise Halt(State.OUTCOME_UNKNOWN)
            self._state(State.BASKET_VERIFIED)
            if page.view not in (View.BASKET, View.PRODUCTS, View.LANDING):
                raise Halt(State.OUTCOME_UNKNOWN)
            if LABEL_ALIASES['open_trolley'].intersection(page.controls):
                self.adapter.act(Action(ActionKind.OPEN_TROLLEY,
                                        visible_label(page, 'open_trolley')), page)
                page = self._wait(lambda p: p.view == View.BASKET)
                if not self._quantities_match(page):
                    raise Halt(State.OUTCOME_UNKNOWN)
            self.adapter.act(Action(ActionKind.CHECKOUT, visible_label(page, 'checkout')), page)
            self._state(State.CHECKOUT)
            page = self._checkout()
            if not self._quantities_match(page):
                raise Halt(State.OUTCOME_UNKNOWN)
            confirm_label = visible_label(page, 'confirm')
            self._state(State.SUMMARY_VERIFIED)
            if not self.authorized:
                return State.SUMMARY_VERIFIED
            if not self.journal.claim(self.run_id):
                raise Halt(State.OUTCOME_UNKNOWN)
            self.context.submission_status = Submission.PENDING
            self._state(State.SUBMISSION_PENDING)
            try:
                self.adapter.act(Action(ActionKind.CONFIRM, confirm_label), page)
            except Exception:
                pass  # The submission fence remains pending; only observe from now on.
            return self._reconcile()
        except Halt as error:
            return self._state(error.state)
        except Exception:
            # Do not expose transport exceptions, URLs, DOM, or credential strings.
            return self._state(State.OUTCOME_UNKNOWN)
