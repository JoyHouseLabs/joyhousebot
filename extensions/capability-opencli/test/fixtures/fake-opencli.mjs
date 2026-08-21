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
if (args[0] === "xiaohongshu" && args[1] === "user") {
  process.stdout.write(JSON.stringify([{
    id: "note123",
    title: "列表标题",
    type: "normal",
    likes: "12",
    cover: "https://sns-webpic-qc.xhscdn.com/cover.jpg",
    url: "https://www.xiaohongshu.com/user/profile/user123/note123?xsec_token=secret_token&xsec_source=pc_user",
  }]));
  process.exit(0);
}
if (args[0] === "xiaohongshu" && args[1] === "note") {
  if (process.env.FAKE_XHS_NOTE_EXIT) process.exit(Number(process.env.FAKE_XHS_NOTE_EXIT));
  process.stdout.write(JSON.stringify([
    {field: "title", value: "完整标题"},
    {field: "author", value: "示例作者"},
    {field: "content", value: "这是完整正文。"},
    {field: "likes", value: "13"},
    {field: "collects", value: "8"},
    {field: "comments", value: "5"},
    {field: "tags", value: "AI, 写作"},
  ]));
  process.exit(0);
}
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
