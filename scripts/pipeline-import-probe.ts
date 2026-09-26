console.log("importing pipeline at " + new Date().toISOString());
const t0 = Date.now();
try {
  const m = await import("@/lib/situation-entry/pipeline");
  console.log("pipeline loaded in " + (Date.now() - t0) + "ms");
  console.log("exports: " + Object.keys(m).slice(0, 20).join(", "));
} catch (e: any) {
  console.log("pipeline import ERROR after " + (Date.now() - t0) + "ms: " + (e?.message || e));
}
console.log("done at " + new Date().toISOString());
