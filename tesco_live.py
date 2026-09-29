"""Conservative visible-UI CDP adapter. No Tesco HTTP APIs or page-state writes.

Site maps are reviewed deployment configuration, not downloaded page instructions.
Raw page evidence stays in memory. Public errors contain only policy enums.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import time
import argparse
from pathlib import Path
from typing import Any, Callable

import tesco_amendment as t
from tesco_cdp import Binding, CDPTransport, RunStore, digest, stop


def cutoff(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        stop(t.State.SLOT_DRIFT)
    return parsed.timestamp()


# Pure DOM reads only. This script never reads password fields, cookies, storage,
# hidden application state or network responses. Its output is never logged.
DOM_SCRIPT = r"""(m => {
  const fail = () => { throw Error('UNSUPPORTED_VISIBLE_STATE'); };
  const visible = e => e && e.getClientRects().length &&
    getComputedStyle(e).visibility === 'visible' && getComputedStyle(e).display !== 'none' &&
    !e.closest('[hidden],[aria-hidden="true"],[inert]');
  const all = (r,s) => [...r.querySelectorAll(s)].filter(visible);
  const one = (r,s) => { const es=all(r,s); if(es.length!==1) fail(); return es[0]; };
  const text = e => {
    if (e.matches('input,textarea,select') || e.isContentEditable) fail();
    return e.innerText.trim().replace(/\s+/g,' ');
  };
  const txt = (r,s) => text(one(r,s));
  const exact = (r,v) => !!v && txt(r,v.selector)===v.text;
  const integer = s => { if(!/^(0|[1-9][0-9]*)$/.test(s)) fail(); return Number(s); };
  const quantity = e => {
    if(e.matches('input[type="number"]')) return integer(e.value);
    return integer(text(e));
  };
  const name = e => e.getAttribute('aria-label') || text(e);
  const controls = (r,scope) => all(r,'button,a[href],[role="button"]').filter(e =>
    !e.disabled && e.getAttribute('aria-disabled')!=='true').map(e => {
      const b=e.getBoundingClientRect(), x=b.x+b.width/2, y=b.y+b.height/2;
      const hit=document.elementFromPoint(x,y);
      return {label:name(e).trim(), scope, x, y, reachable:!!hit && (hit===e || e.contains(hit))};
    });
  if(location.origin!=='https://www.tesco.com' || !location.pathname.startsWith('/groceries/')) fail();
  if(all(document,'input[type="password"]').length) return {authenticated:false};
  const matches=Object.entries(m.views).filter(([v,s]) => all(document,s.root).length===1 && exact(one(document,s.root),s.heading));
  if(matches.length!==1) fail();
  const [view,s]=matches[0], root=one(document,s.root);
  const o=m.orders, ordersRoot=one(document,o.root);
  const orders=all(ordersRoot,o.rows).map(e => ({order:txt(e,o.order),slot:txt(e,o.slot),cutoff:txt(e,o.cutoff)}));
  const b=s.basket, basket=one(root,b.root), quantities={};
  const productKey = e => {
    const u=new URL(e.getAttribute('href'),location.href);
    const match=u.pathname.match(/^\/groceries\/en-GB\/products\/(\d+)\/?$/);
    if(u.origin!==location.origin || !match) fail(); return match[1];
  };
  const rows=all(basket,b.rows);
  rows.forEach(e => {const key=productKey(one(e,b.product)); if(key in quantities) fail();
    const q=quantity(one(e,b.quantity)); if(q<1) fail(); quantities[key]=q;});
  const products=(m.products||[]).flatMap(p => {
    const es=all(root,p.root); if(!es.length) return []; if(es.length!==1) fail(); const e=es[0];
    if(productKey(one(e,p.product))!==p.key || txt(e,p.name)!==p.name_text || txt(e,p.price)!==p.price_text) fail();
    return [{key:p.key,name:p.name_text,price:p.price_text,quantity:quantity(one(e,p.quantity)),controls:controls(e,'product')}];
  });
  const dialogs=all(document,'[role="dialog"],dialog[open],[aria-modal="true"]');
  // A nested modal representation is ambiguous too; never choose a first match.
  if(dialogs.length>1) fail();
  const dialog=dialogs.length ? {heading:txt(dialogs[0],'h1,h2,h3,[role="heading"]'),controls:controls(dialogs[0],'dialog')} : null;
  const general=controls(root,'order').filter(c => !['Checkout to confirm changes','Check out to confirm changes','Check out groceries'].includes(c.label));
  const checkout=controls(basket,'basket').filter(c => ['Checkout to confirm changes','Check out to confirm changes','Check out groceries'].includes(c.label));
  const summaryFields=['location','slot','total','delivery_charge','savings','substitutions','uncertainty'];
  return {authenticated:exact(document,m.authenticated), orders, orders_complete:
    exact(ordersRoot,o.complete) && integer(txt(ordersRoot,o.count))===orders.length,
    order:txt(root,s.order),slot:txt(root,s.slot),cutoff:txt(root,s.cutoff),view,
    making_changes: s.amendment ? exact(root,s.amendment) : false,
    quantities, basket_complete:exact(basket,b.complete) && integer(txt(basket,b.count))===rows.length,
    summary_complete:view==='summary' && summaryFields.every(k=>s.summary && exact(root,s.summary[k])),
    visible_success:view==='confirmation' && exact(root,s.success),
    controls:[...general,...checkout],products,dialog};
})"""


class LiveAdapter:
    def __init__(self, transport, site_map, binding, *, execute=False):
        self.transport, self.site_map, self.binding = transport, site_map, binding
        self.execute = execute
        self.wire = None
        self.last_snapshot = None

    def observe(self) -> t.Snapshot:
        try:
            if self.transport.identity(self.binding.target) != self.binding:
                stop(t.State.AMBIGUOUS_TARGET)
            result = self.transport.call('Runtime.evaluate', {
                'expression': DOM_SCRIPT + '(' + json.dumps(self.site_map) + ')',
                'returnByValue': True, 'awaitPromise': False,
                'timeout': 5000,
            })
            if 'exceptionDetails' in result:
                stop()
            wire = result['result']['value']
            if wire.get('authenticated') is not True:
                stop(t.State.AUTH_REQUIRED)
            orders = tuple(t.Order(digest(o['order']), digest(o['slot']), cutoff(o['cutoff']))
                           for o in wire['orders'])
            if wire['orders_complete'] is not True or len(orders) != 1:
                stop(t.State.AMBIGUOUS_TARGET)
            order, slot = digest(wire['order']), digest(wire['slot'])
            if orders[0].key != order:
                stop(t.State.AMBIGUOUS_TARGET)
            if orders[0].slot != slot or orders[0].cutoff != cutoff(wire['cutoff']):
                stop(t.State.SLOT_DRIFT)
            quantities = t.expected_quantities(wire['quantities'], ())
            if any(not p.isdecimal() for p in quantities):
                stop()
            controls = self._scoped_controls(wire)
            dialog = wire.get('dialog')
            dialog = None if dialog is None else t.Dialog(dialog['heading'], tuple(c['label'] for c in dialog['controls']))
            page = t.Page(self.binding.target, self.binding.browser, order, slot, t.View(wire['view']),
                          making_changes=wire['making_changes'] is True, quantities=quantities,
                          controls=tuple(c['label'] for c in controls),
                          basket_complete=wire['basket_complete'] is True,
                          summary_complete=wire['summary_complete'] is True,
                          visible_success=wire['visible_success'] is True,
                          verified_products=tuple(p['key'] for p in wire['products']), dialog=dialog)
            self.wire = wire
            self.last_snapshot = t.Snapshot(orders, (page,))
            return self.last_snapshot
        except t.Halt:
            raise
        except Exception:
            stop()

    @staticmethod
    def _scoped_controls(wire):
        controls = [c for c in wire['controls'] if c['scope'] in ('order', 'basket')]
        checkout = [c for c in controls if c['label'] in t.CHECKOUT_LABELS and c['scope'] == 'basket']
        # Prefer the amendment-specific control INSIDE the order-bound basket.
        # Page-wide ordinary checkout is never a fallback for a missing basket CTA.
        specific = [c for c in checkout if c['label'] != 'Check out groceries']
        selected = specific if specific else checkout
        if len(selected) > 1:
            stop()
        return [c for c in controls if c['label'] not in t.CHECKOUT_LABELS] + selected

    def act(self, action: t.Action, page: t.Page) -> None:
        if not self.execute:
            stop()
        try:
            before = self.last_snapshot
            if before is None or before.pages != (page,):
                stop()
            fresh = self.observe()
            if fresh != before or time.time() >= fresh.orders[0].cutoff:
                stop()
            if (action.kind != t.ActionKind.ENTER_AMENDMENT and not page.making_changes) or not page.basket_complete:
                stop()
            wire = self.wire
            if wire is None:
                stop()
            if action.kind == t.ActionKind.KEEP_CHANGES:
                if page.dialog != t.Dialog('Cancel changes?', ('No', 'Yes, cancel')) and page.dialog != t.Dialog('Cancel changes?', ('Yes, cancel', 'No')):
                    stop(t.State.UNEXPECTED_DIALOG)
                candidates = [c for c in wire['dialog']['controls'] if c['label'] == 'No']
            elif page.dialog:
                stop(t.State.UNEXPECTED_DIALOG)
            elif action.kind == t.ActionKind.SET_QUANTITY:
                if page.view not in (t.View.BASKET, t.View.PRODUCTS, t.View.LANDING) or not page.making_changes or not page.basket_complete:
                    stop()
                products = [p for p in wire['products'] if p['key'] == action.product]
                if len(products) != 1 or type(action.quantity) is not int or action.product is None:
                    stop()
                product = products[0]
                current = page.quantities.get(action.product, 0)
                if product['quantity'] != current or abs(action.quantity-current) != 1:
                    stop()
                label = 'Add' if current == 0 else ('Increase quantity' if action.quantity > current else 'Decrease quantity')
                candidates = [c for c in product['controls'] if c['label'] == label]
            else:
                views = {
                    t.ActionKind.ENTER_AMENDMENT: (t.View.ORDERS,),
                    t.ActionKind.OPEN_TROLLEY: (t.View.BASKET, t.View.PRODUCTS, t.View.LANDING),
                    t.ActionKind.CHECKOUT: (t.View.BASKET, t.View.PRODUCTS, t.View.LANDING),
                    t.ActionKind.CONTINUE: (t.View.CHECKOUT, t.View.OFFERS, t.View.SUGGESTIONS),
                    t.ActionKind.CONFIRM: (t.View.SUMMARY,),
                }
                if action.kind not in views or page.view not in views[action.kind]:
                    stop()  # Search/text entry is deliberately not implemented.
                if action.label not in t.LABEL_ALIASES[action.kind.value]:
                    stop()
                if action.kind == t.ActionKind.CONFIRM and not page.summary_complete:
                    stop()
                candidates = [c for c in self._scoped_controls(wire) if c['label'] == action.label]
            if len(candidates) != 1:
                stop()
            control = candidates[0]
            x, y = control['x'], control['y']
            if control.get('reachable') is not True or not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (x, y)):
                stop()
            # No internal retries. A timeout after press or release is uncertain.
            for event in ('mousePressed', 'mouseReleased'):
                self.transport.call('Input.dispatchMouseEvent', {
                    'type': event, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1,
                })
        except t.Halt:
            raise
        except Exception:
            stop()


def main(argv=None, *, transport_factory: Callable[..., Any] = CDPTransport):
    parser = argparse.ArgumentParser(description='Order-bound amendment CDP helper. Default: read-only. No login or payment input.')
    parser.add_argument('--endpoint', default='http://127.0.0.1:9222')
    parser.add_argument('--target', required=True, help='Exact CDP page target ID, never a URL')
    parser.add_argument('--site-map', required=True, type=Path, help='Private reviewed visible-DOM map')
    parser.add_argument('--plan', required=True, type=Path, help='JSON array of exact product operations')
    parser.add_argument('--state-dir', required=True, type=Path, help='Private durable directory for this run; reuse on recovery')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true', help='Authorize one existing-order amendment attempt, including Confirm order')
    mode.add_argument('--dry-run', action='store_true', help='Read-only inspection, the default')
    mode.add_argument('--reconcile', action='store_true', help='Read-only reconciliation after an uncertain final submission')
    parser.add_argument('--timeout', type=float, default=8.0, help='CDP operation deadline in seconds, maximum 60')
    args = parser.parse_args(argv)
    try:
        site_map = json.loads(args.site_map.read_text())
        plan = json.loads(args.plan.read_text())
        operations = tuple(t.Operation(o['product'], t.QuantityMode(o['mode']), o['quantity']) for o in plan)
        if any(not o.product.isdecimal() for o in operations):
            stop()
        t.expected_quantities({}, operations)
        transport = transport_factory(args.endpoint, args.target, timeout=args.timeout, execute=args.execute)
        binding = transport.identity(args.target)
        adapter = LiveAdapter(transport, site_map, binding, execute=args.execute)
        with RunStore(args.state_dir) as store:
            if (args.execute or args.reconcile) and not (args.state_dir / 'state.json').exists():
                stop()
            run, context = store.prepare(binding, site_map, operations,
                lambda: t.discover(adapter.observe(), operations, now=time.time()))
            journal = store.journal()
            try:
                engine = t.Orchestrator(adapter, context, journal, run, authorized=args.execute)
                if args.reconcile:
                    if context.submission_status == t.Submission.NOT_STARTED:
                        stop()
                    result = engine.run()
                elif args.execute:
                    store.claim_execution()
                    result = engine.run()
                else:
                    snapshot = adapter.observe()
                    observed = snapshot.pages[0]
                    if (observed.order != context.order or observed.slot != context.slot or
                            snapshot.orders[0].cutoff != context.cutoff or
                            not observed.basket_complete or observed.dialog is not None or
                            dict(observed.quantities) not in (context.baseline_quantities, context.expected_quantities)):
                        stop()
                    print('READ_ONLY')
                    return 0
                print(result.value)
                return 0 if result == t.State.CONFIRMED else 2
            finally:
                journal.close()
    except t.Halt as error:
        print(error.state.value)
    except Exception:
        print(t.State.OUTCOME_UNKNOWN.value)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
