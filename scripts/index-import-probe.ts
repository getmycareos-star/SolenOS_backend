(async () => {
  const mods = [
    "@/lib/situation-entry/caregiver-facing-uncertainty",
    "@/lib/situation-entry/what-changed",
    "@/lib/situation-entry/parse-situation",
    "@/lib/situation-entry/pipeline",
    "@/lib/situation-entry",
  ];
  for (const m of mods) {
    process.stdout.write("IMPORTING " + m + "... ");
    const t0 = Date.now();
    try {
      await import(m);
      process.stdout.write("OK " + (Date.now() - t0) + "ms\n");
    } catch (e: any) {
      process.stdout.write("ERR " + (e?.message || e) + "\n");
    }
  }
  console.log("probe done");
})();
