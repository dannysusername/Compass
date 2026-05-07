# iCal feature backlog (parked)

Pulled from `icalendar-reference.md` review. Build later, in this rough order:

## Tier 1 — what to build next

1. **Configurable per-task alert time**
   - `Task.reminder_minutes` int field, nullable (NULL = no reminder)
   - Add/edit modal dropdown: "No reminder / 5 min / 15 min / 1 hour / 1 day / 1 week"
   - Default 15 min for new tasks
   - iCal emits `-PT{N}M` or skips VALARM if NULL

2. **Smart default alarms for syllabus events by `kind`**
   - Multiple VALARM blocks per event when warranted
   - Mapping (no user config):
     - exam / midterm / final → 1 day before + 1 hour before
     - quiz / problem set / project / paper → 1 day before
     - assignment / deadline → 1 hour before
     - lecture / lab / recitation / discussion → none
     - holiday → none

## Tier 2 — polish

3. `STATUS` (CONFIRMED for active, CANCELLED for completed if we decide to surface them)
4. `TRANSP` — TRANSPARENT for tasks, OPAQUE for lectures/meetings
5. `CLASS:PRIVATE` — across the board
6. `URL` field linking back to Compass (`/classes/{id}` or `/today`)
7. `VTIMEZONE` block for `America/New_York` — spec compliance
8. `SEQUENCE` field — incremented on each task edit for proper change tracking

## Tier 3 — out of scope unless asked

- `CATEGORIES` from tags (low value without a filter UI)
- `RRULE` for recurring lectures (needs syllabus parsing rework)
- `CONFERENCE` for Zoom links (needs URL extraction in syllabus parser)
- `X-APPLE-STRUCTURED-LOCATION` (we don't track locations)
- `VTODO` to populate Apple Reminders.app
- Custom Apple alarm sounds (`Basso`, `Sosumi`, etc.)

## Notes

- Apple Calendar already receives our 15-min `VALARM` correctly. If alerts aren't appearing on iPhone, check Settings → Calendar → Notifications and enable alerts on the subscribed calendar.
- `icalendar-reference.md` (in repo) has the full RFC + Apple-specific reference.
