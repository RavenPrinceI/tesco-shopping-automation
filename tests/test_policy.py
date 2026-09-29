"""Synthetic fixtures only: no browser, network, or account data."""
import unittest
from dataclasses import replace

import tesco_amendment as t


def fixture(**changes):
    order = t.Order('order-fixture', 'slot-fixture', 1000)
    page = t.Page('tab-fixture', 'browser-fixture', order.key, order.slot,
                  t.View.BASKET, making_changes=True, quantities={'product-fixture': 2},
                  basket_complete=True)
    return replace(t.Snapshot((order,), (page,)), **changes)


class DiscoveryTests(unittest.TestCase):
    def test_detached_trolley_is_not_selected(self):
        snapshot = fixture()
        detached = replace(snapshot.pages[0], target='detached-fixture',
                           order=None, making_changes=False)
        context = t.discover(replace(snapshot, pages=(detached, *snapshot.pages)), (), now=10)
        self.assertEqual(context.target, 'tab-fixture')
        self.assertEqual(context.stage, t.State.AMENDING)
        self.assertEqual(context.baseline_quantities, {'product-fixture': 2})

    def test_incomplete_baseline_refused(self):
        snapshot = fixture()
        snapshot = replace(snapshot, pages=(replace(snapshot.pages[0], basket_complete=False),))
        with self.assertRaises(t.Halt) as error:
            t.discover(snapshot, (), now=10)
        self.assertEqual(error.exception.state, t.State.OUTCOME_UNKNOWN)

    def test_invalid_cutoff_fails_closed(self):
        for cutoff in (float('nan'), float('inf'), 0):
            snapshot = fixture(orders=(t.Order('order-fixture', 'slot-fixture', cutoff),))
            with self.subTest(cutoff=cutoff), self.assertRaises(t.Halt):
                t.discover(snapshot, (), now=10)

    def test_multiple_active_orders_refused(self):
        snapshot = fixture()
        with self.assertRaises(t.Halt) as error:
            t.discover(replace(snapshot, orders=(*snapshot.orders,
                t.Order('other-fixture', 'slot-fixture', 1000))), (), now=10)
        self.assertEqual(error.exception.state, t.State.AMBIGUOUS_TARGET)

    def test_two_bound_targets_refused(self):
        snapshot = fixture()
        with self.assertRaises(t.Halt):
            t.discover(replace(snapshot, pages=(*snapshot.pages,
                replace(snapshot.pages[0], target='other-tab-fixture'))), (), now=10)


class QuantityTests(unittest.TestCase):
    def test_add_and_set_have_distinct_existing_quantity_semantics(self):
        baseline = {'product-fixture': 2, 'untouched-fixture': 4}
        self.assertEqual(t.expected_quantities(baseline, (
            t.Operation('product-fixture', t.QuantityMode.ADD, 1),)),
            {'product-fixture': 3, 'untouched-fixture': 4})
        self.assertEqual(t.expected_quantities(baseline, (
            t.Operation('product-fixture', t.QuantityMode.SET, 1),)),
            {'product-fixture': 1, 'untouched-fixture': 4})
        self.assertEqual(t.expected_quantities(baseline, (
            t.Operation('product-fixture', t.QuantityMode.SET, 0),)),
            {'untouched-fixture': 4})

    def test_invalid_or_duplicate_operations_refused(self):
        for operations in [
            (t.Operation('product-fixture', t.QuantityMode.ADD, -1),),
            (t.Operation('product-fixture', t.QuantityMode.SET, True),),
            (t.Operation('product-fixture', t.QuantityMode.ADD, 1),) * 2,
        ]:
            with self.subTest(operations=operations), self.assertRaises(ValueError):
                t.expected_quantities({}, operations)


if __name__ == '__main__':
    unittest.main()
