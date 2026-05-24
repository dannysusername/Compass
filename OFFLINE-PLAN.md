# Offline-everything plan (local-first)

**Goal:** every feature works fully offline and syncs on reconnect, on BOTH
the web PWA and the Chrome extension — identically. The **only** exception is
**syllabus PDF parsing** (needs the xAI/Grok API) and **attachment file
upload** (the bytes need the server's storage); both degrade with a clear
"needs connection" message, everything else just works.

**Conflict model:** newest-edit-wins per row, with a pick-a-side fallback for
the rare unmergeable case (Anki-style). Cloud (Postgres) is the sync hub.

Status legend: ✅ done · �doing · ⬜ todo

---

## Phase 1 — Server: make `/sync` carry every entity ✅ DONE
- ✅ Pull (`GET /sync`) returns classes/tags/tasks/events + deletions.
- ✅ Push (`POST /sync`) for tasks AND classes/tags/events (`_PUSH_KINDS` config).
- ✅ Tombstones on class/tag/event delete routes.
- ✅ Tests: `tests/test_sync_push.py` (9 pass).

## Phase 2 — Shared client sync engine (both surfaces)
- ✅ Extension IndexedDB mirror + queue + reconnect (`lib/sync.js`).
- ✅ Web write-queue + replay + applyToDom (`static/sync.js`).
- ⬜ Generalize both queues to **all kinds** (class/tag/event upsert+delete), not just tasks.
- ⬜ Offline reads for ALL views (extension: month + classes from mirror/cache; web: SW cache + applyToDom for every kind).

## Phase 2.5 — Queues generalized to all kinds ✅ DONE
Both `sync.js` files: `queueUpsert(kind,data)`/`queueDelete(kind,id)` + replay/push group by kind.

## Phase 3 — Wire every WRITE through the offline queue (web `todo.js` + extension handlers)
Tasks: ✅ add · ✅ toggle · ✅ delete · ✅ **full edit modal** · ⬜ recurring (exclude/end-after) · ⬜ reorder.
Events: ✅ toggle · ✅ delete · ⬜ edit · ⬜ clone.
Classes: ⬜ create · ⬜ edit · ⬜ delete.   ← web add-class is a plain form (needs a JS interceptor)
Tags: ⬜ create · ⬜ edit · ⬜ delete.   ← **hard bit:** creating a tag *while tagging a task offline* needs cross-entity temp-id resolution in the server push (a task's tag_id/class_id pointing at a not-yet-created row).
Settings: timezone already best-effort; calendar-token regen needs server (degrade).

**Done + testable now (the daily workflow):** tasks fully offline (view/add/edit/check-off/delete) + events (check-off/delete), both apps, syncing on reconnect, server carries every entity.
**Remaining = setup/occasional ops:** create/edit/delete classes & tags offline, recurring-rule edits, drag-reorder. Lower daily value; the tag-on-task case has real cross-entity complexity.

## Phase 4 — Reconnect + reliability
- ✅ Replay queue on `online` (both).
- ⬜ Replay ALL kinds, ordered (classes/tags before tasks so FKs resolve; id-map remap of class_id/tag_id on queued tasks).
- ⬜ Sync status indicator (offline / syncing / synced).
- ⬜ Pick-a-side conflict prompt (rare).

## Phase 5 — Verify
- ⬜ Full automated pass: `tests/`, `tests_browser/`, `extension-experimental/tests/`.
- ⬜ Manual: install PWA, go offline, do every action, reconnect → consistent on all surfaces.

## Out of scope (genuinely need network)
- Syllabus parsing (xAI/Grok). Offline: queue the upload? No — block with a clear message; parsing can't happen offline.
- Attachment file upload (bytes → R2/local storage). Offline: block that one action with a message; the task itself still saves.
