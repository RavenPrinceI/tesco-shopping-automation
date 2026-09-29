"""Additional fail-closed checks. All transports are in-memory fakes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

import tesco_amendment as t
import tesco_live as live


class WireTransport:
    def __init__(self):
        self.wire = json.loads((Path(__file__).parent/'fixtures/frozen_peas.json').read_text())['evidence']
        self.binding = live.Binding('browser-fixture', 'tab-fixture')
        self.inputs = []
    def identity(self, target):
        return self.binding
    def call(self, method, params):
        if method == 'Runtime.evaluate':
            return {'result': {'value': copy.deepcopy(self.wire)}}
        self.inputs.append((method, params))
        return {}


class SafetyTests(unittest.TestCase):
    def test_websocket_disables_environment_proxy(self):
        options = {}
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, value): pass
            async def recv(self): return '{"id":1,"result":{}}'
        def connect(url, **kwargs):
            options.update(kwargs)
            return Socket()
        transport = live.CDPTransport('http://127.0.0.1:9222', 'tab-fixture')
        transport.ws_url = 'ws://127.0.0.1:9222/devtools/page/tab-fixture'
        with patch.dict(sys.modules, {'websockets':SimpleNamespace(connect=connect)}):
            asyncio.run(transport._exchange('Runtime.evaluate', {}))
        self.assertIn('proxy', options)
        self.assertIsNone(options['proxy'])

    @unittest.skipUnless(shutil.which('node'), 'Node is optional for the offline DOM double')
    def test_dom_extractor_on_sanitized_fixture(self):
        fixtures = Path(__file__).parent/'fixtures'
        result = subprocess.run(['node', str(fixtures/'dom_contract.cjs'), str(fixtures/'site_map.json')],
            input=live.DOM_SCRIPT, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DOM fixture checks passed', result.stdout)

    def setUp(self):
        self.transport = WireTransport()
        self.adapter = live.LiveAdapter(self.transport, {}, self.transport.binding, execute=True)

    def test_live_binding_change_refused(self):
        self.transport.binding = live.Binding('other-browser', 'tab-fixture')
        with self.assertRaises(t.Halt):
            self.adapter.observe()

    def test_cutoff_change_between_observation_and_input_is_refused(self):
        for c in self.transport.wire['controls']:
            c['reachable'] = True
        page = self.adapter.observe().pages[0]
        self.transport.wire['cutoff'] = '2099-01-02T12:00:00+00:00'
        self.transport.wire['orders'][0]['cutoff'] = self.transport.wire['cutoff']
        with self.assertRaises(t.Halt):
            self.adapter.act(t.Action(t.ActionKind.OPEN_TROLLEY, 'View trolley'), page)
        self.assertEqual(self.transport.inputs, [])

    def test_revalidation_blocks_slot_basket_and_order_drift(self):
        for field, value in [('slot', 'new-slot'), ('quantities', {'111111111': 8}),
                             ('order', 'new-order'), ('making_changes', False)]:
            with self.subTest(field=field):
                self.setUp()
                page = self.adapter.observe().pages[0]
                self.transport.wire[field] = value
                with self.assertRaises(t.Halt):
                    self.adapter.act(t.Action(t.ActionKind.OPEN_TROLLEY, 'View trolley'), page)
                self.assertEqual(self.transport.inputs, [])

    def test_ambiguous_checkout_never_selects_first(self):
        c = dict(label='Check out to confirm changes', scope='basket', x=1, y=2, reachable=True)
        self.transport.wire['controls'] = [c, dict(c)]
        with self.assertRaises(t.Halt):
            self.adapter.observe()

    def test_pagewide_checkout_is_not_a_basket_control(self):
        self.transport.wire['controls'] = [dict(label='Check out groceries', scope='page', x=1, y=2)]
        self.assertEqual(self.adapter.observe().pages[0].controls, ())

    def test_specific_checkout_wins_within_bound_basket(self):
        self.transport.wire['controls'] = [dict(label=label, scope='basket', x=1, y=2)
            for label in ('Check out groceries', 'Check out to confirm changes')]
        self.assertEqual(self.adapter.observe().pages[0].controls, ('Check out to confirm changes',))

    def test_exact_dialog_only_and_no_pagewide_no(self):
        self.transport.wire['dialog'] = dict(heading='Cancel changes?', controls=[
            dict(label='No', x=30, y=40, reachable=True), dict(label='Yes, cancel', x=50, y=60, reachable=True)])
        self.transport.wire['controls'].append(dict(label='No', scope='order', x=90, y=90, reachable=True))
        self.adapter.act(t.Action(t.ActionKind.KEEP_CHANGES, 'No'), self.adapter.observe().pages[0])
        self.assertEqual([p['x'] for _, p in self.transport.inputs], [30, 30])
        self.transport.inputs.clear()
        self.transport.wire['dialog']['heading'] = 'Cancel changes? Other question'
        with self.assertRaises(t.Halt):
            self.adapter.act(t.Action(t.ActionKind.KEEP_CHANGES, 'No'), self.adapter.observe().pages[0])
        self.assertEqual(self.transport.inputs, [])

    def test_missing_full_basket_never_mutates(self):
        self.transport.wire['basket_complete'] = False
        with self.assertRaises(t.Halt):
            self.adapter.act(t.Action(t.ActionKind.OPEN_TROLLEY, 'View trolley'), self.adapter.observe().pages[0])
        self.assertEqual(self.transport.inputs, [])

    def test_multi_unit_jump_and_search_are_refused(self):
        for action in [t.Action(t.ActionKind.SET_QUANTITY, '', '222222222', 3),
                       t.Action(t.ActionKind.SEARCH_ENTER, 'Search', '222222222')]:
            with self.assertRaises(t.Halt):
                self.adapter.act(action, self.adapter.observe().pages[0])
        self.assertEqual(self.transport.inputs, [])

    def test_transport_error_is_redacted(self):
        with patch.object(self.transport, 'call', side_effect=RuntimeError('SECRET_FIXTURE')):
            with self.assertRaises(t.Halt) as caught:
                self.adapter.observe()
            self.assertEqual(str(caught.exception), 'OUTCOME_UNKNOWN')

    def test_private_store_rejects_world_readable_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root/'unsafe'
            unsafe.mkdir(mode=0o755)
            with self.assertRaises(t.Halt):
                live.RunStore(unsafe)
            safe = root/'safe'
            safe.mkdir(mode=0o700)
            link = root/'link'
            link.symlink_to(safe, target_is_directory=True)
            with self.assertRaises(t.Halt):
                live.RunStore(link)

    def test_endpoint_response_cannot_redirect_websocket(self):
        transport = live.CDPTransport('http://127.0.0.1:9222', 'tab-fixture')
        with patch.object(transport, '_json', return_value={'webSocketDebuggerUrl':'ws://example.com/devtools/browser/test'}):
            with self.assertRaises(t.Halt):
                transport.identity('tab-fixture')

    def test_transport_input_guard_precedes_network(self):
        transport = live.CDPTransport('http://127.0.0.1:9222', 'tab-fixture')
        transport.ws_url = 'ws://127.0.0.1:9222/devtools/page/tab-fixture'
        with patch.object(transport, '_exchange', side_effect=AssertionError('network forbidden')) as exchange:
            with self.assertRaises(t.Halt):
                transport.call('Input.dispatchMouseEvent', {})
            exchange.assert_not_called()


if __name__ == '__main__':
    unittest.main()
