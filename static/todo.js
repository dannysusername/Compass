// Todo list interactions:
//   - Circular toggle (open ↔ done) via AJAX
//   - Inline edit of task title (click title, type, Enter/blur to save)
//   - Drag burger handle to reorder priority (FLIP animation)
//   - Add task with class picker
//   - Delete task

(function () {
    // ---- Toggle ----

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
            row.classList.toggle('done');
            btn.setAttribute('aria-pressed', wasDone ? 'false' : 'true');
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
            } catch (err) {
                row.classList.toggle('done');
                btn.setAttribute('aria-pressed', wasDone ? 'true' : 'false');
                console.error('toggle failed:', err);
            }
        });
    }

    // ---- Delete ----

    function bindDelete(btn) {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this task?')) return;
            const id = btn.dataset.id;
            try {
                const r = await fetch(`/tasks/${id}/delete`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
                // Remove every row for this task across the page (inline
                // week grid + modal copy + today list) so they stay in sync.
                document.querySelectorAll(
                    `.todo-row[data-kind="task"][data-id="${id}"]`
                ).forEach((row) => {
                    row.style.transition = 'opacity 0.15s ease, max-height 0.15s ease';
                    row.style.opacity = '0';
                    row.style.maxHeight = '0';
                    setTimeout(() => row.remove(), 160);
                });
            } catch (err) {
                alert('Could not delete task: ' + err.message);
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
            form.querySelector('input[name="due_at"]').value = row.dataset.dueDate || '';
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
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = form.querySelector('input[name="task_id"]').value;
            const title = (form.querySelector('input[name="title"]').value || '').trim();
            const due = form.querySelector('input[name="due_at"]').value;
            if (!id || !title) return;
            const fd = new FormData();
            fd.append('title', title);
            if (due) fd.append('due_at', due + 'T23:59:00');
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
                // Update every matching row in place rather than reloading.
                // A reload would re-render the today list with today's
                // filter, dropping any task whose new due date moved out
                // of today/overdue — making a rescheduled task look like
                // it was deleted.
                document.querySelectorAll(
                    `.todo-row[data-kind="task"][data-id="${id}"]`
                ).forEach((row) => {
                    row.dataset.title = title;
                    row.dataset.dueDate = due || '';
                    const titleEl = row.querySelector('.todo-title');
                    if (titleEl) titleEl.textContent = title;
                });
                const modal = document.getElementById('edit-task-modal');
                if (modal) {
                    modal.hidden = true;
                    document.body.classList.remove('modal-open');
                }
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

        function persistOrder() {
            const ids = Array.from(list.querySelectorAll('.todo-row'))
                .filter((el) => el.dataset.kind === 'task')
                .map((el) => parseInt(el.dataset.id, 10))
                .filter((n) => !Number.isNaN(n));
            if (ids.length === 0) return;
            fetch('/tasks/reorder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ task_ids: ids }),
            }).catch((err) => console.error('reorder failed:', err));
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

    function bindAddTaskForm(form) {
        if (form.dataset.bound === '1') return;
        form.dataset.bound = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const titleInput = form.querySelector('input[name="title"]');
            const dueInput = form.querySelector('input[name="due_at"]');
            const classSelect = form.querySelector('[data-add-task-class]');
            const title = (titleInput.value || '').trim();
            if (!title) return;
            const classId = classSelect ? classSelect.value : null;
            // Forms in the partial use `/classes/0/tasks` as a stub; rewrite to picked class.
            const url = classId
                ? `/classes/${classId}/tasks`
                : form.action;
            const fd = new FormData();
            fd.append('title', title);
            if (dueInput && dueInput.value) {
                fd.append('due_at', dueInput.value + 'T23:59:00');
            }
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
                titleInput.value = '';
                if (dueInput) dueInput.value = '';
                // The today list only renders tasks due today, overdue, or
                // with no date. A task scheduled for a future date is saved
                // correctly but filtered out — without feedback the user
                // thinks the add silently failed.
                if (dueValue) {
                    const t = new Date();
                    const todayStr = t.getFullYear() + '-' +
                        String(t.getMonth() + 1).padStart(2, '0') + '-' +
                        String(t.getDate()).padStart(2, '0');
                    if (dueValue > todayStr) {
                        alert(`Task added — due ${dueValue}. It won't appear on the Today list until that date; find it on the class page or Week view.`);
                    }
                }
                window.location.reload();
            } catch (err) {
                console.error('add-task failed:', err);
                alert('Could not add task: ' + err.message);
            }
        });
    }

    // ---- Wire up ----

    function bindAll() {
        document.querySelectorAll('.todo-toggle').forEach(bindToggle);
        document.querySelectorAll('.todo-del').forEach(bindDelete);
        document.querySelectorAll('.todo-edit').forEach(bindEditButton);
        document.querySelectorAll('.todo-list-draggable').forEach(bindDrag);
        document.querySelectorAll('form[data-add-task]').forEach(bindAddTaskForm);
        document.querySelectorAll('form[data-edit-task]').forEach(bindEditTaskForm);
    }
    bindAll();
    window.bindTodoToggles = bindAll;
})();
