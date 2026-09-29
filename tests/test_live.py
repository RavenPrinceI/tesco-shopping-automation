"""Offline CDP contract tests. Never connect to a browser."""
import unittest
import copy
import json
from pathlib import Path
from dataclasses import replace
import tesco_amendment as t
import importlib
import importlib.util
import tempfile
import io
import asyncio
from contextlib import redirect_stdout
from unittest.mock import patch
from test_orchestration import EngineCase, FakeBrowser, basket, page, summary, confirmed


class LiveRouteTests(EngineCase):
    def test_live_adapter_is_read_only_by_default(self):
        self.assertIsNotNone(importlib.util.find_spec('tesco_live'), 'live adapter missing')
        live = importlib.import_module('tesco_live')
        class NoTransport:
            def call(self, *args, **kwargs):
                raise AssertionError('read-only act must not contact CDP')
        adapter = live.LiveAdapter(NoTransport(), {}, None)
        with self.assertRaises(t.Halt):
            adapter.act(t.Action(t.ActionKind.CONFIRM, 'Confirm order'), basket().pages[0])

    def test_landing_trolley_checkout_offers_sequence(self):
        start = page(basket(), view=t.View.LANDING, controls=('View trolley',))
        trolley = basket('Check out to confirm changes')
        checkout = page(trolley, view=t.View.CHECKOUT, controls=('Continue checkout',))
        offers = page(trolley, view=t.View.OFFERS, controls=('Continue checkout',))
        browser = FakeBrowser(start, [
            (t.ActionKind.OPEN_TROLLEY, trolley, None),
            (t.ActionKind.CHECKOUT, checkout, None),
            (t.ActionKind.CONTINUE, offers, None),
            (t.ActionKind.CONTINUE, summary(trolley), None),
            (t.ActionKind.CONFIRM, confirmed(trolley), None),
        ])
        self.assertEqual(self.engine(browser).run(), t.State.CONFIRMED)


class AdapterReplayTests(EngineCase):
    def test_detached_summary_never_confirms(self):
        import tesco_live as live
        wire = json.loads((Path(__file__).parent/'fixtures/frozen_peas.json').read_text())['evidence']
        wire.update(view='summary', making_changes=False, summary_complete=True,
                    controls=[dict(label='Confirm order', scope='order', x=100, y=100, reachable=True)])
        inputs = []
        class Fake:
            def identity(self, target):
                return live.Binding('browser-fixture', 'tab-fixture')
            def call(self, method, params):
                if method == 'Runtime.evaluate':
                    return {'result': {'value': copy.deepcopy(wire)}}
                inputs.append(method)
                return {}
        adapter = live.LiveAdapter(Fake(), {}, live.Binding('browser-fixture', 'tab-fixture'), execute=True)
        with self.assertRaises(t.Halt):
            adapter.act(t.Action(t.ActionKind.CONFIRM, 'Confirm order'), adapter.observe().pages[0])
        self.assertEqual(inputs, [])

    def test_missing_hit_test_never_clicks(self):
        import tesco_live as live
        wire = json.loads((Path(__file__).parent/'fixtures/frozen_peas.json').read_text())['evidence']
        inputs = []
        class Fake:
            def identity(self, target):
                return live.Binding('browser-fixture', 'tab-fixture')
            def call(self, method, params):
                if method != 'Runtime.evaluate':
                    inputs.append(method)
                    return {}
                return {'result': {'value': copy.deepcopy(wire)}}
        adapter = live.LiveAdapter(Fake(), {}, live.Binding('browser-fixture', 'tab-fixture'), execute=True)
        fresh = adapter.observe().pages[0]
        with self.assertRaises(t.Halt):
            adapter.act(t.Action(t.ActionKind.OPEN_TROLLEY, 'View trolley'), fresh)
        self.assertEqual(inputs, [])

    def test_frozen_peas_replay_through_actual_adapter(self):
        import tesco_live as live
        self.assertTrue(hasattr(live, 'Binding'), 'typed binding missing')
        fixture = json.loads((Path(__file__).parent / 'fixtures/frozen_peas.json').read_text())
        baseline = fixture['evidence']
        for control in baseline['controls'] + baseline['products'][0]['controls']:
            control['reachable'] = True
        updated = copy.deepcopy(baseline)
        updated['quantities']['222222222'] = 1
        updated['products'][0]['quantity'] = 1
        stages = [baseline, updated]
        for view in fixture['stages'][1:]:
            wire = copy.deepcopy(updated)
            wire['view'] = view
            wire['products'] = []
            label = {'basket': 'Check out to confirm changes',
                     'summary': 'Confirm order', 'confirmation': None}.get(view, 'Continue checkout')
            wire['controls'] = [] if label is None else [dict(label=label, scope='basket' if view == 'basket' else 'order', x=100, y=100, reachable=True)]
            if view == 'basket':
                wire['controls'].append(dict(label='Check out groceries', scope='page', x=300, y=100))
            wire['summary_complete'] = view == 'summary'
            wire['visible_success'] = view == 'confirmation'
            wire['making_changes'] = view != 'confirmation'
            stages.append(wire)

        class Replay:
            def __init__(self):
                self.index, self.clicks = 0, 0
            def identity(self, target):
                return live.Binding('browser-fixture', 'tab-fixture')
            def call(self, method, params):
                if method == 'Runtime.evaluate':
                    return {'result': {'value': copy.deepcopy(stages[self.index])}}
                if method == 'Input.dispatchMouseEvent' and params['type'] == 'mouseReleased':
                    self.clicks += 1
                    self.index += 1
                return {}
        transport = Replay()
        adapter = live.LiveAdapter(transport, {}, live.Binding('browser-fixture', 'tab-fixture'), execute=True)
        operations = (t.Operation('222222222', t.QuantityMode.ADD, 1),)
        context = t.discover(adapter.observe(), operations, now=10)
        engine = t.Orchestrator(adapter, context, self.journal, str(__import__('uuid').uuid4()),
                                authorized=True, now=lambda: 10, sleep=lambda _: None, max_polls=2)
        self.assertEqual(engine.run(), t.State.CONFIRMED)
        self.assertEqual(transport.clicks, 7)


