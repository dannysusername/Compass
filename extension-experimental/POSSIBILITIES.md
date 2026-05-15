# Add-task / Edit-task — every possibility I can derive from the code

For each field: every state it can be in, every input value it can hold,
every cross-field combo. Each row marks current behavior + whether I need
your call (`ASK`) or it's obvious (`OK`).

Once you've worked through the ASK rows, this doc becomes the bug-prevention
contract for the forms. New bugs that come from a row not on this list mean
I missed a possibility — they get added here.

---

## A. Title (text input)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| A1 | Empty (no chars) | Submit silently no-ops (return early) | **ASK** — silent? error message? disable Add button until typed? |
| A2 | Whitespace-only ("   ") | Treated as empty after `.trim()`, same silent no-op | **ASK** — same as A1 or different? |
| A3 | Leading/trailing whitespace ("  Math hw  ") | Trimmed before submit | OK |
| A4 | Very long (1000+ chars) | Sent as-is to server (no client cap) | **ASK** — cap at 200? 500? no cap? |
| A5 | Emoji / non-ASCII / RTL text | Sent as-is, server stores as-is | OK |
| A6 | HTML-looking text (`<script>` etc.) | Sent as text, rendered as `textContent` (safe) | OK |
| A7 | Newline characters (paste) | Browser strips newlines from `<input type=text>` | OK |

## B. Class (select)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| B1 | User has 0 classes | Only "Personal" option; submits to `/tasks` | OK |
| B2 | User picks Personal explicitly | `class_id=""` sent, server treats as null | OK |
| B3 | User picks a class | `class_id=<id>` sent, server links | OK |
| B4 | User picks first class via default-class-id | Auto-set on form open if user has any class | OK |
| B5 | Class deleted in another tab while form open | Dropdown is stale; submit could 404 | **ASK** — refresh dropdown on form re-focus? show error? silent fail? |
| B6 | User has 50+ classes | Native `<select>` scrolls; usable | OK |

## C. Starts-on (datetime-local OR date)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| C1 | Empty + Due also empty | Both omitted from POST; server may default | **ASK** — should this be a "someday" task? Or block submit? |
| C2 | Empty + Due set | Single-date task, no range | OK |
| C3 | Set + Due empty | Range with no end? Server's behavior unclear | **ASK** — block / warn / allow? |
| C4 | Set in past (yesterday) | Allowed silently | **ASK** — block / warn / allow? |
| C5 | Set today | OK | OK |
| C6 | Set far future (5+ years) | Allowed silently | OK |
| C7 | Equals Due | Server collapses to single-date | OK |
| C8 | After Due | Inline error: "start date must be before end date", no submit | OK |
| C9 | Disabled by All-day or Repeat | Value cleared; not sent | OK |

## D. Due (datetime-local OR date)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| D1 | Empty (no due date) | Currently pre-filled with smart default; user can clear it | **ASK** — task with no due date allowed? Personal "someday" tasks? |
| D2 | Set in past | Allowed (intentional for retroactive todos) | **ASK** — confirm intentional? |
| D3 | Set today | OK | OK |
| D4 | Set far future | OK | OK |
| D5 | Equals Starts | Collapses to single-date | OK |
| D6 | Before Starts | Form blocks with inline error | OK |

## E. All-day (checkbox)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| E1 | Toggle ON | Due input switches `datetime-local → date`, value sliced to YYYY-MM-DD; Starts cleared + disabled | OK |
| E2 | Toggle ON when Due was empty | Due type changes; stays empty | OK |
| E3 | Toggle OFF | Due type back to `datetime-local`, default time `T17:00` appended; Starts re-enabled if Repeat is also off | OK |
| E4 | Toggle ON then OFF | Time portion is `17:00` (lost original time) | **ASK** — preserve original time across toggle? |
| E5 | Toggle ON with Repeat already set | Both restrict Starts; behavior consistent | OK |

## F. Tag (select)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| F1 | No tag (default "") | Sent as `tag_id=""` (no tag) | OK |
| F2 | System tag (exam/quiz/etc.) | Sent as `tag_id=<id>` | OK |
| F3 | User tag | Sent as `tag_id=<id>` | OK |
| F4 | "+ New tag…" sentinel without filling form | Submit blocked with error | OK |
| F5 | "+ New tag…" → fill name → click Create | Tag created, dropdown refreshed everywhere, new tag selected | OK |
| F6 | "+ New tag…" → start typing → switch to a real tag | Inline form auto-hides | OK |
| F7 | "+ New tag…" → empty name → click Create | Focus jumps to name field, no error shown | **ASK** — should show error message? |
| F8 | Tag deleted in another tab while form open | Dropdown stale; submit ignores invalid tag_id (server-side) | OK |

