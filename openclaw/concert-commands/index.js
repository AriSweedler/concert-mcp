// concert-commands: two chat commands for a show's topic. No model sees them.
//
//   /tickets <link>   the show's ticket page: recorded on the calendar event, and the Partiful invite is made
//   /setlist <link>   the show's setlist.fm page, after the show: the setlist playlist is made, and both links
//                     go on the calendar event and on the Partiful invite
//
// The plugin registers the two commands and does nothing else: it runs only when one is typed, and it reads no
// other message. Each runs the concert-mcp CLI for the topic it was typed in and hands the CLI's reply back.
import { execFile } from "node:child_process";

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { replyOf, setlistArgv, ticketsArgv, topicOf, USAGE } from "./rules.js";

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

/** One command: `/name <link>` in a show's topic runs the CLI built by `argv` and replies with what it says. */
function linkCommand(api, name, description, argv) {
  return {
    name,
    description,
    acceptsArgs: true,
    handler: async (ctx) => {
      const link = (ctx.args ?? "").trim();
      const topic = topicOf(ctx);
      // A show lives in its topic: anywhere else there is no telling which show the link is for.
      if (!link || !topic) return { text: USAGE[name] };
      const config = api.pluginConfig ?? {};
      const command = Array.isArray(config.command) && config.command.length ? config.command : DEFAULT_COMMAND;
      try {
        const stdout = await run(argv(command, link, topic), config.timeoutMs ?? DEFAULT_TIMEOUT_MS);
        return { text: replyOf(stdout) ?? `That is not a link I can use. ${USAGE[name]}` };
      } catch (error) {
        api.logger.warn(`concert-commands: concert-mcp did not answer /${name} (${error})`);
        return { text: "The concert tool did not answer. Try again in a minute." };
      }
    },
  };
}

export default definePluginEntry({
  id: "concert-commands",
  name: "Concert Commands",
  description: "The /tickets and /setlist chat commands: a show's links go straight to concert-mcp, with no model turn.",
  register(api) {
    api.registerCommand(
      linkCommand(api, "tickets", "Record this show's ticket link and make its Partiful invite.", ticketsArgv),
    );
    api.registerCommand(
      linkCommand(api, "setlist", "Make this show's setlist playlist from its setlist.fm page.", setlistArgv),
    );
  },
});
