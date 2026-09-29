# Tesco amendment checkout labels and render waits

Observed authenticated amendment flow:

- On the landing/search view, the basket action may be labelled **Checkout to confirm changes**.
- On the trolley page, the same action may be labelled **Check out to confirm changes** (with a space) or **Check out groceries**.
- Match the currently rendered accessible label instead of assuming one spelling, and verify the route contains `isInAmend=true`.
- After each checkout navigation, wait for rendered checkout content before searching for the next control; URL changes can precede the page body.
- Tesco may show a **Cancel changes?** dialog while the amendment flow is active. Choose **No** to preserve the order-bound changes; choose **Yes, cancel** only when cancellation is intended.
- On **Order summary**, **Confirm order** was rendered as a button rather than an anchor. Click it only after the summary visibly contains the requested item/change.
- Verify the final `confirmation?isAmendedOrder=true` page, confirmation message, order identity, and requested item before reporting completion.
