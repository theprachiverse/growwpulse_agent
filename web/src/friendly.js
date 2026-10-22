// Turns technical server messages (CLI flags, raw quota text, UTC timestamps) into plain
// instructions that match the buttons and checkboxes on screen. Unknown messages pass through.

const localTime = (date) =>
  date.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });

// "HH:MM" in UTC -> the next time that clock reading happens, shown in the viewer's timezone.
const nextUtc = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m));
  if (d < now) d.setUTCDate(d.getUTCDate() + 1);
  return d;
};

const RULES = [
  [
    /already appended with different content/i,
    () =>
      "This week's pulse is already in your Google Doc, and today's version is different. " +
      "The Doc can't be edited automatically, so publishing again adds a second, revised section " +
      "below the old one. To do that, tick “Already appended — add a revised section anyway” " +
      "and press Publish again.",
  ],
  [
    /last Doc append had an unknown outcome/i,
    () =>
      "We couldn't confirm whether this week's pulse reached your Google Doc. Open the Doc and look. " +
      "If the section is there, you're done. If it isn't, tick “Already appended — add a revised " +
      "section anyway” and press Publish.",
  ],
  [
    /last email action had an unknown outcome/i,
    () =>
      "We couldn't confirm whether the email went out. Check Drafts and Sent in Gmail. If it isn't " +
      "there, tick “Email already sent for this week — do it again” and press Publish.",
  ],
  [
    /Not enough tokens left: ~([\d,]+) remaining, a run needs ~([\d,]+).*?about ([\dhm ]+)\./i,
    (m) =>
      `Not enough AI quota left for a full run right now (about ${m[1]} available, ${m[2]} needed). ` +
      `It refills gradually: enough will be back in about ${m[3].trim()}. ` +
      "Re-running only the “compose” step needs far less.",
  ],
  [
    /Run limit reached \((\d+)\/(\d+) in the last 24h\)\.(?: The oldest leaves the window at (\d\d:\d\d) UTC)?/i,
    (m) =>
      `You've used all ${m[2]} runs allowed in 24 hours.` +
      (m[3] ? ` The next slot opens at ${localTime(nextUtc(m[3]))}.` : ""),
  ],
  [
    /quota is exhausted; retry after (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC/i,
    (m) =>
      `The AI service's daily limit was reached. It will work again after ` +
      `${localTime(new Date(`${m[1]}T${m[2]}:00Z`))}.`,
  ],
  [
    /daily token limit for (\S+) is used up.*?(?:about ([\dhm ]+)\.)?$/i,
    (m) =>
      `The AI service's daily limit for ${m[1]} is used up. It refills gradually` +
      (m[2] ? `; try again in about ${m[2].trim()}.` : "."),
  ],
  [
    /rejected the request \(HTTP (\d+)\) for model '([^']+)'/i,
    (m) =>
      `The AI service rejected the request: the model “${m[2]}” isn't available for this API key, ` +
      "or the key is invalid. Check the key and the model name in the settings.",
  ],
];

export function friendlyMessage(raw) {
  if (!raw) return raw;
  const text = String(raw);
  for (const [pattern, build] of RULES) {
    const m = text.match(pattern);
    if (m) return build(m);
  }
  return text;
}

// How long the running banner should say a run takes, from where the run started.
export function runDurationHint(job) {
  const from = job?.from_step;
  if (from === "compose" || from === "publish") {
    return "Writing the pulse usually takes 2–4 minutes.";
  }
  return (
    "A full run reads a sample of the reviews with the AI in about 10–15 steps, paced to stay " +
    "inside the free tier, so it takes roughly 15–20 minutes."
  );
}
