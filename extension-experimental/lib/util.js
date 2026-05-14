// Pure formatters + datetime helpers. No DOM, no fetch — safe to import
// from anywhere.

// Compact 12h time used in row labels — "9a", "5p", "11:30a". Returns ""
// when there's no real time to show (no due_at, midnight, all-day).
export function whenLabel(it) {
    if (it.is_all_day) return "•";
    if (!it.due_at) return "";
    const m = it.due_at.match(/T(\d{2}):(\d{2})/);
    if (!m) return "";
    if (m[1] === "00" && m[2] === "00") return "";
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ap = h >= 12 ? "p" : "a";
    if (h === 0) h = 12; else if (h > 12) h -= 12;
    return min === "00" ? `${h}${ap}` : `${h}:${min}${ap}`;
}

// Wall-clock-only datetime-local string from a Date — "YYYY-MM-DDTHH:MM".
export function formatLocal(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Date-only string from a Date — "YYYY-MM-DD".
export function formatLocalDate(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Round CURRENT time UP to the next 30-min mark (0 or 30). Mirrors Apple
// Calendar's "next half hour" default — events default to a slot in the
// future, never a past one.
export function smartDefaultStart() {
    const d = new Date();
    d.setSeconds(0, 0);
    const m = d.getMinutes();
    if (m === 0 || m === 30) {
        d.setMinutes(m + 30);
    } else if (m < 30) {
        d.setMinutes(30);
    } else {
        d.setMinutes(0);
        d.setHours(d.getHours() + 1);
    }
    return formatLocal(d);
}

// Apple-style — due defaults to one hour after start.
export function smartDefaultDue(startStr) {
    const d = new Date(startStr);
    d.setHours(d.getHours() + 1);
    return formatLocal(d);
}

// Same defaults but anchored to a chosen day (used when month-view's
// empty-day strip is tapped — pre-fill the form with that date).
export function smartDefaultsForDay(yyyymmdd) {
    // Use 9am-10am as the default slot when picking a non-today day —
    // the "next 30-min mark" only makes sense for "now".
    const start = `${yyyymmdd}T09:00`;
    const due = `${yyyymmdd}T10:00`;
    return { start, due };
}

// Reminder-chip label ("5 min before", "1h before", "1d before", "1w before").
export function alertLabel(minutes) {
    const m = parseInt(minutes, 10);
    if (m === 0) return "At time";
    if (m < 60) return `${m} min before`;
    if (m < 1440) return `${(m / 60).toFixed(0)}h before`;
    if (m < 10080) return `${(m / 1440).toFixed(0)}d before`;
    return `${(m / 10080).toFixed(0)}w before`;
}

// Format an ISO date ("2026-05-08") as "Today · Fri May 08" for the header.
export function formatHeaderDate(iso) {
    const d = new Date(iso + "T00:00:00");
    const opts = { weekday: "short", month: "short", day: "2-digit" };
    return "Today · " + d.toLocaleDateString(undefined, opts);
}

// Per-attachment size cap. Matches the syllabus PDF cap so users have
// one consistent number to remember. Server's `validate_upload` enforces
// its own ceiling; the client cap is a friendly early-fail.
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

// Add a list of File objects to a buffer with two safety checks:
//   1. Reject any file > MAX_ATTACHMENT_BYTES (return message).
//   2. If a filename is already in the buffer, prompt the user to
//      replace; replacing swaps the entry, keeping false skips it.
// Returns { added, skipped, replaced, error } so the caller can update
// the status line. Mutates `buffer` in place.
export function addFilesToBuffer(files, buffer) {
    let added = 0, skipped = 0, replaced = 0, error = "";
    for (const f of files) {
        if (f.size > MAX_ATTACHMENT_BYTES) {
            error = `${f.name} is larger than 25 MB.`;
            skipped++;
            continue;
        }
        const existingIdx = buffer.findIndex((b) => b.name === f.name);
        if (existingIdx >= 0) {
            const ok = confirm(`"${f.name}" is already attached. Replace it with the new file?`);
            if (ok) {
                buffer[existingIdx] = f;
                replaced++;
            } else {
                skipped++;
            }
            continue;
        }
        buffer.push(f);
        added++;
    }
    return { added, skipped, replaced, error };
}
