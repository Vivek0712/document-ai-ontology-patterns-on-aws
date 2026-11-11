/** Display formatting. Money: thousands separators, two decimals. IDs masked/truncated. */

export function mmss(msValue: number | undefined): string {
  if (msValue === undefined || !Number.isFinite(msValue) || msValue < 0) return "00:00";
  const s = Math.floor(msValue / 1000);
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function duration(msValue: number | undefined): string {
  if (msValue === undefined || !Number.isFinite(msValue)) return "—";
  if (msValue < 1000) return `${Math.round(msValue)} ms`;
  if (msValue < 60_000) return `${(msValue / 1000).toFixed(1)} s`;
  return mmss(msValue);
}

export function bytes(n: number | undefined): string {
  if (n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function money(value: string | undefined): string {
  if (!value) return "—";
  const n = Number(value.replace(/,/g, ""));
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function score(n: number | undefined): string {
  return n === undefined ? "—" : n.toFixed(2);
}

export function shortSha(sha: string | undefined, n = 4): string {
  return sha ? `${sha.slice(0, n)}…` : "—";
}

/** Cognito sub / execution ids: first 6 characters. */
export function shortId(id: string | undefined, n = 6): string {
  if (!id) return "—";
  return id.length > n ? `${id.slice(0, n)}…` : id;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * A Cognito sub for display (keep the full value in `title=`): a UUID's first 8 characters + "…"; any other id
 * (a local dev sub such as `local-dev-owner-0000`) whole up to 20 characters, with a middle ellipsis past that.
 */
export function shortSub(sub: string | undefined): string {
  if (!sub) return "—";
  if (UUID.test(sub)) return `${sub.slice(0, 8)}…`;
  return sub.length <= 20 ? sub : `${sub.slice(0, 10)}…${sub.slice(-9)}`;
}

/**
 * A sub the API sent truncated (`callerSub6`: the access log never holds a full sub) for display: the full sub of a
 * known identity it is the prefix of, shortened by `shortSub`; else the prefix itself, marked as one ("b5d09f…").
 */
export function subLabel(value: string | undefined, known: (string | undefined)[] = []): string {
  if (!value) return "—";
  const prefix = value.replace(/…$/, "");
  const full = known.find((k) => k && k !== prefix && k.startsWith(prefix));
  if (full) return shortSub(full);
  return prefix.length <= 6 ? `${prefix}…` : shortSub(prefix);
}

export function shortExecution(arn: string | undefined): string {
  if (!arn) return "—";
  const name = arn.split(":").pop() ?? arn;
  return shortId(name, 18);
}

/** Mask runs of 8+ digits except the last 4 (mirror of masking.mask_long_digits). */
export function maskLongDigits(text: string): string {
  return text.replace(/\d{8,}/g, (m) => "•".repeat(m.length - 4) + m.slice(-4));
}

export function time(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-GB", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export function dateTime(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-CA")} ${d.toLocaleTimeString("en-GB", { hour12: false })}`;
}

export function relative(iso: string | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.round((now - Date.parse(iso)) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** A Bedrock model id as its family name for captions ("us.anthropic.claude-sonnet-4-6" → "Sonnet"); the id otherwise. */
export function modelFamily(modelId: string | undefined): string {
  if (!modelId) return "—";
  const id = modelId.toLowerCase();
  const claude = /claude-(?:\d+-)?(opus|sonnet|haiku)/.exec(id);
  if (claude) return claude[1][0].toUpperCase() + claude[1].slice(1);
  const nova = /nova-(\d+)-(lite|pro|micro|premier)/.exec(id) ?? /nova-(lite|pro|micro|premier)/.exec(id);
  if (nova) return nova.length === 3 ? `Nova ${nova[1]} ${nova[2][0].toUpperCase()}${nova[2].slice(1)}` : `Nova ${nova[1][0].toUpperCase()}${nova[1].slice(1)}`;
  return modelId;
}
