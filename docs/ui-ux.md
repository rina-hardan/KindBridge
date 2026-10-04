# UI/UX specification: KindBridge

Goal: professional, calm civic-ops UI (not a marketing landing page). Hebrew-capable layout (`dir=rtl` when `Accept-Language` starts with `he`), otherwise LTR English. Shared app chrome: logo KindBridge, role badge, logout.

## Technical approach

- Server-rendered Jinja templates in `app/templates/` with a shared `base.html` (chrome, flash messages, CSRF token meta tag).
- One CSS framework for the whole app (default: Bootstrap 5, using its RTL build when `dir=rtl`). Use logical properties (`margin-inline-start`) in custom CSS so one stylesheet works in both directions.
- Small vanilla JS only: `fetch` calls to `/api/...` with the CSRF header, then update the page or redirect.
- Route list must match `architecture.md` section 3 (view routes below are the GET pages).

## Visual rules

- Neutral background, one accent (deep teal) for primary buttons.
- Status badges with a fixed color per status: `PENDING_REVIEW`, `MATCH_PROPOSED`, `NO_MATCH`, `ASSIGNED`, `COMPLETED`, `CANCELLED`. Add an `OVERDUE` badge when `preferred_date` is in the past on an open request.
- Tables: zebra optional, sticky header, pagination (20 rows), empty state with a single CTA.
- Forms: inline validation; never clear the form on 400; field-level error messages.
- AI scores: numeric + short rationale + top score components; no raw chain-of-thought.
- Address and phone: masked `••••` until the viewer is allowed (assigned volunteer or admin).
- Every action shows feedback (success toast or error message) and a loading state on the button while the request runs.

## UX principles applied

- Visibility of status: every request shows its status badge on every screen.
- Error prevention: destructive or irreversible actions (Reject all, Cancel, Release) use a confirm modal; Approve stays disabled until a candidate is selected.
- Consistency: same table, filter bar, and button styles on every list screen.
- Role-appropriate navigation: the navbar shows only links the current role may open.
- Accessibility: labels on all inputs, sufficient color contrast, keyboard-reachable actions, status never conveyed by color alone.

## Screens

### Public

| Route | Content |
| :--- | :--- |
| `/login` | Email, password, link to register |
| `/register` | Role toggle Requester / Volunteer; volunteer fields: city, skills, experience (résumé), vehicle, frequency |

### Requester

| Route | Course map | Content |
| :--- | :--- | :--- |
| `/me/requests` | table 4.3, search 4.1 | Filters + table of own tickets |
| `/me/requests/new` | input 4.5 | Create request form |
| `/requests/<id>` | detail 4.2 | Status, description; volunteer identity only after ASSIGNED; Cancel button for non-terminal requests |

### Volunteer

| Route | Content |
| :--- | :--- |
| `/me/profile` | Edit résumé fields (re-embed on save); availability control (Available / Temporarily unavailable with an until-date / Inactive) |
| `/me/tasks` | Table of ASSIGNED / COMPLETED; Complete and Release actions; address and phone visible for ASSIGNED tasks |

### Admin

| Route | Course map | Content |
| :--- | :--- | :--- |
| `/dashboard` | 4.4 | KPIs: counts by request status; bar/list of **candidate count per open request**; volunteer gauges Available/Busy/Inactive |
| `/requests` | 4.1 + 4.3 | Global search/filter table |
| `/requests/<id>` | 4.2 | Three proposed cards (score, rationale, top components, city); Approve on one; Reject all (with reason); Override picker; Retrigger. After ASSIGNED: assigned volunteer details and Cancel |

Empty `NO_MATCH`: banner “No eligible volunteers” + Retrigger + Override.

## Interaction notes

- Approve is disabled until a candidate radio is selected.
- Reject all asks for a reason (required) in a confirm modal.
- Override requires a text reason (min 10 chars) and a volunteer picker (admin directory query); available from `MATCH_PROPOSED` and `NO_MATCH`.
- Cancel from assigned state: confirm modal “Volunteer will be notified”.
- Release (volunteer): confirm modal; the task disappears from the active list.
- Exemption: a small “Do not match me with this requester” action on an ASSIGNED or COMPLETED task row (volunteer) and on the admin request detail; calls `POST /api/exemptions`.
- Dashboard widgets must use `GetAdminDashboardQuery` only (no extra ad-hoc SQL in the template).