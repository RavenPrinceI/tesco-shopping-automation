# Tesco amendment checkout labels and render waits

Observed authenticated amendment flow:

- On the landing/search view, the basket action may be labelled **Checkout to confirm changes**.
- On the trolley page, the same action may be labelled **Check out to confirm changes** (with a space) or **Check out groceries**.
- Match the currently rendered enabled accessible label instead of assuming one spelling. `isInAmend=true` supports, but cannot replace, visible order-bound amendment evidence. `LABEL_ALIASES` in `tesco_amendment.py` centralizes supported labels.
- Resolve checkout within the order-bound basket region. When ordinary **Check out groceries** and **Check out to confirm changes** coexist, select the unique amendment-specific control in that region. Never select the first page-wide match.
- The rendered route can include **landing/search → trolley → checkout → Offers → Suggestions → Order summary**. The checkout view is a separate stage, not Offers. **Continue checkout** and **Continue to checkout** are both supported.
- After each checkout input, use bounded waits for rendered content and enabled controls. URL changes can precede the page body. Offers and Suggestions are optional; never infer a missing stage from a timer alone.
- Only auto-resolve a dialog when its heading is exactly **Cancel changes?** and its controls are exactly **No** and **Yes, cancel**. Choose **No** before submission. Any other dialog or a dialog after submission stops automation. Cancellation is outside the helper API.
- On **Order summary**, **Confirm order** was rendered as a button rather than an anchor. Click it only after the summary visibly contains the requested item/change.
- Verify visible amended-order success, the same order and slot, and the entire expected basket before reporting completion. `confirmation?isAmendedOrder=true` alone is not success. See [policy helper](policy-helper.md) for durable final-click fencing and offline fixtures.
