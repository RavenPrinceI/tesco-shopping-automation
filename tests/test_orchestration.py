"""Scripted visible-page fixtures exercise policy, not a live Tesco client."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import tesco_amendment as t
from test_policy import fixture


class FakeBrowser:
    def __init__(self, snapshot, transitions=()):
        self.snapshot = snapshot
        self.transitions = list(transitions)
        self.actions = []
        self.observations = 0

    def observe(self):
        self.observations += 1
        return self.snapshot

    def act(self, action, page):
        # A real adapter must also recheck the page binding/control before input.
        assert page in self.snapshot.pages
        self.actions.append(action)
        if self.transitions:
            expected_kind, snapshot, error = self.transitions.pop(0)
            assert action.kind == expected_kind, (action.kind, expected_kind)
            self.snapshot = snapshot
            if error:
                raise error


def page(snapshot, **changes):
    return replace(snapshot, pages=(replace(snapshot.pages[0], **changes),))


def basket(label='Check out groceries'):
    return page(fixture(), controls=(label,), basket_complete=True)


def summary(snapshot):
    return page(snapshot, view=t.View.SUMMARY, controls=('Confirm order',),
                summary_complete=True)


def confirmed(snapshot):
    return page(snapshot, view=t.View.CONFIRMATION, making_changes=False,
                controls=(), visible_success=True)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = t.Journal(Path(self.temp.name) / 'journal.sqlite')
        self.addCleanup(self.journal.close)

    def engine(self, adapter, context=None):
        context = context or t.discover(adapter.snapshot, (), now=10)
        return t.Orchestrator(adapter, context, self.journal, str(uuid4()),
                              authorized=True, now=lambda: 10, sleep=lambda _: None, max_polls=3)


class EngineTests(EngineCase):
    def test_checkout_aliases_require_visible_summary_and_confirmation(self):
        for label in ('Checkout to confirm changes', 'Check out to confirm changes',
                      'Check out groceries'):
            with self.subTest(label=label):
                start = basket(label)
                end = confirmed(start)
                browser = FakeBrowser(start, [
                    (t.ActionKind.CHECKOUT, summary(start), None),
                    (t.ActionKind.CONFIRM, end, None),
                ])
                engine = self.engine(browser)
                self.assertEqual(engine.run(), t.State.CONFIRMED)
                self.assertEqual(engine.context.submission_status, t.Submission.CONFIRMED)
                self.assertEqual([a.kind for a in browser.actions],
                                 [t.ActionKind.CHECKOUT, t.ActionKind.CONFIRM])


    def test_enters_amendment_from_single_order_card(self):
        start = page(basket(), view=t.View.ORDERS, making_changes=False,
                     controls=('Make changes',))
        amended = basket()
        browser = FakeBrowser(start, [
            (t.ActionKind.ENTER_AMENDMENT, amended, None),
            (t.ActionKind.CHECKOUT, summary(amended), None),
            (t.ActionKind.CONFIRM, confirmed(amended), None),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.CONFIRMED)

    def test_optional_checkout_stages_and_delayed_render(self):
        start = basket()
        offers = page(start, view=t.View.OFFERS, controls=('Continue to checkout',))
        suggestions = page(start, view=t.View.SUGGESTIONS, controls=('Continue to checkout',))
        loading = page(start, view=t.View.LOADING, controls=())
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, loading, None),
            (t.ActionKind.CONTINUE, suggestions, None),
            (t.ActionKind.CONTINUE, summary(start), None),
            (t.ActionKind.CONFIRM, confirmed(start), None),
        ])
        engine = self.engine(browser)
        engine.sleep = lambda _: setattr(browser, 'snapshot', offers)
        self.assertEqual(engine.run(), t.State.CONFIRMED)
        self.assertEqual([a.kind for a in browser.actions].count(t.ActionKind.CONTINUE), 2)

    def test_unknown_dialog_stops_without_input(self):
        start = page(basket(), dialog=t.Dialog('Unrecognized fixture question', ('No', 'Yes')))
        browser = FakeBrowser(start)
        self.assertEqual(self.engine(browser).run(), t.State.UNEXPECTED_DIALOG)
        self.assertEqual(browser.actions, [])

    def test_only_exact_cancel_changes_dialog_chooses_no(self):
        start = page(basket(), dialog=t.Dialog('Cancel changes?', ('No', 'Yes, cancel')))
        browser = FakeBrowser(start, [
            (t.ActionKind.KEEP_CHANGES, basket(), None),
            (t.ActionKind.CHECKOUT, summary(basket()), None),
            (t.ActionKind.CONFIRM, confirmed(basket()), None),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.CONFIRMED)
        self.assertEqual(browser.actions[0].label, 'No')


class MutationTests(EngineCase):
    def test_checkout_from_verified_product_results(self):
        start = page(basket(), view=t.View.PRODUCTS)
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, confirmed(start), None),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.CONFIRMED)

    def test_search_cannot_mutate_if_results_drift_to_summary(self):
        start = page(basket(), controls=('Search', 'Check out groceries'))
        drifted = page(summary(start), verified_products=('new-product-fixture',))
        browser = FakeBrowser(start, [(t.ActionKind.SEARCH_ENTER, drifted, None)])
        context = t.discover(start, (t.Operation('new-product-fixture', t.QuantityMode.ADD, 1),), now=10)
        self.assertEqual(self.engine(browser, context).run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual(len(browser.actions), 1)

    def test_invalid_context_fingerprint_is_refused(self):
        start = basket()
        context = replace(t.discover(start, (), now=10), basket_fingerprint='invalid-fixture')
        with self.assertRaises(t.Halt):
            self.engine(FakeBrowser(start), context)

    def test_failed_enter_uses_one_checked_form_fallback(self):
        start = page(basket(), controls=('Search', 'Check out groceries'))
        results = page(start, view=t.View.PRODUCTS,
                       verified_products=('new-product-fixture',))
        edited = page(start, quantities={'product-fixture': 2, 'new-product-fixture': 1})
        browser = FakeBrowser(start, [
            (t.ActionKind.SEARCH_ENTER, start, None),
            (t.ActionKind.SEARCH_SUBMIT, results, None),
            (t.ActionKind.SET_QUANTITY, edited, None),
            (t.ActionKind.CHECKOUT, summary(edited), None),
            (t.ActionKind.CONFIRM, confirmed(edited), None),
        ])
        context = t.discover(start, (t.Operation('new-product-fixture', t.QuantityMode.ADD, 1),), now=10)
        self.assertEqual(self.engine(browser, context).run(), t.State.CONFIRMED)
        self.assertEqual([a.kind for a in browser.actions].count(t.ActionKind.SEARCH_SUBMIT), 1)

    def test_lost_quantity_response_reconciles_without_second_input(self):
        start = page(basket(), verified_products=('product-fixture',))
        edited = page(start, quantities={'product-fixture': 3})
        browser = FakeBrowser(start, [
            (t.ActionKind.SET_QUANTITY, edited, TimeoutError('synthetic private text')),
            (t.ActionKind.CHECKOUT, summary(edited), None),
            (t.ActionKind.CONFIRM, confirmed(edited), None),
        ])
        context = t.discover(start, (t.Operation('product-fixture', t.QuantityMode.ADD, 1),), now=10)
        self.assertEqual(self.engine(browser, context).run(), t.State.CONFIRMED)
        self.assertEqual(browser.actions[0].quantity, 3)
        self.assertEqual([a.kind for a in browser.actions].count(t.ActionKind.SET_QUANTITY), 1)


class SimulatedCrash(BaseException):
    pass


class RecoveryTests(EngineCase):
    def test_slot_drift_blocks_final_click(self):
        start = basket()
        drift = page(summary(start), slot='changed-slot-fixture')
        browser = FakeBrowser(start, [(t.ActionKind.CHECKOUT, drift, None)])
        self.assertEqual(self.engine(browser).run(), t.State.SLOT_DRIFT)
        self.assertEqual(len(browser.actions), 1)

    def test_disappearing_payment_target_reconciles_new_confirmation_target(self):
        start = basket()
        payment = page(start, view=t.View.PAYMENT, making_changes=False,
                       target='payment-tab-fixture', controls=())
        missing = replace(start, pages=())
        restored = page(confirmed(start), target='replacement-tab-fixture')
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, payment, None),
        ])
        engine = self.engine(browser)
        recovery = iter((missing, restored))
        engine.sleep = lambda _: setattr(browser, 'snapshot', next(recovery))
        self.assertEqual(engine.run(), t.State.CONFIRMED)
        self.assertEqual(engine.context.target, 'replacement-tab-fixture')
        self.assertEqual(len(browser.actions), 2)

    def test_delayed_offers_controls_are_polled(self):
        start = basket()
        waiting = page(start, view=t.View.OFFERS, controls=())
        rendered = page(waiting, controls=('Continue to checkout',))
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, waiting, None),
            (t.ActionKind.CONTINUE, summary(start), None),
            (t.ActionKind.CONFIRM, confirmed(start), None),
        ])
        engine = self.engine(browser)
        engine.sleep = lambda _: setattr(browser, 'snapshot', rendered)
        self.assertEqual(engine.run(), t.State.CONFIRMED)

    def test_payment_page_is_read_only_while_waiting_for_confirmation(self):
        start = basket()
        payment = page(start, view=t.View.PAYMENT, making_changes=False, controls=())
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, payment, None),
        ])
        engine = self.engine(browser)
        engine.sleep = lambda _: setattr(browser, 'snapshot', confirmed(start))
        self.assertEqual(engine.run(), t.State.CONFIRMED)
        self.assertEqual(len(browser.actions), 2)

    def test_mutations_cannot_run_on_checkout_page(self):
        start = page(basket(), view=t.View.SUMMARY, verified_products=('product-fixture',))
        edited = page(start, quantities={'product-fixture': 3})
        browser = FakeBrowser(start, [(t.ActionKind.SET_QUANTITY, edited, None)])
        context = t.discover(start, (t.Operation('product-fixture', t.QuantityMode.ADD, 1),), now=10)
        self.assertEqual(self.engine(browser, context).run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual(browser.actions, [])

    def test_lost_final_response_reconciles_without_reclick(self):
        start = basket()
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, confirmed(start), TimeoutError('private fixture error')),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.CONFIRMED)
        self.assertEqual(len(browser.actions), 2)

    def test_crash_before_or_after_final_click_never_retries(self):
        for after_click in (False, True):
            with self.subTest(after_click=after_click):
                start = basket()
                after = confirmed(start) if after_click else summary(start)
                browser = FakeBrowser(start, [
                    (t.ActionKind.CHECKOUT, summary(start), None),
                    (t.ActionKind.CONFIRM, after, SimulatedCrash()),
                ])
                engine = self.engine(browser)
                with self.assertRaises(SimulatedCrash):
                    engine.run()
                # Reopen storage to prove the pending fence survived connection loss.
                reopened = t.Journal(Path(self.temp.name) / 'journal.sqlite')
                self.addCleanup(reopened.close)
                fresh = t.Orchestrator(browser, engine.context, reopened, engine.run_id,
                    authorized=True, now=lambda: 10, sleep=lambda _: None, max_polls=3)
                expected = t.State.CONFIRMED if after_click else t.State.OUTCOME_UNKNOWN
                self.assertEqual(fresh.run(), expected)
                self.assertEqual([a.kind for a in browser.actions].count(t.ActionKind.CONFIRM), 1)

    def test_confirmation_route_without_visible_success_is_not_success(self):
        start = basket()
        misleading = page(confirmed(start), visible_success=False,
                          route_hint='confirmation?isAmendedOrder=true')
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, misleading, None),
        ])
        engine = self.engine(browser)
        self.assertEqual(engine.run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual(engine.run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual([a.kind for a in browser.actions].count(t.ActionKind.CONFIRM), 1)
        self.assertLess(browser.observations, 20)

    def test_journal_rejects_arbitrary_identifiers_and_bindings(self):
        with self.assertRaises(ValueError):
            self.journal.bind(str(uuid4()), 'NOT_A_DIGEST_FIXTURE')
        with self.assertRaises(ValueError):
            self.journal.event('NOT_A_UUID_FIXTURE', t.State.AMENDING)

    def test_auth_cutoff_and_ambiguous_target_block_all_input(self):
        for transform, expected in (
            (lambda s: replace(s, authenticated=False), t.State.AUTH_REQUIRED),
            (lambda s: replace(s, orders_complete=False), t.State.AMBIGUOUS_TARGET),
            (lambda s: replace(s, pages=(*s.pages, replace(s.pages[0], target='duplicate-fixture'))),
             t.State.AMBIGUOUS_TARGET),
            (lambda s: replace(s, orders=(replace(s.orders[0], cutoff=0),)), t.State.SLOT_DRIFT),
        ):
            start = basket()
            browser = FakeBrowser(start)
            engine = self.engine(browser)
            browser.snapshot = transform(start)
            self.assertEqual(engine.run(), expected)
            self.assertEqual(browser.actions, [])

    def test_pending_submission_does_not_auto_dismiss_dialog(self):
        start = basket()
        dialog = page(confirmed(start), dialog=t.Dialog('Cancel changes?', ('No', 'Yes, cancel')))
        browser = FakeBrowser(start, [
            (t.ActionKind.CHECKOUT, summary(start), None),
            (t.ActionKind.CONFIRM, dialog, None),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.UNEXPECTED_DIALOG)
        self.assertEqual(len(browser.actions), 2)

    def test_unchanged_quantity_after_timeout_never_retries(self):
        start = page(basket(), verified_products=('product-fixture',))
        browser = FakeBrowser(start, [(t.ActionKind.SET_QUANTITY, start, TimeoutError())])
        context = t.discover(start, (t.Operation('product-fixture', t.QuantityMode.ADD, 1),), now=10)
        engine = self.engine(browser, context)
        self.assertEqual(engine.run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual(engine.run(), t.State.OUTCOME_UNKNOWN)
        self.assertEqual(len(browser.actions), 1)

    def test_incomplete_or_changed_summary_never_submits(self):
        for bad in (page(summary(basket()), summary_complete=False),
                    page(summary(basket()), quantities={'product-fixture': 9})):
            browser = FakeBrowser(basket(), [(t.ActionKind.CHECKOUT, bad, None)])
            self.assertEqual(self.engine(browser).run(), t.State.OUTCOME_UNKNOWN)
            self.assertEqual(len(browser.actions), 1)

    def test_unauthorized_run_stops_at_verified_summary(self):
        start = basket()
        browser = FakeBrowser(start, [(t.ActionKind.CHECKOUT, summary(start), None)])
        engine = self.engine(browser)
        engine.authorized = False
        self.assertEqual(engine.run(), t.State.SUMMARY_VERIFIED)
        self.assertEqual(len(browser.actions), 1)
        self.assertEqual(engine.context.submission_status, t.Submission.NOT_STARTED)

    def test_database_fence_is_shared_between_connections(self):
        start = basket()
        engine = self.engine(FakeBrowser(start))
        other = t.Journal(Path(self.temp.name) / 'journal.sqlite')
        self.addCleanup(other.close)
        self.assertTrue(self.journal.claim(engine.run_id))
        self.assertFalse(other.claim(engine.run_id))

    def test_changed_context_cannot_reuse_run_identity(self):
        engine = self.engine(FakeBrowser(basket()))
        changed = replace(engine.context, slot='different-slot-fixture')
        with self.assertRaises(t.Halt):
            t.Orchestrator(engine.adapter, changed, self.journal, engine.run_id)

    def test_redacted_journal_contains_no_labels_or_exception_text(self):
        start = basket()
        browser = FakeBrowser(start, [(t.ActionKind.CHECKOUT, start,
                                      RuntimeError('DO_NOT_LOG_FIXTURE_TEXT'))])
        engine = self.engine(browser)
        self.assertEqual(engine.run(), t.State.OUTCOME_UNKNOWN)
        data = (Path(self.temp.name) / 'journal.sqlite').read_bytes()
        for forbidden in (b'DO_NOT_LOG_FIXTURE_TEXT', b'order-fixture', b'slot-fixture',
                          b'product-fixture', b'Check out groceries'):
            self.assertNotIn(forbidden, data)
        self.assertTrue(all(value in {s.value for s in t.State}
                            for value in self.journal.events()))
        with self.assertRaises(ValueError):
            self.journal.event(engine.run_id, 'DO_NOT_LOG_FIXTURE_TEXT')  # type: ignore[arg-type]


if __name__ == '__main__':
    unittest.main()
