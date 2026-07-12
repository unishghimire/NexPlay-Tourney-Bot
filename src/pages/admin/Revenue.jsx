import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import StatCard from "@/components/admin/StatCard";
import StatusBadge from "@/components/admin/StatusBadge";
import { DollarSign, Server as ServerIcon, Clock } from "lucide-react";

export default function Revenue() {
  const [subs, setSubs] = useState([]);
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [subscriptions, planList] = await Promise.all([
        base44.entities.Subscription.list("-created_date", 500),
        base44.entities.Plan.list("-created_date", 50),
      ]);
      setSubs(subscriptions);
      setPlans(planList);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const activeSubs = subs.filter((s) => s.status === "active");
  const trialSubs = subs.filter((s) => s.status === "trial");
  const mrr = activeSubs.reduce((sum, s) => {
    const plan = plans.find((p) => p.name === s.plan_name);
    return sum + (plan?.monthly_price || 0);
  }, 0);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="w-8 h-8 border-4 border-gray-700 rounded-full animate-spin" style={{ borderTopColor: "#FFD700" }} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Revenue" subtitle="Subscription billing and MRR overview" />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <StatCard label="MRR" value={`$${mrr.toFixed(0)}`} icon={DollarSign} accent="gold" sub="Monthly recurring revenue" />
        <StatCard label="Paid Servers" value={activeSubs.length} icon={ServerIcon} accent="green" sub="Active subscriptions" />
        <StatCard label="Trial Servers" value={trialSubs.length} icon={Clock} accent="purple" sub="On free trial" />
      </div>

      <div className="np-bg-card rounded-xl border np-border overflow-hidden">
        <div className="px-6 py-4 border-b np-border">
          <h3 className="text-sm font-semibold text-white">Subscriptions</h3>
        </div>
        <div className="overflow-x-auto np-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b np-border text-gray-500">
                <th className="text-left px-4 py-3 font-semibold">Server</th>
                <th className="text-left px-4 py-3 font-semibold">Plan</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-left px-4 py-3 font-semibold">Started</th>
                <th className="text-left px-4 py-3 font-semibold">Renews</th>
                <th className="text-left px-4 py-3 font-semibold">Billing Notes</th>
              </tr>
            </thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.id} className="border-b np-border hover:np-bg-card-hover transition-colors">
                  <td className="px-4 py-3 font-medium text-white">{s.guild_name}</td>
                  <td className="px-4 py-3 np-text-gold font-semibold">{s.plan_name}</td>
                  <td className="px-4 py-3"><StatusBadge status={s.status} /></td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{s.started_at ? new Date(s.started_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{s.renews_at ? new Date(s.renews_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs max-w-xs truncate">{s.billing_notes || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {subs.length === 0 && <div className="py-12 text-center text-gray-500 text-sm">No subscriptions yet</div>}
      </div>
    </div>
  );
}