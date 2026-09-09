# HOMEZ V6.5 Mobile Usage Guide (English)

- Audience: anyone checking the HOMEZ Console on a phone or small
  screen, any role
- Target runtime: 5-8 minutes
- Version: HOMEZ V6.5
- Verification: real responsive CSS and the `#mobile-nav-toggle`
  button behavior captured at three real viewports (430x932, 390x844,
  360x800), using the Manager role.

## In scope / out of scope

**Covered here**: logging in on a mobile screen, how the top bar
rearranges on a narrow screen, opening the left menu via the
hamburger icon, layout differences across screen sizes, and that
permission enforcement is identical to desktop even on mobile.

**Not covered here**: HOMEZ has no separate native mobile app — this
guide covers only the **responsive browser layout**. There are no
mobile-only features (e.g., push notifications).

## 1. Mobile login screen

Even at 430x932, the login screen keeps the same card layout as
desktop, just narrower.

![Mobile login screen](screenshots/04_mobile/00_login_430x932.png)

## 2. After login — the top bar narrows

After login, on a narrow screen the left menu is hidden by default
and a hamburger icon (☰) appears at the top-left of the top bar
instead. The search field and status badges remain but wrap onto
their own lines.

![Dashboard - 430x932](screenshots/04_mobile/01_dashboard_430x932.png)

## 3. Opening the menu via the hamburger icon

Tapping the ☰ icon in the top bar slides in the same full left menu
used on desktop (same six groups, same "Coming soon" badges).

![Mobile menu open](screenshots/04_mobile/03_nav_drawer_open_430x932.png)

## 4. Comparing screen sizes

The same account and dashboard were checked at three real viewports.

| Viewport | Screen |
|---|---|
| 430x932 | ![430x932](screenshots/04_mobile/01_dashboard_430x932.png) |
| 390x844 | ![390x844](screenshots/04_mobile/01_dashboard_390x844.png) |
| 360x800 | ![360x800](screenshots/04_mobile/01_dashboard_360x800.png) |

All three keep a single-column, stacked-card layout with no clipped
or overlapping elements.

## 5. Permission enforcement is identical on mobile

Opening Product Candidates on mobile as Manager shows the same "You
don't have permission to view this data (admin access required)"
message seen on desktop — proof that the server-side permission check
is unaffected by screen size.

![Mobile product candidates - permission denied](screenshots/04_mobile/02_candidates_430x932.png)

## Feature include/exclude summary

**Real, implemented features covered here**: responsive mobile login,
the hamburger menu, layout across three viewports, and identical
permission enforcement on mobile.

**Does not exist**: a native mobile app, push notifications, or any
mobile-only shortcuts.

## In-app help summary

See [apphelp/04_mobile_usage_help_ko.md](apphelp/04_mobile_usage_help_ko.md).
