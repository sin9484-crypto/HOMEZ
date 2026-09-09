# HOMEZ V6.5 Admin Initial Setup Guide (English)

- Audience: the SUPER_ADMIN registering a company for the first time,
  and admins inviting teammates
- Target runtime: 5-8 minutes
- Version: HOMEZ V6.5
- Verification: checked against the real markup in
  `app/web/console.html` (`register-gate`/`setup-gate`/
  `company-recovery-gate`/`view-account-security`) and real screen
  captures against a temp demo database (virtual company "HOMEZ Guide
  Demo Co.", virtual accounts such as
  `manager@homez-guide-demo.com`).

## In scope / out of scope

**Covered here**: the sign-up (registration request) screen, the real
field structure of the first-admin setup screen, issuing invite
codes, the pending-approval queue, user management (role change /
deactivate / start password reset), adding a new user, changing your
own password, revoking sessions, generating recovery codes, and
entering the Store Connection (Coupang/Naver) screen.

**Not covered here**:
- Actually completing a real Coupang/Naver connection — this screen
  only goes up to the credential-entry UI; no real credentials were
  entered or saved while producing this guide. See
  `FEATURE_LIMITATIONS.md`.
- Product registration itself → Guide 3
- Troubleshooting → Guide 5

## 1. Requesting access for a new teammate (Sign up)

From the login screen, clicking "Sign up" opens this screen.

![Register screen](screenshots/02_admin_setup/03_register_ko.png)

Real fields (from source): login email, display name, password (+
confirm), a password-policy checklist (10+ chars / uppercase /
lowercase / digit / special character), and an optional company
invite code. **A request can be submitted without an invite code,
but as the screen states, it cannot be used until an admin
approves it.**

## 2. Approving requests as an admin

After logging in, an admin opens "Settings · Account & Security" and
sees a "Pending sign-up approvals" panel listing requests to approve
or reject. (This panel only populates when requests exist; the demo
environment for this guide was seeded with pre-approved accounts, so
it was captured empty — the real UI structure was confirmed from
source.)

## 3. Issuing an invite code

In the same Settings screen, the "Invite codes" panel lets you choose
the maximum grantable role (VIEWER/STAFF/MANAGER/ADMIN/SUPER_ADMIN)
and press "Generate invite code" to issue one.

![Invite codes and user management](screenshots/02_admin_setup/04_invitations_and_requests.png)

> Security note: an issued invite code is displayed in full on
> screen, so it must always be masked in any video recording (per the
> shared production rules — invite codes, recovery codes, and tokens
> must never be exposed).

## 4. User management (SUPER_ADMIN only)

Further down the same screen is a real table of every user in the
company: username, email, role, status (active/inactive), last
login, and per-row actions — "Details / Deactivate / Start password
reset / Change role." Right below it, an "Add new user" form lets you
create an account directly by entering username, email, role, and a
temporary password.

## 5. Entering the Store Connection screen

The "Store Connection" item in the left menu opens the Coupang/Naver
connection walkthrough and credential-entry wizard.

![Store connection](screenshots/02_admin_setup/01_store_connection.png)

The screen carries an explicit yellow banner: "Procedure guide is an
example — actual seller-center screens may change." **No real Access
Key / Secret Key is entered anywhere in this guide.**

## 6. Account & Security settings at a glance

The "Settings · Account & Security" screen combines language
switching, changing your own password, session management (revoke
all other sessions), and generating recovery codes (10 one-time codes
for self-service password reset while logged out; regenerating
invalidates all previous codes) on one screen.

![Full account & security screen](screenshots/02_admin_setup/02_account_security_full.png)

> Security note: recovery codes are shown on screen exactly once,
> right after generation. Any recording must use a demo-only account
> and mask the code area in the final video.

## Feature include/exclude summary

**Real, implemented features covered here**: sign-up requests, the
pending-approval queue UI, invite-code issuance, the user list/role
change/deactivate/start-password-reset actions, adding a new user,
changing your password, revoking sessions, generating recovery codes,
and entering the Store Connection screen.

**Shown but not actually exercised in this guide**: saving and
verifying a real Coupang/Naver Access Key (sensitive, not
demonstrated).

**Explicitly V7 and out of scope**: real external seller-center API
approval / a completed real connection.

## In-app help summary

See [apphelp/02_admin_setup_help_ko.md](apphelp/02_admin_setup_help_ko.md).
