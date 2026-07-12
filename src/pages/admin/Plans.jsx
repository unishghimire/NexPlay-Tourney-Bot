import React, { useState, useEffect, useCallback } from "react";
import { base44 } from "@/api/base44Client";
import PageHeader from "@/components/admin/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, Check, X, Pencil } from "lucide-react";

export default function Plans() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [editValues, setEditValues] = useState({});
  const [addModal, setAddModal] = useState(false);
  const [newPlan, setNewPlan] = useState({ name: "Basic", monthly_price: 0, yearly_price: 0, tournament_limit: 10, features: [], active: true });

  const load = useCallback(async () => {
    try {
      const data = await base44.entities.Plan.list("-created_date", 50);
      setPlans(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startEdit = (plan) => {
    setEditingId(plan.id);
    setEditValues({ monthly_price: plan.monthly_price, yearly_price: plan.yearly_price, tournament_limit: plan.tournament_limit, features: plan.features || [] });
  };

  const saveEdit = async (id) => {
    try {
      await base44.entities.Plan.update(id, editValues);
      setPlans((prev) => prev.map((p) => (p.id === id ? { ...p, ...editValues } : p)));
      toast.success("Plan updated");
      setEditingId(null);
    } catch (e) {
      toast.error("Failed to update plan");
    }
  };

  const toggleActive = async (plan) => {
    try {
      await base44.entities.Plan.update(plan.id, { active: !plan.active });
      setPlans((prev) => prev.map((p) => (p.id === plan.id ? { ...p, active: !p.active } : p)));
    } catch (e) {
      toast.error("Failed to toggle");
    }
  };

  const handleAdd = async () => {
    try {
      await base44.entities.Plan.create(newPlan);
      toast.success("Plan created");
      setAddModal(false);
      setNewPlan({ name: "Basic", monthly_price: 0, yearly_price: 0, tournament_limit: 10, features: [], active: true });
      load();
    } catch (e) {
      toast.error("Failed to create plan");
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
        title="Plans"
        subtitle="Manage subscription tiers"
        actions={<Button onClick={() => setAddModal(true)} className="np-bg-gold text-black hover:brightness-110"><Plus className="w-4 h-4 mr-2" /> Add Plan</Button>}
      />

      <div className="np-bg-card rounded-xl border np-border overflow-hidden">
        <div className="overflow-x-auto np-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b np-border text-gray-500">
                <th className="text-left px-4 py-3 font-semibold">Name</th>
                <th className="text-left px-4 py-3 font-semibold">Monthly Price</th>
                <th className="text-left px-4 py-3 font-semibold">Yearly Price</th>
                <th className="text-left px-4 py-3 font-semibold">Tournament Limit</th>
                <th className="text-left px-4 py-3 font-semibold">Features</th>
                <th className="text-left px-4 py-3 font-semibold">Active</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {plans.map((plan) => (
                <tr key={plan.id} className="border-b np-border">
                  <td className="px-4 py-3 font-medium np-text-gold">{plan.name}</td>
                  {editingId === plan.id ? (
                    <>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={editValues.monthly_price}
                          onChange={(e) => setEditValues({ ...editValues, monthly_price: Number(e.target.value) })}
                          className="np-bg-base border-np-border text-white w-24 h-8"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={editValues.yearly_price}
                          onChange={(e) => setEditValues({ ...editValues, yearly_price: Number(e.target.value) })}
                          className="np-bg-base border-np-border text-white w-24 h-8"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={editValues.tournament_limit}
                          onChange={(e) => setEditValues({ ...editValues, tournament_limit: Number(e.target.value) })}
                          className="np-bg-base border-np-border text-white w-24 h-8"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          value={(editValues.features || []).join(", ")}
                          onChange={(e) => setEditValues({ ...editValues, features: e.target.value.split(",").map((f) => f.trim()).filter(Boolean) })}
                          className="np-bg-base border-np-border text-white h-8"
                          placeholder="feature1, feature2"
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-4 py-3 text-white">${plan.monthly_price}</td>
                      <td className="px-4 py-3 text-white">${plan.yearly_price}</td>
                      <td className="px-4 py-3 text-white">{plan.tournament_limit}</td>
                      <td className="px-4 py-3 text-gray-400 text-xs max-w-xs truncate">{(plan.features || []).join(", ") || "—"}</td>
                    </>
                  )}
                  <td className="px-4 py-3">
                    <Switch checked={plan.active} onCheckedChange={() => toggleActive(plan)} />
                  </td>
                  <td className="px-4 py-3">
                    {editingId === plan.id ? (
                      <div className="flex gap-1">
                        <Button size="icon" variant="ghost" onClick={() => saveEdit(plan.id)} className="text-emerald-400 hover:np-bg-card-hover h-8 w-8">
                          <Check className="w-4 h-4" />
                        </Button>
                        <Button size="icon" variant="ghost" onClick={() => setEditingId(null)} className="text-red-400 hover:np-bg-card-hover h-8 w-8">
                          <X className="w-4 h-4" />
                        </Button>
                      </div>
                    ) : (
                      <Button size="icon" variant="ghost" onClick={() => startEdit(plan)} className="text-gray-400 hover:np-bg-card-hover h-8 w-8">
                        <Pencil className="w-4 h-4" />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {plans.length === 0 && <div className="py-12 text-center text-gray-500 text-sm">No plans yet</div>}
      </div>

      {/* Add Plan Modal */}
      <Dialog open={addModal} onOpenChange={setAddModal}>
        <DialogContent className="np-bg-card border-np-border text-white">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-white">Add Plan</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Plan Name</label>
              <select
                value={newPlan.name}
                onChange={(e) => setNewPlan({ ...newPlan, name: e.target.value })}
                className="w-full np-bg-base border-np-border text-white rounded-md px-3 py-2 text-sm"
              >
                <option value="Free Trial">Free Trial</option>
                <option value="Basic">Basic</option>
                <option value="Pro">Pro</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Monthly Price ($)</label>
                <Input type="number" value={newPlan.monthly_price} onChange={(e) => setNewPlan({ ...newPlan, monthly_price: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Yearly Price ($)</label>
                <Input type="number" value={newPlan.yearly_price} onChange={(e) => setNewPlan({ ...newPlan, yearly_price: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Tournament Limit</label>
              <Input type="number" value={newPlan.tournament_limit} onChange={(e) => setNewPlan({ ...newPlan, tournament_limit: Number(e.target.value) })} className="np-bg-base border-np-border text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Features (comma-separated)</label>
              <Input
                value={(newPlan.features || []).join(", ")}
                onChange={(e) => setNewPlan({ ...newPlan, features: e.target.value.split(",").map((f) => f.trim()).filter(Boolean) })}
                className="np-bg-base border-np-border text-white"
                placeholder="feature1, feature2"
              />
            </div>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" onClick={() => setAddModal(false)} className="border-np-border text-white">Cancel</Button>
            <Button onClick={handleAdd} className="np-bg-gold text-black hover:brightness-110">Create Plan</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}