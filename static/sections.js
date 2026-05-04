// StudyFlow — section-view picker with per-section kind grouping.
// Each section has +Tailor / +Verbatim buttons inline next to its heading.
// Plain click → starts a new card with that section.
// Ctrl/Cmd+click → adds the section to the active card with whichever kind
//                  button you clicked (so you can mix verbatim and tailored
//                  sections within one card).
// Tailor cards have a per-card "prompt" textarea in the side panel — type
// instructions like "What do I need to do to prepare?" or "List the most
// important points" and Grok processes the section accordingly.
// Click × on a picked section → remove from its group.

(function () {
  const page = document.querySelector('.sections-page');
  if (!page) return;
  const syllabusId = page.dataset.syllabusId;
  const classId = page.dataset.classId;
  const textPane = document.getElementById('sections-text');
  const builder = document.getElementById('card-builder');
  const builderGroups = document.getElementById('builder-groups');
  const cardCount = document.getElementById('card-count');
  const submitBtn = document.getElementById('submit-cards');
  const clearBtn = document.getElementById('clear-all');
  const status = document.getElementById('submit-status');

  // Each entry: {id, sections: Map<index, kind>}
  // sections preserves insertion order (Map does), but we sort by index when
  // building the card so document order is preserved.
  let groups = [];
  let activeGroupId = null;
  let nextGroupId = 1;
  const GROUP_COLORS = ['#A04528', '#5C8A3A', '#1F3D7A', '#C77A1F', '#7A4528', '#3A6B6B', '#8A3A6B', '#5A5A1F'];

  function colorFor(groupId) {
    return GROUP_COLORS[(groupId - 1) % GROUP_COLORS.length];
  }
  function findGroupOfIndex(idx) {
    return groups.find((g) => g.sections.has(idx));
  }
  function getGroupById(id) { return groups.find((g) => g.id === id); }

  function pickSection(idx, kind, withModifier) {
    const existing = findGroupOfIndex(idx);
    if (existing) return;  // re-clicking a picked section: use × to remove
    if (withModifier && activeGroupId !== null) {
      const g = getGroupById(activeGroupId);
      if (g) {
        g.sections.set(idx, kind);
      } else {
        const g2 = { id: nextGroupId++, sections: new Map([[idx, kind]]), prompt: '' };
        groups.push(g2);
        activeGroupId = g2.id;
      }
    } else {
      const g = { id: nextGroupId++, sections: new Map([[idx, kind]]), prompt: '' };
      groups.push(g);
      activeGroupId = g.id;
    }
    render();
  }

  function setGroupPrompt(groupId, prompt) {
    const g = getGroupById(groupId);
    if (!g) return;
    g.prompt = prompt;
    // Don't re-render — would steal focus from the textarea. Submit-button
    // disabled state is updated via direct call below.
    updateSubmitButtonState();
  }

  function hasMissingTailorPrompt() {
    return groups.some((g) => {
      const hasTailor = Array.from(g.sections.values()).some((k) => k === 'tailor');
      return hasTailor && !(g.prompt || '').trim();
    });
  }

  function updateSubmitButtonState() {
    submitBtn.disabled = groups.length === 0 || hasMissingTailorPrompt();
  }

  function removeSection(idx) {
    const g = findGroupOfIndex(idx);
    if (!g) return;
    g.sections.delete(idx);
    if (g.sections.size === 0) {
      groups = groups.filter((x) => x.id !== g.id);
      if (activeGroupId === g.id) {
        activeGroupId = groups.length ? groups[groups.length - 1].id : null;
      }
    }
    render();
  }

  function setSectionKind(groupId, idx, kind) {
    const g = getGroupById(groupId);
    if (!g || !g.sections.has(idx)) return;
    g.sections.set(idx, kind);
    render();
  }
  function setActiveGroup(groupId) { activeGroupId = groupId; render(); }
  function removeGroup(groupId) {
    groups = groups.filter((g) => g.id !== groupId);
    if (activeGroupId === groupId) {
      activeGroupId = groups.length ? groups[groups.length - 1].id : null;
    }
    render();
  }
  function clearAll() { groups = []; activeGroupId = null; render(); }

  // ---- Render ----

  function render() {
    textPane.querySelectorAll('.section-article').forEach((art) => {
      const idx = parseInt(art.dataset.sectionIndex, 10);
      const g = findGroupOfIndex(idx);
      const marker = art.querySelector('.section-marker');
      const tailorBtn = art.querySelector('.add-btn--tailor');
      const verbatimBtn = art.querySelector('.add-btn--verbatim');
      const removeBtn = art.querySelector('.remove-pick');
      if (g) {
        const sectionKind = g.sections.get(idx);
        art.classList.add('picked');
        art.classList.toggle('active-group', g.id === activeGroupId);
        marker.style.background = colorFor(g.id);
        marker.style.borderColor = colorFor(g.id);
        marker.textContent = String(g.id);
        marker.title = `Card ${g.id} · this section: ${sectionKind}`;
        tailorBtn.hidden = true;
        verbatimBtn.hidden = true;
        removeBtn.hidden = false;
      } else {
        art.classList.remove('picked', 'active-group');
        marker.style.background = '';
        marker.style.borderColor = '';
        marker.textContent = '';
        marker.title = '';
        tailorBtn.hidden = false;
        verbatimBtn.hidden = false;
        removeBtn.hidden = true;
      }
    });

    if (groups.length === 0) { builder.hidden = true; return; }
    builder.hidden = false;
    cardCount.textContent = String(groups.length);
    builderGroups.innerHTML = '';
    groups.forEach((g) => {
      const li = document.createElement('li');
      li.className = 'builder-group' + (g.id === activeGroupId ? ' active' : '');
      li.style.borderLeftColor = colorFor(g.id);
      const sortedEntries = Array.from(g.sections.entries()).sort((a, b) => a[0] - b[0]);
      const sectionRows = sortedEntries.map(([i, kind]) => {
        const art = textPane.querySelector(`[data-section-index="${i}"]`);
        const heading = art ? art.querySelector('.section-article-heading').textContent : `#${i}`;
        return `
          <li class="builder-section" data-section-index="${i}">
            <span class="builder-section-name" title="${escapeHtml(heading)}">${escapeHtml(heading)}</span>
            <span class="builder-section-toggle">
              <label class="kind-pill kind-pill--tailor">
                <input type="radio" name="kind-${g.id}-${i}" value="tailor" ${kind === 'tailor' ? 'checked' : ''}> tlr
              </label>
              <label class="kind-pill kind-pill--verbatim">
                <input type="radio" name="kind-${g.id}-${i}" value="verbatim" ${kind === 'verbatim' ? 'checked' : ''}> vrb
              </label>
              <button type="button" class="builder-section-remove" data-section-index="${i}" aria-label="Remove section">×</button>
            </span>
          </li>
        `;
      }).join('');

      // Tailor card → show prompt textarea. Verbatim-only card → no prompt needed.
      const hasTailor = sortedEntries.some(([, k]) => k === 'tailor');
      const promptBlock = hasTailor ? `
        <div class="builder-prompt">
          <label>
            <span class="builder-prompt-label">Tailor prompt</span>
            <textarea class="builder-prompt-input" rows="4" placeholder="Use AI to customize the information"
              data-group-id="${g.id}">${escapeHtml(g.prompt || '')}</textarea>
          </label>
        </div>
      ` : '';

      li.innerHTML = `
        <div class="builder-group-head">
          <span class="builder-group-num" style="background:${colorFor(g.id)}">${g.id}</span>
          <span class="builder-group-label">Card ${g.id} · ${g.sections.size} section${g.sections.size > 1 ? 's' : ''}</span>
          <button type="button" class="remove-group" aria-label="Remove card">×</button>
        </div>
        <ul class="builder-section-list">${sectionRows}</ul>
        ${promptBlock}
      `;
      // Click anywhere in the group (except form controls) → make it active.
      // Form controls are filtered so typing in the prompt textarea or
      // toggling a kind pill doesn't trigger a re-render that would steal
      // focus mid-keystroke.
      li.addEventListener('click', (e) => {
        if (e.target.closest('input,button,textarea,label')) return;
        setActiveGroup(g.id);
        const firstIdx = sortedEntries[0][0];
        const art = textPane.querySelector(`[data-section-index="${firstIdx}"]`);
        if (art) art.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      li.querySelector('.remove-group').addEventListener('click', (e) => {
        e.stopPropagation();
        removeGroup(g.id);
      });
      li.querySelectorAll('.builder-section-remove').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          removeSection(parseInt(btn.dataset.sectionIndex, 10));
        });
      });
      li.querySelectorAll('input[type="radio"]').forEach((r) => {
        r.addEventListener('change', () => {
          const [, , idxStr] = r.name.split('-');
          setSectionKind(g.id, parseInt(idxStr, 10), r.value);
        });
      });
      const promptInput = li.querySelector('.builder-prompt-input');
      if (promptInput) {
        promptInput.addEventListener('input', () => {
          setGroupPrompt(parseInt(promptInput.dataset.groupId, 10), promptInput.value);
        });
      }
      builderGroups.appendChild(li);
    });
    updateSubmitButtonState();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
  }

  // ---- Click handling ----

  textPane.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const art = btn.closest('.section-article');
    if (!art) return;
    const idx = parseInt(art.dataset.sectionIndex, 10);
    const act = btn.dataset.act;
    if (act === 'remove') {
      removeSection(idx);
    } else {
      pickSection(idx, act, e.ctrlKey || e.metaKey);
    }
  });

  clearBtn.addEventListener('click', clearAll);

  submitBtn.addEventListener('click', async () => {
    if (groups.length === 0) return;
    if (hasMissingTailorPrompt()) {
      status.hidden = false;
      status.classList.add('error');
      status.textContent = 'Each tailor card needs a prompt — tell Grok what you want from those sections.';
      return;
    }
    submitBtn.disabled = true;
    clearBtn.disabled = true;
    status.hidden = false;
    status.classList.remove('error');
    const tailorCount = groups.reduce(
      (n, g) => n + Array.from(g.sections.values()).filter((k) => k === 'tailor').length,
      0
    );
    status.textContent = `Creating ${groups.length} card${groups.length > 1 ? 's' : ''}…` +
      (tailorCount ? ` (${tailorCount} Grok call${tailorCount > 1 ? 's' : ''}, ~5-15s each)` : '');
    try {
      const body = {
        groups: groups.map((g) => ({
          sections: Array.from(g.sections.entries())
            .sort((a, b) => a[0] - b[0])
            .map(([index, kind]) => ({ index, kind })),
          prompt: g.prompt || null,
        })),
      };
      const r = await fetch(`/syllabus/${syllabusId}/sections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(detail.detail || `${r.status} ${r.statusText}`);
      }
      const result = await r.json();
      window.location.href = result.redirect_to || `/classes/${classId}`;
    } catch (err) {
      status.classList.add('error');
      status.textContent = `Error: ${err.message}`;
      submitBtn.disabled = false;
      clearBtn.disabled = false;
    }
  });

  render();
})();
