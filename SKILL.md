---
name: tesco-shopping-automation
description: Use for private Tesco basket, slot, and checkout automation.
license: MIT
metadata:
  hermes:
    tags: [tesco, groceries, browser, cdp, checkout, approval, novnc]
    category: software-development
---

# Tesco shopping automation

## When to use

Use for an already-authenticated graphical Tesco Chromium session when inspecting orders, copying or editing one basket, booking one delivery slot, traversing checkout, or preparing an order for user approval.

## Reusable policy helper

Use `tesco_live.py` around `tesco_amendment.py`, not one-off temporary mutation
scripts. Follow the [live entry point and evidence contract](references/live-cdp.md).
The CLI defaults to read-only; it requires a saved baseline before `--execute`.
The same private state directory binds the browser, exact target, map, plan,
original basket, expected basket and durable input fences across restarts.
Never reset that directory to retry uncertain input.

```bash
python3 tesco_live.py --target "$TESCO_TARGET" \
  --site-map "$TESCO_SITE_MAP" --plan "$TESCO_PLAN" \
  --state-dir "$TESCO_RUN_DIR" --dry-run
```

After successful inspection and under the user's current amendment request,
replace `--dry-run` with `--execute` for one attempt. Use `--reconcile` only for
read-only verification of an uncertain final submission. No initial-order,
slot, authentication, voucher or payment actions are supported. The adapter
also refuses search/text entry and quantity jumps larger than one unit.

The tests are offline fixtures, not proof of current Tesco compatibility. A
reviewed production DOM map is still required. If the page cannot expose the
complete active-order list, basket and cutoff at every stage, stop and extend
the tested adapter. Do not synthesize evidence or bypass it with a temporary
script. Keep runtime page data and configuration private and outside Git.

## Core safety boundary

1. Maintain exactly one active Tesco grocery order. Before every consequential action, inspect upcoming orders and reserved slots.
2. Refuse to act when more than one active/upcoming order is detected.
3. Never implicitly replace a slot, create a second order, or infer approval from an earlier basket or slot approval.
4. Basket edits and slot discovery can be automated; selecting a slot is consequential and must follow an explicit request/confirmation policy.
5. Do not enter credentials, new payment details, MFA codes, or security codes. For an initial order, stop at the saved-card payment screen after **Continue to payment**. For an amendment, the final boundary is **Order summary → Confirm order**.
6. At either final boundary, produce a fresh summary: total, item quantities, delivery location, date/time, delivery charge, offers/substitutions, and uncertainty. For an existing confirmed order, proceed through Tesco's **Confirm order** without asking for another confirmation; the user's amendment request is authorization to complete it. Ask for explicit confirmation only when selecting or setting up a new delivery slot.
7. A new-slot confirmation is single-use and must be bound to the current order, slot, basket hash, requested action, and visible cutoff. Re-inspect before execution.
8. Journal submission intent durably before the final click. Never retry final submission, including after a crash or lost response. Reconcile the same order, slot and complete expected basket from visible success evidence. A URL or title alone is insufficient; unresolved outcomes remain unknown.

## Live browser architecture

- Use headful Chromium in the isolated virtual display, not hidden API calls or direct internal Tesco state manipulation.
- Keep CDP bound to `127.0.0.1` only.
- Use Tailscale-only noVNC for user handover; never expose the browser publicly.
- Use semantic accessible labels, visible text, stable route patterns, headings, and DOM relationships before CSS selectors.
- Capture title, final URL, visible headings/body state, and a screenshot at important boundaries.
- Treat hidden script/telemetry text as non-authoritative; classify errors from visible rendered content and relevant page structure, not raw `document.body.innerText` alone.

## Persistent authentication profile

A browser profile can retain cookies across Chromium restarts, but Tesco may invalidate the server-side session or require re-authentication. Never export cookies or copy credentials into Hermes configuration.

For Snap Chromium, use a writable persistent profile under Chromium's private home, for example:

`$CHROMIUM_PROFILE_DIR/tesco-chromium`

Keep `CHROMIUM_PROFILE_DIR` local to the deployment; never commit the actual profile path, cookies, or browser state.

Set the profile directory to mode `700`. Before migrating a profile:

1. Confirm the source directory exists and is readable before stopping or altering Chromium.
2. Stop Chromium cleanly and wait for the process to exit only after that check succeeds.
3. Copy to a new destination before deleting or altering anything at the source.
4. Verify the destination exists and has restrictive permissions.
5. Launch Chromium with the destination and verify the rendered page title and URL.
6. Preserve the original until the destination and rendered authenticated session have been verified.

If the source is missing, do not claim migration succeeded; launch a fresh persistent profile and ask the user to authenticate through noVNC.

## Order-bound amendment entry

Tesco's current site requires entering the order-bound change flow from **My orders**:

