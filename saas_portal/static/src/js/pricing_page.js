/**
 * SaaS Pricing Page — Billing Toggle + Customize Your Plan
 */
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

        /* ── Render duration buttons dynamically ── */
        function renderDurs(pkgId) {
            durContainer.innerHTML = '';
            var tiers = DURATION_DATA[pkgId] || [];
            if (!tiers.length) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'dur-btn active';
                b.setAttribute('data-months', '1');
                b.textContent = 'Monthly';
                durContainer.appendChild(b);
                selDur = 1;
                b.addEventListener('click', onDurClick);
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

            document.getElementById('basePriceLabel').textContent =
                'Base Price (' + p.package_name + ')';
            document.getElementById('basePriceValue').textContent =
                s + fmt(p.base_price) + '/month';

            var dRow = document.getElementById('discountRow');
            if (p.discount_percent > 0) {
                dRow.style.display = 'flex';
                document.getElementById('discountLabel').textContent =
                    'Duration Discount (' + p.label + ')';
                document.getElementById('discountValue').textContent =
                    '-' + s + fmt(p.discount_amount) + '/month';
            } else {
                dRow.style.display = 'none';
            }

            document.getElementById('monthlyPriceValue').textContent =
                s + fmt(p.monthly_price) + '/month';
            document.getElementById('durationValue').textContent =
                p.duration_months + ' month' + (p.duration_months > 1 ? 's' : '');

            var sRow = document.getElementById('setupRow');
            if (p.setup_fee > 0) {
                sRow.style.display = 'flex';
                document.getElementById('setupFeeValue').textContent = s + fmt(p.setup_fee);
            } else {
                sRow.style.display = 'none';
            }

            var total = p.total_price + (p.setup_fee || 0);
            document.getElementById('totalPriceValue').textContent = s + fmt(total);

            var bText = p.duration_months === 1
                ? 'Billed monthly'
                : 'Billed every ' + p.duration_months + ' months';
            document.getElementById('totalBilled').textContent = bText;

            document.getElementById('customizeCta').href =
                '/saas/signup?package_id=' + p.package_id +
                (p.duration_months > 1 ? '&duration=' + p.duration_months : '');

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
