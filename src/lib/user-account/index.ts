/**
 * User account lifecycle helpers — password change + full account deletion.
 * Deletion removes credentials, telemetry rows, consent profile, governance settings,
 * and the care-recipient identity record associated with the user's care key.
 */

import { changeUserPassword, deleteUserCredentials, resetCareStateStoreForTests } from "../identity-continuity";
import { getTelemetryStore } from "../telemetry-persistence/server";
import { getCareRecipientIdentity } from "../care-recipient-identity";
import { clearUserGovernanceSettings } from "../settings-governance";
import { deleteConsentProfile, revokeConsent } from "../policy-engine";
import {
  deleteDurableFile,
  livingCareRecordDataDir,
  sanitizeDurableCareKey,
} from "../living-care-record-persistence/fs-store";

export type ChangePasswordInput = {
  user_id: string;
  current_password: string;
  new_password: string;
};

/**
 * Change a persistent user's password. Throws on validation problems;
 * returns false when the user does not exist or current password is wrong.
 */
export async function changePassword(input: ChangePasswordInput): Promise<boolean> {
  return changeUserPassword(input);
}

export type DeleteAccountInput = {
  user_id: string;
  care_key?: string | null;
};

/**
 * Permanently delete a user account and all associated data.
 * - telemetry (users/interactions/feedback/documents)
 * - credentials + email binding
 * - consent profile (fully removed, not revoked)
 * - governance settings
 * - care-recipient identity file for the user's care key
 */
export async function deleteUserAccount(input: DeleteAccountInput): Promise<void> {
  const store = await getTelemetryStore();
  await store.deleteUser(input.user_id);

  deleteUserCredentials(input.user_id);

  // Remove consent profile entirely (not revoke → limited_mode).
  try {
    deleteConsentProfile(input.user_id);
  } catch {
    // Non-fatal — consent may not exist yet.
  }
  try {
    revokeConsent(input.user_id);
  } catch {
    // Non-fatal
  }

  clearUserGovernanceSettings(input.user_id);

  if (input.care_key) {
    try {
      const identity = getCareRecipientIdentity(input.care_key);
      if (identity?.care_key) {
        const file = livingCareRecordDataDir(
          "care-recipient-identity",
          `${sanitizeDurableCareKey(input.care_key)}.json`,
        );
        deleteDurableFile(file);
      }
    } catch {
      // Non-fatal — identity may not exist.
    }
  }

  // Best-effort in-memory state cleanup — do not nuke the whole process store
  // since other users may share the same store instance.
  try {
    resetCareStateStoreForTests();
  } catch {
    // Non-fatal
  }
}

export { getCareRecipientIdentity };

