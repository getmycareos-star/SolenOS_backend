const withTimeout = <T,>(p: Promise<T>, ms: number, label: string): Promise<T> =>
  Promise.race([
    p,
    new Promise<T>((_r, rej) => setTimeout(() => rej(new Error("TIMEOUT " + ms + "ms")), ms)),
  ]).catch((e) => {
    console.log(label + " =>", e?.message);
    return undefined as any;
  });

async function test(label: string, mod: string) {
  const mark = Date.now();
  const res = await withTimeout(import(mod), 15000, label);
  console.log(label + " resolved in " + (Date.now() - mark) + "ms" + (res ? " OK" : ""));
}

(async () => {
  console.log("start");
  await test("entry-behavior-protocol", "@/lib/entry-behavior-protocol");
  await test("context-store", "@/lib/situation-entry/context-store");
  await test("situation-entry/index", "@/lib/situation-entry");
  console.log("DONE");
})();
