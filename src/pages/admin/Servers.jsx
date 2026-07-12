import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import StatusBadge from "@/components/admin/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { Search, ChevronRight, Gavel, ArrowUpCircle, ShieldBan, ShieldCheck } from "lucide-react";

export default function Servers() {
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [detailData, setDetailData] = useState({ tournaments: [], messages: [], loading: false });
  const [banModal, setBanModal] = useState(false);
  const [banReason, setBanReason] = useState("");
  const [upgradeModal, setUpgradeModal] = useState(false);
  const [upgradePlan, setUpgradePlan] = useState("Basic");

  const loadServers = useCallback(async () => {
    try {
      const data = await base44.entities.Server.list("-created_date", 500);
      setServers(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  const loadDetail = async (server) => {
    setSelected(server);
    setDetailData({ tournaments: [], messages: [], loading: true });
    try {
      const [tourns, msgs] = await Promise.all([
        base44.entities.Tournament.filter({ guild_id: server.guild_id }, "-created_date", 50),
        base44.entities.SupportMessage.filter({ guild_id: server.guild_id }, "-created_date", 50),
      ]);
      setDetailData({ tournaments: tourns, messages: msgs, loading: false });
    } catch (e) {
      setDetailData({ tournaments: [], messages: [], loading: false });
    }
  };

  const handleAddTrial = async () => {
    try {
      await base44.entities.Server.update(selected.id, {
        subscription_status: "trial",
        plan_name: "Free Trial",
        tournament_limit: 3,
      });
      await base44.entities.AdminNotification.create({
        type: "trial_expiry",
        severity: "info",
        message: `30-day trial added to ${selected.guild_name}`,
        read_by_unish: false,
      });
      toast.success("30-day trial added");
      setServers((prev) => prev.map((s) => (s.id === selected.id ? { ...s, subscription_status: "trial", plan_name: "Free Trial", tournament_limit: 3 } : s)));
      setSelected(null);
    } catch (e) {
      toast.error("Failed to add trial");
    }
  };

  const handleUpgrade = async () => {
    try {
      await base44.entities.Server.update(selected.id, {
        subscription_status: "active",
        plan_name: upgradePlan,
      });
      await base44.entities.AdminNotification.create({
        type: "upgrade",
        severity: "info",
        message: `${selected.guild_name} upgraded to ${upgradePlan} plan`,
        read_by_unish: false,
      });
      toast.success(`Upgraded to ${upgradePlan}`);
      setServers((prev) => prev.map((s) => (s.id === selected.id ? { ...s, subscription_status: "active", plan_name: upgradePlan } : s)));
      setUpgradeModal(false);
      setSelected(null);
    } catch (e) {
      toast.error("Failed to upgrade");
    }
  };

  const handleBan = async () => {
    try {
      await base44.entities.Server.update(selected.id, {
        subscription_status: "banned",
        ban_reason: banReason,
      });
      await base44.entities.AdminNotification.create({
        type: "ban",
        severity: "critical",
        message: `${selected.guild_name} banned: ${banReason}`,
        read_by_unish: false,
      });
      toast.success("Server banned");
      setServers((prev) => prev.map((s) => (s.id === selected.id ? { ...s, subscription_status: "banned", ban_reason: banReason } : s)));
      setBanModal(false);
      setBanReason("");
      setSelected(null);
    } catch (e) {
      toast.error("Failed to ban server");
    }
  };

  const handleUnban = async () => {
    try {
      await base44.entities.Server.update(selected.id, {
        subscription_status: "trial",
        ban_reason: "",
      });
      toast.success("Server unbanned");
      setServers((prev) => prev.map((s) => (s.id === selected.id ? { ...s, subscription_status: "trial", ban_reason: "" } : s)));
      setSelected(null);
    } catch (e) {
      toast.error("Failed to unban");
    }
  };

  const filtered = servers.filter((s) => {
    const matchFilter = filter === "all" || s.subscription_status === filter;
    const matchSearch = !search || s.guild_name?.toLowerCase().includes(search.toLowerCase()) || s.owner_name?.toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="w-8 h-8 border-4 border-gray-700 rounded-full animate-spin" style={{ borderTopColor: "#FFD700" }} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Servers" subtitle={`${servers.length} servers on the platform`} />

      {/* Filter Bar */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <Input
            placeholder="Search by guild or owner name..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10 np-bg-card border-np-border text-white placeholder:text-gray-600"
          />
        </div>
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="w-full sm:w-48 np-bg-card border-np-border text-white">
            <SelectValue placeholder="Filter by status" />
          </SelectTrigger>
          <SelectContent className="np-bg-card border-np-border">
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="trial">Trial</SelectItem>
            <SelectItem value="expired">Expired</SelectItem>
            <SelectItem value="banned">Banned</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <div className="np-bg-card rounded-xl border np-border overflow-hidden">
        <div className="overflow-x-auto np-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b np-border text-gray-500">
                <th className="text-left px-4 py-3 font-semibold">Guild Name</th>
                <th className="text-left px-4 py-3 font-semibold">Owner</th>
                <th className="text-left px-4 py-3 font-semibold">Plan</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-left px-4 py-3 font-semibold">Tournaments</th>
                <th className="text-left px-4 py-3 font-semibold">Last Active</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => loadDetail(s)}
                  className="border-b np-border cursor-pointer hover:np-bg-card-hover transition-colors"
                >
                  <td className="px-4 py-3 font-medium text-white">{s.guild_name}</td>
                  <td className="px-4 py-3 text-gray-400">{s.owner_name}</td>
                  <td className="px-4 py-3 text-gray-300">{s.plan_name}</td>
                  <td className="px-4 py-3"><StatusBadge status={s.subscription_status} /></td>
                  <td className="px-4 py-3 text-gray-400">
                    <span className="text-white">{s.tournaments_used || 0}</span> / {s.tournament_limit || 0}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {s.last_active ? new Date(s.last_active).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-3"><ChevronRight className="w-4 h-4 text-gray-600" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length === 0 && (
          <div className="py-12 text-center text-gray-500 text-sm">No servers found</div>
        )}
      </div>

      {/* Detail Modal */}
      <Dialog open={!!selected && !banModal && !upgradeModal} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className="np-bg-card border-np-border text-white max-w-3xl max-h-[85vh] overflow-y-auto np-scroll">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle className="text-xl font-bold text-white">{selected.guild_name}</DialogTitle>
              </DialogHeader>

              {/* Server Info */}
              <div className="grid grid-cols-2 gap-4 p-4 np-bg-base rounded-lg border np-border">
                <div><p className="text-xs text-gray-500">Guild ID</p><p className="text-sm text-white font-mono">{selected.guild_id}</p></div>
                <div><p className="text-xs text-gray-500">Owner</p><p className="text-sm text-white">{selected.owner_name}</p></div>
                <div><p className="text-xs text-gray-500">Plan</p><p className="text-sm np-text-gold font-semibold">{selected.plan_name}</p></div>
                <div><p className="text-xs text-gray-500">Status</p><StatusBadge status={selected.subscription_status} /></div>
                <div><p className="text-xs text-gray-500">Tournaments</p><p className="text-sm text-white">{selected.tournaments_used || 0} / {selected.tournament_limit || 0}</p></div>
                <div><p className="text-xs text-gray-500">Members</p><p className="text-sm text-white">{selected.member_count || 0}</p></div>
                {selected.ban_reason && <div className="col-span-2"><p className="text-xs text-gray-500">Ban Reason</p><p className="text-sm text-red-400">{selected.ban_reason}</p></div>}
              </div>

              {/* Actions */}
              <div className="flex flex-wrap gap-2">
                {selected.subscription_status === "banned" ? (
                  <Button onClick={handleUnban} className="bg-emerald-600 hover:bg-emerald-700 text-white">
                    <ShieldCheck className="w-4 h-4 mr-2" /> Unban Server
                  </Button>
                ) : (
                  <>
                    <Button onClick={handleAddTrial} variant="outline" className="border-np-border text-white hover:np-bg-card-hover">
                      <ArrowUpCircle className="w-4 h-4 mr-2" /> Add 30-day Trial
                    </Button>
                    <Button onClick={() => setUpgradeModal(true)} className="np-bg-gold text-black hover:brightness-110">
                      <ArrowUpCircle className="w-4 h-4 mr-2" /> Upgrade Plan
                    </Button>
                    <Button onClick={() => setBanModal(true)} variant="outline" className="border-red-700/50 text-red-400 hover:bg-red-900/20">
                      <ShieldBan className="w-4 h-4 mr-2" /> Ban Server
                    </Button>
                  </>
                )}
              </div>

              {/* Tournaments */}
              <div>
                <h4 className="text-sm font-semibold text-white mb-2">Tournaments ({detailData.tournaments.length})</h4>
                {detailData.loading ? (
                  <p className="text-xs text-gray-500">Loading...</p>
                ) : detailData.tournaments.length === 0 ? (
                  <p className="text-xs text-gray-500">No tournaments</p>
                ) : (
                  <div className="space-y-2 max-h-40 overflow-y-auto np-scroll">
                    {detailData.tournaments.map((t) => (
                      <div key={t.id} className="flex items-center justify-between p-2 np-bg-base rounded border np-border text-xs">
                        <span className="text-white">{t.name}</span>
                        <StatusBadge status={t.status} />
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Support Messages */}
              <div>
                <h4 className="text-sm font-semibold text-white mb-2">Support Messages ({detailData.messages.length})</h4>
                {detailData.loading ? (
                  <p className="text-xs text-gray-500">Loading...</p>
                ) : detailData.messages.length === 0 ? (
                  <p className="text-xs text-gray-500">No support messages</p>
                ) : (
                  <div className="space-y-2 max-h-40 overflow-y-auto np-scroll">
                    {detailData.messages.map((m) => (
                      <div key={m.id} className="flex items-center justify-between p-2 np-bg-base rounded border np-border text-xs">
                        <span className="text-gray-300 truncate flex-1 mr-2">{m.message}</span>
                        <StatusBadge status={m.status} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Ban Modal */}
      <Dialog open={banModal} onOpenChange={(o) => { setBanModal(o); if (!o) setBanReason(""); }}>
        <DialogContent className="np-bg-card border-np-border text-white">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-white">Ban {selected?.guild_name}</DialogTitle>
          </DialogHeader>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Reason for banning</label>
            <Textarea
              value={banReason}
              onChange={(e) => setBanReason(e.target.value)}
              placeholder="Enter ban reason..."
              className="np-bg-base border-np-border text-white"
              rows={3}
            />
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" onClick={() => setBanModal(false)} className="border-np-border text-white">Cancel</Button>
            <Button onClick={handleBan} disabled={!banReason.trim()} className="bg-red-600 hover:bg-red-700 text-white">
              <Gavel className="w-4 h-4 mr-2" /> Confirm Ban
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Upgrade Modal */}
      <Dialog open={upgradeModal} onOpenChange={setUpgradeModal}>
        <DialogContent className="np-bg-card border-np-border text-white">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-white">Upgrade {selected?.guild_name}</DialogTitle>
          </DialogHeader>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Select Plan</label>
            <Select value={upgradePlan} onValueChange={setUpgradePlan}>
              <SelectTrigger className="np-bg-base border-np-border text-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="np-bg-card border-np-border">
                <SelectItem value="Basic">Basic</SelectItem>
                <SelectItem value="Pro">Pro</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" onClick={() => setUpgradeModal(false)} className="border-np-border text-white">Cancel</Button>
            <Button onClick={handleUpgrade} className="np-bg-gold text-black hover:brightness-110">Confirm Upgrade</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}