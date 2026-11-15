// concert-commands: `/tickets <link>` in a show's topic records the show's ticket link. No model sees it.
//
// The plugin registers one chat command and does nothing else: it runs only when someone types /tickets, and it
// reads no other message. The command runs `concert-mcp tickets --link <link> --topic <chat>:<thread>`, which
// finds the show, writes the link to its calendar event and makes the Partiful invite, and hands the CLI's reply
// back as the command's reply.
import { execFile } from "node:child_process";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { replyOf, ticketsArgv, topicOf, USAGE } from "./rules.js";

const DEFAULT_COMMAND = ["concert-mcp"];
const DEFAULT_TIMEOUT_MS = 240_000;

/** Run the CLI; resolves with its stdout whatever the exit code (exit 1 is "the reply asks the user something"). */
function run(argv, timeoutMs) {
  return new Promise((resolve, reject) => {
    execFile(argv[0], argv.slice(1), { timeout: timeoutMs, maxBuffer: 1 << 20 }, (error, stdout) => {
      if (error && (error.killed || typeof error.code !== "number")) reject(error);
      else resolve(String(stdout));
    });
  });
}

export default definePluginEntry({
  id: "concert-commands",
  name: "Concert Commands",
  description: "The /tickets chat command: a show's ticket link goes straight to concert-mcp, with no model turn.",
  register(api) {
    api.registerCommand({
      name: "tickets",
      description: "Record this show's ticket link and make its Partiful invite.",
      acceptsArgs: true,
      handler: async (ctx) => {
        const link = (ctx.args ?? "").trim();
        const topic = topicOf(ctx);
        // A show lives in its topic: anywhere else there is no telling which show the link is for.
        if (!link || !topic) return { text: USAGE };
        const config = api.pluginConfig ?? {};
        const command = Array.isArray(config.command) && config.command.length ? config.command : DEFAULT_COMMAND;
        try {
          const stdout = await run(ticketsArgv(command, link, topic), config.timeoutMs ?? DEFAULT_TIMEOUT_MS);
          return { text: replyOf(stdout) ?? `That is not a link I can use. ${USAGE}` };
        } catch (error) {
          api.logger.warn(`concert-commands: concert-mcp did not answer (${error})`);
          return { text: "The concert tool did not answer. Try again in a minute." };
        }
      },
    });
  },
});
