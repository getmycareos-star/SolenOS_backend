import type { Demand, CareStateDimension } from "./types";

/**
 * DEMAND-TO-STATE BRIDGE
 *
 * Links demands to state dimensions so that demand pressure can be
 * contextualized against the person's actual care state.
 *
 * BREAK: Previously, demands were attached only to situations (flat summaries).
 * NOW: Demands link to specific state dimensions (medications, mobility, etc.)
 * so the system can answer:
 *   "This demand exists BECAUSE the person's mobility changed from independent to assisted."
 */

export function linkDemandToStateDimension(
  demand: Demand,
  dimension: CareStateDimension,
): Demand {
  return {
    ...demand,
    description: `${demand.description} [STATE:${dimension}]`,
  };
}

export function getDemandsForDimension(
  demands: readonly Demand[],
  dimension: CareStateDimension,
): Demand[] {
  const marker = `[STATE:${dimension}]`;
  return demands.filter((d) => d.description.includes(marker));
}

export function annotateDemandWithStateContext(
  demand: Demand,
  stateDimensions: readonly CareStateDimension[],
): Demand {
  const relevantDimensions = stateDimensions.filter((dim) =>
    demand.description.toLowerCase().includes(dim.replace(/_/g, " ")),
  );
  return {
    ...demand,
    description:
      relevantDimensions.length > 0
        ? `${demand.description} [STATE:${relevantDimensions.join(",")}]`
        : demand.description,
  };
}

export function demandsAffectedByStateChange(
  demands: readonly Demand[],
  changedDimension: CareStateDimension,
): Demand[] {
  return demands.filter((d) => {
    const lower = d.description.toLowerCase();
    const dimWords = changedDimension.replace(/_/g, " ");
    return lower.includes(dimWords) || lower.includes(changedDimension);
  });
}
