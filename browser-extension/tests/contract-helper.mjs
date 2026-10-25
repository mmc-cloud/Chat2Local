// Test-only runner: exercise real argument validation without a browser or DOM.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const context = vm.createContext({ URL, Date, chrome: { tabs: {
  create: async () => ({ id: 1, windowId: 1 }), query: async () => [],
  get: async () => { throw Error("No test tab"); },
} } });
vm.runInContext(readFileSync(new URL("../browser-safety.js", import.meta.url), "utf8"), context);
for (const name of ["browser-navigation.js", "browser-groups.js", "browser-bookmarks.js"]) vm.runInContext(readFileSync(new URL("../" + name, import.meta.url), "utf8"), context);
vm.runInContext(readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8"), context);
const api = context.Chat2LocalBrowser;
let input = "";
for await (const chunk of process.stdin) input += chunk;
const accepted = [];
for (const item of JSON.parse(input)) {
  const result = await api.execute({ capability: item.name, arguments: item.arguments, expires_at: Date.now() + 10000 });
  accepted.push(result.error?.code !== "invalid_arguments");
}
process.stdout.write(JSON.stringify({ capabilities: api.capabilities, accepted }));
