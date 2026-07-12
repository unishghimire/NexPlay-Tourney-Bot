import React from "react";

export default function StatCard({ label, value, icon: Icon, accent = "gold", sub }) {
  const accentMap = {
    gold: { text: "np-text-gold", glow: "np-glow-gold", bg: "np-bg-gold" },
    purple: { text: "np-text-purple", glow: "np-glow-purple", bg: "np-bg-purple" },
    green: { text: "text-emerald-400", glow: "", bg: "bg-emerald-500" },
    red: { text: "text-red-400", glow: "", bg: "bg-red-500" },
  };
  const a = accentMap[accent] || accentMap.gold;

  return (
    <div className="np-bg-card rounded-xl border np-border p-5 transition-all duration-200 hover:np-bg-card-hover hover:border-opacity-50 group">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wider text-gray-500 font-medium mb-2">{label}</p>
          <p className="text-2xl font-bold text-white truncate">{value}</p>
          {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
        </div>
        {Icon && (
          <div className={`w-10 h-10 rounded-lg np-bg-card-hover flex items-center justify-center ${a.text} group-hover:scale-110 transition-transform`}>
            <Icon className="w-5 h-5" />
          </div>
        )}
      </div>
    </div>
  );
}