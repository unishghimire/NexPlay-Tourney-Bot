import React from "react";

const statusConfig = {
  active: { label: "Active", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  trial: { label: "Trial", className: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30" },
  expired: { label: "Expired", className: "bg-red-500/15 text-red-400 border-red-500/30" },
  banned: { label: "Banned", className: "bg-red-900/30 text-red-300 border-red-700/40" },
  cancelled: { label: "Cancelled", className: "bg-gray-500/15 text-gray-400 border-gray-500/30" },
  open: { label: "Open", className: "bg-blue-500/15 text-blue-400 border-blue-500/30" },
  pending: { label: "Pending", className: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30" },
  resolved: { label: "Resolved", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  draft: { label: "Draft", className: "bg-gray-500/15 text-gray-400 border-gray-500/30" },
  completed: { label: "Completed", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
};

const severityConfig = {
  info: { label: "Info", className: "bg-blue-500/15 text-blue-400 border-blue-500/30" },
  warning: { label: "Warning", className: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30" },
  critical: { label: "Critical", className: "bg-red-500/15 text-red-400 border-red-500/30" },
};

export default function StatusBadge({ status, severity }) {
  if (severity) {
    const cfg = severityConfig[severity] || severityConfig.info;
    return (
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${cfg.className}`}>
        {cfg.label}
      </span>
    );
  }
  const cfg = statusConfig[status] || { label: status, className: "bg-gray-500/15 text-gray-400 border-gray-500/30" };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}