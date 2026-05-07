// Tiny modal opener/closer. Buttons with data-open-modal="<id>" show that
// element; clicks on .modal-overlay or [data-close-modal], or pressing
// Escape, hide it.

(function () {
    function open(modal) {
        modal.hidden = false;
        document.body.classList.add('modal-open');
        // Move focus to first focusable element inside.
        const focusable = modal.querySelector(
            'input, select, textarea, button:not([data-close-modal])'
        );
        if (focusable) focusable.focus();
    }

    function close(modal) {
        modal.hidden = true;
        document.body.classList.remove('modal-open');
    }

    // Delegated open: works for buttons that arrive after page load
    // (e.g. via softRefresh replacing the today-list-block partial).
    document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-open-modal]');
        if (!trigger) return;
        const id = trigger.getAttribute('data-open-modal');
        const modal = document.getElementById(id);
        if (modal) open(modal);
    });

    // Track where the click started. If a user is selecting text in a
    // textarea and drags the cursor onto the backdrop before releasing,
    // the click event fires on the backdrop — but they didn't intend to
    // close. Only close when BOTH mousedown and mouseup happened on the
    // backdrop itself (not any child element).
    let mousedownTarget = null;
    document.addEventListener('mousedown', (e) => {
        mousedownTarget = e.target;
    });

    document.addEventListener('click', (e) => {
        const overlay = e.target.closest('.modal-overlay');
        if (!overlay) return;
        // Backdrop click: close only if the gesture started AND ended on
        // the overlay itself (a deliberate background click), not a
        // text-selection drag-out from inside the dialog.
        if (e.target === overlay && mousedownTarget === overlay) {
            close(overlay);
            return;
        }
        // Or click on any [data-close-modal] inside.
        if (e.target.closest('[data-close-modal]')) close(overlay);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.modal-overlay:not([hidden])').forEach(close);
    });
})();
