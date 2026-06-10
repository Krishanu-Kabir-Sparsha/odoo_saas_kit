/**
 * Perfect HR — close the mobile header menu after an in-page (anchor) click.
 *
 * Odoo's mobile off-canvas / collapsed navbar auto-closes when a link navigates
 * to another page (the reload hides it), but NOT for same-page anchor links
 * (e.g. /#pricing, /#ai, /#features) because there is no reload — so the menu
 * stayed open over the section you just scrolled to.
 *
 * IMPORTANT: we close it by triggering the menu's OWN dismiss control (the X
 * button) rather than calling the Bootstrap API ourselves. When the off-canvas
 * opens, Bootstrap locks page scroll via `body { overflow: hidden }` + a
 * backdrop; only its native close path unlocks that. Closing it any other way
 * (e.g. `new bootstrap.Offcanvas(el).hide()`, which is out of sync on an
 * already-open menu) leaves the body scroll-locked until you reopen the menu.
 *
 * Loaded globally via web.assets_frontend, so it works on every website page.
 */
(function () {
    'use strict';

    // Safety net: if the menu is fully closed but the body is still
    // scroll-locked (no open menu, no backdrop), release it.
    function releaseScrollLockIfStuck() {
        if (document.querySelector('.offcanvas.show, .navbar-collapse.show, .offcanvas-backdrop, .modal-backdrop')) {
            return;
        }
        if (document.body.style.overflow === 'hidden') {
            document.body.style.overflow = '';
            document.body.style.paddingRight = '';
        }
        document.body.classList.remove('modal-open');
    }

    function closeMobileMenu() {
        var menu = document.querySelector('.offcanvas.show, .navbar-collapse.show');
        if (menu) {
            // 1) Preferred: click the menu's own dismiss control (full teardown).
            var dismiss = menu.querySelector(
                '[data-bs-dismiss="offcanvas"], [data-bs-dismiss="collapse"], .btn-close'
            );
            if (dismiss) {
                dismiss.click();
            } else {
                // 2) Fallback: only the EXISTING Bootstrap instance — never `new`,
                //    which would be out of sync and skip the scroll-unlock.
                var bs = window.bootstrap;
                var inst = null;
                if (bs) {
                    if (menu.classList.contains('offcanvas') && bs.Offcanvas) {
                        inst = bs.Offcanvas.getInstance(menu);
                    } else if (bs.Collapse) {
                        inst = bs.Collapse.getInstance(menu);
                    }
                }
                if (inst) {
                    inst.hide();
                } else {
                    // 3) Last resort: tear down manually.
                    menu.classList.remove('show');
                    document.querySelectorAll('.offcanvas-backdrop').forEach(function (b) { b.remove(); });
                }
            }
        }
        // Run the safety net after the close animation settles.
        setTimeout(releaseScrollLockIfStuck, 500);
    }

    document.addEventListener('click', function (ev) {
        var link = ev.target.closest('a[href]');
        if (!link) return;
        if ((link.getAttribute('href') || '').indexOf('#') === -1) return;  // anchor links only
        if (!document.querySelector('.offcanvas.show, .navbar-collapse.show')) return;  // only if a menu is open
        // Let Odoo's smooth-scroll start first, then close the menu.
        setTimeout(closeMobileMenu, 100);
    }, true);
})();
