import { createHash } from "node:crypto";

import { getTelemetryStore } from "../telemetry-persistence/server";
import {
  getOrCreateCareSession,
  upgradeSessionToPersistent,
  getCareSession,
} from "./care-state-store";
import type { IdentityContinuityState } from "./types";

const userCredentials = new Map<string, { email: string; password_hash: string }>();
const emailToUserId = new Map<string, string>();

function hashPassword(password: string): string {
  return createHash("sha256").update(password).digest("hex");
}

export function resetAuthCredentialsForTests(): void {
  userCredentials.clear();
  emailToUserId.clear();
}

export interface StateUpgradeResult {
  user_id: string;
  care_session_id: string;
  identity_state: IdentityContinuityState;
}

/**
 * Ephemeral → persistent binding. Preserves all care graph, memory, and decisions.
 */
export async function upgradeEphemeralToPersistent(params: {
  care_session_id: string;
  email: string;
  password: string;
  telemetry_user_id?: string;
}): Promise<StateUpgradeResult> {
  const session = getCareSession(params.care_session_id);
  if (!session) {
    throw new Error("care session not found");
  }

  const store = await getTelemetryStore();
  const { user_id } = await store.ensureUser(params.telemetry_user_id);

  const emailKey = params.email.toLowerCase();
  userCredentials.set(user_id, { email: emailKey, password_hash: hashPassword(params.password) });
  emailToUserId.set(emailKey, user_id);

  const upgraded = upgradeSessionToPersistent(session, user_id);
  return {
    user_id,
    care_session_id: upgraded.care_session_id,
    identity_state: upgraded,
  };
}

export async function authenticatePersistentUser(params: {
  email: string;
  password: string;
}): Promise<{ user_id: string } | null> {
  const emailKey = params.email.toLowerCase();
  const userId = emailToUserId.get(emailKey);
  if (!userId) return null;

  const creds = userCredentials.get(userId);
  if (!creds || creds.password_hash !== hashPassword(params.password)) {
    return null;
  }

  await getTelemetryStore().then((store) => store.ensureUser(userId));
  return { user_id: userId };
}

export function bindSessionToUser(
  careSessionId: string,
  userId: string,
): IdentityContinuityState {
  const session = getOrCreateCareSession({
    care_session_id: careSessionId,
    user_id: userId,
  });
  return upgradeSessionToPersistent(session, userId);
}

/**
 * Change the password for a persistent user.
 * Returns false when the user does not exist or the current password is wrong.
 */
export async function changeUserPassword(params: {
  user_id: string;
  current_password: string;
  new_password: string;
}): Promise<boolean> {
  const creds = userCredentials.get(params.user_id);
  if (!creds || creds.password_hash !== hashPassword(params.current_password)) {
    return false;
  }
  if (!params.new_password || params.new_password.length < 8) {
    throw new Error("new password must be at least 8 characters");
  }
  userCredentials.set(params.user_id, {
    email: creds.email,
    password_hash: hashPassword(params.new_password),
  });
  await getTelemetryStore().then((store) => store.ensureUser(params.user_id));
  return true;
}

/** Remove stored credentials + email binding for a user (used on account deletion). */
export function deleteUserCredentials(userId: string): void {
  const creds = userCredentials.get(userId);
  if (creds) {
    emailToUserId.delete(creds.email);
  }
  userCredentials.delete(userId);
}
