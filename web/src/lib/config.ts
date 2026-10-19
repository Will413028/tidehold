import "server-only";

// The one module that reads the environment (next.config.ts reads it at build time).
export const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
