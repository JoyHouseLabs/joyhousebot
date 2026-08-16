const args = process.argv.slice(2);

if (args[0] === "--version") {
  process.stdout.write("opencli 1.8.6\n");
  process.exit(0);
}
if (args[0] === "doctor") {
  process.stdout.write("[OK] Extension: connected (v1.8.6)\n[OK] Connectivity: connected in 0.1s\n");
  process.exit(Number(process.env.FAKE_DOCTOR_EXIT ?? 0));
}

const value = (name) => args.find((item) => item.startsWith(`--${name}=`))?.slice(name.length + 3);
const mode = value("mode");
if (mode?.startsWith("exit-")) process.exit(Number(mode.slice(5)));
if (mode === "invalid-json") {
  process.stdout.write("not-json");
  process.exit(0);
}
if (mode === "large") {
  process.stdout.write(JSON.stringify({value: "x".repeat(100_000)}));
  process.exit(0);
}
if (mode === "empty") process.exit(66);
if (mode === "slow") setTimeout(() => process.exit(0), 10_000);
else {
  process.stdout.write(JSON.stringify({
    args,
    profile: process.env.OPENCLI_PROFILE ?? null,
    query: value("query") ?? null,
  }));
}
