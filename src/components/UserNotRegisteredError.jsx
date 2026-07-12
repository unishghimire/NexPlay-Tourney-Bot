import React from "react";

export default function UserNotRegisteredError() {
  return (
    <div className="min-h-screen flex items-center justify-center np-bg-base">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-white mb-4">Access Denied</h1>
        <p className="text-gray-400">You are not registered to access this panel.</p>
      </div>
    </div>
  );
}