class TransportAndStateTests(unittest.TestCase):
    def test_http_and_websocket_whole_call_deadlines(self):
        import tesco_live as live
        self.assertTrue(hasattr(live.CDPTransport, '_http_exchange'), 'HTTP aggregate deadline missing')
        transport = live.CDPTransport('http://127.0.0.1:9222', 'tab-fixture', timeout=0.01)
        transport.ws_url = 'ws://127.0.0.1:9222/devtools/page/tab-fixture'
        async def stalled(*args):
            await asyncio.sleep(10)
        with patch.object(transport, '_exchange', stalled), patch.object(transport, '_http_exchange', stalled):
            with self.assertRaises(t.Halt):
                transport.call('Runtime.evaluate', {})
            with self.assertRaises(t.Halt):
                transport._json('/json/list')

    def test_cli_execute_once_then_read_only_reconcile(self):
        import tesco_live as live
        wire = json.loads((Path(__file__).parent/'fixtures/frozen_peas.json').read_text())['evidence']
        wire['view'] = 'basket'
        wire['controls'] = [dict(label='Check out to confirm changes', scope='basket', x=50, y=50, reachable=True)]
        clicks = []
        class Transport:
            def __init__(self, *args, **kwargs):
                self.execute = kwargs['execute']
            def identity(self, target):
                return live.Binding('browser-fixture', 'tab-fixture')
            def call(self, method, params):
                if method == 'Runtime.evaluate':
                    return {'result': {'value': copy.deepcopy(wire)}}
                assert self.execute
                if params['type'] == 'mouseReleased':
                    clicks.append(wire['view'])
                    if wire['view'] == 'basket':
                        wire.update(view='summary', summary_complete=True,
                            controls=[dict(label='Confirm order', scope='order', x=60, y=60, reachable=True)])
                    else:
                        wire.update(view='confirmation', making_changes=False, visible_success=True, controls=[])
                return {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'map.json').write_text('{}')
            (root/'plan.json').write_text('[]')
            args = ['--site-map', str(root/'map.json'), '--plan', str(root/'plan.json'),
                    '--state-dir', str(root/'state'), '--target', 'tab-fixture']
            with redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(args + ['--execute'], transport_factory=Transport), 2)
                self.assertEqual(clicks, [])
                self.assertEqual(live.main(args, transport_factory=Transport), 0)
                self.assertEqual(live.main(args + ['--execute'], transport_factory=Transport), 0)
                self.assertEqual(live.main(args + ['--execute'], transport_factory=Transport), 2)
                self.assertEqual(live.main(args + ['--reconcile'], transport_factory=Transport), 0)
            self.assertEqual(clicks, ['basket', 'summary'])

    def test_cli_defaults_to_read_only_and_redacts_errors(self):
        import tesco_live as live
        self.assertTrue(hasattr(live, 'main'), 'CLI missing')
        wire = json.loads((Path(__file__).parent/'fixtures/frozen_peas.json').read_text())['evidence']
        class ReadOnlyTransport:
            def __init__(self, *args, **kwargs):
                self.execute = kwargs.get('execute', False)
            def identity(self, target):
                return live.Binding('browser-fixture', 'tab-fixture')
            def call(self, method, params):
                if method != 'Runtime.evaluate' or self.execute:
                    raise AssertionError('Unexpected mutation')
                return {'result': {'value': wire}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'map.json').write_text('{}')
            (root/'plan.json').write_text('[{"product":"222222222","mode":"add","quantity":1}]')
            args = ['--site-map', str(root/'map.json'), '--plan', str(root/'plan.json'),
                    '--state-dir', str(root/'state'), '--target', 'tab-fixture']
            output = io.StringIO()
            with redirect_stdout(output), patch.object(t.Orchestrator, 'run', side_effect=AssertionError('run is mutating')):
                result = live.main(args, transport_factory=ReadOnlyTransport)
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), 'READ_ONLY\n')
            (root/'plan.json').write_text('DO_NOT_LOG_ACCOUNT_DATA')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(live.main(args, transport_factory=ReadOnlyTransport), 2)
            self.assertEqual(output.getvalue(), 'OUTCOME_UNKNOWN\n')

    def test_transport_rejects_remote_endpoint_and_has_finite_deadline(self):
        import tesco_live as live
        self.assertTrue(hasattr(live, 'CDPTransport'), 'CDP transport missing')
        for endpoint in ('http://example.com:9222', 'http://localhost:9222',
                         'http://127.0.0.1:9222/path', 'http://x@127.0.0.1:9222'):
            with self.assertRaises(t.Halt):
                live.CDPTransport(endpoint, 'tab-fixture')
        for timeout in (0, float('inf'), -1, 61):
            with self.assertRaises(t.Halt):
                live.CDPTransport('http://127.0.0.1:9222', 'tab-fixture', timeout=timeout)

    def test_state_retains_original_baseline_and_prevents_second_execution(self):
        import tesco_live as live
        self.assertTrue(hasattr(live, 'RunStore'), 'private persistence missing')
        with tempfile.TemporaryDirectory() as directory:
            with live.RunStore(Path(directory)/'private') as store:
                context = t.discover(basket(), (), now=10)
                context.order = live.digest(context.order)
                context.slot = live.digest(context.slot)
                context.baseline_quantities = {'111111111': 2}
                context.expected_quantities = {'111111111': 2}
                context.basket_fingerprint = t.fingerprint(context.expected_quantities)
                binding = live.Binding(context.browser, context.target)
                stored = store.prepare(binding, {}, (), lambda: context)
                store.claim_execution()
                with self.assertRaises(t.Halt):
                    store.claim_execution()
            with live.RunStore(Path(directory)/'private') as store:
                restored = store.prepare(binding, {}, (), lambda: self.fail('must not rediscover'))
                self.assertEqual(stored, restored)
                with self.assertRaises(t.Halt):
                    store.prepare(live.Binding('another-browser', context.target), {}, (), lambda: context)
            for path in (Path(directory)/'private').iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(b'order-fixture', path.read_bytes())


if __name__ == '__main__':
    unittest.main()
