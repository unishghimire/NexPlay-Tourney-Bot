import React from "react";
import { Link } from "react-router-dom";

export default function PageNotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center np-bg-base">
      <div className="text-center">
        <h1 className="text-6xl font-bold np-text-gold mb-4">404</h1>
        <p className="text-gray-400 mb-6">Page not found</p>
        <Link to="/" className="np-text-purple hover:underline">Back to Dashboard</Link>
      </div>
    </div>
  );
}