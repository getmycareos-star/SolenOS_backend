import {
  processSituationInput,
  toCaregiverSituationResponse,
  resetCareContextRootStore,
} from "@/lib/situation-entry";

console.log("IMPORT DONE " + new Date().toISOString());

(async () => {
  console.log("CALLING processSituationInput('hi') at " + new Date().toISOString());
  const t0 = Date.now();
  try {
    const r = await processSituationInput({
      raw_input: "hi",
      caregiver_id: "greeting_probe",
      contributor_id: "greeting_probe",
      timestamp: new Date().toISOString(),
    });
    console.log("returned in " + (Date.now() - t0) + "ms");
    console.log(
      "entry_behavior_layer:",
      JSON.stringify((r as any).entry_behavior_layer, null, 2),
    );
    console.log(
      "final_output:",
      JSON.stringify((r as any).final_output, null, 2),
    );
    console.log(
      "composed_response present:",
      (r as any).composed_response ? "YES" : "NO",
    );
    const dto = toCaregiverSituationResponse(r as any);
    console.log("DTO keys:", Object.keys(dto));
    console.log(
      "DTO entry_behavior_layer:",
      JSON.stringify((dto as any).entry_behavior_layer, null, 2),
    );
    console.log(
      "DTO composed_response:",
      JSON.stringify((dto as any).composed_response, null, 2),
    );
  } catch (e: any) {
    console.log("THREW after " + (Date.now() - t0) + "ms:", e?.stack || e?.message || e);
  }
  console.log("PROBE END " + new Date().toISOString());
})();
