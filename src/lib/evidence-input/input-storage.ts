/**
 * Evidence & Input Intelligence — Immutable Input Storage
 *
 * Original inputs are NEVER modified. They are stored with cryptographic
 * hashes for integrity verification. This is the foundation of the
 * provenance chain.
 */

import { createHash } from "node:crypto";
import type { ImmutableInput, InputType } from "./types";

const inputStore = new Map<string, ImmutableInput>();

function generateInputId(): string {
  return `inp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function computeHash(bytes: Buffer | Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * Store an immutable input. Original bytes are preserved exactly as received.
 * Returns the stored input with its generated ID and hash.
 */
export function storeImmutableInput(params: {
  original_bytes: Buffer | Uint8Array;
  content_type?: string | null;
  original_filename?: string | null;
  received_from?: string | null;
  ingestion_metadata?: Record<string, unknown>;
}): ImmutableInput {
  const input_id = generateInputId();
  const content_hash = computeHash(params.original_bytes);

  const input: ImmutableInput = {
    input_id,
    original_bytes: params.original_bytes,
    content_hash,
    content_type: params.content_type ?? null,
    original_filename: params.original_filename ?? null,
    byte_size: params.original_bytes.length,
    received_at: new Date().toISOString(),
    received_from: params.received_from ?? null,
    ingestion_metadata: params.ingestion_metadata ?? {},
  };

  inputStore.set(input_id, input);
  return input;
}

/**
 * Retrieve an immutable input by ID. Returns null if not found.
 */
export function getImmutableInput(input_id: string): ImmutableInput | null {
  return inputStore.get(input_id) ?? null;
}

/**
 * Verify the integrity of an input against its stored hash.
 */
export function verifyInputIntegrity(input_id: string): boolean {
  const input = inputStore.get(input_id);
  if (!input) return false;
  const current_hash = computeHash(input.original_bytes);
  return current_hash === input.content_hash;
}

/**
 * Get all stored inputs.
 */
export function getAllInputs(): ImmutableInput[] {
  return [...inputStore.values()];
}

/**
 * Check if an identical file already exists (by hash).
 */
export function findDuplicateInput(content_hash: string): ImmutableInput | null {
  for (const input of inputStore.values()) {
    if (input.content_hash === content_hash) return input;
  }
  return null;
}

/**
 * Clear the input store (for testing).
 */
export function clearInputStore(): void {
  inputStore.clear();
}
