# UI/UX specification: KindBridge

Goal: professional, calm civic-ops UI (not a marketing landing page). Hebrew-capable layout (`dir=rtl` when `Accept-Language` starts with `he`), otherwise LTR English. Shared app chrome: logo KindBridge, role badge, logout.

## Visual rules

- Neutral background, one accent (deep teal) for primary buttons.
- Tables: zebra optional, sticky header, empty state with a single CTA.
- Forms: inline validation; never clear the form on 400.
- AI scores: numeric + short rationale; no raw chain-of-thought.
- Address and phone: masked `••••` until the viewer is allowed (assigned volunteer or admin).

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
| `/me/requests/<id>` | detail 4.2 | Status, description; volunteer identity only after ASSIGNED |

### Volunteer

| Route | Content |
| :--- | :--- |
| `/me/profile` | Edit résumé fields (re-embed on save) |
| `/me/tasks` | Table of ASSIGNED / COMPLETED; Complete and Release actions |

### Admin

| Route | Course map | Content |
| :--- | :--- | :--- |
| `/dashboard` | 4.4 | KPIs: counts by request status; bar/list of **candidate count per open request**; volunteer gauges Available/Busy/Inactive |
| `/requests` | 4.1 + 4.3 | Global search/filter table |
| `/requests/<id>` | 4.2 | Three proposed cards (score, rationale, city); Approve on one; Reject all; Override picker; Retrigger |

Empty `NO_MATCH`: banner “No eligible volunteers” + Retrigger + Override.

## Interaction notes

- Approve is disabled until a candidate radio is selected.
- Override requires a text reason (min 10 chars) and a volunteer picker (admin directory query).
- Cancel from assigned state: confirm modal “Volunteer will be notified”.
- Dashboard widgets must use `GetAdminDashboardQuery` only (no extra ad-hoc SQL in the template).
