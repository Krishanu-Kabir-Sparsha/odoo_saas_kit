/**
 * Perfect HR — Terms & Conditions acceptance gate.
 *
 * Loaded globally via web.assets_frontend, so it works on every website page.
 * A single capture-phase click listener intercepts:
 *
 *   • Any link whose path is /saas/signup  (hero "Start Free Trial", every
 *     pricing-card "Get Started"/trial link, the Customize "Get Started Now")
 *       → opens the modal in GATE mode: the visitor must scroll to the bottom,
 *         tick "I have read and accept", then "Accept & Continue" proceeds to
 *         the signup page it intercepted.
 *
 *   • The footer "Terms and Condition" link  (set its URL to "#terms")
 *       → opens the modal in VIEW mode: read-only, just the text + Close.
 *
 * We match /saas/signup by PATHNAME (not substring) so the signup page's own
 * "Log in here" link — /web/login?redirect=/saas/signup… — is NOT gated.
 */
(function () {
    'use strict';

    function ready(fn) {
        if (document.readyState !== 'loading') { fn(); }
        else { document.addEventListener('DOMContentLoaded', fn); }
    }

    ready(function () {
        var overlay = document.getElementById('phrTermsOverlay');
        if (!overlay) { return; }

        var body      = document.getElementById('phrTermsBody');
        var notice    = document.getElementById('phrTermsNotice');
        var check     = document.getElementById('phrTermsCheck');
        var accept    = document.getElementById('phrTermsAccept');
        var cancel    = document.getElementById('phrTermsCancel');
        var planWrap  = document.getElementById('phrTermsPlan');
        var planName  = document.getElementById('phrTermsPlanName');

        var targetUrl = null;      // where "Accept & Continue" should go (gate mode)
        var reachedBottom = false; // latch: once scrolled to the end, stay unlocked

        /* ── open / close ── */
        function setMode(mode) {
            var view = (mode === 'view');
            overlay.classList.toggle('mode-view', view);
            overlay.classList.toggle('mode-gate', !view);
            cancel.textContent = view ? 'Close' : 'Cancel';
        }

        function openModal() {
            overlay.style.display = 'flex';
            overlay.classList.add('is-open');
            document.body.style.overflow = 'hidden';
            body.scrollTop = 0;
            reachedBottom = false;
            check.checked = false;
            check.disabled = true;
            accept.disabled = true;
            if (notice) { notice.style.display = ''; }   // re-show; evalScroll hides it at the bottom
            // Let layout settle, then decide whether the text even needs scrolling.
            setTimeout(evalScroll, 40);
        }

        function closeModal() {
            overlay.style.display = 'none';
            overlay.classList.remove('is-open');
            document.body.style.overflow = '';
            targetUrl = null;
        }

        /* Enable the accept checkbox once the reader reaches the bottom. Latched:
           scrolling back up keeps it enabled. If the text is short enough to need
           no scrolling at all, it unlocks immediately. */
        function evalScroll() {
            if (!overlay.classList.contains('mode-gate') || reachedBottom) { return; }
            var atBottom = (body.scrollTop + body.clientHeight) >= (body.scrollHeight - 6);
            if (atBottom) {
                reachedBottom = true;
                check.disabled = false;
                if (notice) { notice.style.display = 'none'; }
            }
        }

        /* Best-effort plan name for the header chip (gate mode). */
        function derivePlanName(a) {
            var card = a.closest ? a.closest('.pricing-card') : null;
            if (card) {
                var n = card.querySelector('.plan-name');
                if (n && n.textContent.trim()) { return n.textContent.trim(); }
            }
            if (a.id === 'customizeCta') {
                var active = document.querySelector('#tierButtons .tier-btn.active');
                if (active && active.getAttribute('data-package-name')) {
                    return active.getAttribute('data-package-name');
                }
            }
            try {
                var pid = new URL(a.href, location.origin).searchParams.get('package_id');
                if (pid) {
                    var tb = document.querySelector('#tierButtons .tier-btn[data-package-id="' + pid + '"]');
                    if (tb && tb.getAttribute('data-package-name')) {
                        return tb.getAttribute('data-package-name');
                    }
                }
            } catch (e) { /* ignore */ }
            return '';
        }

        function withConsent(url) {
            // Mark that the visitor accepted via the modal, so the signup form
            // arrives with its Terms checkbox pre-ticked (the server still enforces it).
            try {
                var u = new URL(url, location.origin);
                u.searchParams.set('consent', '1');
                return u.href;
            } catch (e) {
                return url + (url.indexOf('?') > -1 ? '&' : '?') + 'consent=1';
            }
        }

        function openGate(url, plan) {
            targetUrl = withConsent(url);
            setMode('gate');
            if (plan) { planName.textContent = plan; planWrap.style.display = ''; }
            else { planWrap.style.display = 'none'; }
            openModal();
        }

        function openView() {
            targetUrl = null;
            setMode('view');
            openModal();
        }

        /* ── modal-local events ── */
        body.addEventListener('scroll', evalScroll);
        window.addEventListener('resize', evalScroll);

        check.addEventListener('change', function () {
            accept.disabled = !check.checked;
        });

        accept.addEventListener('click', function () {
            if (accept.disabled || !targetUrl) { return; }
            window.location.href = targetUrl;
        });

        overlay.addEventListener('click', function (e) {
            var closer = e.target.closest('[data-phr-terms-close]');
            if (closer) { closeModal(); }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay.style.display === 'flex') { closeModal(); }
        });

        /* ── global link interception (capture phase, so we beat navigation) ── */
        document.addEventListener('click', function (e) {
            var a = e.target.closest ? e.target.closest('a') : null;
            if (!a) { return; }
            if (a.closest('#phrTermsOverlay')) { return; }   // never gate clicks inside the modal

            var raw = a.getAttribute('href') || '';
            var u = null;
            try { u = new URL(a.href, location.origin); } catch (err) { u = null; }

            // Footer "Terms and Condition" → read-only view
            if (raw === '#terms' || (u && u.hash === '#terms') || (u && u.pathname === '/terms')) {
                e.preventDefault();
                e.stopPropagation();
                openView();
                return;
            }

            // Signup CTAs → acceptance gate
            if (u && u.pathname === '/saas/signup') {
                e.preventDefault();
                e.stopPropagation();
                openGate(a.href, derivePlanName(a));
                return;
            }
        }, true);
    });
})();
