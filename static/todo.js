// Todo list interactions:
//   - Circular toggle (open ↔ done) via AJAX
//   - Inline edit of task title (click title, type, Enter/blur to save)
//   - Drag burger handle to reorder priority (FLIP animation)
//   - Add task with class picker
//   - Delete task

(function () {
    // ---- DOM scope helper ----

    // Apply the callback to the live document AND to each template that
    // backs a day-modal. Without this, mutations made while a day-modal is
    // open never reach the template — so reopening the modal reverts the
    // change to the server-rendered state.
    function forEachScope(cb) {
        cb(document);
        document.querySelectorAll('template[data-day-modal-content]').forEach((tpl) => {
            cb(tpl.content);
        });
    }

    // ---- Toggle ----

    function setDoneEverywhere(kind, id, done) {
        // Update every copy of this item on the page — today list rows,
        // week-day-modal rows, and the compact calendar cells. Keeps the
        // UI honest when the same task appears in multiple places.
        const sel =
            `.todo-row[data-kind="${kind}"][data-id="${id}"], ` +
            `.day-cell-item[data-kind="${kind}"][data-id="${id}"]`;
        forEachScope((root) => {
            root.querySelectorAll(sel).forEach((el) => {
                el.classList.toggle('done', done);
                const tog = el.querySelector('.todo-toggle');
                if (tog) tog.setAttribute('aria-pressed', done ? 'true' : 'false');
            });
        });
    }

    function bindToggle(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async () => {
            const row = btn.closest('.todo-row');
            if (!row) return;
            const kind = row.dataset.kind;
            const id = row.dataset.id;
            const url = kind === 'event' ? `/events/${id}/toggle` : `/tasks/${id}/toggle`;
            const wasDone = row.classList.contains('done');
            setDoneEverywhere(kind, id, !wasDone);
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
            } catch (err) {
                // Offline: keep the optimistic state, queue it for the next
                // sync (tasks only — events are server-generated).
                if (kind === 'task' && window.CompassSync && CompassSync.isOffline(err)) {
                    await CompassSync.queueTaskUpsert({
                        id: Number(id),
                        completed_at: !wasDone ? new Date().toISOString() : null,
                    });
                } else {
                    setDoneEverywhere(kind, id, wasDone);
                    console.error('toggle failed:', err);
                }
            }
        });
    }

    // ---- Delete ----

    // Show the recurring-delete dialog and resolve to one of:
    //   'this'   — exclude just this occurrence
    //   'future' — set rrule_until to right before this occurrence
    //   'all'    — fall through to a normal delete-everything
    //   null     — user cancelled
    function showRecurringDeleteDialog(occurrenceLabel) {
        const modal = document.getElementById('delete-recurring-modal');
        if (!modal) return Promise.resolve('all');
        const promptEl = modal.querySelector('[data-recurring-prompt]');
        if (promptEl && occurrenceLabel) {
            promptEl.textContent = `This task repeats. Pick which instances to delete starting ${occurrenceLabel}.`;
        }
        modal.hidden = false;
        document.body.classList.add('modal-open');
        return new Promise((resolve) => {
            let done = false;
            const finish = (val) => {
                if (done) return;
                done = true;
                modal.hidden = true;
                document.body.classList.remove('modal-open');
                resolve(val);
            };
            modal.querySelectorAll('[data-delete-mode]').forEach((b) => {
                b.onclick = () => finish(b.dataset.deleteMode);
            });
            modal.querySelectorAll('[data-close-modal]').forEach((b) => {
                b.onclick = () => finish(null);
            });
        });
    }

    function removeRowsFromUI(kind, id) {
        const sel =
            `.todo-row[data-kind="${kind}"][data-id="${id}"], ` +
            `.day-cell-item[data-kind="${kind}"][data-id="${id}"]`;
        document.querySelectorAll(sel).forEach((el) => {
            el.style.transition = 'opacity 0.15s ease, max-height 0.15s ease';
            el.style.opacity = '0';
            el.style.maxHeight = '0';
            setTimeout(() => el.remove(), 160);
        });
        document.querySelectorAll('template[data-day-modal-content]').forEach((tpl) => {
            tpl.content.querySelectorAll(sel).forEach((el) => el.remove());
        });
    }

    function bindDelete(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async () => {
            const row = btn.closest('.todo-row');
            const kind = btn.dataset.kind || 'task';
            const label = kind === 'event' ? 'event' : 'task';
            const id = btn.dataset.id;
            const isRecurring = !!(row && kind === 'task' && row.dataset.rrule);

            if (isRecurring) {
                const occurrenceAt = row.dataset.dueAt || '';
                const occurrenceLabel = occurrenceAt
                    ? new Date(occurrenceAt).toLocaleDateString()
                    : '';
                const mode = await showRecurringDeleteDialog(occurrenceLabel);
                if (!mode) return;
                if (mode === 'this' || mode === 'future') {
                    const path = mode === 'this' ? 'exclude' : 'end-after';
                    const fd = new FormData();
                    fd.append('occurrence_at', occurrenceAt);
                    try {
                        const r = await fetch(`/tasks/${id}/${path}`, {
                            method: 'POST', body: fd,
                            headers: { 'Accept': 'application/json' },
                        });
                        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
                        // Server-side mutation changes which dates render —
                        // softRefresh re-pulls and swaps the affected
                        // sections so the rows disappear from every view.
                        await softRefresh();
                    } catch (err) {
                        alert('Could not delete: ' + err.message);
                    }
                    return;
                }
                // mode === 'all' falls through to the standard delete below.
            } else if (!confirm(`Delete this ${label}?`)) {
                return;
            }

            const url = kind === 'event'
                ? `/events/${id}/delete`
                : `/tasks/${id}/delete`;
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
                removeRowsFromUI(kind, id);
            } catch (err) {
                // Offline: keep it removed + queue the delete (non-recurring
                // tasks only; recurring this/future + events need the server).
                if (kind === 'task' && window.CompassSync && CompassSync.isOffline(err)) {
                    await CompassSync.queueTaskDelete(id);
                    removeRowsFromUI(kind, id);
                } else {
                    alert(`Could not delete ${label}: ` + err.message);
                }
            }
        });
    }

    // ---- Alert chips (modal) ----

    function alertLabel(minutes) {
        const m = parseInt(minutes, 10);
        if (Number.isNaN(m)) return String(minutes);
        if (m === 0) return 'At time';
        if (m < 60) return `${m} min before`;
        if (m === 60) return '1 hour before';
        if (m < 1440) return `${m / 60} hours before`;
        if (m === 1440) return '1 day before';
        if (m < 10080) return `${m / 1440} days before`;
        if (m === 10080) return '1 week before';
        return `${m} min before`;
    }

    function getAlerts(form) {
        const hidden = form.querySelector('[data-alerts-value]');
        if (!hidden || !hidden.value) return [];
        return hidden.value.split(',')
            .map((x) => parseInt(x, 10))
            .filter((n) => !Number.isNaN(n));
    }

    function setAlerts(form, list) {
        const hidden = form.querySelector('[data-alerts-value]');
        const chips = form.querySelector('[data-alerts-chips]');
        if (!hidden || !chips) return;
        // Dedupe + sort descending so "1 day, 1 hour, 15 min" reads naturally.
        const seen = new Set();
        const cleaned = list
            .map((x) => parseInt(x, 10))
            .filter((n) => !Number.isNaN(n) && n >= 0 && n <= 40320)
            .filter((n) => { if (seen.has(n)) return false; seen.add(n); return true; })
            .sort((a, b) => b - a);
        hidden.value = cleaned.join(',');
        chips.innerHTML = '';
        cleaned.forEach((m) => {
            const chip = document.createElement('span');
            chip.className = 'alert-chip';
            chip.dataset.minutes = String(m);
            const label = document.createElement('span');
            label.textContent = alertLabel(m);
            const rm = document.createElement('button');
            rm.type = 'button';
            rm.className = 'alert-chip-remove';
            rm.setAttribute('aria-label', `Remove ${alertLabel(m)} reminder`);
            rm.textContent = '×';
            rm.addEventListener('click', () => {
                form.dataset.alertsTouched = '1';
                setAlerts(form, getAlerts(form).filter((x) => x !== m));
            });
            chip.appendChild(label);
            chip.appendChild(rm);
            chips.appendChild(chip);
        });
    }

    function bindAllDayBehavior(form) {
        // When "All day" is checked: switch starts_at + due_at inputs to
        // type="date" so they show only YYYY-MM-DD with no time component
        // (per the user's "just put the date, no hours" preference). Also
        // clear and disable starts_at — an all-day task is anchored to the
        // due date alone. Idempotent.
        if (form.dataset.allDayBound === '1') return;
        form.dataset.allDayBound = '1';
        const checkbox = form.querySelector('[data-task-all-day]');
        const startsInput = form.querySelector('input[name="starts_at"]');
        const startsLabel = startsInput ? startsInput.closest('label') : null;
        const dueInput = form.querySelector('input[name="due_at"]');
        if (!checkbox) return;

        function toDate(input) {
            // YYYY-MM-DDTHH:MM → YYYY-MM-DD; preserve empty values.
            if (!input) return;
            if (input.value && input.type === 'datetime-local') {
                input.value = input.value.slice(0, 10);
            }
            input.type = 'date';
        }
        function toDateTime(input, defaultTime) {
            if (!input) return;
            if (input.value && input.type === 'date') {
                input.value = input.value + 'T' + (defaultTime || '09:00');
            }
            input.type = 'datetime-local';
        }

        // Only the ADD form invents convenience defaults. The EDIT form
        // must show exactly what the task has — injecting a smart-default
        // *start* (e.g. 6pm) into an edited task that had none would make
        // start > due and silently trip the "start before due" submit
        // guard, blocking the save (this is the overdue-edit bug: a past
        // due date is always < a present-day default start).
        const isAddForm = form.matches('[data-add-task]');
        const todayDate = () => {
            const d = new Date();
            const pad = (n) => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        };
        const sync = () => {
            const on = !!checkbox.checked;
            // Starts-on is disabled ONLY by a Repeat (rrule + range are
            // mutually exclusive — see bindRruleVisibility). All-day does
            // NOT disable it: an all-day task may span multiple days
            // (start date → due date), it just has no time component.
            const rruleSelect = form.querySelector('[data-task-rrule]');
            const hasRrule = !!(rruleSelect && rruleSelect.value);
            if (on) {
                // All-day: date-only inputs. Keep the start field usable so
                // the user can set a multi-day all-day span; clear it only
                // when a Repeat forces it off.
                if (startsInput) {
                    toDate(startsInput);
                    startsInput.disabled = hasRrule;
                    if (hasRrule) startsInput.value = '';
                }
                toDate(dueInput);
                // An all-day task must have a date — default due to today
                // when empty so we never create a dateless, hard-to-manage
                // task. A date the user already typed is left alone.
                if (dueInput && !dueInput.value) dueInput.value = todayDate();
            } else {
                // Restore datetime inputs. Starts stays disabled if Repeat
                // is set; otherwise re-enable. Only the ADD form pre-fills a
                // default start — see note above.
                if (startsInput) {
                    startsInput.disabled = hasRrule;
                    toDateTime(startsInput, '09:00');
                    if (hasRrule) {
                        startsInput.value = '';
                    } else if (isAddForm && !startsInput.value) {
                        startsInput.value = _smartDefaultStart();
                    }
                }
                toDateTime(dueInput, '10:00');
                if (isAddForm && dueInput && !dueInput.value && startsInput && startsInput.value) {
                    dueInput.value = _smartDefaultDue(startsInput.value);
                }
            }
            if (startsLabel) startsLabel.classList.toggle('disabled', hasRrule);
        };
        sync();
        checkbox.addEventListener('change', sync);
    }

    function bindRruleVisibility(form) {
        if (form.dataset.rruleVisBound === '1') return;
        form.dataset.rruleVisBound = '1';
        const select = form.querySelector('[data-task-rrule]');
        const untilLabel = form.querySelector('[data-task-rrule-until]');
        if (!select || !untilLabel) return;
        // Starts-on can't coexist with a Repeat: server-side _emit_task
        // ignores starts_at when rrule is set (one task can repeat OR
        // span a range, not both), and the iCal feed mirrors that. We
        // grey out + clear the field here so what the user enters
        // matches what the web app renders matches what Apple Calendar
        // shows. Same disabled-label pattern as the All-day checkbox.
        // Note: All-day might ALSO want starts disabled, so we OR the
        // two conditions instead of unilaterally re-enabling.
        const startsInput = form.querySelector('input[name="starts_at"]');
        const startsLabel = startsInput ? startsInput.closest('label') : null;
        const sync = () => {
            const showing = !!select.value;
            untilLabel.hidden = !showing;
            if (!showing) {
                const inp = untilLabel.querySelector('input');
                if (inp) inp.value = '';
            }
            // Only a Repeat disables Starts-on. All-day no longer does —
            // an all-day task can span a date range.
            if (startsInput) {
                startsInput.disabled = showing;
                if (showing) startsInput.value = '';
            }
            if (startsLabel) startsLabel.classList.toggle('disabled', showing);
        };
        sync();
        select.addEventListener('change', sync);
    }

    function bindAlertsAdder(form) {
        if (form.dataset.alertsBound === '1') return;
        form.dataset.alertsBound = '1';
        const select = form.querySelector('[data-alerts-add-select]');
        if (!select) return;
        select.addEventListener('change', () => {
            const v = select.value;
            if (!v) return;
            form.dataset.alertsTouched = '1';
            setAlerts(form, [...getAlerts(form), parseInt(v, 10)]);
            select.value = '';
        });
    }

    // ---- Attachments (edit modal — talks to server) ----

    function attachmentRowEl(att, onRemove) {
        const li = document.createElement('li');
        li.className = 'attachment-row';
        li.dataset.attachmentId = String(att.id);
        const name = document.createElement('span');
        name.className = 'attachment-name';
        name.textContent = att.original_name || att.filename;
        li.appendChild(name);
        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'attachment-remove';
        rm.setAttribute('aria-label', `Remove ${att.original_name || att.filename}`);
        rm.textContent = '×';
        rm.addEventListener('click', async () => {
            if (!confirm(`Remove "${att.original_name || att.filename}"?`)) return;
            try {
                const r = await fetch(`/attachments/${att.id}/delete`, {
                    method: 'POST', headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status}`);
                onRemove();
            } catch (err) {
                alert('Could not remove: ' + err.message);
            }
        });
        li.appendChild(rm);
        return li;
    }

    function renderAttachments(listEl, attachments, onChange) {
        listEl.innerHTML = '';
        attachments.forEach((att) => {
            listEl.appendChild(attachmentRowEl(att, () => {
                const next = attachments.filter((x) => x.id !== att.id);
                renderAttachments(listEl, next, onChange);
                onChange(next);
            }));
        });
    }

    function bindEditAttachments(form, taskId, initial) {
        const wrap = form.querySelector('[data-task-attachments-edit]');
        const listEl = form.querySelector('[data-attachments-list]');
        const fileInput = form.querySelector('[data-attachments-input]');
        if (!wrap || !listEl || !fileInput) return;
        wrap.hidden = false;
        let attachments = initial.slice();
        const onChange = (next) => { attachments = next; };
        renderAttachments(listEl, attachments, onChange);
        // Replace the input element to clear listeners across re-opens.
        const fresh = fileInput.cloneNode(true);
        fileInput.parentNode.replaceChild(fresh, fileInput);
        fresh.addEventListener('change', async () => {
            const f = fresh.files && fresh.files[0];
            if (!f) return;
            const fd = new FormData();
            fd.append('file', f);
            try {
                const r = await fetch(`/tasks/${taskId}/attachments`, {
                    method: 'POST', body: fd,
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status}`);
                const att = await r.json();
                attachments = [...attachments, att];
                renderAttachments(listEl, attachments, onChange);
                onChange(attachments);
            } catch (err) {
                alert('Could not upload: ' + err.message);
            } finally {
                fresh.value = '';
            }
        });
    }

    // ---- Pending attachments (add modal — buffered until task created) ----

    function bindPendingAttachments(form) {
        if (form.dataset.pendingAttBound === '1') return;
        form.dataset.pendingAttBound = '1';
        const listEl = form.querySelector('[data-pending-attachments-list]');
        const fileInput = form.querySelector('[data-pending-attachments-input]');
        if (!listEl || !fileInput) return;
        form._pendingFiles = form._pendingFiles || [];
        function rerender() {
            listEl.innerHTML = '';
            form._pendingFiles.forEach((f, idx) => {
                const li = document.createElement('li');
                li.className = 'attachment-row';
                const name = document.createElement('span');
                name.className = 'attachment-name';
                name.textContent = f.name;
                li.appendChild(name);
                const rm = document.createElement('button');
                rm.type = 'button';
                rm.className = 'attachment-remove';
                rm.textContent = '×';
                rm.addEventListener('click', () => {
                    form._pendingFiles.splice(idx, 1);
                    rerender();
                });
                li.appendChild(rm);
                listEl.appendChild(li);
            });
        }
        fileInput.addEventListener('change', () => {
            for (const f of fileInput.files || []) {
                form._pendingFiles.push(f);
            }
            fileInput.value = '';
            rerender();
        });
    }

    // ---- Edit button → open edit modal ----

    function bindEditButton(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async () => {
            const row = btn.closest('.todo-row');
            const modal = document.getElementById('edit-task-modal');
            if (!row || !modal) return;
            const form = modal.querySelector('form[data-edit-task]');
            if (!form) return;
            const taskId = row.dataset.id || '';
            form.querySelector('input[name="task_id"]').value = taskId;
            form.querySelector('input[name="title"]').value = row.dataset.title || '';
            const dueAt = row.dataset.dueAt || '';
            const startsAt = row.dataset.startsAt || '';
            form.querySelector('input[name="due_at"]').value = dueAt;
            const startsInput = form.querySelector('input[name="starts_at"]');
            if (startsInput) startsInput.value = startsAt;
            // All-day checkbox — pre-check if the task was saved that way.
            // Dispatch change so the bound handler clears + disables the
            // Starts-on field when this opens for an all-day task.
            const allDayCheckbox = form.querySelector('[data-task-all-day]');
            const origAllDay = row.dataset.isAllDay === '1';
            if (allDayCheckbox) {
                allDayCheckbox.checked = origAllDay;
                form.dataset.origIsAllDay = origAllDay ? '1' : '';
                allDayCheckbox.dispatchEvent(new Event('change'));
            }
            // Repeat dropdown — pre-select stored RRULE (or "Doesn't repeat").
            const rruleSelect = form.querySelector('[data-task-rrule]');
            const origRrule = row.dataset.rrule || '';
            if (rruleSelect) {
                rruleSelect.value = origRrule;
                form.dataset.origRrule = origRrule;
                // Sync end-date visibility against the freshly-set value.
                rruleSelect.dispatchEvent(new Event('change'));
            }
            // Alerts + attachments + rrule_until come from the details
            // endpoint (kept off every row's dataset to keep DOM size sane).
            setAlerts(form, []);
            const untilInput = form.querySelector('input[name="rrule_until"]');
            if (untilInput) untilInput.value = '';
            const attachWrap = form.querySelector('[data-task-attachments-edit]');
            if (attachWrap) attachWrap.hidden = true;
            try {
                const r = await fetch(`/tasks/${taskId}/details.json`, {
                    headers: { 'Accept': 'application/json' },
                });
                if (r.ok) {
                    const d = await r.json();
                    setAlerts(form, d.alerts || []);
                    form.dataset.origAlerts = (d.alerts || []).join(',');
                    if (untilInput) untilInput.value = d.rrule_until || '';
                    bindEditAttachments(form, taskId, d.attachments || []);
                }
            } catch (_) { /* network blip; user can still save */ }
            // Notes textarea: populate from row's data-notes (comes through
            // as the actual text via Jinja's HTML escaping). Stored in
            // dataset.origNotes so we can detect a change at submit time.
            const notesField = form.querySelector('textarea[name="notes"]');
            if (notesField) {
                const notes = row.dataset.notes || '';
                notesField.value = notes;
                form.dataset.origNotes = notes;
            }
            // Class dropdown: pre-select the row's current class (or "0"
            // for Personal tasks). Tracked in origClassId so we can detect
            // a class move on submit.
            const classSelect = form.querySelector('[data-edit-task-class]');
            if (classSelect) {
                // Personal tasks have data-class-id="0" (PERSONAL_BUCKET sentinel).
                const cid = row.dataset.classId && row.dataset.classId !== '0'
                    ? row.dataset.classId : '0';
                classSelect.value = cid;
                form.dataset.origClassId = cid;
            }
            // Track originals so we can decide whether to patch in place
            // or soft-refresh (date / range / tag changes need server-side
            // re-render to land in the right calendar cell / list section).
            form.dataset.origDueAt = dueAt;
            form.dataset.origStartsAt = startsAt;
            const tagSelect = form.querySelector('[data-add-task-tag]');
            if (tagSelect) {
                tagSelect.value = row.dataset.tagId || '';
                form.dataset.origTagId = row.dataset.tagId || '';
                const newForm = form.querySelector('[data-new-tag-form]');
                if (newForm) newForm.hidden = true;
            }
            // Open modal (modal.js owns the open behavior, but we trigger directly here).
            modal.hidden = false;
            document.body.classList.add('modal-open');
            const titleInput = form.querySelector('input[name="title"]');
            if (titleInput) { titleInput.focus(); titleInput.select(); }
        });
    }

    function bindEditTaskForm(form) {
        if (form.dataset.bound === '1') return;
        form.dataset.bound = '1';
        bindTagPicker(form);
        bindRruleVisibility(form);
        bindAllDayBehavior(form);
        bindAlertsAdder(form);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = form.querySelector('input[name="task_id"]').value;
            const title = (form.querySelector('input[name="title"]').value || '').trim();
            const due = form.querySelector('input[name="due_at"]').value;
            const starts = form.querySelector('input[name="starts_at"]').value || '';
            if (!id || !title) return;
            if (starts && due && starts > due) {
                alert('Cannot save event, the start date must be before the end date');
                return;
            }
            let tagId;
            try {
                tagId = await resolveTagId(form);
            } catch (_) { return; }
            const origTagId = form.dataset.origTagId || '';
            const origDueAt = form.dataset.origDueAt || '';
            const origStartsAt = form.dataset.origStartsAt || '';
            const origNotes = form.dataset.origNotes || '';
            const origClassId = form.dataset.origClassId || '0';
            const origRrule = form.dataset.origRrule || '';
            const origAllDay = form.dataset.origIsAllDay || '';
            const notesField = form.querySelector('textarea[name="notes"]');
            const classSelect = form.querySelector('[data-edit-task-class]');
            const rruleSelect = form.querySelector('[data-task-rrule]');
            const allDayCheckbox = form.querySelector('[data-task-all-day]');
            const newNotes = notesField ? notesField.value : '';
            const newClassId = classSelect ? classSelect.value : origClassId;
            const newRrule = rruleSelect ? rruleSelect.value : '';
            const newAllDay = allDayCheckbox && allDayCheckbox.checked ? '1' : '';
            const tagChanged = tagId !== origTagId;
            const dateChanged = (due || '') !== origDueAt
                || (starts || '') !== origStartsAt;
            const notesChanged = newNotes !== origNotes;
            // Class move means the row jumps to a different bucket on
            // home/today/week — softRefresh re-renders it in the right place.
            const classChanged = newClassId !== origClassId;
            // Recurrence + all-day BOTH change which dates a task renders
            // on (rrule expands across the window; all-day swaps the time
            // for an "All day" cell). Treat them like dateChanged.
            const rruleChanged = newRrule !== origRrule;
            const allDayChanged = newAllDay !== origAllDay;
            const fd = new FormData();
            fd.append('title', title);
            if (due) fd.append('due_at', due);
            if (starts) fd.append('starts_at', starts);
            // Always send tag_id (form value). '' means clear.
            fd.append('tag_id', tagId);
            // Notes: send the current value so server can update or clear.
            if (notesField) fd.append('notes', notesField.value || '');
            // class_id: '' (or '0' from the dropdown) means Personal.
            if (classSelect) fd.append('class_id', newClassId);
            // Repeat: always send (even '') so a clear takes effect server-side.
            if (rruleSelect) fd.append('rrule', rruleSelect.value || '');
            // Alerts: send the joined chip list (could be empty = no alerts).
            const alertsHidden = form.querySelector('[data-alerts-value]');
            if (alertsHidden) fd.append('alerts', alertsHidden.value || '');
            // All-day: always send (even unchecked) so the field can clear.
            fd.append('is_all_day', allDayCheckbox && allDayCheckbox.checked ? '1' : '');
            // Recurrence end-date: always send so a clear takes effect.
            const untilLabel = form.querySelector('[data-task-rrule-until]');
            const untilInput = form.querySelector('input[name="rrule_until"]');
            const untilValue = (untilInput && !untilLabel?.hidden) ? (untilInput.value || '') : '';
            fd.append('rrule_until', untilValue);
            try {
                const r = await fetch(`/tasks/${id}/edit`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: fd,
                });
                if (!r.ok) {
                    let detail = `${r.status} ${r.statusText}`;
                    try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
                    throw new Error(detail);
                }
                const modal = document.getElementById('edit-task-modal');
                if (modal) {
                    modal.hidden = true;
                    document.body.classList.remove('modal-open');
                }
                // Date change moves the row between sections (today vs
                // overdue) or between calendar cells; tag swap changes
                // colors and pill text. softRefresh swaps the affected
                // sections without the full-reload flash.
                if (tagChanged || dateChanged || notesChanged || classChanged
                    || rruleChanged || allDayChanged) {
                    await softRefresh();
                    return;
                }
                // Title-only edit: patch every copy in place so the
                // user sees the change instantly across today list,
                // day modal, and calendar cells.
                forEachScope((root) => {
                    root.querySelectorAll(
                        `.todo-row[data-kind="task"][data-id="${id}"]`
                    ).forEach((row) => {
                        row.dataset.title = title;
                        row.dataset.dueAt = due || '';
                        row.dataset.startsAt = starts || '';
                        const titleEl = row.querySelector('.todo-title');
                        if (titleEl) titleEl.textContent = title;
                    });
                    root.querySelectorAll(
                        `.day-cell-item[data-kind="task"][data-id="${id}"]`
                    ).forEach((cell) => {
                        const titleEl = cell.querySelector('.day-cell-title');
                        if (titleEl) titleEl.textContent = title;
                    });
                });
            } catch (err) {
                // Offline: queue the full edit + patch the row optimistically.
                // (Reminders/alerts are separate rows, not synced offline yet.)
                if (window.CompassSync && CompassSync.isOffline(err)) {
                    await CompassSync.queueTaskUpsert({
                        id: Number(id),
                        title,
                        due_at: due || null,
                        starts_at: starts || null,
                        tag_id: tagId ? Number(tagId) : null,
                        notes: notesField ? (notesField.value || null) : null,
                        class_id: newClassId || null,
                        rrule: (rruleSelect && rruleSelect.value) || '',
                        rrule_until: untilValue || null,
                        is_all_day: !!(allDayCheckbox && allDayCheckbox.checked),
                    });
                    const modal = document.getElementById('edit-task-modal');
                    if (modal) {
                        modal.hidden = true;
                        document.body.classList.remove('modal-open');
                    }
                    forEachScope((root) => {
                        root.querySelectorAll(
                            `.todo-row[data-kind="task"][data-id="${id}"]`
                        ).forEach((row) => {
                            row.dataset.title = title;
                            row.dataset.dueAt = due || '';
                            row.dataset.startsAt = starts || '';
                            const titleEl = row.querySelector('.todo-title');
                            if (titleEl) titleEl.textContent = title;
                        });
                    });
                    return;
                }
                alert('Could not save: ' + err.message);
            }
        });
    }

    // ---- Drag to reorder + move across classes ----
    //
    // Drag is now bound on the [data-class-block-list] container instead of
    // each .todo-list-draggable. That lets a row drag from one class block
    // into another (cross-class move) and persist the class_id change.
    // Events can only reorder within their source list — CalendarEvent's
    // class_id isn't nullable, so we don't let them migrate.

    function bindDrag(container) {
        if (container.dataset.dragBound === '1') return;
        container.dataset.dragBound = '1';
        let dragRow = null;
        let dragKind = null;
        let sourceList = null;
        let sourceClassKey = null;
        let pointerStart = null;
        let isDragging = false;
        const MOVE_THRESHOLD = 5;

        const allLists = () =>
            Array.from(container.querySelectorAll('.todo-list-draggable'));

        function classKeyOfList(list) {
            const block = list.closest('.class-block');
            return block ? (block.dataset.bucketKey || null) : null;
        }

        function applyFlipReorder(targetList, insertBeforeRow) {
            if (!dragRow || !targetList) return;
            const currentParent = dragRow.parentNode;
            const currentNext = dragRow.nextSibling;
            // Skip if nothing would change
            if (currentParent === targetList) {
                if (insertBeforeRow === dragRow) return;
                if (insertBeforeRow && insertBeforeRow === currentNext) return;
                if (!insertBeforeRow && currentNext === null) return;
            }
            const all = Array.from(container.querySelectorAll('.todo-row'));
            const firstRects = new Map();
            all.forEach((c) => firstRects.set(c, c.getBoundingClientRect()));
            if (insertBeforeRow) targetList.insertBefore(dragRow, insertBeforeRow);
            else targetList.appendChild(dragRow);
            all.forEach((c) => {
                const first = firstRects.get(c);
                const last = c.getBoundingClientRect();
                const dx = first.left - last.left;
                const dy = first.top - last.top;
                if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
                c.style.transition = 'none';
                c.style.transform = `translate(${dx}px, ${dy}px)`;
                void c.offsetHeight;
                c.style.transition = 'transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)';
                c.style.transform = '';
            });
        }

        function findDropTarget(clientX, clientY) {
            // Events stay in their source list — block cross-class drops.
            const candidates = (dragKind === 'event' && sourceList)
                ? [sourceList] : allLists();
            // Prefer the list whose bounding box the cursor is over; fall
            // back to the list whose center is closest.
            let best = null;
            let bestDist = Infinity;
            for (const list of candidates) {
                const rect = list.getBoundingClientRect();
                const insideX = clientX >= rect.left && clientX <= rect.right;
                let dist;
                if (insideX) {
                    dist = clientY < rect.top ? (rect.top - clientY)
                         : (clientY > rect.bottom ? (clientY - rect.bottom) : 0);
                } else {
                    const cx = (rect.left + rect.right) / 2;
                    const cy = (rect.top + rect.bottom) / 2;
                    // Heavily penalise lists the cursor isn't over horizontally
                    dist = Math.hypot(clientX - cx, clientY - cy) + 9999;
                }
                if (dist < bestDist) { bestDist = dist; best = list; }
            }
            if (!best) return null;
            const rows = Array.from(best.querySelectorAll('.todo-row:not(.dragging)'));
            for (const target of rows) {
                const rect = target.getBoundingClientRect();
                if (clientY < rect.top + rect.height / 2) {
                    return { list: best, before: target };
                }
            }
            return { list: best, before: null };
        }

        function moveTowards(clientX, clientY) {
            if (!dragRow) return;
            const drop = findDropTarget(clientX, clientY);
            if (!drop) return;
            applyFlipReorder(drop.list, drop.before);
        }

        // Persist accepts the captured state explicitly so a stray
        // pointermove fired between pointerup and the awaited fetch can't
        // mutate `dragRow` mid-flight (which was the cause of "row jumps
        // to a random position after I drop it").
        async function persistDrop(droppedRow, droppedSourceList, droppedSourceClassKey, droppedKind) {
            if (!droppedRow) return;
            const newList = droppedRow.parentNode;
            const newClassKey = classKeyOfList(newList);
            const taskId = droppedRow.dataset.id;
            // The week tab's day modal shows ONE day's slice of items,
            // including any multi-day tasks that span this day. Persisting
            // a global `position` from here would re-sort that task on
            // every other day too, which the user explicitly doesn't want.
            // Inside the day modal, route the reorder to /tasks/reorder-day
            // so it stores a DayItemPosition keyed on this day only —
            // other days keep the global Task/Event.position.
            // Cross-class moves still persist (changing class_id is a real
            // edit, not an order change).
            const inDayModal = !!container.closest('#day-modal');
            const dayDate = container.dataset.dayDate || '';

            // Step 1: cross-class move (tasks only). PATCH class_id only —
            // edit_task does partial updates so we don't have to round-trip
            // the rest of the task's fields. bucketKey "0" represents the
            // synthetic Personal bucket; server treats class_id="" as NULL.
            if (droppedKind === 'task' && newClassKey && newClassKey !== droppedSourceClassKey) {
                const fd = new FormData();
                fd.append('class_id', newClassKey === '0' ? '' : newClassKey);
                try {
                    const r = await fetch(`/tasks/${taskId}/edit`, {
                        method: 'POST', body: fd,
                        headers: { 'Accept': 'application/json' },
                    });
                    if (r.ok) {
                        droppedRow.dataset.classId = newClassKey;
                    } else if (droppedSourceList) {
                        droppedSourceList.appendChild(droppedRow);
                    }
                } catch (err) {
                    console.error('cross-class move failed:', err);
                    if (droppedSourceList) droppedSourceList.appendChild(droppedRow);
                }
            }

            // Step 2: persist row order. In the day modal, send to the
            // per-day endpoint so only that day's order changes. Elsewhere
            // (home/today, class page), use the global reorder endpoint.
            const items = Array.from(container.querySelectorAll('.todo-row'))
                .map((el) => {
                    const kind = el.dataset.kind;
                    const id = parseInt(el.dataset.id, 10);
                    if ((kind !== 'task' && kind !== 'event') || Number.isNaN(id)) return null;
                    return { kind, id };
                })
                .filter(Boolean);
            if (items.length > 0) {
                const url = inDayModal ? '/tasks/reorder-day' : '/tasks/reorder';
                const body = inDayModal
                    ? JSON.stringify({ day: dayDate, items })
                    : JSON.stringify({ items });
                try {
                    await fetch(url, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                        body,
                    });
                } catch (err) {
                    console.error('reorder failed:', err);
                }
            }
            // Week page: re-pull the calendar so day-cells AND each day's
            // <template data-day-modal-content> reflect the new state. The
            // open day modal's body was cloned from the template at click
            // time and lives under #day-modal (hoisted to <body>), NOT
            // under .month-grid — so replacing .month-grid leaves the
            // visible drag in place while updating the calendar pills and
            // priming the next reopen with the persisted order.
            await refreshMonthGridOnly();
        }

        function onPointerMove(e) {
            if (!dragRow || !pointerStart) return;
            const dx = e.clientX - pointerStart.x;
            const dy = e.clientY - pointerStart.y;
            if (!isDragging && Math.hypot(dx, dy) < MOVE_THRESHOLD) return;
            if (!isDragging) {
                isDragging = true;
                dragRow.classList.add('dragging');
                document.body.classList.add('cards-dragging');
            }
            moveTowards(e.clientX, e.clientY);
            e.preventDefault();
        }
        function onPointerUp() {
            if (!dragRow) return;
            const wasDragging = isDragging;
            const droppedRow = dragRow;
            const droppedKind = dragKind;
            const droppedSourceList = sourceList;
            const droppedSourceClassKey = sourceClassKey;
            // Clear state SYNCHRONOUSLY so any stale pointermove fired
            // between this pointerup and the awaited fetch in persistDrop
            // can't re-trigger applyFlipReorder and snap the row away from
            // where the user dropped it.
            dragRow = null;
            dragKind = null;
            sourceList = null;
            sourceClassKey = null;
            pointerStart = null;
            isDragging = false;
            droppedRow.classList.remove('dragging');
            document.body.classList.remove('cards-dragging');
            if (wasDragging) {
                persistDrop(droppedRow, droppedSourceList, droppedSourceClassKey, droppedKind);
            }
        }

        container.querySelectorAll('.todo-drag-handle').forEach((handle) => {
            if (handle.dataset.dragHandleBound === '1') return;
            handle.dataset.dragHandleBound = '1';
            handle.addEventListener('pointerdown', (e) => {
                if (e.button !== undefined && e.button !== 0) return;
                const row = handle.closest('.todo-row');
                if (!row || !container.contains(row)) return;
                dragRow = row;
                dragKind = row.dataset.kind || 'task';
                sourceList = row.closest('.todo-list-draggable');
                sourceClassKey = sourceList ? classKeyOfList(sourceList) : null;
                pointerStart = { x: e.clientX, y: e.clientY };
                isDragging = false;
                e.preventDefault();
            });
        });
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', onPointerUp);
        document.addEventListener('pointercancel', onPointerUp);
    }

    // ---- Add task (with class picker) ----

    async function resolveTagId(form) {
        // Returns a tag id string ('' if no tag, or a numeric id). May
        // create a new tag on the server if the user picked '+ New tag'.
        const tagSelect = form.querySelector('[data-add-task-tag]');
        if (!tagSelect) return '';
        if (tagSelect.value !== '__new__') return tagSelect.value;
        const newForm = form.querySelector('[data-new-tag-form]');
        if (!newForm) return '';
        const nameInput = newForm.querySelector('[data-new-tag-name]');
        const colorInput = newForm.querySelector('[data-new-tag-color]');
        const name = (nameInput?.value || '').trim();
        const color = colorInput?.value || '';
        if (!name) { alert('Tag name required'); throw new Error('tag name required'); }
        const fd = new FormData();
        fd.append('name', name);
        fd.append('color', color);
        const r = await fetch('/tags', {
            method: 'POST', body: fd,
            headers: { 'Accept': 'application/json' },
        });
        if (!r.ok) {
            let detail = `${r.status} ${r.statusText}`;
            try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
            throw new Error(detail);
        }
        const tag = await r.json();
        return String(tag.id);
    }

    function bindTagPicker(form) {
        if (form.dataset.tagBound === '1') return;
        form.dataset.tagBound = '1';
        const select = form.querySelector('[data-add-task-tag]');
        const newForm = form.querySelector('[data-new-tag-form]');
        if (!select || !newForm) return;
        const cancelBtn = newForm.querySelector('[data-cancel-new-tag]');
        select.addEventListener('change', () => {
            if (select.value === '__new__') {
                newForm.hidden = false;
                const nameInput = newForm.querySelector('[data-new-tag-name]');
                if (nameInput) nameInput.focus();
            } else {
                newForm.hidden = true;
            }
        });
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                select.value = '';
                newForm.hidden = true;
            });
        }
    }

    function bindAddTaskForm(form) {
        if (form.dataset.bound === '1') return;
        form.dataset.bound = '1';
        bindTagPicker(form);
        bindRruleVisibility(form);
        bindAllDayBehavior(form);
        bindAlertsAdder(form);
        bindPendingAttachments(form);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const titleInput = form.querySelector('input[name="title"]');
            const dueInput = form.querySelector('input[name="due_at"]');
            const startsInput = form.querySelector('input[name="starts_at"]');
            const classSelect = form.querySelector('[data-add-task-class]');
            const notesField = form.querySelector('textarea[name="notes"]');
            const title = (titleInput.value || '').trim();
            if (!title) return;
            const startsVal = startsInput ? (startsInput.value || '') : '';
            const dueVal = dueInput ? (dueInput.value || '') : '';
            if (startsVal && dueVal && startsVal > dueVal) {
                alert('Cannot save event, the start date must be before the end date');
                return;
            }
            // Empty class value = "Personal" — POST to /tasks (no class).
            // Numeric value = real class — POST to /classes/{id}/tasks.
            const classId = classSelect ? classSelect.value : '';
            const url = classId ? `/classes/${classId}/tasks` : '/tasks';
            let tagId;
            try {
                tagId = await resolveTagId(form);
            } catch (_) { return; }  // user-facing alert already shown
            const fd = new FormData();
            fd.append('title', title);
            if (dueInput && dueInput.value) fd.append('due_at', dueInput.value);
            if (startsInput && startsInput.value) {
                fd.append('starts_at', startsInput.value);
            }
            if (tagId) fd.append('tag_id', tagId);
            // All-day flag — checkbox value 1 when checked, omitted otherwise.
            const allDayCheckbox = form.querySelector('[data-task-all-day]');
            if (allDayCheckbox && allDayCheckbox.checked) {
                fd.append('is_all_day', '1');
            }
            // Recurrence end date — only sent when Repeat is set AND the
            // user picked a date. Server clears it whenever rrule is empty.
            const untilLabel = form.querySelector('[data-task-rrule-until]');
            const untilInput = form.querySelector('input[name="rrule_until"]');
            if (untilInput && untilInput.value && untilLabel && !untilLabel.hidden) {
                fd.append('rrule_until', untilInput.value);
            }
            if (notesField && notesField.value.trim()) fd.append('notes', notesField.value);
            // Repeat (allow-listed server-side, see _normalize_rrule).
            const rruleSelect = form.querySelector('[data-task-rrule]');
            if (rruleSelect && rruleSelect.value) fd.append('rrule', rruleSelect.value);
            // Alerts: only send if the user explicitly added/removed any. An
            // omitted field tells the server to apply smart defaults by tag.
            const alertsHidden = form.querySelector('[data-alerts-value]');
            const alertsTouched = form.dataset.alertsTouched === '1';
            if (alertsHidden && alertsTouched) fd.append('alerts', alertsHidden.value || '');
            // Capture the date values now (before any cleanup) so the
            // future-date heads-up alert below can reference them, and
            // critically: any error after this point won't have already
            // wiped the form fields the user typed into.
            const dueValueAtSubmit = dueInput ? dueInput.value : '';
            const startsValueAtSubmit = startsInput ? startsInput.value : '';
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: fd,
                });
                if (!r.ok) {
                    let detail = `${r.status} ${r.statusText}`;
                    try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
                    throw new Error(detail);
                }
                const created = await r.json().catch(() => null);
                // Upload any buffered attachments now that we have a task id.
                if (created && created.id && form._pendingFiles && form._pendingFiles.length) {
                    for (const f of form._pendingFiles) {
                        const afd = new FormData();
                        afd.append('file', f);
                        try {
                            await fetch(`/tasks/${created.id}/attachments`, {
                                method: 'POST', body: afd,
                                headers: { 'Accept': 'application/json' },
                            });
                        } catch (_) { /* keep going, others may still upload */ }
                    }
                    form._pendingFiles = [];
                }
                // Close the modal first — softRefresh will replace the
                // entire #add-task-modal with a fresh empty one rendered by
                // the server, so we don't need to manually clear fields.
                // Doing it this order means any error in the cleanup below
                // can't leave the modal stuck open with a half-cleared form.
                const addModal = form.closest('.modal-overlay');
                if (addModal) {
                    addModal.hidden = true;
                    document.body.classList.remove('modal-open');
                }
                // The today list only renders tasks due today, overdue, or
                // with no date. A task scheduled for a future date is saved
                // correctly but filtered out — without feedback the user
                // thinks the add silently failed. (For ranged tasks the
                // start date is what determines if it shows today.)
                const checkStart = startsValueAtSubmit || dueValueAtSubmit;
                if (checkStart) {
                    const t = new Date();
                    const todayStr = t.getFullYear() + '-' +
                        String(t.getMonth() + 1).padStart(2, '0') + '-' +
                        String(t.getDate()).padStart(2, '0');
                    if (checkStart.slice(0, 10) > todayStr) {
                        alert(`Task added — won't appear on the Today list until ${checkStart.slice(0, 10)}. Find it on the class page or Week view.`);
                    }
                }
                await softRefresh();
            } catch (err) {
                // Offline: queue the new task so it's not lost — it syncs and
                // appears on reconnect. (No optimistic row on the web yet, so
                // we tell the user; toggle/delete of existing rows DO show
                // offline.)
                if (window.CompassSync && CompassSync.isOffline(err)) {
                    await CompassSync.queueTaskUpsert({
                        title,
                        due_at: (dueInput && dueInput.value) || null,
                        starts_at: (startsInput && startsInput.value) || null,
                        is_all_day: !!(allDayCheckbox && allDayCheckbox.checked),
                        rrule: (rruleSelect && rruleSelect.value) || '',
                        tag_id: tagId ? Number(tagId) : null,
                        class_id: classId || null,
                        notes: (notesField && notesField.value.trim()) || null,
                    });
                    const addModal = form.closest('.modal-overlay');
                    if (addModal) {
                        addModal.hidden = true;
                        document.body.classList.remove('modal-open');
                    }
                    alert("Saved offline — it'll sync and appear when you're back online.");
                    return;
                }
                console.error('add-task failed:', err);
                alert('Could not add task: ' + err.message);
                // Form fields stay populated so the user can fix and resubmit.
            }
        });
    }

    // ---- Targeted month-grid refresh ----

    // Used after a drag reorder on the week page. Refreshes the calendar
    // (day cells + per-day <template> content) without touching #day-modal,
    // so an open modal stays open and the user sees their drag persist.
    async function refreshMonthGridOnly() {
        try {
            const url = window.location.pathname + window.location.search;
            const r = await fetch(url, {
                headers: { 'Accept': 'text/html' },
                cache: 'no-cache',
                credentials: 'same-origin',
            });
            if (!r.ok) return;
            const html = await r.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const fresh = doc.querySelector('.month-grid');
            const stale = document.querySelector('.month-grid');
            if (fresh && stale) {
                stale.replaceWith(fresh);
                bindAll();
                if (typeof window.rebindDayCells === 'function') window.rebindDayCells();
            }
        } catch (err) {
            console.error('month-grid refresh failed:', err);
        }
    }

    // ---- Soft refresh ----

    // Re-fetch the current page and swap just the data-driven sections in
    // place. Avoids the full-reload flash and preserves scroll position.
    // Used after operations that may move rows between sections (add task,
    // edit task with date change) where patching one row in place isn't
    // enough.
    async function softRefresh() {
        // Capture which rows are currently expanded so we can re-open them
        // after the re-render. Without this the drawer collapses on every
        // edit-save and the user thinks something didn't take effect.
        const expandedKeys = new Set();
        document.querySelectorAll('.todo-row.expanded').forEach((row) => {
            expandedKeys.add(`${row.dataset.kind}:${row.dataset.id}`);
        });

        try {
            const url = window.location.pathname + window.location.search;
            const r = await fetch(url, {
                headers: { 'Accept': 'text/html' },
                cache: 'no-cache',
                credentials: 'same-origin',
            });
            if (!r.ok) return false;
            const html = await r.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');
            // Selectors covering: today list partial, week calendar grid,
            // and the modal markup that carries up-to-date tag option lists.
            // #manage-tags-modal is intentionally omitted so callers can keep
            // it open across patches.
            const selectors = [
                '.today-list-block',
                '.month-grid',
                '#add-task-modal',
                '#edit-task-modal',
                '#day-modal',
            ];
            let replaced = false;
            for (const sel of selectors) {
                const fresh = doc.querySelector(sel);
                const stale = document.querySelector(sel);
                if (fresh && stale) { stale.replaceWith(fresh); replaced = true; }
            }
            if (replaced) {
                bindAll();
                if (typeof window.rebindDayCells === 'function') window.rebindDayCells();
                // Modals were hoisted to <body> at first load; the fresh
                // copies fetched here are nested inside the today partial,
                // so move them back to body so the X stays reachable.
                if (typeof window.hoistModalsToBody === 'function') {
                    window.hoistModalsToBody();
                }
                // Re-expand rows that were open before the re-render so
                // edits don't visually destroy the user's open drawer.
                expandedKeys.forEach((key) => {
                    const [kind, id] = key.split(':');
                    document.querySelectorAll(
                        `.todo-row[data-kind="${kind}"][data-id="${id}"]`
                    ).forEach((row) => {
                        const drawer = row.querySelector('.todo-drawer');
                        const main = row.querySelector('[data-row-toggle]');
                        if (drawer) drawer.hidden = false;
                        row.classList.add('expanded');
                        if (main) main.setAttribute('aria-expanded', 'true');
                    });
                });
            }
            return replaced;
        } catch (err) {
            console.error('softRefresh failed:', err);
            return false;
        }
    }

    // ---- Manage tags modal ----

    function bindManageTags() {
        const modal = document.getElementById('manage-tags-modal');
        if (!modal) return;
        const list = modal.querySelector('[data-tag-manage-list]');

        async function refresh() {
            list.innerHTML = '';
            const r = await fetch('/tags.json', { headers: { 'Accept': 'application/json' } });
            if (!r.ok) { list.innerHTML = '<li class="empty">Could not load tags.</li>'; return; }
            const tags = await r.json();
            if (tags.length === 0) {
                list.innerHTML = '<li class="empty">No tags yet.</li>';
                return;
            }
            tags.forEach((tag) => {
                const isSystem = !!tag.is_system;
                const li = document.createElement('li');
                li.className = 'tag-manage-row' + (isSystem ? ' is-system' : '');
                li.innerHTML = `
                    <span class="tag-swatch" style="background: ${tag.color}"></span>
                    <span class="tag-manage-name"></span>
                    ${isSystem ? '<span class="tag-system-badge">system</span>' : ''}
                    <span class="tag-manage-actions"></span>
                `;
                li.querySelector('.tag-manage-name').textContent = tag.name;
                const actions = li.querySelector('.tag-manage-actions');
                const editBtn = document.createElement('button');
                editBtn.type = 'button';
                editBtn.className = 'link-btn';
                editBtn.textContent = 'Edit';
                editBtn.addEventListener('click', () => editInline(li, tag));
                actions.appendChild(editBtn);
                if (!isSystem) {
                    const delBtn = document.createElement('button');
                    delBtn.type = 'button';
                    delBtn.className = 'link-btn danger';
                    delBtn.textContent = 'Delete';
                    delBtn.addEventListener('click', () => deleteTag(tag));
                    actions.appendChild(document.createTextNode(' · '));
                    actions.appendChild(delBtn);
                }
                list.appendChild(li);
            });
        }

        function editInline(li, tag) {
            li.innerHTML = `
                <input type="color" data-edit-color>
                <input type="text" value="" data-edit-name>
                <button type="button" class="link-btn" data-edit-save>Save</button>
                <button type="button" class="link-btn" data-edit-cancel>Cancel</button>
            `;
            li.querySelector('[data-edit-name]').value = tag.name;
            // HTML5 color inputs only accept lowercase hex; uppercase
            // silently falls back to default. Lowercase defensively so
            // the picker pre-fills with the actual saved color.
            const colorInput = li.querySelector('[data-edit-color]');
            if (colorInput) colorInput.value = (tag.color || '#000000').toLowerCase();
            li.querySelector('[data-edit-cancel]').addEventListener('click', refresh);
            li.querySelector('[data-edit-save]').addEventListener('click', async () => {
                const newName = (li.querySelector('[data-edit-name]').value || '').trim();
                const newColor = li.querySelector('[data-edit-color]').value;
                if (!newName) return;
                const fd = new FormData();
                fd.append('name', newName);
                fd.append('color', newColor);
                const r = await fetch(`/tags/${tag.id}/edit`, {
                    method: 'POST', body: fd,
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) {
                    let detail = `${r.status} ${r.statusText}`;
                    try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
                    alert('Could not save: ' + detail);
                    return;
                }
                const tagId = String(tag.id);
                applyTagEditToTree(document, tagId, newName, newColor);
                document.querySelectorAll('template[data-day-modal-content]').forEach((tpl) => {
                    applyTagEditToTree(tpl.content, tagId, newName, newColor);
                });
                refresh();
            });
        }

        function applyTagDeletionToTree(root, tagId) {
            // Tasks are the only thing that can carry a user tag (events
            // use sub_kind colors, and system tags can't be deleted). So
            // clearing the tag on a task row means dropping the color and
            // pill entirely — no fallback.
            root.querySelectorAll(`.todo-row[data-tag-id="${tagId}"]`).forEach((row) => {
                row.dataset.tagId = '';
                row.classList.remove('has-tag');
                row.style.removeProperty('--tag-color');
                const pill = row.querySelector('.todo-tag');
                if (pill) pill.remove();
            });
            root.querySelectorAll(`.day-cell-item[data-tag-id="${tagId}"]`).forEach((cell) => {
                cell.dataset.tagId = '';
                cell.classList.remove('has-tag');
                cell.style.removeProperty('--tag-color');
            });
            root.querySelectorAll(`[data-add-task-tag] option[value="${tagId}"]`).forEach((opt) => opt.remove());
        }

        function applyTagEditToTree(root, tagId, newName, newColor) {
            // Tasks: matched via data-tag-id; recolor row and pill, rename pill.
            root.querySelectorAll(`.todo-row[data-tag-id="${tagId}"]`).forEach((row) => {
                row.style.setProperty('--tag-color', newColor);
                const pill = row.querySelector('.todo-tag');
                if (pill) {
                    pill.textContent = newName;
                    pill.style.setProperty('--tag-color', newColor);
                }
            });
            root.querySelectorAll(`.day-cell-item[data-tag-id="${tagId}"]`).forEach((cell) => {
                cell.style.setProperty('--tag-color', newColor);
            });
            // Events: matched via data-sub-kind-id (system tags only). Same
            // patch — recolor row and rename/recolor pill.
            root.querySelectorAll(`.todo-row[data-sub-kind-id="${tagId}"]`).forEach((row) => {
                row.style.setProperty('--tag-color', newColor);
                const pill = row.querySelector('.todo-tag');
                if (pill) {
                    pill.textContent = newName;
                    pill.style.setProperty('--tag-color', newColor);
                }
            });
            root.querySelectorAll(`.day-cell-item[data-sub-kind-id="${tagId}"]`).forEach((cell) => {
                cell.style.setProperty('--tag-color', newColor);
            });
            // Tag option lists in add/edit modals.
            root.querySelectorAll(`[data-add-task-tag] option[value="${tagId}"]`).forEach((opt) => {
                opt.textContent = newName;
                opt.dataset.color = newColor;
            });
        }

        async function deleteTag(tag) {
            if (!confirm(`Delete tag "${tag.name}"? Tasks using it will become untagged.`)) return;
            const r = await fetch(`/tags/${tag.id}/delete`, {
                method: 'POST', headers: { 'Accept': 'application/json' },
            });
            if (!r.ok) {
                let detail = `${r.status} ${r.statusText}`;
                try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
                alert('Could not delete: ' + detail);
                return;
            }
            const tagId = String(tag.id);
            applyTagDeletionToTree(document, tagId);
            // Week view stashes per-day todo-rows inside <template> elements
            // that get cloned when a day cell is clicked — patch those too
            // so the modal opened later doesn't show the dead tag.
            document.querySelectorAll('template[data-day-modal-content]').forEach((tpl) => {
                applyTagDeletionToTree(tpl.content, tagId);
            });
            refresh();
        }

        document.querySelectorAll('[data-open-manage-tags]').forEach((btn) => {
            if (btn.dataset.bound === '1') return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', () => {
                refresh();
                modal.hidden = false;
                document.body.classList.add('modal-open');
            });
        });
    }

    // ---- Default due-date prefill ----

    function _formatLocal(d) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function _smartDefaultStart() {
        // Round CURRENT time UP to the next 30-min mark so events default to
        // a slot that's actually in the future. Mirrors Apple Calendar's
        // "next half hour" default. Returns YYYY-MM-DDTHH:MM.
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
        return _formatLocal(d);
    }

    function _smartDefaultDue(startStr) {
        // Apple-style: due defaults to one hour after start, also on a
        // 30-min boundary. start is YYYY-MM-DDTHH:MM in local time.
        const d = new Date(startStr);
        d.setHours(d.getHours() + 1);
        return _formatLocal(d);
    }

    function _populateSmartDefaults(form) {
        // Make sure starts_at + due_at are never empty when the modal opens.
        // If they were left over from a previous open, refresh them too —
        // a stale 9am from yesterday is more confusing than the user typing
        // their own value.
        const startsInput = form.querySelector('input[name="starts_at"]');
        const dueInput = form.querySelector('input[name="due_at"]');
        if (!startsInput || !dueInput) return;
        const startStr = _smartDefaultStart();
        if (!startsInput.value) startsInput.value = startStr;
        if (!dueInput.value) dueInput.value = _smartDefaultDue(startsInput.value);
    }

    // Delegated: any click that opens #add-task-modal prefills sensible
    // defaults (starts = next half-hour, due = +1 hour) if empty, mirroring
    // Apple's "always have a value here" behavior.
    document.addEventListener('click', (e) => {
        const trig = e.target.closest('[data-open-modal="add-task-modal"]');
        if (!trig) return;
        document.querySelectorAll('#add-task-modal form[data-add-task]').forEach(_populateSmartDefaults);
    });

    // ---- Wire up ----

    // ---- Class-block drag (reorder bucket order on home/today) ----

    function bindClassBlockDrag(list) {
        // Distinct flag from bindDrag — both functions wire onto the
        // same [data-class-block-list] container, so sharing
        // `dragBound` meant whichever ran first claimed the container
        // and the other silently no-op'd. (Task drag won, class-block
        // drag died.)
        if (list.dataset.classDragBound === '1') return;
        list.dataset.classDragBound = '1';
        let dragBlock = null;
        let pointerStart = null;
        let isDragging = false;
        const MOVE_THRESHOLD = 5;

        function applyFlipReorder(insertBeforeBlock) {
            const currentNext = dragBlock.nextSibling;
            if (insertBeforeBlock === dragBlock) return;
            if (insertBeforeBlock && insertBeforeBlock === currentNext) return;
            if (!insertBeforeBlock && currentNext === null) return;

            const all = Array.from(list.querySelectorAll(':scope > .class-block'));
            const firstRects = new Map();
            all.forEach((c) => firstRects.set(c, c.getBoundingClientRect()));
            if (insertBeforeBlock) list.insertBefore(dragBlock, insertBeforeBlock);
            else list.appendChild(dragBlock);
            all.forEach((c) => {
                const first = firstRects.get(c);
                const last = c.getBoundingClientRect();
                const dy = first.top - last.top;
                if (Math.abs(dy) < 1) return;
                c.style.transition = 'none';
                c.style.transform = `translateY(${dy}px)`;
                void c.offsetHeight;
                c.style.transition = 'transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)';
                c.style.transform = '';
            });
        }

        function moveTowards(clientY) {
            if (!dragBlock) return;
            const others = Array.from(
                list.querySelectorAll(':scope > .class-block:not(.dragging)')
            );
            let insertBefore = null;
            for (const target of others) {
                const rect = target.getBoundingClientRect();
                if (clientY < rect.top + rect.height / 2) { insertBefore = target; break; }
            }
            applyFlipReorder(insertBefore);
        }

        async function persistOrder() {
            const order = Array.from(list.querySelectorAll(':scope > .class-block'))
                .map((b) => b.dataset.bucketKey)
                .filter((k) => k != null);
            try {
                await fetch('/classes/reorder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ order }),
                });
            } catch (err) {
                console.error('class reorder failed:', err);
            }
        }

        function onPointerMove(e) {
            if (!dragBlock || !pointerStart) return;
            const dy = e.clientY - pointerStart.y;
            if (!isDragging && Math.abs(dy) < MOVE_THRESHOLD) return;
            if (!isDragging) {
                isDragging = true;
                dragBlock.classList.add('dragging');
                document.body.classList.add('cards-dragging');
            }
            moveTowards(e.clientY);
            e.preventDefault();
        }
        function onPointerUp() {
            if (!dragBlock) return;
            const wasDragging = isDragging;
            dragBlock.classList.remove('dragging');
            document.body.classList.remove('cards-dragging');
            dragBlock = null;
            pointerStart = null;
            isDragging = false;
            if (wasDragging) persistOrder();
        }

        list.querySelectorAll('.class-block-drag').forEach((handle) => {
            handle.addEventListener('pointerdown', (e) => {
                if (e.button !== undefined && e.button !== 0) return;
                const block = handle.closest('.class-block');
                if (!block) return;
                dragBlock = block;
                pointerStart = { x: e.clientX, y: e.clientY };
                isDragging = false;
                e.preventDefault();
            });
        });
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', onPointerUp);
        document.addEventListener('pointercancel', onPointerUp);
    }

    // ---- Row expand ----
    // Click on .todo-row-main toggles the .todo-drawer (notes + edit/delete
    // buttons) below the row. Reduces inline clutter — drawer-collapsed by
    // default. Clicks on the toggle circle, drag handle, or any inner
    // <button> are ignored so they keep their own behavior.

    function bindRowExpand(main) {
        if (main.dataset.bound === '1') return;
        main.dataset.bound = '1';

        function shouldIgnore(target) {
            return Boolean(
                target.closest('.todo-toggle') ||
                target.closest('.todo-drag-handle') ||
                target.closest('button')
            );
        }

        function toggle() {
            const row = main.closest('.todo-row');
            if (!row) return;
            const drawer = row.querySelector('.todo-drawer');
            if (!drawer) return;
            const willOpen = drawer.hidden;
            drawer.hidden = !willOpen;
            main.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            row.classList.toggle('expanded', willOpen);
        }

        main.addEventListener('click', (e) => {
            if (shouldIgnore(e.target)) return;
            toggle();
        });
        main.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (shouldIgnore(e.target)) return;
            e.preventDefault();
            toggle();
        });
    }

    function bindAll() {
        document.querySelectorAll('.todo-toggle').forEach(bindToggle);
        document.querySelectorAll('.todo-del').forEach(bindDelete);
        document.querySelectorAll('.todo-edit').forEach(bindEditButton);
        document.querySelectorAll('[data-row-toggle]').forEach(bindRowExpand);
        document.querySelectorAll('[data-class-block-list]').forEach(bindDrag);
        document.querySelectorAll('[data-class-block-list]').forEach(bindClassBlockDrag);
        document.querySelectorAll('form[data-add-task]').forEach(bindAddTaskForm);
        document.querySelectorAll('form[data-edit-task]').forEach(bindEditTaskForm);
        bindManageTags();
    }
    bindAll();
    window.bindTodoToggles = bindAll;
})();
