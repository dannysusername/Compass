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

    document.querySelectorAll('[data-open-modal]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const id = btn.getAttribute('data-open-modal');
            const modal = document.getElementById(id);
            if (modal) open(modal);
        });
    });

    document.addEventListener('click', (e) => {
        const overlay = e.target.closest('.modal-overlay');
        if (!overlay) return;
        // Click on the overlay background (not its child dialog) closes.
        if (e.target === overlay) close(overlay);
        // Or click on any [data-close-modal] inside.
        if (e.target.closest('[data-close-modal]')) close(overlay);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.modal-overlay:not([hidden])').forEach(close);
    });
})();