1. Open **My orders**.
2. Inspect upcoming orders and confirm exactly one active/upcoming order.
3. Select that order and click **Make changes**.
4. Verify the page visibly says **Making changes** and shows the target order's delivery date before editing.
5. Only then add, remove, or change quantities.
6. Re-inspect the order identity and reserved slot after each mutation.

Edits made in the ordinary grocery basket while outside this change flow are **not attached to the existing order**. They are a separate normal basket and must not be reported as an order amendment. If the user intends to amend an existing order, discard or leave the detached basket alone and re-enter through **My orders → Make changes**.

## Slot and checkout lifecycle

Delivery reservations are time-limited. Record the reservation start time when the slot is booked, warn the user before the roughly two-hour reservation window expires, and also honor Tesco's visible checkout cutoff. Treat a slot as live only after a fresh inspection. A slot can expire while working through checkout, and Tesco may redirect back to the basket with `InvalidSlot` / “Please book a new slot”. When this occurs:

1. Do not continue into payment.
2. Keep the basket unchanged.
3. Inspect available slots and present the choice; do not silently replace the slot.
4. After booking a fresh slot, immediately re-run the checkout sequence.

### Initial checkout

1. Inspect basket and confirm a single current slot.
2. Open basket checkout.
3. Continue through Offers.
4. Continue through Suggestions.
5. Reach Review and checkout.
6. Continue to payment only after verifying the order summary.
7. Stop at the payment page with saved/masked payment method visible; do not input or alter payment details.
8. Present the approval summary and wait.

The initial-order final boundary is **Continue to payment → saved-card payment screen**. The Review and checkout page is an earlier review point, not the final boundary.

### Confirmed-order amendment

1. Open **My orders**, inspect upcoming orders, select the single target order, and click **Make changes**. Verify the authenticated groceries landing page is in change/amend mode and bound to that order.
2. Add, remove, or change quantities only inside that order-bound flow. Re-inspect the basket and verify exactly one active order and the same reserved slot.
3. From landing/search, open the trolley when its control is present. Resolve checkout only inside the order-bound basket region. Labels include **Checkout to confirm changes**, **Check out to confirm changes** and **Check out groceries**. If both ordinary and amendment-specific checkout are present, prefer the unique amendment-specific control within that region, never a first page-wide match. Require visible amendment evidence; `isInAmend=true` is supporting evidence only.
4. Traverse **trolley → checkout → Offers → Suggestions → Order summary** as rendered. Intermediate stages may be absent. Support both **Continue checkout** and **Continue to checkout**. Use bounded visible-state waits, not a fixed URL sequence.
5. At Order summary, capture a fresh approval summary. For an existing confirmed order, the user's amendment request authorizes clicking **Confirm order**; do not ask again. Ask for explicit confirmation only before selecting or setting up a new delivery slot.
6. After authorization, click **Confirm order** once and complete the amendment workflow; do not stop with changes merely staged in the basket. Verify the resulting URL, visible confirmation, order identity, and that every requested item/change appears under the confirmed order. In the verified flow this navigated directly to `confirmation?isAmendedOrder=true`, without a separate `payment.tesco.com` page. Do not assume that behavior.
7. Report an amendment as complete only after the confirmed-order page verifies it. If confirmation is blocked, report clearly that the change is still pending and identify the exact next action; never present a staged basket as an updated order.

For product interpretation, **Brewdog Punk IPA 4X330ml** is one four-pack. If the user asks for four cans, add one pack unless they explicitly request four packs.

## Verification and recovery

- After every basket mutation, re-inspect the same order ID and slot identity; stop on slot drift.
- Clubcard voucher discovery is read-only and may classify visible vouchers as available, already applied/auto-applied, redeemable/request-required, unavailable, or expired. Never redeem, request, or apply a voucher automatically. Present the exact voucher, action, saving, and current order/slot context first; require a fresh explicit confirmation for that specific voucher action, then verify the post-action voucher state and order.
- A login page means `AUTH_REQUIRED`. Stop and hand over to the user. The helper never inspects autofill, reads or types credentials, or submits authentication forms.
- If an input does not visibly take effect, inspect the current state before further input. A failed search Enter may use one checked form-submission fallback. Never apply that fallback rule to quantity mutations or final submission. Reconcile lost responses from the expected quantities or confirmation evidence instead.
- If direct navigation to checkout redirects to the basket, inspect and click the live semantic checkout anchor/control. Resolve basket attention filters such as **Show items** or **Show full basket**, then re-inspect before retrying.
- Only an exact **Cancel changes?** dialog with **No** and **Yes, cancel** controls may be automatically dismissed with **No**, and only before submission. Unknown dialogs stop the flow.
- Record whether a requested quantity means **add** or **set** before input. Compare the complete expected basket, including unchanged items, after each mutation and at confirmation.
- If a target disappears, rediscover read-only and accept only a unique target in the same browser with the same visible order and slot. Never select a tab from its trolley URL alone.
- Never report an amendment complete from a confirmation URL or order number alone. Require visible success for the same order, slot and complete expected basket.
