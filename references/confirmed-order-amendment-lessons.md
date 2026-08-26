# Confirmed-order amendment lessons

Validated workflow for a single active Tesco grocery order:

1. Inspect `/shop/en-GB/orders` and verify exactly one upcoming order, its delivery slot, and the visible amendment cutoff.
2. Enter amendment mode from the order-list card's **Make changes** control. Do not assume the confirmation page's similarly named control will enter amendment mode; it may return to the orders page.
3. If Tesco has expired the session, inspect the login page. If the browser has populated the saved password and the user has authorized reauthentication, click **Sign in** once; never read, copy, print, or type the password. If empty, hand off through private noVNC.
4. After authentication, inspect the ordinary trolley. If the requested product was accidentally added outside amendment mode, remove that stray item before continuing; otherwise it risks becoming a separate new order.
5. Re-enter amendment mode from the existing order card, add the requested product, and verify the trolley says **Making changes** and exposes **Check out to confirm changes** with `isInAmend=true`.
6. Traverse checkout to **Order summary**. For an existing confirmed order, the user's request authorizes **Confirm order**; do not ask for a second confirmation. Explicit confirmation is required only when selecting or setting up a new delivery slot.
7. Click **Confirm order** once and verify `confirmation?isAmendedOrder=true`, the visible success message, the requested item, the same delivery slot, and the updated order totals.

Product interpretation lesson: when the user asks for a generic Worcestershire sauce without a brand or size, use the standard Tesco own-brand 150ml product rather than Lea & Perrins, and state that assumption.
