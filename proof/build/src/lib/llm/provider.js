"use strict";
/**
 * SolenOS LLM provider abstraction.
 *
 * Provider-agnostic boundary. Runtime AI goes through here — no module outside
 * this layer should know which model or host backs it.
 */
Object.defineProperty(exports, "__esModule", { value: true });
