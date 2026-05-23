# Add/Edit Task — edge-case checklist

Living document. Each row: behavior we want + status. **FIXED** = already
in the code, **TODO** = needs work, **VERIFY** = code looks right but
needs you to click-test once.

## A. Visibility coupling (the End-date class of bug)

| # | Rule | Status | Notes |
|---|------|--------|-------|
| A1 | End-date field hidden when Repeat = Doesn't repeat | **FIXED** | CSS `[hidden]` was being overridden by `.field { display: flex }` — now `[hidden] { display: none !important }` enforces it |
| A2 | End-date field cleared when Repeat changes from set → none | FIXED | `syncRruleVisibility()` zeroes `rrule_until.value` |
| A3 | End-date never sent to server without an rrule | **FIXED** | Add-task: only sends when `rrule.value && rrule_until.value`. Edit-task: explicitly sends `""` when no rrule (clears server-side stale state) |
| A4 | Starts-on field disabled (greyed + cleared) ONLY when Repeat is on — All-day does NOT disable it (an all-day task may span a date range) | FIXED | `syncStartsDisabled()` keys off `hasRrule` only |
| A5 | Starts-on field re-enabled when Repeat off (regardless of All-day) | FIXED | Same function |
| A6 | Reminders / Attachments inputs always usable (no coupling) | FIXED | They're independent of date/repeat |

## B. Field type / format

| # | Rule | Status | Notes |
|---|------|--------|-------|
| B1 | Toggling All-day ON: Due input switches `datetime-local → date`, value sliced to YYYY-MM-DD | FIXED | `syncAllDay()` |
| B2 | Toggling All-day OFF: Due input switches `date → datetime-local`, value gets `T17:00` appended | FIXED | Same |
| B3 | Toggling All-day ON with Starts populated: Starts KEPT (date-only), so a multi-day all-day span survives; Due defaults to today when empty | FIXED | Inside `syncAllDay` (only a Repeat clears Starts) |
| B4 | All-day Due picker shows day picker, NOT time picker | VERIFY | Browser-native via `type=date` |
| B5 | Repeat dropdown limited to: Doesn't repeat / Daily / Weekly / Weekdays / Monthly | FIXED | Server's `_ALLOWED_RRULES` whitelist matches |

## C. Submit-time constraints

| # | Rule | Status | Notes |
|---|------|--------|-------|
| C1 | Title required (empty → no submit, no error toast — silent ignore) | FIXED | `if (!title) return;` |
| C2 | Title-only is valid: server creates with smart-default Due | VERIFY | Smart defaults pre-fill Due when form opens, so Title-only still has Due in form |
| C3 | Starts > Due → inline red status, no submit | FIXED | `if (starts && due && starts > due)` |
| C4 | Tag = "+ New tag…" without finishing inline create → inline error | FIXED | Both add and edit |
| C5 | Reminder list always sent (even empty) on add — server's smart defaults skip when explicit | FIXED | `fd.append("alerts", ...)` always for ADD |
| C6 | Reminder list NOT sent on edit until details have loaded — protects existing reminders | FIXED | `if (editDetailsLoaded)` guard |
| C7 | Attachments uploaded AFTER task create succeeds — failed upload doesn't roll back the task | FIXED | Best-effort loop after create |
| C8 | Submit while submitting (double-tap) | **TODO** | Should disable Save button during the await. Currently re-entrant |
| C9 | Cancel mid-attachment-upload | KNOWN | Upload runs on save, not while form is open — can't cancel mid-upload because there isn't one until save fires |

## D. State preservation across opens

