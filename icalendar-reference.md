# iCalendar Reference for Apple Calendar (Full Feature Support)

## The specs you actually need

The format you want to write is **iCalendar** (file extension `.ics`, MIME type `text/calendar`). It's defined across several RFCs — to get everything Apple Calendar supports, you'll touch most of them.

| RFC | What it covers | Why you care |
|---|---|---|
| **[RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545)** | Core iCalendar spec (2009, supersedes RFC 2445) | The base. Components, properties, parameters, recurrence rules, time zones |
| **[RFC 5546](https://datatracker.ietf.org/doc/html/rfc5546)** | iTIP — scheduling protocol | `METHOD`, invitations, REPLY/CANCEL semantics |
| **[RFC 6047](https://datatracker.ietf.org/doc/html/rfc6047)** | iMIP — iTIP over email | How invites are sent via email |
| **[RFC 6321](https://datatracker.ietf.org/doc/html/rfc6321)** | xCal — XML representation | Skip unless you want XML |
| **[RFC 7265](https://datatracker.ietf.org/doc/html/rfc7265)** | jCal — JSON representation | Skip unless you want JSON |
| **[RFC 7529](https://datatracker.ietf.org/doc/html/rfc7529)** | Non-Gregorian recurrences | `RSCALE` parameter |
| **[RFC 7953](https://datatracker.ietf.org/doc/html/rfc7953)** | Availability (`VAVAILABILITY`) | Free/busy with rules |
| **[RFC 7986](https://datatracker.ietf.org/doc/html/rfc7986)** | New properties (`NAME`, `COLOR`, `IMAGE`, `CONFERENCE`, `REFRESH-INTERVAL`, `SOURCE`) | Modern calendar metadata Apple supports |
| **[RFC 9073](https://datatracker.ietf.org/doc/html/rfc9073)** | Event publishing extensions | Structured data, locations, participants |
| **[RFC 9074](https://datatracker.ietf.org/doc/html/rfc9074)** | `VALARM` extensions | Snooze, proximity triggers |
| **[RFC 9253](https://datatracker.ietf.org/doc/html/rfc9253)** | Relationship support | Project-management style links between events |

Apple also defines a bunch of **non-standard `X-APPLE-*` properties** that aren't in any RFC but are required to unlock features like map-pinned locations, travel time, and custom alarm sounds. Those are documented at the bottom.

The single most useful link to bookmark: **https://datatracker.ietf.org/doc/html/rfc5545** — the core spec.

---

## File structure rules (RFC 5545 §3.1)

- Encoding: **UTF-8**
- Line endings: **CRLF** (`\r\n`), not `\n`. Apple Calendar will choke or silently drop fields if you use Unix line endings.
- **Line folding**: lines must be ≤ 75 octets. Longer lines wrap by inserting `CRLF` followed by a single space or tab. To unfold, remove `CRLF<space>` or `CRLF<tab>`.
- Property/parameter names are case-insensitive but conventionally UPPERCASE.
- Special characters in TEXT values must be escaped: `\\` `\,` `\;` `\n` (newline). Quote characters and colons inside parameter values require `DQUOTE` wrapping.

A minimal valid file:

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Your Name//Your App//EN
BEGIN:VEVENT
UID:unique-id-1@yourdomain.com
DTSTAMP:20260506T120000Z
DTSTART:20260510T140000Z
DTEND:20260510T150000Z
SUMMARY:Test event
END:VEVENT
END:VCALENDAR
```

---

## Components (the building blocks)

Every component is wrapped in `BEGIN:VXXX` / `END:VXXX`.

| Component | Purpose |
|---|---|
| `VCALENDAR` | Top-level wrapper. Required. |
| `VEVENT` | An event (meeting, appointment, all-day) |
| `VTODO` | A to-do / reminder |
| `VJOURNAL` | A journal entry (Apple Calendar ignores these — Reminders.app doesn't read them either) |
| `VFREEBUSY` | Free/busy time info for scheduling |
| `VTIMEZONE` | Time zone definition (with nested `STANDARD` and `DAYLIGHT` sub-components) |
| `VALARM` | Reminder/notification, nested inside `VEVENT` or `VTODO` |
| `VAVAILABILITY` | Recurring availability windows (RFC 7953) — limited Apple support |

---

## VCALENDAR-level properties

Required:
- `VERSION:2.0`
- `PRODID:-//Vendor//Product//EN` — your app's identifier

Optional but useful with Apple Calendar:
- `CALSCALE:GREGORIAN` (default)
- `METHOD` — `PUBLISH`, `REQUEST`, `REPLY`, `CANCEL`, `ADD`, `REFRESH`, `COUNTER`, `DECLINECOUNTER`
- `NAME:My Calendar` (RFC 7986) — human-readable calendar name
- `DESCRIPTION:...` (RFC 7986)
- `COLOR:cornflowerblue` (RFC 7986) — CSS3 color names; Apple respects this on subscription
- `REFRESH-INTERVAL;VALUE=DURATION:PT1H` (RFC 7986) — how often subscribers should poll
- `SOURCE:https://example.com/cal.ics` (RFC 7986)
- `URL:https://example.com/cal.ics`
- `IMAGE` (RFC 7986)
- `CATEGORIES:Work,Personal`

Apple-specific (non-standard but widely supported):
- `X-WR-CALNAME:My Calendar` — older equivalent of `NAME`. **Use both** for max compatibility.
- `X-WR-CALDESC:...` — older equivalent of `DESCRIPTION`
- `X-WR-TIMEZONE:America/New_York` — default time zone for the calendar
- `X-PUBLISHED-TTL:PT1H` — older equivalent of `REFRESH-INTERVAL`
- `X-APPLE-CALENDAR-COLOR:#FF5733` — overrides the standard `COLOR` on Apple platforms

---

## VEVENT — every property

This is the core. Required: `UID`, `DTSTAMP`. If `METHOD` is absent at the calendar level, you also need `DTSTART`.

### Identification & timing
| Property | Value type | Notes |
|---|---|---|
| `UID` | TEXT | Globally unique. Use a UUID + `@yourdomain`. |
| `DTSTAMP` | DATE-TIME (UTC) | When the iCalendar record was created. Required. |
| `DTSTART` | DATE-TIME or DATE | Start. Use `VALUE=DATE` for all-day events. |
| `DTEND` | DATE-TIME or DATE | End (exclusive). For all-day, this is the day *after* the last day. |
| `DURATION` | DURATION | Alternative to `DTEND`. `PT1H30M` etc. Mutually exclusive with `DTEND`. |
| `CREATED` | DATE-TIME (UTC) | When event was first created in calendaring system |
| `LAST-MODIFIED` | DATE-TIME (UTC) | Last edit timestamp |
| `SEQUENCE` | INTEGER | Increment on each edit; iTIP uses this to detect updates |

### Display
| Property | Notes |
|---|---|
| `SUMMARY` | The event title |
| `DESCRIPTION` | Body text. Escape newlines as `\n`. |
| `LOCATION` | Plain-text location |
| `URL` | Associated link |
| `CATEGORIES` | Comma-separated tags |
| `COLOR` | RFC 7986 — CSS3 color name |
| `IMAGE;VALUE=URI:...` | RFC 7986 — banner image |
| `GEO` | `latitude;longitude` — Apple uses this with `LOCATION` |

### Status & classification
| Property | Allowed values |
|---|---|
| `STATUS` | `TENTATIVE` / `CONFIRMED` / `CANCELLED` |
| `TRANSP` | `OPAQUE` (busy) / `TRANSPARENT` (free) — affects free/busy lookups |
| `CLASS` | `PUBLIC` / `PRIVATE` / `CONFIDENTIAL` |
| `PRIORITY` | 0–9 (0 = undefined, 1 = highest, 9 = lowest) |

### People
| Property | Notes |
|---|---|
| `ORGANIZER` | `mailto:` URI; supports `CN`, `SENT-BY`, `DIR`, `LANGUAGE` parameters |
| `ATTENDEE` | One per attendee. Many parameters: `CUTYPE`, `MEMBER`, `ROLE`, `PARTSTAT`, `RSVP`, `DELEGATED-TO`, `DELEGATED-FROM`, `SENT-BY`, `CN`, `DIR`, `LANGUAGE` |
| `CONTACT` | Free-form contact text |

`ATTENDEE` parameter values:
- `ROLE`: `CHAIR` / `REQ-PARTICIPANT` / `OPT-PARTICIPANT` / `NON-PARTICIPANT`
- `PARTSTAT`: `NEEDS-ACTION` / `ACCEPTED` / `DECLINED` / `TENTATIVE` / `DELEGATED`
- `CUTYPE`: `INDIVIDUAL` / `GROUP` / `RESOURCE` / `ROOM` / `UNKNOWN`
- `RSVP`: `TRUE` / `FALSE`

### Recurrence (this is where it gets fun)
| Property | Notes |
|---|---|
| `RRULE` | Recurrence rule. See full grammar below. |
| `RDATE` | Explicit additional dates the event recurs |
| `EXDATE` | Dates to exclude from the recurrence set |
| `RECURRENCE-ID` | Used with `UID` to override a single instance of a recurring event |

**RRULE parts** (one `FREQ` required, others optional, semicolon-separated):
- `FREQ=` `SECONDLY` / `MINUTELY` / `HOURLY` / `DAILY` / `WEEKLY` / `MONTHLY` / `YEARLY`
- `INTERVAL=N` — every N units
- `COUNT=N` — total occurrences
- `UNTIL=YYYYMMDDTHHMMSSZ` — end date (mutually exclusive with `COUNT`)
- `BYSECOND=`, `BYMINUTE=`, `BYHOUR=`
- `BYDAY=MO,TU,WE,TH,FR` — also supports ordinals like `1MO` (first Monday), `-1FR` (last Friday)
- `BYMONTHDAY=1,15,-1` — negatives count from end of month
- `BYYEARDAY=1,-1`
- `BYWEEKNO=`
- `BYMONTH=1,12`
- `BYSETPOS=N` — pick the Nth match within the period (e.g. `BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1` = last weekday of month)
- `WKST=MO` — week start
- `RSCALE=` (RFC 7529) — non-Gregorian calendars

Examples:
- Every weekday: `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR`
- Every other Tuesday for 10 occurrences: `RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU;COUNT=10`
- Last Friday of each month: `RRULE:FREQ=MONTHLY;BYDAY=-1FR`
- Annually on Jan 1: `RRULE:FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1`

### Relationships & attachments
| Property | Notes |
|---|---|
| `RELATED-TO` | Reference another event's UID. `RELTYPE` parameter: `PARENT`/`CHILD`/`SIBLING`, plus RFC 9253 temporal types |
| `ATTACH` | URI or inline base64 attachment. Apple Calendar shows these as paperclip items. |
| `CONFERENCE` | RFC 7986 — video conference URL with feature flags |

### Conference (RFC 7986)
```
CONFERENCE;VALUE=URI;FEATURE=AUDIO,VIDEO;LABEL=Zoom Link:https://zoom.us/j/123
```
`FEATURE` values: `AUDIO`, `VIDEO`, `PHONE`, `MODERATOR`, `SCREEN`, `CHAT`, `FEED`

---

## VALARM (notifications)

Nested inside `VEVENT` or `VTODO`. Required: `ACTION` and `TRIGGER`.

| Property | Notes |
|---|---|
| `ACTION` | `DISPLAY` / `AUDIO` / `EMAIL` / `PROCEDURE` (deprecated) |
| `TRIGGER` | When to fire. `-PT15M` (15 min before start), `-PT0S` (at start), absolute UTC datetime with `VALUE=DATE-TIME` |
| `DESCRIPTION` | Required for `DISPLAY` and `EMAIL` |
| `SUMMARY` | Required for `EMAIL` (subject line) |
| `ATTENDEE` | Required for `EMAIL` |
| `ATTACH` | Required for `AUDIO`; sound file URI |
| `DURATION` + `REPEAT` | Snooze: e.g. repeat 3 times every 5 min |
| `ACKNOWLEDGED` (RFC 9074) | When user dismissed it |
| `RELATED-TO`, `PROXIMITY` (RFC 9074) | Geofenced alarms — Apple supports these |
| `UID` (RFC 9074) | Lets you reference and modify alarms across syncs |

`TRIGGER;RELATED=END:-PT5M` triggers 5 minutes before the event *ends*.

Apple-specific:
- `X-APPLE-DEFAULT-ALARM:TRUE` — marks this as the default rather than user-set
- `X-WR-ALARMUID` — Apple's UID for the alarm pre-RFC-9074

---

## VTODO (reminders)

Same general property set as `VEVENT`, but timing properties differ:

| Property | Notes |
|---|---|
| `DUE` | Due date/time |
| `COMPLETED` | When marked done (DATE-TIME UTC) |
| `PERCENT-COMPLETE` | 0–100 |
| `STATUS` | `NEEDS-ACTION` / `IN-PROCESS` / `COMPLETED` / `CANCELLED` |

Apple Calendar puts these in **Reminders.app**, not the calendar grid.

---

## VTIMEZONE (do this right or DST will haunt you)

If your `DTSTART`/`DTEND` use a `TZID` parameter (rather than UTC `Z` suffix or floating time), you **must** include a `VTIMEZONE` for it. Apple Calendar will fall back to UTC and shift events by hours otherwise.

Each `VTIMEZONE` has nested `STANDARD` and (if applicable) `DAYLIGHT` blocks. Example for US Eastern:

```
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE
```

Then events reference it: `DTSTART;TZID=America/New_York:20260510T140000`

**The three time formats**:
1. **UTC**: `20260510T140000Z` (Z suffix)
2. **Local with TZID**: `DTSTART;TZID=America/New_York:20260510T140000`
3. **Floating** (no TZID, no Z): `DTSTART:20260510T140000` — interpreted as local time wherever displayed. Useful for "8am wherever you are" events but rarely what you want.

For **all-day events**: `DTSTART;VALUE=DATE:20260510` — no time component.

---

## Apple-specific extensions (not in any RFC)

These unlock features that pure RFC iCalendar can't express. They're prefixed `X-APPLE-` or `X-`.

### Rich location (the big one)

Without this, Apple Calendar treats `LOCATION` as plain text. With it, you get the map pin, "Travel time" suggestions, and "Time to leave" notifications.

```
LOCATION:Apple Park\, One Apple Park Way\, Cupertino\, CA 95014
X-APPLE-STRUCTURED-LOCATION;VALUE=URI;X-ADDRESS=One Apple Park Way\nCupertino\, CA 95014;X-APPLE-RADIUS=49.91307587029686;X-TITLE=Apple Park:geo:37.334606,-122.009102
```

Parameters on `X-APPLE-STRUCTURED-LOCATION`:
- `X-ADDRESS` — formatted address (escape `\n` between lines)
- `X-APPLE-RADIUS` — geofence radius in meters (used for proximity alarms)
- `X-TITLE` — display name shown above the address
- `X-APPLE-MAPKIT-HANDLE` — opaque MapKit identifier (you can usually omit this)

The value is a `geo:lat,lng` URI.

### Travel time
```
X-APPLE-TRAVEL-DURATION;VALUE=DURATION:PT30M
X-APPLE-TRAVEL-START;ROUTING=AUTOMOBILE;VALUE=URI;X-ADDRESS=...;X-TITLE=Home:geo:37.7,-122.4
```
`ROUTING` values: `AUTOMOBILE`, `WALKING`, `TRANSIT`

### Custom alarm sound
```
BEGIN:VALARM
ACTION:AUDIO
TRIGGER:-PT15M
ATTACH;VALUE=URI:Basso
END:VALARM
```
`Basso`, `Blow`, `Bottle`, `Frog`, `Funk`, `Glass`, `Hero`, `Morse`, `Ping`, `Pop`, `Purr`, `Sosumi`, `Submarine`, `Tink` are built-in macOS sounds.

### Other useful X-properties
- `X-APPLE-CREATOR-IDENTITY` / `X-APPLE-CREATOR-TEAM-IDENTITY` — app/team identifiers
- `X-APPLE-CALENDAR-COLOR:#FF5733` — calendar color (overrides RFC 7986 `COLOR`)
- `X-APPLE-DEFAULT-ALARM:TRUE` — alarm came from default settings
- `X-ALT-DESC;FMTTYPE=text/html:<html>...</html>` — HTML description (Outlook also reads this; Apple Calendar shows the plain `DESCRIPTION` but exporters preserve it)
- `X-APPLE-EWS-BUSYSTATUS` — Exchange-style status for Mail invites

---

## Practical checklist for "max Apple Calendar functionality"

When you're generating events, hit as many of these as apply:

1. **Identification**: `UID`, `DTSTAMP`, `CREATED`, `LAST-MODIFIED`, `SEQUENCE`
2. **Timing**: `DTSTART` and `DTEND` (or `DURATION`) with explicit `TZID` and a matching `VTIMEZONE` block
3. **Display**: `SUMMARY`, `DESCRIPTION`, `URL`, `COLOR`, `CATEGORIES`
4. **Location**: `LOCATION` + `GEO` + `X-APPLE-STRUCTURED-LOCATION` (all three)
5. **Travel**: `X-APPLE-TRAVEL-DURATION` + `X-APPLE-TRAVEL-START` if commuting matters
6. **Status**: `STATUS`, `TRANSP`, `CLASS`, `PRIORITY`
7. **Conferencing**: `CONFERENCE` (RFC 7986) for Zoom/Meet links — Apple Calendar shows a "Join" button
8. **Alarms**: at least one `VALARM`, with `PROXIMITY` if location-aware
9. **Recurrence**: `RRULE` + `EXDATE` for skipped instances; use `RECURRENCE-ID` for instance-specific overrides
10. **People**: `ORGANIZER` and `ATTENDEE` with `PARTSTAT` and `ROLE`
11. **Attachments**: `ATTACH` for any files
12. **Calendar metadata**: `NAME` + `X-WR-CALNAME`, `DESCRIPTION` + `X-WR-CALDESC`, `COLOR` + `X-APPLE-CALENDAR-COLOR`, `REFRESH-INTERVAL` + `X-PUBLISHED-TTL`

---

## Tools to validate your output

- **iCalendar Validator** — https://icalendar.org/validator.html — parses your file against RFC 5545
- **iCal4j** (Java), **icalendar** (Python — `pip install icalendar`), **ical.js** (JavaScript) — production parsers; their source code is also good reference for what real implementations accept
- The fastest debug loop: write `.ics`, drag onto Apple Calendar, see what survives. Apple is a bit lenient on input but strict on time zones.
