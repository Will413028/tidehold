import { ApiError, api } from "./api";

/** The anonymous workspace of this browser: created before the first operator write. */
export async function ensureWorkspace(): Promise<void> {
  try {
    await api("/api/workspaces/me");
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) throw e;
    await api("/api/workspaces", { body: {} });
  }
}