## G. Repeat (select)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| G1 | "Doesn't repeat" (default) | End-date field hidden + cleared, Starts re-enabled (if all-day is off) | OK |
| G2 | Daily / Weekly / Weekdays / Monthly | End-date field shown; Starts cleared + disabled | OK |
| G3 | Pick a repeat → switch to another repeat | Same effect (End-date stays visible, value preserved) | OK |
| G4 | Pick a repeat → switch back to "Doesn't repeat" | End-date hidden + value cleared | OK |

## H. End date (rrule_until — datetime-local)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| H1 | Empty with Repeat set | Recurrence runs forever | OK |
| H2 | Set with Repeat NOT set | Field hidden via CSS now; if value lingers it's NOT sent (defensive) | **FIXED** |
| H3 | Set BEFORE Due | Server stores; renders zero occurrences (silent) | **ASK** — warn user? block submit? |
| H4 | Set EQUAL to Due | Only one occurrence renders | OK |
| H5 | Set AFTER Due | Multiple occurrences render | OK |
| H6 | Set in past relative to today | Recurrence already over; silent | **ASK** — warn? |

## I. Reminders (chips + hidden CSV input + add-picker)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| I1 | No chips | Sent as `alerts=""` (server: explicit "no reminders") | OK |
| I2 | One chip | Sent as CSV with one offset | OK |
| I3 | Many chips | Sent as CSV; server replaces existing | OK |
| I4 | Duplicate offset added (5 min then 5 min again) | Auto-deduped via `[...new Set(...)]` | OK |
| I5 | Picker fires `change` for empty option | Ignored | OK |
| I6 | Reminder offset of 0 ("at time of event") | Allowed | OK |
| I7 | Custom offset (not in preset list) | No way to enter; preset only | **ASK** — add custom-minutes input? |

