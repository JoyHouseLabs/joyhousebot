import {readFile, writeFile} from "node:fs/promises";
import {join} from "node:path";

const models = JSON.parse(await readFile(join(process.env.PI_CODING_AGENT_DIR, "models.json"), "utf8"));
await writeFile(join(process.cwd(), "pi-capture.json"), JSON.stringify({
  argv: process.argv.slice(2),
  apiKey: models.providers.joyhouse.apiKey,
  tokenAvailable: String(process.env.JOYHOUSE_MODEL_GRANT ?? "").startsWith("jhm_"),
}));

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  if (!buffer.includes("\n")) return;
  process.stdout.write(`${JSON.stringify({
    type: "message_end",
    message: {role: "assistant", content: [{type: "text", text: "fixture summary"}]},
  })}\n`);
  process.stdout.write(`${JSON.stringify({type: "agent_end"})}\n`);
});
