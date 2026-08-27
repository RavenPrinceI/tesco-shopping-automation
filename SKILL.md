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

## Core safety boundary

1. Maintain exactly one active Tesco grocery order. Before every consequential action, inspect upcoming orders and reserved slots.
2. Refuse to act when more than one active/upcoming order is detected.
3. Never implicitly replace a slot, create a second order, or infer approval from an earlier basket or slot approval.
4. Basket edits and slot discovery can be automated; selecting a slot is consequential and must follow an explicit request/confirmation policy.
5. Do not enter credentials, new payment details, MFA codes, or security codes. For an initial order, stop at the saved-card payment screen after **Continue to payment**. For an amendment, the final boundary is **Order summary → Confirm order**.
6. At either final boundary, produce a fresh summary: total, item quantities, delivery location, date/time, delivery charge, offers/substitutions, and uncertainty. For an existing confirmed order, proceed through Tesco's **Confirm order** without asking for another confirmation; the user's amendment request is authorization to complete it. Ask for explicit confirmation only when selecting or setting up a new delivery slot.
7. A new-slot confirmation is single-use and must be bound to the current order, slot, basket hash, requested action, and visible cutoff. Re-inspect before execution.
8. Never retry a payment submission blindly. If the page remains unchanged after a click, verify the URL/title/order-confirmation state and stop.

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

1. From the confirmation page, choose **Change grocery order** and verify the authenticated groceries landing page is in change/amend mode.
2. Add, remove, or change quantities as requested. Re-inspect the basket and verify exactly one active order.
3. Click the live basket control labelled **Check out to confirm changes**; verify its route includes `isInAmend=true`.
4. Traverse **Offers → Suggestions → Order summary**.
5. At Order summary, capture a fresh approval summary. For an existing confirmed order, the user's amendment request authorizes clicking **Confirm order**; do not ask again. Ask for explicit confirmation only before selecting or setting up a new delivery slot.
6. After authorization, click **Confirm order** once and complete the amendment workflow; do not stop with changes merely staged in the basket. Verify the resulting URL, visible confirmation, order identity, and that every requested item/change appears under the confirmed order. In the verified flow this navigated directly to `confirmation?isAmendedOrder=true`, without a separate `payment.tesco.com` page. Do not assume that behavior.
7. Report an amendment as complete only after the confirmed-order page verifies it. If confirmation is blocked, report clearly that the change is still pending and identify the exact next action; never present a staged basket as an updated order.

For product interpretation, **Brewdog Punk IPA 4X330ml** is one four-pack. If the user asks for four cans, add one pack unless they explicitly request four packs.

## Verification and recovery

- After every basket mutation, re-inspect the same order ID and slot identity; stop on slot drift.
- Clubcard voucher discovery is read-only and may classify visible vouchers as available, already applied/auto-applied, redeemable/request-required, unavailable, or expired. Never redeem, request, or apply a voucher automatically. Present the exact voucher, action, saving, and current order/slot context first; require a fresh explicit confirmation for that specific voucher action, then verify the post-action voucher state and order.
- Treat login pages as an authentication prerequisite, not as a reason to guess credentials. When Tesco redirects to its password page, first inspect whether the browser's saved-password autofill has populated the password field. Never read, print, copy, or type the password. If the field is populated and the user has authorized reauthentication, click the visible **Sign in** control once, then verify that Tesco returns to an authenticated page. If the field is empty, hand over via noVNC and wait for the user to select autofill or authenticate manually.
- If a click does not navigate, inspect the control's accessible name, URL, disabled state, and surrounding page before retrying. Prefer one real browser-input retry only after checking that no order confirmation occurred.
- If direct navigation to checkout redirects to the basket, inspect and click the live semantic checkout anchor/control. Resolve basket attention filters such as **Show items** or **Show full basket**, then re-inspect before retrying.
- Never report an order as placed without a confirmation page or order number.
