import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { base44 } from "@/api/base44Client";
import StatCard from "@/components/admin/StatCard";
import StatusBadge from "@/components/admin/StatusBadge";
import PageHeader from "@/components/admin/PageHeader";
import { Button } from "@/components/ui/button";
import { Server as ServerIcon, Activity, Clock, DollarSign, Trophy, Bell, Plus, Ticket } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from "recharts";

export default function Dashboard() {
  const navigate = useNavigate();
  const [stats, setStats] = useState({ total: 0, active: 0, trial: 0, mrr: 0, tournaments: 0, unread: 0 });
  const [planDist, setPlanDist] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    try {
      const [servers, plans, tournaments, notifications] = await Promise.all([
        base44.entities.Server.list("-created_date", 500),
        base44.entities.Plan.list("-created_date", 50),
        base44.entities.Tournament.filter({ status: "active" }, "-created_date", 500),
        base44.entities.AdminNotification.filter({ read_by_unish: false }, "-created_date", 5),
      ]);

      const activeServers = servers.filter((s) => s.subscription_status === "active");
      const trialServers = servers.filter((s) => s.subscription_status === "trial");

      const mrr = activeServers.reduce((sum, s) => {
        const plan = plans.find((p) => p.name === s.plan_name);
        return sum + (plan?.monthly_price || 0);
      }, 0);

      setStats({
        total: servers.length,
        active: activeServers.length,
        trial: trialServers.length,
        mrr,
        tournaments: tournaments.length,
        unread: notifications.length,
      });

      const dist = ["Free Trial", "Basic", "Pro"].map((planName) => ({
        name: planName,
        value: servers.filter((s) => s.plan_name === planName).length,
      }));
      setPlanDist(dist);
      setNotifications(notifications);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const COLORS = ["#6b7280", "#9B59B6", "#FFD700"];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="w-8 h-8 border-4 border-gray-700 border-t-np-bg-gold rounded-full animate-spin" style={{ borderTopColor: "#FFD700" }} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Overview of your NexPlay Tournament Bot platform" />

      {/* Stat Tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
        <StatCard label="Total Servers" value={stats.total} icon={ServerIcon} accent="gold" />
        <StatCard label="Active Servers" value={stats.active} icon={Activity} accent="green" />
        <StatCard label="Trial Servers" value={stats.trial} icon={Clock} accent="purple" />
        <StatCard label="MRR" value={`$${stats.mrr.toFixed(0)}`} icon={DollarSign} accent="gold" />
        <StatCard label="Active Tournaments" value={stats.tournaments} icon={Trophy} accent="purple" />
        <StatCard label="Unread Alerts" value={stats.unread} icon={Bell} accent="red" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        {/* Plan Distribution */}
        <div className="np-bg-card rounded-xl border np-border p-6">
          <h3 className="text-sm font-semibold text-white mb-4">Plan Distribution</h3>
          {planDist.every((p) => p.value === 0) ? (
            <div className="h-64 flex items-center justify-center text-gray-500 text-sm">No server data yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={planDist} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3}>
                  {planDist.map((_, i) => (
                    <Cell key={i} fill={COLORS[i]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a44", borderRadius: 8, color: "#fff" }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Recent Notifications */}
        <div className="lg:col-span-2 np-bg-card rounded-xl border np-border p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white">Recent Notifications</h3>
            <Button variant="ghost" size="sm" className="text-gray-400 hover:text-white" onClick={() => navigate("/notifications")}>
              View All
            </Button>
          </div>
          {notifications.length === 0 ? (
            <div className="h-40 flex items-center justify-center text-gray-500 text-sm">All caught up — no unread notifications</div>
          ) : (
            <div className="space-y-3">
              {notifications.map((n) => (
                <div key={n.id} className="flex items-start gap-3 p-3 rounded-lg np-bg-base border np-border">
                  <StatusBadge severity={n.severity} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-200">{n.message}</p>
                    <p className="text-[10px] text-gray-500 mt-1">
                      {n.type.replace(/_/g, " ")} · {new Date(n.created_date).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="np-bg-card rounded-xl border np-border p-6">
        <h3 className="text-sm font-semibold text-white mb-4">Quick Actions</h3>
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => navigate("/servers")} className="np-bg-gold text-black hover:brightness-110 font-semibold">
            <Plus className="w-4 h-4 mr-2" /> Add Server
          </Button>
          <Button onClick={() => navigate("/promo-codes")} className="np-bg-purple text-white hover:brightness-110 font-semibold">
            <Ticket className="w-4 h-4 mr-2" /> Create Promo Code
          </Button>
        </div>
      </div>
    </div>
  );
}