| # | Rule | Status | Notes |
|---|------|--------|-------|
| D1 | FAB → form opens with all volatile fields cleared, except sticky Class | FIXED | `resetVolatileFields()` then `applyDefaultClass()` |
| D2 | Month-day → form opens with date pre-filled (9–10am that day), other fields cleared | FIXED | `showAddTaskForDay()` |
| D3 | Cancel form → next open is clean (not "I see my last attempt") | FIXED | `resetVolatileFields()` runs every show |
| D4 | Successful add → form resets but Class stays sticky for rapid-fire adds | FIXED | `addForm.reset(); addForm.class_id.value = stickyClass` |
| D5 | Edit form: dropdowns populated BEFORE setting values (so `select.value = "5"` doesn't drop to "" silently) | FIXED | `await ensureLookups()` before `populateEditor()` |
| D6 | Edit form: Save disabled until `/tasks/{id}/details.json` resolves — protects alerts + rrule_until | FIXED | `setSaveEnabled(false)` until promise resolves |
| D7 | Edit form: detail fetch fails → Save re-enabled, alerts/rrule_until omitted from submit (preserved server-side) | FIXED | `editDetailsLoaded = false` skips those fields |

## E. Recurring task semantics

| # | Rule | Status | Notes |
|---|------|--------|-------|
| E1 | Edit recurring on a non-anchor occurrence + change Repeat to None → server caps rrule_until at this occurrence ("stop here") | FIXED | Server logic; client sends `rrule="" + due_at=occurrence` |
| E2 | Edit recurring on the anchor occurrence + change Repeat to None → wipe path (becomes a single-date task) | FIXED | Server detects `cap_at <= anchor` |
| E3 | Delete recurring row → bottom-sheet picker (this date / this+future / entire task) | FIXED | `behaviors/recurring-sheet.js` |
| E4 | Recurring task + Starts-on populated → Starts auto-cleared and disabled (rrule + range mutually exclusive — see CLAUDE.md) | FIXED | `syncStartsDisabled` |
| E5 | Recurring task with explicit `rrule_until` in past → server stores it; web app + iCal feed silently render zero occurrences | KNOWN | Server-side concern; no client warning. Acceptable |
| E6 | Delete from edit modal (recurring task) → opens the SAME bottom-sheet picker as the row × | FIXED | `#editor-delete` handler calls `showRecurringSheet(editSourceRow, ...)` |
| E7 | Delete from edit modal: occurrence_at sent to `/exclude` and `/end-after` is the row's ORIGINAL `dueAt`, not the form's current `due_at` (user may have edited it) | **FIXED** | `editSourceRow.dataset.dueAt` captured at editor open |
| E8 | Delete from edit modal (non-recurring) → `confirm()` then hard-delete | FIXED | Same UX as row × non-recurring branch |
| E9 | Delete success from edit modal → reload + close editor (same return routing as Save: Class-detail returns to Class-detail) | FIXED | `await load(); hideEditor()` reuses `editReturnToClass` |
| E10 | Delete failure from edit modal → editor stays open with red status, Save + Delete re-enabled for retry | FIXED | `performDeleteFromEditor` catch branch |

## F. Cross-class drag (tasks only — events are tied to their class)

| # | Rule | Status | Notes |
|---|------|--------|-------|
| F1 | Drag task between class blocks in Today → server updates class_id, other fields preserved | FIXED | `behaviors/drag.js` sends ONLY `class_id` |
| F2 | Drag task between class blocks within a Month day → same, plus per-day reorder | FIXED | Same scope detection |
| F3 | Drag event across class blocks → blocked (events stay in their class) | FIXED | `if (row.dataset.kind === "task")` gate |
| F4 | Drag from Personal block → server interprets `class_id=""` as Personal sentinel | FIXED | `classIdOfList(...) === "0" ? "" : ...` |

## G. Tag dropdown

| # | Rule | Status | Notes |
|---|------|--------|-------|
| G1 | System tags grouped above user tags | FIXED | `fillTagSelect` builds optgroups |
| G2 | "+ New tag…" sentinel reveals inline name+color picker | FIXED | `bindInlineNewTag` |
| G3 | Creating a new tag inline refreshes EVERY tag-select on the page | FIXED | Bust `state.tagsPromise`, refill all `select[name='tag_id']` |
| G4 | New tag's color hex valid? | VERIFY | `<input type="color">` enforces `#rrggbb` browser-side |
| G5 | Pick "+ New tag…" then pick another tag → inline form auto-hides | FIXED | `change` listener on the select |

## H. Class dropdown

| # | Rule | Status | Notes |
|---|------|--------|-------|
| H1 | Add-task default Class = first user class (or Personal if none) | FIXED | `defaultClassId()` reads cached classes list |
| H2 | Edit task: Personal task shows `value="0"`, server interprets "0" as Personal | FIXED | Mismatch with add-task's `value=""` is intentional; server accepts both |
| H3 | Class dropdown empty (no classes) → "Personal (no class)" only option, submit goes to `/tasks` | FIXED | `applyDefaultClass()` falls back to "" |

## I. Reminders chips

| # | Rule | Status | Notes |
|---|------|--------|-------|
| I1 | Picker shows preset offsets (0, 5, 15, 30, 60, 120, 1440, 2880, 10080 minutes) | FIXED | Static `<select>` options |
| I2 | Adding same offset twice deduplicates | FIXED | `[...new Set(...)]` |
| I3 | Chips sort descending (largest offset first → "1 week before" then "1 day before" etc.) | FIXED | `.sort((a, b) => b - a)` |
| I4 | × on chip removes it, hidden CSV updates immediately | FIXED | Re-renders chips |
| I5 | Hidden `<input name="alerts">` always carries current CSV — submit reads its value | FIXED | `hidden.value = cleaned.join(",")` |
| I6 | Edit modal: existing alerts pre-populate from `/tasks/{id}/details.json` | FIXED | `editAlerts = (d.alerts || [])...` |

## J. Attachments

| # | Rule | Status | Notes |
|---|------|--------|-------|
| J1 | Add-task: file picker buffers → uploaded after task create returns id | FIXED | `for (const f of addPendingFiles) await api.addAttachment(...)` |
| J2 | Edit-task: existing attachments shown with × delete (immediate, confirmed) | FIXED | `renderExistingAttachmentRow` calls `api.deleteAttachment` |
| J3 | Edit-task: new attachments buffer, upload after save | FIXED | Same pattern as add |
| J4 | Existing attachment click → opens in new tab via `chrome.tabs.create` | FIXED | Anchor click in extension origin doesn't navigate panel; explicit tab open |
| J5 | Attachment delete failure → row stays, alert message | FIXED | `try/catch` around `deleteAttachment` |
| J6 | Same file picked twice in a row → both buffered (no dedup) | KNOWN | Add-task allows duplicates by filename; user can × the dup |

## K. Visual / layout (the things that look broken even when logic works)

| # | Rule | Status | Notes |
|---|------|--------|-------|
| K1 | All `[hidden]` elements actually hidden regardless of CSS specificity | **FIXED** | Global `[hidden] { display: none !important }` |
| K2 | Disabled Starts-on label shows greyed (visual disabled state) | FIXED | `.field.disabled` CSS |
| K3 | Save button shows "Loading…" while details fetch in flight | FIXED | `setSaveEnabled` toggles label |
| K4 | Status line clears when form re-opens | FIXED | `setStatus("", "")` in show* functions |
| K5 | Long task title in row → truncates with ellipsis | VERIFY | Existing CSS does this; confirm |
| K6 | Long tag name in dropdown → truncates? | KNOWN | Native `<select>` limits — browser handles |

## L. Navigation / scroll preservation

| # | Rule | Status | Notes |
|---|------|--------|-------|
| L1 | Open day from month → cancel/back → land at same scroll position | **FIXED** | `nav.js` saves `window.scrollY` on first list→secondary hop, restores in `returnToList` with double-rAF |
| L2 | Open class-detail from Today → back → land at same scroll position | FIXED | Same mechanism |
| L3 | Edit task from class-detail → save → land back on class-detail (not Today) | FIXED | `editReturnToClass` captured at editor open |
| L4 | Tab change resets saved scroll (Today scroll shouldn't restore on Month) | FIXED | `resetSavedScroll()` in `setView` |

## M. Auth / network

| # | Rule | Status | Notes |
|---|------|--------|-------|
| M1 | Any 401 from any fetch → bounce to login surface | FIXED | `if (err instanceof NotAuthenticated) showLogin()` everywhere |
| M2 | Server unreachable on boot → show login with error message (not stuck on "Loading…") | FIXED | `boot()` catch branch |
| M3 | Login → cookie shared with website tabs (same origin) | FIXED | `credentials: "include"` |
| M4 | Logout → clears local state, re-shows login | FIXED | `clearForLogout()` |

## N. Syllabus parsing entitlement (free pool vs. own key)

| # | Rule | Status | Notes |
|---|------|--------|-------|
| N1 | Own key set → Upload-syllabus enabled, Settings shows "unlimited", no cap | FIXED | `canParse` true via `me.xai_api_key_set`; server skips counter |
| N2 | No own key, server pool configured, parses left → Upload enabled, Settings shows "N of M used · K left" | FIXED | `canParse` via `server_key_available && free_parses_remaining > 0` |
| N3 | No own key, free_parses_remaining == 0 → Upload disabled with tooltip; click bounces to Settings with "add your own key" message | FIXED | `blockMsg` (server-pool variant) |
| N4 | No own key AND no server pool (dev/tests) → Upload disabled, "set your xAI key" tooltip; `/syllabus` returns `need_key` | FIXED | `server_key_available` false → legacy path |
| N5 | `limit_reached` raised mid-upload (raced past the cap) → routes to Settings with the out-of-parses message | FIXED | `syllabus.js` catch branch matches `limit_reached` |
| N6 | Successful free-pool parse → `/me.json` re-fetched so count + button state update without a full reload | FIXED | `api.me()` refresh in poll() done branch |
| N7 | Counter increments at upload enqueue (not parse success) → a failed parse still spends a credit | KNOWN | Intentional: keeps displayed count truthful + blocks burst abuse. Acceptable at this cap |
| N8 | Adding an own key while at the cap → immediately uncapped (remaining becomes null/unlimited) | FIXED | `_parse_usage` recomputed from `xai_api_key` presence |
| N9 | Admin-granted unlimited (no own key) → `free_parses_remaining === null` → Upload enabled, Settings shows "granted by an admin" | FIXED | `canParse` treats `remaining === null` as uncapped; settings.js has a granted branch |
| N10 | Granted user still needs the server pool (`server_key_available`) — grant without `XAI_API_KEY` configured still blocks (`need_key`) | FIXED | Server gate checks `server_key_available` before the cap |

---

## Open items — pick what to attack next

- **C8** (debounce double-submit on Save / Add buttons) — annoying and easy.
- **K5** (verify long-title truncation actually works in the wild)
- **B4** (verify all-day picker behavior in current Chrome)
- Anything else you spot. Add a row, mark TODO, send it back.
