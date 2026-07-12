import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import StatusBadge from "@/components/admin/StatusBadge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { Check, Bell } from "lucide-react";

export default function Notifications() {
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");

  const load = useCallback(async () => {
    try {
      const data = await base44.entities.AdminNotification.list("-created_date", 200);
      setNotifications(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const unsub = base44.entities.AdminNotification.subscribe(() => load());
    return unsub;
  }, [load]);

  const markAsRead = async (id) => {
    try {
      await base44.entities.AdminNotification.update(id, { read_by_unish: true });
      setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read_by_unish: true } : n)));
      toast.success("Marked as read");
    } catch (e) {
      toast.error("Failed to mark as read");
    }
  };

  const filtered = notifications.filter((n) => filter === "all" || n.type === filter);
  const unreadCount = notifications.filter((n) => !n.read_by_unish).length;

  const typeOptions = [
    { value: "all", label: "All Types" },
    { value: "billing", label: "Billing" },
    { value: "escalation", label: "Escalation" },
    { value: "api_error", label: "API Error" },
    { value: "ban", label: "Ban" },
    { value: "upgrade", label: "Upgrade" },
    { value: "trial_expiry", label: "Trial Expiry" },
    { value: "new_server", label: "New Server" },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="w-8 h-8 border-4 border-gray-700 rounded-full animate-spin" style={{ borderTopColor: "#FFD700" }} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Notifications"
        subtitle={`${unreadCount} unread of ${notifications.length} total`}
        actions={
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-48 np-bg-card border-np-border text-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="np-bg-card border-np-border">
              {typeOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />

      <div className="space-y-3">
        {filtered.length === 0 ? (
          <div className="np-bg-card rounded-xl border np-border py-16 text-center">
            <Bell className="w-10 h-10 text-gray-700 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">No notifications found</p>
          </div>
        ) : (
          filtered.map((n) => (
            <div
              key={n.id}
              className={`np-bg-card rounded-xl border np-border p-4 flex items-start gap-4 transition-all ${
                !n.read_by_unish ? "border-l-4 border-l-[#FFD700]" : "opacity-60"
              }`}
            >
              <div className="flex-shrink-0">
                <StatusBadge severity={n.severity} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">{n.type.replace(/_/g, " ")}</span>
                  <span className="text-[10px] text-gray-600">·</span>
                  <span className="text-[10px] text-gray-500">{new Date(n.created_date).toLocaleString()}</span>
                </div>
                <p className="text-sm text-gray-200">{n.message}</p>
              </div>
              {!n.read_by_unish && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => markAsRead(n.id)}
                  className="text-gray-400 hover:text-white hover:np-bg-card-hover flex-shrink-0"
                >
                  <Check className="w-4 h-4 mr-1" /> Mark Read
                </Button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}