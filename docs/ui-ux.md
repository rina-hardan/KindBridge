# UI/UX specification: KindBridge

Goal: professional, calm civic-ops UI (not a marketing landing page). Screen copy is Hebrew and the layout is right-to-left (`lang=he`, Bootstrap RTL). The navbar language control stores a `kb_lang` cookie and can switch the same screens to English, left-to-right. Free-text fields accept Hebrew or English in either screen language. City and skill matching treats the two languages as the same value for the shared vocabulary. Shared app chrome: logo KindBridge, language control, role badge, logout.

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
| `/` | Opens `/login`. A signed-in person goes to `/me` |
| `/login` | Email, password, link to register |
| `/register` | Person only. Full name, email, phone, password, residence city, home address. No requester or volunteer fields. Admin is seeded and has no residence fields |
| `/me` | Account. Personal details, with email shown and not editable. Name, phone, city, and home address can be saved. Beside the form, two entries: ask for help, and volunteer |
| `/me/requester` | Entry when the person has no requester profile. Saves default city, default address, accessibility notes, and emergency contact, then opens `/me/requests` |
| `/me/volunteer` | Add volunteering, or update it later. Vehicle checkbox, one free-text field (domain, traits, past experience), and frequency. Unavailability dates are optional and may be left blank. City is the residence city and is not asked. Capacity and the inactive toggle are not shown. Saving stays on this screen |
| `/me/requests` | Help-request area, only after a requester profile exists. Filterable table of the person's own tickets, a link to create one, and cancel. There is no edit action |
| `/me/tasks` | Volunteer area, opened directly from the account page, including when both lists are empty. No enrollment form in front of it. Header button is always "Add new volunteering" and opens `/me/volunteer`. The main column is "Volunteering you took on": the volunteer's own `ASSIGNED` and `COMPLETED` tasks, with a status filter and pagination (20 rows). Address and phone are shown for `ASSIGNED` tasks and masked `••••` for `COMPLETED`. An assigned row has Complete and Release. The other column, on the left in Hebrew, is "Volunteering you want to do": the saved narrative, frequency, and vehicle flag, or an empty state |

### Requester

| Route | Course map | Content |
| :--- | :--- | :--- |
| `/me/requests` | table 4.3, search 4.1 | Filters (status, category, urgency, city) and a table of own tickets. Status defaults to open requests (`PENDING_REVIEW`, `MATCH_PROPOSED`, `NO_MATCH`, `ASSIGNED`), so a cancelled ticket leaves the list until that status is chosen. No edit. Cancel confirms, and when the ticket is `ASSIGNED` the confirm says the volunteer will be notified |
| `/me/requests/new` | input 4.5 | Create request form. City and address start from the requester profile. Submitting opens a new ticket; it does not change an existing one |
| `/requests/<id>` | detail 4.2 | Status, description; volunteer identity only after ASSIGNED; Cancel button for non-terminal requests. No edit |

### Volunteer

| Route | Content |
| :--- | :--- |
| `/me/volunteer` | Vehicle, free-text narrative, frequency, and date ranges the volunteer cannot help (from–until, add, cancel). Opened by "Add new volunteering" on `/me/tasks`. The form title is "Add volunteering" until a profile exists, then "Update availability" |
| `/me/profile` | Redirects to `/me/volunteer` |
| `/me/tasks` | Two columns. Main: ASSIGNED / COMPLETED under "Volunteering you took on", with Complete and Release; address and phone visible for ASSIGNED tasks. Release asks for confirmation. Left in Hebrew: "Volunteering you want to do". Header: "Add new volunteering" |

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