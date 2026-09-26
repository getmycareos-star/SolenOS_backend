import {
  processSituationInput,
  toCaregiverSituationResponse,
  resetCareContextRootStore,
} from "@/lib/situation-entry";

function show(label: string, result: any) {
  const dto = toCaregiverSituationResponse(result);
  console.log("=== " + label + " ===");
  console.log("entry_behavior_layer:", JSON.stringify(result.entry_behavior_layer, null, 2));
  console.log("final_output.what_is_happening:", JSON.stringify(result?.final_output?.what_is_happening, null, 2));
  console.log("final_output.what_matters_now:", JSON.stringify(result?.final_output?.what_matters_now, null, 2));
  console.log("final_output.what_to_ask_next:", JSON.stringify(result?.final_output?.what_to_ask_next, null, 2));
  console.log("final_output.risk_level:", JSON.stringify(result?.final_output?.risk_level, null, 2));
  console.log("adoption_wedge.sections:", JSON.stringify(result?.adoption_wedge_layer?.sections, null, 2));
  console.log("composed_response present:", result.composed_response ? "YES" : "NO");
  console.log("DTO keys:", Object.keys(dto));
  console.log("DTO entry_behavior_layer:", JSON.stringify(dto.entry_behavior_layer, null, 2));
  console.log("DTO composed_response:", JSON.stringify(dto.composed_response, null, 2));
  console.log("DTO what_i_understood:", JSON.stringify(dto.what_i_understood, null, 2));
  console.log("DTO what_is_uncertain:", JSON.stringify(dto.what_is_uncertain, null, 2));
  console.log("DTO what_changed:", JSON.stringify(dto.what_changed, null, 2));
  console.log("DTO context:", JSON.stringify(dto.context, null, 2));
}

async function main() {
  try {
    console.log("starting probe 1 (hi)...");
    resetCareContextRootStore();
    const r1 = await processSituationInput({
      raw_input: "hi",
      caregiver_id: "caregiver_greeting_probe",
      contributor_id: "caregiver_greeting_probe",
      timestamp: new Date().toISOString(),
    });
    show("GREETING hi", r1);
  } catch (e: any) {
    console.log("GREETING hi THREW:", e?.stack || e?.message || String(e));
  }

  try {
    console.log("starting probe 2 (how are you)...");
    resetCareContextRootStore();
    const r2 = await processSituationInput({
      raw_input: "how are you",
      caregiver_id: "caregiver_greeting_probe",
      contributor_id: "caregiver_greeting_probe",
      timestamp: new Date().toISOString(),
    });
    show("GREETING how-are-you", r2);
  } catch (e: any) {
    console.log("GREETING how-are-you THREW:", e?.stack || e?.message || String(e));
  }
  console.log("ALL DONE");
}

main();
