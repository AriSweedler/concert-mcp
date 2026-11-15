import assert from "node:assert/strict";
import { test } from "node:test";

import { replyOf, ticketsArgv, topicOf } from "./rules.js";

const LINK = "https://www.ticketweb.com/event/zed-fox/123";

test("the topic is the supergroup and the thread the command was typed in", () => {
  assert.equal(topicOf({ to: "telegram:-1001234567890:topic:7", messageThreadId: 7 }), "-1001234567890:7");
  assert.equal(topicOf({ from: "telegram:group:-1001234567890", to: "bot", messageThreadId: "7" }), "-1001234567890:7");
  assert.equal(topicOf({ to: "telegram:-1001234567890", messageThreadId: 1 }), null); // General owns no show
  assert.equal(topicOf({ to: "telegram:12345", messageThreadId: 7 }), null); // a DM is not a forum
  assert.equal(topicOf({ to: "telegram:-1001234567890" }), null);
  assert.equal(topicOf(undefined), null);
});

test("the CLI call returns the reply instead of sending it, and names the topic", () => {
  assert.deepEqual(ticketsArgv(["uv", "run", "concert-mcp"], LINK, "-1001234567890:7"), [
    "uv", "run", "concert-mcp", "--no-send", "tickets", `--link=${LINK}`, "--topic=-1001234567890:7",
  ]);
});

test("the reply is the output without its status line; a FIX or nothing is no reply", () => {
  assert.equal(replyOf("**Partiful made:** [x](y)\n• [Tickets](z)\nNOT SENT: delivery disabled\n"), "**Partiful made:** [x](y)\n• [Tickets](z)");
  assert.equal(replyOf("I could not read the artist from that.\nNOT SENT: no live chat (CLI run)\n"), "I could not read the artist from that.");
  assert.equal(replyOf("FIX: pass the ticket page's address in tickets\n"), null);
  assert.equal(replyOf("NOT SENT: no reply\n"), null);
  assert.equal(replyOf(""), null);
});
