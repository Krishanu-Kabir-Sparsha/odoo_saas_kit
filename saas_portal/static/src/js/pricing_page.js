/**
 * SaaS Pricing Page — Billing Toggle + Customize Your Plan
 */

/* ═══════ MODULE MODAL (global — called from onclick attributes) ═══════ */
function saasOpenModuleModal(btn) {
    var pkgId = btn.getAttribute('data-package-id');
    var modal = document.getElementById('saasModuleModal_' + pkgId);
    if (modal) {
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
}

function saasCloseModuleModal(el) {
    var modal = el.closest('.saas-module-modal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }
}

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.saas-module-modal').forEach(function (m) {
            if (m.style.display !== 'none') {
                m.style.display = 'none';
                document.body.style.overflow = '';
            }
        });
    }
});

(function () {
    document.addEventListener('DOMContentLoaded', function () {

        /* ═══════ BILLING TOGGLE ═══════ */
        var wrap = document.getElementById('billingToggle');
        if (wrap) {
            var btns = wrap.querySelectorAll('.toggle-btn');
            var pill = wrap.querySelector('.toggle-pill');
            function positionPill(btn) {
                pill.style.width = btn.offsetWidth + 'px';
                pill.style.transform = 'translateX(' + (btn.offsetLeft - 4) + 'px)';
            }
            positionPill(btns[0]);
            btns.forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var cycle = this.getAttribute('data-cycle');
                    btns.forEach(function (b) { b.classList.remove('active'); });
                    this.classList.add('active');
                    positionPill(this);
                    document.querySelectorAll('.monthly-price').forEach(function (el) {
                        el.style.display = cycle === 'monthly' ? 'flex' : 'none';
                    });
                    document.querySelectorAll('.yearly-price').forEach(function (el) {
                        el.style.display = cycle === 'yearly' ? 'flex' : 'none';
                    });
                });
            });
            window.addEventListener('resize', function () {
                var a = wrap.querySelector('.toggle-btn.active');
                if (a) positionPill(a);
            });
        }

        /* ═══════ CUSTOMIZE YOUR PLAN ═══════ */
        var jsonEl = document.getElementById('durationDataJson');
        if (!jsonEl) return;

        var DURATION_DATA = {};
        try { DURATION_DATA = JSON.parse(jsonEl.textContent || '{}'); } catch (e) { }
        var currSym = jsonEl.getAttribute('data-currency') || '\u09f3';

        var selPkg = null, selDur = null;
        var durContainer = document.getElementById('durationButtons');
        var modSection = document.getElementById('modulesSection');
        var modGrid = document.getElementById('modulesGrid');
        var bCard = document.getElementById('breakdownCard');
        var bPlaceholder = document.getElementById('breakdownPlaceholder');

        function fmt(n) {
            return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        function escHtml(s) {
            var d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        }

        /* ── Render duration buttons dynamically ──
         * Only the discounted prepaid terms live here — plain base-monthly
         * pricing is already shown on the main plan cards, so repeating it
         * would be redundant. Falls back to a single Monthly button only if a
         * package has no duration tiers configured at all. */
        function renderDurs(pkgId) {
            durContainer.innerHTML = '';
            var tiers = DURATION_DATA[pkgId] || [];
            if (!tiers.length) {
                var mb = document.createElement('button');
                mb.type = 'button';
                mb.className = 'dur-btn active';
                mb.setAttribute('data-months', '1');
                mb.textContent = 'Monthly';
                mb.addEventListener('click', onDurClick);
                durContainer.appendChild(mb);
                selDur = 1;
                return;
            }
            tiers.forEach(function (t, i) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'dur-btn' + (i === 0 ? ' active' : '');
                b.setAttribute('data-months', t.duration_months);
                b.textContent = t.label;
                if (t.discount_percent > 0) {
                    var badge = document.createElement('span');
                    badge.className = 'discount-badge';
                    badge.textContent = '-' + t.discount_percent + '%';
                    b.appendChild(badge);
                }
                b.addEventListener('click', onDurClick);
                durContainer.appendChild(b);
                if (i === 0) selDur = t.duration_months;
            });
        }

        function onDurClick() {
            durContainer.querySelectorAll('.dur-btn').forEach(function (b) { b.classList.remove('active'); });
            this.classList.add('active');
            selDur = parseInt(this.getAttribute('data-months'));
            fetchPricing();
        }

        /* ── Tier buttons ── */
        document.querySelectorAll('#tierButtons .tier-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#tierButtons .tier-btn').forEach(function (b) { b.classList.remove('active'); });
                this.classList.add('active');
                selPkg = parseInt(this.getAttribute('data-package-id'));
                renderDurs(selPkg);
                fetchPricing();
            });
        });

        /* Auto-select first tier on page load */
        var firstTier = document.querySelector('#tierButtons .tier-btn');
        if (firstTier) { firstTier.click(); }

        /* ── Fetch pricing from backend API ── */
        function fetchPricing() {
            if (!selPkg || !selDur) return;
            bPlaceholder.style.display = 'none';
            bCard.style.display = 'block';
            document.getElementById('totalPriceValue').textContent = '...';

            fetch('/saas/pricing/calculate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'call',
                    params: { package_id: selPkg, duration_months: selDur }
                })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var p = data.result;
                    if (!p || p.error) return;
                    updateUI(p);
                })
                .catch(function (e) { console.error(e); });
        }

        /* ── Update breakdown UI ── */
        function updateUI(p) {
            var s = p.currency_symbol || currSym;

            // Null-safe helpers: if the rendered template is out of sync with this
            // JS (e.g. a cached/stale page missing a row), skip the element instead
            // of throwing — the calculator degrades gracefully rather than crashing.
            function setText(id, t) { var el = document.getElementById(id); if (el) { el.textContent = t; } }
            function showRow(id, show) { var el = document.getElementById(id); if (el) { el.style.display = show ? 'flex' : 'none'; } }

            var dur = p.duration_months;
            var totalBefore = p.base_price * dur;                // pre-discount term cost
            var totalDiscount = (p.discount_amount || 0) * dur;  // total saved over the term
            var total = p.total_price + (p.setup_fee || 0);
            var hasDiscount = p.discount_percent > 0 && totalDiscount > 0;

            // 1) Base price (per month)
            setText('basePriceValue', s + fmt(p.base_price) + '/month');

            // 2) Per-month discount, labelled with % and term
            showRow('discountRow', hasDiscount);
            if (hasDiscount) {
                setText('discountLabel', 'Duration Discount (' + p.discount_percent + '% / ' +
                    dur + ' month' + (dur > 1 ? 's' : '') + ')');
                setText('discountValue', '-' + s + fmt(p.discount_amount) + '/month');
            }

            // 3) Monthly price after discount
            showRow('monthlyAfterRow', hasDiscount);
            if (hasDiscount) {
                setText('monthlyPriceValue', s + fmt(p.monthly_price) + '/month');
            }

            // 4) Total discount over the term
            showRow('totalDiscountRow', hasDiscount && dur > 1);
            if (hasDiscount && dur > 1) {
                setText('totalDiscountValue', '-' + s + fmt(totalDiscount));
            }

            // Setup fee (one-time)
            showRow('setupRow', p.setup_fee > 0);
            if (p.setup_fee > 0) {
                setText('setupFeeValue', s + fmt(p.setup_fee));
            }

            // 5) Total before discount (struck through), only when discounted
            showRow('totalBeforeRow', hasDiscount);
            if (hasDiscount) {
                setText('totalBeforeValue', s + fmt(totalBefore));
            }

            // 6) Final total
            setText('totalPriceValue', s + fmt(total));
            setText('totalBilled', dur === 1 ? 'billed monthly · cancel anytime'
                : 'paid upfront · covers ' + dur + ' months');

            var ctaEl = document.getElementById('customizeCta');
            if (ctaEl) {
                ctaEl.href = '/saas/signup?package_id=' + p.package_id +
                    (p.duration_months > 1 ? '&duration=' + p.duration_months : '');
            }

            /* Modules */
            if (p.modules && p.modules.length) {
                modSection.style.display = 'block';
                modGrid.innerHTML = '';
                p.modules.forEach(function (m) {
                    var chip = document.createElement('div');
                    chip.className = 'module-chip';
                    chip.innerHTML =
                        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
                        'stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/>' +
                        '</svg><span>' + escHtml(m.name) + '</span>';
                    modGrid.appendChild(chip);
                });
            } else {
                modSection.style.display = 'none';
            }
        }
    });
})();
