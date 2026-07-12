import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus } from "lucide-react";

export default function Offers() {
  const [offers, setOffers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [addModal, setAddModal] = useState(false);
  const [newOffer, setNewOffer] = useState({ title: "", description: "", discount_percent: 0, starts_at: "", ends_at: "", active: true });

  const load = useCallback(async () => {
    try {
      const data = await base44.entities.Offer.list("-created_date", 100);
      setOffers(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleActive = async (offer) => {
    try {
      await base44.entities.Offer.update(offer.id, { active: !offer.active });
      setOffers((prev) => prev.map((o) => (o.id === offer.id ? { ...o, active: !o.active } : o)));
    } catch (e) {
      toast.error("Failed to toggle");
    }
  };

  const handleAdd = async () => {
    if (!newOffer.title.trim()) {
      toast.error("Title is required");
      return;
    }
    try {
      await base44.entities.Offer.create(newOffer);
      toast.success("Offer created");
      setAddModal(false);
      setNewOffer({ title: "", description: "", discount_percent: 0, starts_at: "", ends_at: "", active: true });
      load();
    } catch (e) {
      toast.error("Failed to create offer");
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
        title="Offers"
        subtitle={`${offers.length} promotional offers`}
        actions={<Button onClick={() => setAddModal(true)} className="np-bg-gold text-black hover:brightness-110"><Plus className="w-4 h-4 mr-2" /> Create Offer</Button>}
      />

      <div className="np-bg-card rounded-xl border np-border overflow-hidden">
        <div className="overflow-x-auto np-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b np-border text-gray-500">
                <th className="text-left px-4 py-3 font-semibold">Title</th>
                <th className="text-left px-4 py-3 font-semibold">Discount</th>
                <th className="text-left px-4 py-3 font-semibold">Start Date</th>
                <th className="text-left px-4 py-3 font-semibold">End Date</th>
                <th className="text-left px-4 py-3 font-semibold">Active</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id} className="border-b np-border hover:np-bg-card-hover transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-white">{o.title}</p>
                    {o.description && <p className="text-xs text-gray-500 mt-0.5 truncate max-w-xs">{o.description}</p>}
                  </td>
                  <td className="px-4 py-3"><span className="np-text-gold font-semibold">{o.discount_percent}%</span></td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{o.starts_at ? new Date(o.starts_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{o.ends_at ? new Date(o.ends_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3"><Switch checked={o.active} onCheckedChange={() => toggleActive(o)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {offers.length === 0 && <div className="py-12 text-center text-gray-500 text-sm">No offers yet</div>}
      </div>

      {/* Create Modal */}
      <Dialog open={addModal} onOpenChange={setAddModal}>
        <DialogContent className="np-bg-card border-np-border text-white max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-white">Create Offer</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Title</label>
              <Input value={newOffer.title} onChange={(e) => setNewOffer({ ...newOffer, title: e.target.value })} className="np-bg-base border-np-border text-white" placeholder="Summer Sale" />
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Description</label>
              <Textarea value={newOffer.description} onChange={(e) => setNewOffer({ ...newOffer, description: e.target.value })} className="np-bg-base border-np-border text-white" rows={2} placeholder="Offer description..." />
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Discount %</label>
              <Input type="number" value={newOffer.discount_percent} onChange={(e) => setNewOffer({ ...newOffer, discount_percent: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Start Date</label>
                <Input type="date" value={newOffer.starts_at} onChange={(e) => setNewOffer({ ...newOffer, starts_at: e.target.value })} className="np-bg-base border-np-border text-white" />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">End Date</label>
                <Input type="date" value={newOffer.ends_at} onChange={(e) => setNewOffer({ ...newOffer, ends_at: e.target.value })} className="np-bg-base border-np-border text-white" />
              </div>
            </div>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" onClick={() => setAddModal(false)} className="border-np-border text-white">Cancel</Button>
            <Button onClick={handleAdd} className="np-bg-gold text-black hover:brightness-110">Create Offer</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}