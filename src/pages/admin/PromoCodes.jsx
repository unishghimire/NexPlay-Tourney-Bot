import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, Copy, Trash2, Check } from "lucide-react";

export default function PromoCodes() {
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [addModal, setAddModal] = useState(false);
  const [newCode, setNewCode] = useState({ code: "", discount_percent: 10, max_uses: 100, expires_at: "", active: true });
  const [copiedId, setCopiedId] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await base44.entities.PromoCode.list("-created_date", 100);
      setCodes(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const copyCode = async (code, id) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopiedId(id);
      toast.success("Code copied to clipboard");
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      toast.error("Failed to copy");
    }
  };

  const toggleActive = async (item) => {
    try {
      await base44.entities.PromoCode.update(item.id, { active: !item.active });
      setCodes((prev) => prev.map((c) => (c.id === item.id ? { ...c, active: !c.active } : c)));
    } catch (e) {
      toast.error("Failed to toggle");
    }
  };

  const handleDelete = async (id) => {
    try {
      await base44.entities.PromoCode.delete(id);
      setCodes((prev) => prev.filter((c) => c.id !== id));
      toast.success("Promo code deleted");
    } catch (e) {
      toast.error("Failed to delete");
    }
  };

  const handleAdd = async () => {
    if (!newCode.code.trim()) {
      toast.error("Code is required");
      return;
    }
    try {
      await base44.entities.PromoCode.create(newCode);
      toast.success("Promo code created");
      setAddModal(false);
      setNewCode({ code: "", discount_percent: 10, max_uses: 100, expires_at: "", active: true });
      load();
    } catch (e) {
      toast.error("Failed to create promo code");
    }
  };

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
        title="Promo Codes"
        subtitle={`${codes.length} promo codes`}
        actions={<Button onClick={() => setAddModal(true)} className="np-bg-gold text-black hover:brightness-110"><Plus className="w-4 h-4 mr-2" /> Create Promo Code</Button>}
      />

      <div className="np-bg-card rounded-xl border np-border overflow-hidden">
        <div className="overflow-x-auto np-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b np-border text-gray-500">
                <th className="text-left px-4 py-3 font-semibold">Code</th>
                <th className="text-left px-4 py-3 font-semibold">Discount</th>
                <th className="text-left px-4 py-3 font-semibold">Max Uses</th>
                <th className="text-left px-4 py-3 font-semibold">Used</th>
                <th className="text-left px-4 py-3 font-semibold">Expires</th>
                <th className="text-left px-4 py-3 font-semibold">Active</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {codes.map((c) => (
                <tr key={c.id} className="border-b np-border hover:np-bg-card-hover transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold np-text-gold">{c.code}</span>
                      <button onClick={() => copyCode(c.code, c.id)} className="text-gray-500 hover:np-text-gold transition-colors">
                        {copiedId === c.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-white">{c.discount_percent}%</td>
                  <td className="px-4 py-3 text-gray-400">{c.max_uses}</td>
                  <td className="px-4 py-3 text-gray-400">{c.used_count || 0}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{c.expires_at ? new Date(c.expires_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3"><Switch checked={c.active} onCheckedChange={() => toggleActive(c)} /></td>
                  <td className="px-4 py-3">
                    <Button size="icon" variant="ghost" onClick={() => handleDelete(c.id)} className="text-red-400 hover:bg-red-900/20 h-8 w-8">
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {codes.length === 0 && <div className="py-12 text-center text-gray-500 text-sm">No promo codes yet</div>}
      </div>

      {/* Create Modal */}
      <Dialog open={addModal} onOpenChange={setAddModal}>
        <DialogContent className="np-bg-card border-np-border text-white">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-white">Create Promo Code</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Code</label>
              <Input
                value={newCode.code}
                onChange={(e) => setNewCode({ ...newCode, code: e.target.value.toUpperCase() })}
                className="np-bg-base border-np-border text-white font-mono"
                placeholder="SUMMER25"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Discount %</label>
                <Input type="number" value={newCode.discount_percent} onChange={(e) => setNewCode({ ...newCode, discount_percent: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Max Uses</label>
                <Input type="number" value={newCode.max_uses} onChange={(e) => setNewCode({ ...newCode, max_uses: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Expires At</label>
              <Input type="date" value={newCode.expires_at} onChange={(e) => setNewCode({ ...newCode, expires_at: e.target.value })} className="np-bg-base border-np-border text-white" />
            </div>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" onClick={() => setAddModal(false)} className="border-np-border text-white">Cancel</Button>
            <Button onClick={handleAdd} className="np-bg-gold text-black hover:brightness-110">Create</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}