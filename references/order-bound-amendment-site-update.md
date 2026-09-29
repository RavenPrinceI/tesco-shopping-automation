# Order-bound amendment site update

Tesco separates a normal grocery basket from an existing-order amendment.

## Verified entry point

1. Open **My orders**.
2. Confirm exactly one upcoming order.
3. Select the order and click **Make changes**.
4. Verify the landing page visibly says **Making changes** and shows the target order's delivery date.
5. Only then search for products or edit quantities.

A normal basket opened outside this flow is detached. Its items are not attached to the existing order, even if the same account and delivery address are visible.

## Verified amendment sequence

- Search can be performed from the order-bound groceries landing page.
- If the visible search field is populated but Enter does not navigate, submit the visible search form with its normal browser form action.
- Verify the result product name, size, price, and quantity before adding it.
- After adding, verify the product shows one unit in the basket, appears in the order-bound basket, and the delivery information still belongs to the target order.
- The basket summary may label the amendment control **Check out groceries** rather than **Checkout to confirm changes**. The resulting checkout URL should include `isInAmend=true`.
- Continue through **Offers → Suggestions → Order summary**.
- On Order summary, verify the changed item, item count, delivery slot, total, delivery charge, savings, and uncertainty.
- For an already confirmed order, the user's amendment request authorizes one **Confirm order** click. Verify the final URL contains `confirmation?isAmendedOrder=true` and the visible confirmation says Tesco has received the changes.

## Recovery and drift pitfalls

- Do not use a normal `/trolley` tab as proof that an order amendment is active. Multiple Tesco tabs can show detached baskets or stale order-bound state.
- Before any mutation, identify the correct tab by its visible **Making changes** text, target delivery date, and order-bound basket contents.
- Do not click **Add all to basket** more than once. Repeating it can double quantities.
- If a direct navigation to the normal basket loses the order-bound context, stop treating that basket as the order. Return through **My orders → Make changes** and rebuild only if necessary.
- If Tesco shows a **Cancel changes?** dialog while the amendment is active, choose **No** to preserve the changes; choose **Yes, cancel** only when cancellation is intended.
- Never report success from a staged basket. Require the amended-order confirmation page.