## J. Attachments

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| J1 | No files | Nothing sent | OK |
| J2 | One file | Buffered, uploaded after task create | OK |
| J3 | Many files | All buffered, uploaded sequentially | OK |
| J4 | Same filename twice in a row | Both buffered (dups allowed) | **ASK** — block dup or allow? |
| J5 | Very large file (server's `validate_upload` may reject) | Buffered without size check; reject surfaces only at upload time | **ASK** — client-side size cap? what value? |
| J6 | Non-PDF / non-image type | Allowed to buffer; server's `validate_upload` may reject | **ASK** — restrict to specific types? what list? |
| J7 | Cancel form before save | Buffered files dropped (correct) | OK |
| J8 | Save succeeds but attachment upload fails | Task exists; attachment missing; no user warning | **ASK** — show warning? retry? |
| J9 | Network drops mid-upload | Some attachments succeed, others fail | **ASK** — same as J8 |
| J10 | Edit modal: × on existing attachment | Confirm dialog → immediate delete (not buffered) | OK |
| J11 | Edit modal: × then save without confirming → ALREADY deleted | Currently no undo | **ASK** — accept "delete is final" or add undo? |

## K. Notes (textarea)

| # | Input | Current behavior | Status |
|---|-------|------------------|--------|
| K1 | Empty | Not sent (omitted from form data) on add; sent as `""` on edit (clears server value) | OK |
| K2 | Short | Sent as-is | OK |
| K3 | Very long (10k+ chars) | Sent as-is; server has no enforced limit I can see | **ASK** — client cap? |
| K4 | Multi-line | Newlines preserved | OK |
| K5 | HTML-looking | Sent as text | OK |

## L. Submit (validation, errors, in-flight)

| # | Scenario | Current behavior | Status |
|---|----------|------------------|--------|
| L1 | Title empty + click Add | Silent return | **ASK** — disable button until title typed, OR show "Title required" |
| L2 | Validation fail (starts > due, __new__ tag unfilled) | Inline error, no submit | OK |
| L3 | Server 400 (e.g., bad date format) | Status line shows server's error | OK |
| L4 | Server 401 (session expired) | Bounce to login; form data lost | OK (acceptable) |
| L5 | Server 500 | "Couldn't add: …" shown | OK |
| L6 | Network unreachable | "Couldn't add: …" shown | OK |
| L7 | Slow save → user clicks Add again | Add button disabled while in flight (just fixed) | OK |
| L8 | Save success → form clears → reload list → hide form | All OK | OK |
| L9 | User switches tab mid-save | Save continues; if it errors, user is on a different tab and never sees it | **ASK** — block tab switch? show toast on the new tab? silent? |
| L10 | User closes side panel mid-save | Save aborted (HTTP request canceled by browser) | **ASK** — accept? warn? |

## M. Cancel / dismiss

| # | Action | Current behavior | Status |
|---|--------|------------------|--------|
| M1 | Click Cancel button | Returns to list; on reopen, form is reset | OK |
| M2 | Click ← Back arrow | Same as Cancel | OK |
| M3 | Press Escape key | No handler → does nothing | **ASK** — Escape closes form? |
| M4 | Switch tab while form is open | Form hides; on tab-switch-back via list, form stays open underneath? | **ASK** — preserve or reset on tab switch? |

## N. State across opens (sticky vs reset)

| # | Field | Current behavior on FAB re-open | Status |
|---|-------|----------------------------------|--------|
| N1 | Title | Cleared | OK |
| N2 | Class | **Sticky** (preserved) | OK — explicit decision |
| N3 | Starts/Due | Re-pre-filled with smart defaults | OK |
| N4 | All-day | Cleared (off) | OK |
| N5 | Tag | Cleared | OK |
| N6 | Repeat + End-date | Cleared | OK |
| N7 | Reminders chips | Cleared | OK |
| N8 | Attachments buffered | Cleared | OK |
| N9 | Notes | Cleared | OK |

## O. Edit-task specific

| # | Scenario | Current behavior | Status |
|---|----------|------------------|--------|
| O1 | Open editor before /tasks/{id}/details.json resolves | Save disabled, shows "Loading…" | OK |
| O2 | Details fetch fails | Save re-enabled, alerts/rrule_until OMITTED from submit (preserved server-side) | OK |
| O3 | Edit recurring on first occurrence + remove rrule | Server enters wipe path → becomes single-date | OK |
| O4 | Edit recurring on later occurrence + remove rrule | Server caps rrule_until at this occurrence ("stop here") | OK |
| O5 | Edit + change class | Server moves task; row updates after reload | OK |
| O6 | Edit + delete an existing attachment via × | Confirms then fires immediate /attachments/{id}/delete | OK |
| O7 | Edit + add new attachment + click Cancel | Buffered files dropped (correct — never uploaded) | OK |
| O8 | Edit + delete existing + immediately Cancel | Existing attachment ALREADY deleted server-side; cancel doesn't undo | **ASK** — should delete-existing be deferred (buffer the deletion) like new files? |
| O9 | Edit + change Repeat from Daily → Weekly | Server replaces rrule | OK |
| O10 | Edit + clear All-day on a date-only task | Form switches type to datetime-local with default `T17:00` time appended | OK |

## P. Edit-modal Delete button

| # | Scenario | Current behavior | Status |
|---|----------|------------------|--------|
| P1 | Click Delete on a non-recurring task | `confirm("Delete this task?")` → POST `/tasks/{id}/delete` → reload + close editor | OK |
| P2 | Click Delete on a recurring task | Opens the bottom-sheet picker (this date / this+future / entire task) | OK |
| P3 | Picker → "this date" | POST `/tasks/{id}/exclude` with `occurrence_at=<row's original dueAt>` (NOT the form's current due_at) | OK |
| P4 | Picker → "this and future" | POST `/tasks/{id}/end-after` with same `occurrence_at` | OK |
| P5 | Picker → "entire task" | POST `/tasks/{id}/delete` | OK |
| P6 | Picker → Cancel | Sheet closes, editor stays open, no API call | OK |
| P7 | User changed `due_at` in form, then clicks Delete on recurring → "this date" | Sheet acts on the ORIGINAL occurrence (row's dueAt), not the typed value. **Intentional** — Delete operates on the task instance the editor opened against, not on form contents. | OK |
| P8 | Delete fires before /tasks/{id}/details.json resolves | Currently Save is disabled by details-loading; Delete is **not** disabled and acts on row data only — safe because Delete doesn't need rrule_until/alerts | OK |
| P9 | Delete API returns 401 | Bounce to login (same as Save) | OK |
| P10 | Delete API returns 5xx / network error | Editor stays open, red "Couldn't delete: …" status, Save + Delete re-enabled for retry | OK |
| P11 | Double-tap Delete | Guarded — handler returns early if `deleteBtn.disabled`; button disabled during in-flight call | OK |
| P12 | Delete editor opened from Class-detail | Returns to Class-detail after success (reuses `editReturnToClass`) | OK |
| P13 | Delete a row whose underlying class was deleted in another tab mid-edit | Server 404; surfaced as "Couldn't delete: 404 …" red status. User can cancel out. | **ASK** — okay as-is, or auto-bounce to a fresh list? |

---

## ASK rows summary (the only ones I need your call on)

A1, A2, A4, B5, C1, C3, C4, D1, D2, E4, F7, H3, H6, I7, J4, J5, J6, J8, J9, J11, K3, L1, L9, L10, M3, M4, O8, P13

That's 28 decisions. I'll walk you through them in batches of 3-4 per turn.
After your last answer, I apply them all and we ship.
