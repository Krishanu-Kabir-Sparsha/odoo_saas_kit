/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

class SaasDashboard extends Component {
    static template = "saas_admin.DashboardMain";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            data: null,
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });

        onMounted(() => {
            if (this.state.data) {
                this._renderCharts();
            }
        });
    }

    async loadDashboardData() {
        try {
            const data = await this.orm.call(
                "saas.admin.mixin",
                "get_dashboard_data",
                []
            );
            this.state.data = data;
            this.state.loading = false;
            setTimeout(() => this._renderCharts(), 100);
        } catch (e) {
            console.error("Dashboard data load failed:", e);
            this.state.loading = false;
        }
    }

    async onRefresh() {
        this.state.loading = true;
        await this.loadDashboardData();
    }

    // ─── Format Helpers ───

    formatCurrency(val) {
        if (val >= 1000000) return "৳" + (val / 1000000).toFixed(1) + "M";
        if (val >= 1000) return "৳" + (val / 1000).toFixed(1) + "K";
        return "৳" + (val || 0).toFixed(0);
    }

    formatNumber(val) {
        if (val >= 1000000) return (val / 1000000).toFixed(1) + "M";
        if (val >= 1000) return (val / 1000).toFixed(1) + "K";
        return (val || 0).toLocaleString();
    }

    getHealthColor(val) {
        if (val > 85) return "red";
        if (val > 60) return "orange";
        return "green";
    }

    getSubPct(count) {
        const total = this.state.data?.total_subscriptions || 1;
        return ((count / total) * 100).toFixed(0);
    }

    // ─── Clickable KPI Cards ───

    onClickActive() {
        this._openSubscriptions([["state", "=", "active"]], "Active Subscriptions");
    }

    onClickPending() {
        this._openSubscriptions([["state", "=", "pending"]], "Pending Payment");
    }

    onClickRevenue() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Paid Invoices",
            res_model: "account.move",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["move_type", "=", "out_invoice"],
                ["payment_state", "=", "paid"],
            ],
        });
    }

    onClickPoints() {
        this.onOpenPoints();
    }

    // ─── Breakdown Clicks ───

    onClickBreakdown(state) {
        const labels = {
            active: "Active Subscriptions",
            pending: "Pending Subscriptions",
            suspended: "Suspended Subscriptions",
            provisioning_failed: "Failed Provisioning",
            cancelled: "Cancelled Subscriptions",
        };
        if (state === "cancelled") {
            this._openSubscriptions(
                [["state", "in", ["cancelled", "force_cancelled"]]],
                labels[state]
            );
        } else {
            this._openSubscriptions([["state", "=", state]], labels[state]);
        }
    }

    // ─── Quick Actions ───

    onOpenSubscriptions() {
        this._openSubscriptions([], "All Subscriptions");
    }

    onOpenPackages() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Packages",
            res_model: "saas.package",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    onOpenPoints() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Points Transactions",
            res_model: "saas.points.transaction",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    onOpenPointsFiltered(type) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Points — ${type}`,
            res_model: "saas.points.transaction",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["transaction_type", "=", type]],
        });
    }

    onOpenPointsBalance() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Partner Points",
            res_model: "saas.partner.points",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    onOpenInvoices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Invoices",
            res_model: "account.move",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["move_type", "=", "out_invoice"]],
        });
    }

    onOpenProvisioning() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Provisioning History",
            res_model: "tenant.provisioner",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    onOpenProvisioningFiltered(state) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Provisioning — ${state}`,
            res_model: "tenant.provisioner",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", state]],
        });
    }

    onOpenHealthLog() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "System Health History",
            res_model: "saas.system.health",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    // ─── Package Click ───

    onClickPackage(pkgName) {
        const pkg = (this.state.data?.package_stats || []).find(
            (p) => p.name === pkgName
        );
        if (pkg && pkg.id) {
            this._openSubscriptions(
                [["package_id", "=", pkg.id], ["state", "=", "active"]],
                `${pkgName} — Subscribers`
            );
        } else {
            this.onOpenPackages();
        }
    }

    // ─── Shared helper ───

    _openSubscriptions(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || "Subscriptions",
            res_model: "saas.subscription",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: domain,
        });
    }

    // ─── Charts ───

    _renderCharts() {
        const data = this.state.data;
        if (!data) return;
        this._renderRevenueChart(data.revenue_trend || []);
    }

    _renderRevenueChart(trend) {
        const canvas = document.getElementById("dashRevenueChart");
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        const labels = trend.map((d) => d.date);
        const values = trend.map((d) => d.amount);

        const W = (canvas.width = canvas.parentElement.offsetWidth);
        const H = (canvas.height = canvas.parentElement.offsetHeight);
        const pad = { top: 20, right: 20, bottom: 30, left: 50 };
        const chartW = W - pad.left - pad.right;
        const chartH = H - pad.top - pad.bottom;

        ctx.clearRect(0, 0, W, H);

        const maxVal = Math.max(...values, 1);

        // Grid
        ctx.strokeStyle = "#e2e8f0";
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad.top + (chartH / 4) * i;
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(W - pad.right, y);
            ctx.stroke();

            const val = maxVal - (maxVal / 4) * i;
            ctx.fillStyle = "#94a3b8";
            ctx.font = "11px Inter, sans-serif";
            ctx.textAlign = "right";
            ctx.fillText(
                val >= 1000 ? (val / 1000).toFixed(0) + "K" : val.toFixed(0),
                pad.left - 8,
                y + 4
            );
        }

        // X-axis labels
        ctx.textAlign = "center";
        labels.forEach((lbl, i) => {
            if (i % 5 === 0 || i === labels.length - 1) {
                const x = pad.left + (chartW / Math.max(labels.length - 1, 1)) * i;
                ctx.fillStyle = "#94a3b8";
                ctx.fillText(lbl, x, H - 6);
            }
        });

        if (values.length < 2) return;

        const points = values.map((v, i) => ({
            x: pad.left + (chartW / (values.length - 1)) * i,
            y: pad.top + chartH - (v / maxVal) * chartH,
        }));

        // Fill gradient
        const gradient = ctx.createLinearGradient(0, pad.top, 0, H - pad.bottom);
        gradient.addColorStop(0, "rgba(59, 130, 246, 0.15)");
        gradient.addColorStop(1, "rgba(59, 130, 246, 0)");

        ctx.beginPath();
        ctx.moveTo(points[0].x, H - pad.bottom);
        points.forEach((p) => ctx.lineTo(p.x, p.y));
        ctx.lineTo(points[points.length - 1].x, H - pad.bottom);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Line
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        points.forEach((p) => ctx.lineTo(p.x, p.y));
        ctx.strokeStyle = "#3b82f6";
        ctx.lineWidth = 2.5;
        ctx.lineJoin = "round";
        ctx.stroke();

        // Dots
        points.forEach((p) => {
            ctx.beginPath();
            ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = "#3b82f6";
            ctx.fill();
            ctx.beginPath();
            ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
            ctx.strokeStyle = "rgba(59,130,246,0.3)";
            ctx.lineWidth = 1.5;
            ctx.stroke();
        });
    }
}

registry.category("actions").add("saas_admin.dashboard", SaasDashboard);
