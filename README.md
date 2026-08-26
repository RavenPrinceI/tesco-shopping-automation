# Tesco shopping automation skill

This directory contains the Hermes skill instructions for private Tesco grocery
basket inspection, delivery-slot discovery, confirmed-order amendments, and
checkout verification.

The skill is the **operational playbook**. It is not, by itself, a Tesco API
client, browser driver, credential store, or complete server deployment. A
fresh agent can reproduce the workflow when the runtime dependencies described
below are provisioned on the target machine.

## What is included

- `SKILL.md` — safety boundaries, browser procedure, approval rules, and
  verification requirements.
- `references/` — checkout labels, rendering waits, and confirmed-order
  amendment lessons.

This repository intentionally contains no Tesco credentials, cookies, saved
passwords, payment data, MFA codes, order data, `.env` files, or browser
profiles.

## Runtime components required

A working deployment consists of all of the following:

1. **Hermes Agent** with this skill installed under its active
   `$HERMES_HOME/skills/` tree. A Git checkout is source material; Hermes does
   not automatically load a skill merely because it exists on GitHub.
2. **A browser interaction implementation** capable of loopback Chrome DevTools
   Protocol (CDP), semantic accessible-control discovery, visible-page waits,
   and screenshots. The implementation should use the normal Hermes browser
   tooling or an equivalent reviewed helper; it must not call hidden Tesco APIs
   or manipulate internal application state.
3. **Headful Chromium** running with the persistent profile for the Tesco
   account. The profile directory must be private to its OS user and have
   restrictive permissions (normally mode `700`).
4. **An isolated graphical display**, such as Xvfb, so Chromium remains headful
   while running unattended.
5. **Loopback-only CDP**, normally on `127.0.0.1`. Do not bind the debugging
   endpoint to a LAN, Tailscale, or public interface.
6. **Private noVNC/VNC handover** for manual authentication when Tesco expires
   the session. Bind the handover service to a trusted private interface such
   as Tailscale only; never expose it publicly.
7. **A valid Tesco session** in the persistent profile. Authentication, saved
   passwords, MFA, security codes, and payment details remain under the user's
   control and are never copied into Hermes configuration.
8. **Network access to Tesco** and a functioning Tesco account with the target
   delivery address and payment method already configured by the user.

The skill does not require or store a Tesco API key. It operates through the
visible Tesco website in the authenticated graphical browser.

## Fresh-machine setup outline

The exact service manager and paths are host-specific, but a reproducible
setup should follow this order:

1. Install Hermes Agent and confirm the active profile/home directory.
2. Install the skill from this repository into that profile's skills tree.
3. Install Chromium, an isolated display, VNC/noVNC, and the CDP/browser helper
   used by the deployment.
4. Create a dedicated persistent Chromium profile with restrictive ownership
   and permissions. Do not copy cookies or credentials through shell commands.
5. Start the display, VNC handover, and Chromium with CDP bound only to
   `127.0.0.1`.
6. Open Tesco through the graphical browser. If authentication is required,
   hand control to the user through the private noVNC endpoint; the agent must
   not type, inspect, print, or transmit credentials or MFA codes.
7. Verify the rendered Tesco title, URL, authenticated state, and CDP
   availability before attempting any order operation.
8. Verify that exactly one active/upcoming grocery order exists. Stop if more
   than one order is present.
9. Use the procedure in `SKILL.md`. Re-inspect the live order, slot, basket,
   visible cutoff, and account state before every consequential action.

## Runtime separation

Keep these concerns separate:

```text
Skills repository       -> versioned instructions and references
Hermes profile          -> installed skill and user-approved configuration
Browser helper          -> CDP transport and semantic page interaction
Chromium profile        -> local authenticated session; never commit or copy
Tesco                  -> live basket, order, slot, and checkout state
```

The browser helper and host service configuration are deployment components,
not substitutes for the skill. If they are maintained in another repository,
provision and version that repository separately. Do not place helper secrets,
Tesco session data, or local machine paths in this skill repository.

## Safety and approval model

- Maintain exactly one active Tesco grocery order.
- Delivery-slot discovery is read-only. Selecting or changing a slot requires
  fresh explicit user confirmation.
- An amendment request authorizes completion of that existing-order amendment
  through Tesco's **Confirm order** screen.
- Never silently replace a slot, create a second order, apply a voucher, or
  alter payment details.
- Clubcard voucher discovery is read-only unless the user gives fresh,
  specific confirmation for one identified voucher action.
- Treat a staged amendment basket as incomplete. Report success only after the
  final Tesco confirmation page verifies the requested item or change.
- Delivery reservations can expire during checkout. Record the approximate
  reservation start time, honor Tesco's visible cutoff, and stop for slot
  re-selection rather than silently booking a replacement.

## Reproducibility checklist

Before calling the deployment ready, verify all of the following:

- [ ] The skill is installed in the active Hermes profile.
- [ ] The browser helper can connect to loopback CDP.
- [ ] Chromium is headful and uses a private persistent profile.
- [ ] The graphical display and private noVNC handover work.
- [ ] Tesco renders an authenticated page without exposing credentials.
- [ ] CDP is not listening on a non-loopback interface.
- [ ] Exactly one active/upcoming order is visible.
- [ ] A read-only slot-availability check does not alter the booked slot.
- [ ] A test amendment reaches a final confirmation page before being called
      complete.
- [ ] Logs, Git history, images, browser profiles, and environment files do
      not contain credentials, cookies, payment data, or MFA codes.

## Fresh-agent limitation

A new agent should not rely on this chat's history or on transient session
context. It must rediscover the authenticated page, current order, basket,
slot, cutoff, and available actions live. Durable user preferences may improve
personalisation, but the safety rules and verification requirements are
contained in `SKILL.md` and this README.
