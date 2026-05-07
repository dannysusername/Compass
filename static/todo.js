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
                setDoneEverywhere(kind, id, wasDone);
                console.error('toggle failed:', err);
            }
        });
    }

    // ---- Delete ----

    function bindDelete(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async () => {
            const kind = btn.dataset.kind || 'task';
            const label = kind === 'event' ? 'event' : 'task';
            if (!confirm(`Delete this ${label}?`)) return;
            const id = btn.dataset.id;
            const url = kind === 'event'
                ? `/events/${id}/delete`
                : `/tasks/${id}/delete`;
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
                // Remove every copy of this item across the page (today
                // list, week-view day modal, AND the calendar cells) so
                // they stay in sync.
                const sel =
                    `.todo-row[data-kind="${kind}"][data-id="${id}"], ` +
                    `.day-cell-item[data-kind="${kind}"][data-id="${id}"]`;
                document.querySelectorAll(sel).forEach((el) => {
                    el.style.transition = 'opacity 0.15s ease, max-height 0.15s ease';
                    el.style.opacity = '0';
                    el.style.maxHeight = '0';
                    setTimeout(() => el.remove(), 160);
                });
                // Live-DOM rows fade out; template fragments aren't visible
                // so we just yank them — keeps the day-modal honest on
                // re-open.
                document.querySelectorAll('template[data-day-modal-content]').forEach((tpl) => {
                    tpl.content.querySelectorAll(sel).forEach((el) => el.remove());
                });
            } catch (err) {
                alert(`Could not delete ${label}: ` + err.message);
            }
        });
    }

    // ---- Edit button → open edit modal ----

    function bindEditButton(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => {
            const row = btn.closest('.todo-row');
            const modal = document.getElementById('edit-task-modal');
            if (!row || !modal) return;
            const form = modal.querySelector('form[data-edit-task]');
            if (!form) return;
            form.querySelector('input[name="task_id"]').value = row.dataset.id || '';
            form.querySelector('input[name="title"]').value = row.dataset.title || '';
            const dueAt = row.dataset.dueAt || '';
            const startsAt = row.dataset.startsAt || '';
            form.querySelector('input[name="due_at"]').value = dueAt;
            const startsInput = form.querySelector('input[name="starts_at"]');
            const startsLabel = form.querySelector('[data-task-starts]');
            if (startsInput) startsInput.value = startsAt;
            if (startsLabel) startsLabel.hidden = !startsAt;
            syncStartsToggleLabel(form);
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
        bindTaskStartsToggle(form);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = form.querySelector('input[name="task_id"]').value;
            const title = (form.querySelector('input[name="title"]').value || '').trim();
            const due = form.querySelector('input[name="due_at"]').value;
            const startsLabel = form.querySelector('[data-task-starts]');
            const startsRaw = form.querySelector('input[name="starts_at"]').value;
            // Hidden starts label = user opted out of the range, ignore the field's stale value.
            const starts = (startsLabel && startsLabel.hidden) ? '' : startsRaw;
            if (!id || !title) return;
            let tagId;
            try {
                tagId = await resolveTagId(form);
            } catch (_) { return; }
            const origTagId = form.dataset.origTagId || '';
            const origDueAt = form.dataset.origDueAt || '';
            const origStartsAt = form.dataset.origStartsAt || '';
            const origNotes = form.dataset.origNotes || '';
            const origClassId = form.dataset.origClassId || '0';
            const notesField = form.querySelector('textarea[name="notes"]');
            const classSelect = form.querySelector('[data-edit-task-class]');
            const newNotes = notesField ? notesField.value : '';
            const newClassId = classSelect ? classSelect.value : origClassId;
            const tagChanged = tagId !== origTagId;
            const dateChanged = (due || '') !== origDueAt
                || (starts || '') !== origStartsAt;
            const notesChanged = newNotes !== origNotes;
            // Class move means the row jumps to a different bucket on
            // home/today/week — softRefresh re-renders it in the right place.
            const classChanged = newClassId !== origClassId;
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
                if (tagChanged || dateChanged || notesChanged || classChanged) {
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
                alert('Could not save: ' + err.message);
            }
        });
    }

    // ---- Drag to reorder ----

    function bindDrag(list) {
        if (list.dataset.dragBound === '1') return;
        list.dataset.dragBound = '1';
        let dragRow = null;
        let pointerStart = null;
        let isDragging = false;
        const MOVE_THRESHOLD = 5;

        function applyFlipReorder(insertBeforeRow) {
            const currentNext = dragRow.nextSibling;
            if (insertBeforeRow === dragRow) return;
            if (insertBeforeRow && insertBeforeRow === currentNext) return;
            if (!insertBeforeRow && currentNext === null) return;

            const all = Array.from(list.querySelectorAll('.todo-row'));
            const firstRects = new Map();
            all.forEach((c) => firstRects.set(c, c.getBoundingClientRect()));
            if (insertBeforeRow) list.insertBefore(dragRow, insertBeforeRow);
            else list.appendChild(dragRow);
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
            if (!dragRow) return;
            const others = Array.from(list.querySelectorAll('.todo-row:not(.dragging)'));
            let insertBefore = null;
            for (const target of others) {
                const rect = target.getBoundingClientRect();
                if (clientY < rect.top + rect.height / 2) { insertBefore = target; break; }
            }
            applyFlipReorder(insertBefore);
        }

        async function persistOrder() {
            // Send both tasks and events so Grok-extracted items
            // (quizzes, lectures, exams) reorder too — they have a position
            // column on CalendarEvent, same as Task.
            const items = Array.from(list.querySelectorAll('.todo-row'))
                .map((el) => {
                    const kind = el.dataset.kind;
                    const id = parseInt(el.dataset.id, 10);
                    if ((kind !== 'task' && kind !== 'event') || Number.isNaN(id)) return null;
                    return { kind, id };
                })
                .filter(Boolean);
            if (items.length === 0) return;
            try {
                await fetch('/tasks/reorder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ items }),
                });
            } catch (err) {
                console.error('reorder failed:', err);
                return;
            }
            // Week page: re-pull the calendar so day-cells AND each day's
            // <template data-day-modal-content> reflect the new priority.
            // Skip #day-modal so an open modal doesn't disappear mid-drag.
            await refreshMonthGridOnly();
        }

        function onPointerMove(e) {
            if (!dragRow || !pointerStart) return;
            const dy = e.clientY - pointerStart.y;
            if (!isDragging && Math.abs(dy) < MOVE_THRESHOLD) return;
            if (!isDragging) {
                isDragging = true;
                dragRow.classList.add('dragging');
                document.body.classList.add('cards-dragging');
            }
            moveTowards(e.clientY);
            e.preventDefault();
        }
        function onPointerUp() {
            if (!dragRow) return;
            const wasDragging = isDragging;
            if (dragRow) dragRow.classList.remove('dragging');
            document.body.classList.remove('cards-dragging');
            dragRow = null;
            pointerStart = null;
            isDragging = false;
            if (wasDragging) persistOrder();
        }

        list.querySelectorAll('.todo-drag-handle').forEach((handle) => {
            handle.addEventListener('pointerdown', (e) => {
                if (e.button !== undefined && e.button !== 0) return;
                const row = handle.closest('.todo-row');
                if (!row) return;
                dragRow = row;
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

    function syncStartsToggleLabel(form) {
        const btn = form.querySelector('[data-toggle-task-starts]');
        const lbl = form.querySelector('[data-task-starts]');
        if (!btn || !lbl) return;
        btn.textContent = lbl.hidden ? '+ Add start date' : '− Remove start';
    }

    function bindTaskStartsToggle(form) {
        // Reveal/hide the optional "Starts on" datetime-local input. The
        // hidden state is what the submit handlers use to decide whether
        // to send the starts_at value (so a stale value from a prior open
        // doesn't accidentally get submitted after the user backed out).
        if (form.dataset.startsBound === '1') {
            syncStartsToggleLabel(form);
            return;
        }
        form.dataset.startsBound = '1';
        const toggleBtn = form.querySelector('[data-toggle-task-starts]');
        const startsLabel = form.querySelector('[data-task-starts]');
        const startsInput = form.querySelector('input[name="starts_at"]');
        if (!toggleBtn || !startsLabel) return;
        syncStartsToggleLabel(form);
        toggleBtn.addEventListener('click', () => {
            startsLabel.hidden = !startsLabel.hidden;
            if (startsLabel.hidden && startsInput) startsInput.value = '';
            else if (!startsLabel.hidden && startsInput) startsInput.focus();
            syncStartsToggleLabel(form);
        });
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
        bindTaskStartsToggle(form);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const titleInput = form.querySelector('input[name="title"]');
            const dueInput = form.querySelector('input[name="due_at"]');
            const startsInput = form.querySelector('input[name="starts_at"]');
            const startsLabel = form.querySelector('[data-task-starts]');
            const classSelect = form.querySelector('[data-add-task-class]');
            const notesField = form.querySelector('textarea[name="notes"]');
            const title = (titleInput.value || '').trim();
            if (!title) return;
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
            if (startsInput && startsInput.value
                && startsLabel && !startsLabel.hidden) {
                fd.append('starts_at', startsInput.value);
            }
            if (tagId) fd.append('tag_id', tagId);
            if (notesField && notesField.value.trim()) fd.append('notes', notesField.value);
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
                const dueValue = dueInput ? dueInput.value : '';
                const startsValue = startsInput ? startsInput.value : '';
                titleInput.value = '';
                if (dueInput) dueInput.value = '';
                if (startsInput) startsInput.value = '';
                if (startsLabel) startsLabel.hidden = true;
                if (notesField) notesField.value = '';
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
                const checkStart = startsValue || dueValue;
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
                console.error('add-task failed:', err);
                alert('Could not add task: ' + err.message);
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

    function _todayEndOfDayLocal() {
        // "YYYY-MM-DDT23:59" — datetime-local format. Gives the user a
        // sensible end-of-day deadline when they open the add-task modal,
        // so they don't have to set a time and accidentally end up with
        // 00:00 (start of day, technically already past).
        const t = new Date();
        const y = t.getFullYear();
        const mo = String(t.getMonth() + 1).padStart(2, '0');
        const d = String(t.getDate()).padStart(2, '0');
        return `${y}-${mo}-${d}T23:59`;
    }

    // Delegated: any click that opens #add-task-modal prefills due_at to
    // today end-of-day if the user hasn't typed anything yet. Skips the
    // week-page "+ Add task for this day" path, which sets its own date.
    document.addEventListener('click', (e) => {
        const trig = e.target.closest('[data-open-modal="add-task-modal"]');
        if (!trig) return;
        document.querySelectorAll(
            '#add-task-modal form[data-add-task] input[name="due_at"]'
        ).forEach((el) => {
            if (!el.value) el.value = _todayEndOfDayLocal();
        });
    });

    // ---- Wire up ----

    // ---- Class-block drag (reorder bucket order on home/today) ----

    function bindClassBlockDrag(list) {
        if (list.dataset.dragBound === '1') return;
        list.dataset.dragBound = '1';
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
        document.querySelectorAll('.todo-list-draggable').forEach(bindDrag);
        document.querySelectorAll('[data-class-block-list]').forEach(bindClassBlockDrag);
        document.querySelectorAll('form[data-add-task]').forEach(bindAddTaskForm);
        document.querySelectorAll('form[data-edit-task]').forEach(bindEditTaskForm);
        bindManageTags();
    }
    bindAll();
    window.bindTodoToggles = bindAll;
})();
