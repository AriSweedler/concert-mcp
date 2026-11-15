// The command's rules, pure, so `node --test` covers them without OpenClaw.

export const USAGE = "Send /tickets <link to the ticket page> in the show's topic.";

/**
 * '<chat>:<thread>' of the Telegram forum topic a command was typed in, as concert-mcp keys a topic; null anywhere
 * else. The chat is the supergroup id (-100…) found in the command's `to` or `from`; General (thread 1) owns no show.
 */
export function topicOf(ctx) {
  const thread = ctx?.messageThreadId;
  if (thread === undefined || thread === null || String(thread) === "" || String(thread) === "1") return null;
  const chat = /-100\d+/.exec(`${ctx.to ?? ""} ${ctx.from ?? ""}`);
  return chat ? `${chat[0]}:${thread}` : null;
}

/**
 * The CLI call: the reply comes back on stdout and OpenClaw delivers it, so --no-send. The values ride in the
 * `--flag=value` form: a topic starts with `-` (a supergroup id), which a bare argument would pass off as a flag.
 */
export function ticketsArgv(command, link, topic) {
  return [...command, "--no-send", "tickets", `--link=${link}`, `--topic=${topic}`];
}

/**
 * The chat reply in the CLI's output; null when there is none, or when it is a `FIX:` line (addressed to a model:
 * the argument was not a link). The last line is the CLI's delivery status (`NOT SENT: delivery disabled`).
 */
export function replyOf(stdout) {
  const lines = String(stdout ?? "").trimEnd().split("\n");
  if (lines.length && /^(NOT SENT|SENT)\b/.test(lines[lines.length - 1])) lines.pop();
  const text = lines.join("\n").trim();
  return text && !text.startsWith("FIX:") ? text : null;
}
