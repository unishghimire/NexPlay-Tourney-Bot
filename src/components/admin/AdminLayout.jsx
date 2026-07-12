import React, { useState, useEffect } from "react";
import { Outlet, NavLink, useLocation } from "react-router-dom";
import { base44 } from "@/api/base44Client";
import {
  LayoutDashboard,
  Server,
  CreditCard,
  Ticket,
  Tag,
  DollarSign,
  Bell,
  Trophy,
  Menu,
  X,
} from "lucide-react";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/servers", label: "Servers", icon: Server },
  { to: "/plans", label: "Plans", icon: CreditCard },
  { to: "/promo-codes", label: "Promo Codes", icon: Ticket },
  { to: "/offers", label: "Offers", icon: Tag },
  { to: "/revenue", label: "Revenue", icon: DollarSign },
  { to: "/notifications", label: "Notifications", icon: Bell },
];

export default function AdminLayout() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const items = await base44.entities.AdminNotification.filter(
          { read_by_unish: false },
          "-created_date",
          100
        );
        if (active) setUnreadCount(items.length);
      } catch {
        /* noop */
      }
    };
    load();
    const unsub = base44.entities.AdminNotification.subscribe(() => load());
    return () => {
      active = false;
      unsub();
    };
  }, [location.pathname]);

  const SidebarContent = () => (
    <div className="flex h-full flex-col">
      {/* Logo */}
      <div className="flex items-center gap-3 px-6 py-6 border-b np-border">
        <div className="w-10 h-10 rounded-xl np-gradient-gold-purple flex items-center justify-center np-glow-gold">
          <Trophy className="w-5 h-5 text-black" />
        </div>
        <div>
          <h1 className="text-lg font-bold tracking-tight text-white">NexPlay</h1>
          <p className="text-[10px] uppercase tracking-widest np-text-gold font-semibold">Admin Panel</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto np-scroll">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isNotifications = item.label === "Notifications";
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? "np-bg-card-hover text-white shadow-lg"
                    : "text-gray-400 hover:text-white hover:np-bg-card"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && <span className="absolute left-0 w-1 h-6 rounded-r-full np-bg-gold" />}
                  <Icon className={`w-4 h-4 ${isActive ? "np-text-gold" : ""}`} />
                  <span className="flex-1">{item.label}</span>
                  {isNotifications && unreadCount > 0 && (
                    <span className="px-2 py-0.5 text-[10px] font-bold rounded-full np-bg-gold text-black">
                      {unreadCount}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Owner */}
      <div className="px-4 py-4 border-t np-border">
        <div className="flex items-center gap-3 px-2">
          <div className="w-9 h-9 rounded-full np-gradient-gold-purple flex items-center justify-center text-black font-bold text-sm">
            UG
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-white truncate">Unish Ghimire</p>
            <p className="text-[10px] text-gray-500">Owner & Admin</p>
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen np-bg-base flex">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:flex w-64 np-bg-card border-r np-border flex-shrink-0 fixed inset-y-0 left-0 z-30">
        <SidebarContent />
      </aside>

      {/* Mobile Sidebar */}
      {mobileOpen && (
        <>
          <div
            className="fixed inset-0 bg-black/60 z-40 lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="fixed inset-y-0 left-0 w-64 np-bg-card border-r np-border z-50 lg:hidden">
            <SidebarContent />
          </aside>
        </>
      )}

      {/* Main */}
      <div className="flex-1 lg:ml-64 flex flex-col min-w-0">
        {/* Mobile Top Bar */}
        <div className="lg:hidden flex items-center justify-between px-4 py-3 np-bg-card border-b np-border sticky top-0 z-20">
          <button onClick={() => setMobileOpen(true)} className="text-white">
            <Menu className="w-6 h-6" />
          </button>
          <span className="font-bold np-text-gold">NexPlay Admin</span>
          <div className="w-6" />
        </div>

        <main className="flex-1 p-4 lg:p-8 np-scroll">
          <Outlet />
        </main>
      </div>

      {/* Close button for mobile */}
      {mobileOpen && (
        <button
          onClick={() => setMobileOpen(false)}
          className="fixed top-4 right-4 z-50 lg:hidden text-white"
        >
          <X className="w-6 h-6" />
        </button>
      )}
    </div>
  );
}