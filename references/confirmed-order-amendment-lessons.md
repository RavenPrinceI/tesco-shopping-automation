# Confirmed-order amendment lessons

Validated workflow for a single active Tesco grocery order:

1. Inspect `/shop/en-GB/orders` and verify exactly one upcoming order, its delivery slot, and the visible amendment cutoff.
2. Enter amendment mode from the order-list card's **Make changes** control. Do not assume the confirmation page's similarly named control will enter amendment mode; it may return to the orders page.
3. If Tesco has expired the session, inspect the login page. If the browser has populated the saved password and the user has authorized reauthentication, click **Sign in** once; never read, copy, print, or type the password. If empty, hand off through private noVNC.
4. After authentication, inspect the ordinary trolley. If an item was accidentally added outside amendment mode, do not treat it as part of the order and do not automatically remove it; first stop and re-enter the order-bound flow, then verify the target order and basket before making any mutation.
5. Re-enter amendment mode from the existing order card, add the requested product, and verify the trolley says **Making changes** and exposes an amendment checkout control such as **Check out to confirm changes** or **Check out groceries** with `isInAmend=true`.
6. If multiple Tesco tabs exist, identify the active tab by its visible **Making changes** state, target delivery date, and order-bound basket contents. A normal `/trolley` tab is not proof that an amendment is active.
7. Traverse checkout to **Order summary**. For an existing confirmed order, the user's request authorizes **Confirm order**; do not ask for a second confirmation. Explicit confirmation is required only when selecting or setting up a new delivery slot.
8. Click **Confirm order** once and verify `confirmation?isAmendedOrder=true`, the visible success message, the requested item, the same delivery slot, and the updated order totals.

Sanitized regression case: adding one Sweet Baby Ray's Barbecue Sauce 510g through the order-bound flow produced a visible item quantity of one, preserved the booked slot, reached Order summary, and ended at the amended-order confirmation page. No account, order, address, payment, or identifier data is recorded here.

Product interpretation lesson: when the user asks for a generic Worcestershire sauce without a brand or size, use the standard Tesco own-brand 150ml product rather than Lea & Perrins, and state that assumption.
