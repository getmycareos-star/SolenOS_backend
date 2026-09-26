import {
  classifyEntryInput,
  isGreetingOrNonSemantic,
  isSessionReentryInput,
} from "../src/lib/entry-behavior-protocol";

console.log("module loaded OK");
const tests = ["hi", "hello", "hey solenos", "how are you", "thanks", "patient fell", "medication changed", ""];
for (const t of tests) {
  console.log(JSON.stringify({ input: t, classification: classifyEntryInput({ raw_input: t, has_documents: false }), isGreeting: isGreetingOrNonSemantic(t), isReentry: isSessionReentryInput({ raw_input: t, has_documents: false }) }));
}
console.log("DONE");
