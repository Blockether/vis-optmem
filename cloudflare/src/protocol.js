// The vis_optmem.remote protocol, version 1, without Cloudflare APIs.
//
// worker.js serves it. The functions here are pure, so `node --test` checks them.
// Keep the messages the same as in src/vis_optmem/remote.py.

export const VERSION = 1;

/** Every operation: the MemoryStore method of the same name and its arguments. */
export const OPS = {
  exists: [],
  create: [],
  overrides: [],
  write_overrides: [["overrides", "sizes"]],
  count: [],
  entries: [["lo", "int"], ["hi", "int"]],
  append: [["items", "items"]],
  level_count: [["size", "int"]],
  summary: [["lo", "int"], ["hi", "int"]],
  put_summary: [["lo", "int"], ["hi", "int"], ["text", "str"]],
  drop_summaries: [["lo", "int"], ["hi", "int"]],
  due: [["total", "int"], ["limit", "int?"]],
  due_count: [["total", "int"]],
  search: [["query", "str"], ["limit", "int"]],
  duplicate: [["text", "str"]],
  matches: [["pattern", "str"], ["newest", "int"]],
};

/** A request that the service refuses. `type` is the Python exception name. */
export class ProtocolError extends Error {
  constructor(status, type, message) {
    super(message);
    this.status = status;
    this.type = type;
  }
}

const invalid = (message) => new ProtocolError(400, "ValueError", message);

export function failure(type, message) {
  return { error: { type, message: String(message) } };
}

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function check(kind, name, value) {
  if (kind === "int?" && value === null) return null;
  if ((kind === "int" || kind === "int?") && Number.isInteger(value)) return value;
  if (kind === "str" && typeof value === "string") return value;
  if (
    kind === "items" &&
    Array.isArray(value) &&
    value.every(
      (item) =>
        Array.isArray(item) &&
        item.length === 2 &&
        item.every((part) => typeof part === "string"),
    )
  ) {
    return value;
  }
  if (
    kind === "sizes" &&
    isObject(value) &&
    Object.values(value).every((v) => Number.isInteger(v))
  ) {
    return value;
  }
  throw invalid(`Argument ${name} is not a valid ${kind}.`);
}

/** Check one request. Return `{op, memory, args}` or throw a ProtocolError. */
export function checkRequest(request) {
  if (!isObject(request)) throw invalid("The request must be a JSON object.");
  if (request.version !== VERSION) throw invalid(`Use protocol version ${VERSION}.`);
  const { op, memory } = request;
  if (!Object.hasOwn(OPS, op)) {
    throw invalid(`Unknown op '${op}'. Use one of: ${Object.keys(OPS).join(", ")}.`);
  }
  if (typeof memory !== "string" || !memory) {
    throw invalid("Give the memory as a non-empty string.");
  }
  const args = request.args ?? {};
  if (!isObject(args)) throw invalid("args must be a JSON object.");
  const names = OPS[op].map(([name]) => name);
  if (Object.keys(args).sort().join() !== [...names].sort().join()) {
    throw invalid(`${op} takes the arguments: ${names.join(", ") || "none"}.`);
  }
  const checked = {};
  for (const [name, kind] of OPS[op]) checked[name] = check(kind, name, args[name]);
  return { op, memory, args: checked };
}

/** True when `allowed` (a comma list, or "*") includes `memory`. */
export function isServed(memory, allowed) {
  const names = String(allowed ?? "").split(",").map((name) => name.trim());
  return names.includes("*") || names.includes(memory);
}

// Names: the duplicate check

const ENVIRONMENTS = new Set([
  "dev", "development", "staging", "stage", "prod", "production", "preprod", "qa",
  "uat", "sandbox",
]);
const TECHNICAL = /\d|[\\/_.:@=$~#]|^-|\p{Ll}\p{Lu}|^\p{Lu}{2,}$/u;
const PROPER = /^\p{Lu}\p{Ll}+$/u;
const PROPER_STEM = 5;

/**
 * The names in `text` that two memories must share to say the same fact.
 *
 * Embeddings cannot separate "port 5432" from "port 5433", or staging from
 * production. So a name is a technical token (digits, paths, file names, flags,
 * CamelCase, ALL-CAPS), an environment word, or a capitalized word that does not
 * start a sentence (its first 5 letters).
 */
export function names(text) {
  const found = new Set();
  let sentenceStart = true;
  for (const raw of String(text).split(/\s+/)) {
    const token = raw.replace(/^[("'`[{]+/, "").replace(/[.,;:!?)"'`\]}]+$/, "");
    if (token) {
      const lower = token.toLowerCase();
      if (ENVIRONMENTS.has(lower)) found.add(`env:${lower}`);
      else if (TECHNICAL.test(token)) found.add(lower);
      else if (!sentenceStart && PROPER.test(token)) {
        found.add(`name:${lower.slice(0, PROPER_STEM)}`);
      }
    }
    if (raw) sentenceStart = /[.!?]["')\]]*$/.test(raw);
  }
  return found;
}

export function sameNames(a, b) {
  const left = names(a);
  const right = names(b);
  return left.size === right.size && [...left].every((name) => right.has(name));
}

// Vectors

/** `vector` scaled to length 1, as a Float32Array. */
export function normalize(vector) {
  let sum = 0;
  for (const x of vector) sum += x * x;
  const scale = sum > 0 ? 1 / Math.sqrt(sum) : 0;
  return Float32Array.from(vector, (x) => x * scale);
}

export function dot(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

/**
 * The ids of the vectors closest to `query`, best first.
 *
 * `vectors[id]` is a normalized vector or null. Keep scores of at least `min`.
 * For equal scores, the newer memory comes first.
 */
export function nearest(query, vectors, min, limit) {
  const scored = [];
  vectors.forEach((vector, id) => {
    if (!vector) return;
    const score = dot(query, vector);
    if (score >= min) scored.push([score, id]);
  });
  scored.sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  return scored.slice(0, Math.max(limit, 0)).map(([, id]) => id);
}

// Patterns

/**
 * A JavaScript RegExp for the Python regular expression `pattern`, without case.
 *
 * It accepts the common Python forms: (?P<name>...), (?P=name), \A, \Z and a
 * leading (?i). Throw a ProtocolError for a pattern that does not compile.
 */
export function pythonPattern(pattern) {
  const source = pattern
    .replace(/^\(\?i\)/, "")
    .replace(/\(\?P</g, "(?<")
    .replace(/\(\?P=(\w+)\)/g, "\\k<$1>")
    .replace(/\\A/g, "^")
    .replace(/\\Z/g, "$");
  try {
    return new RegExp(source, "i");
  } catch {
    throw invalid(`The pattern ${JSON.stringify(pattern)} is not a valid regular expression.`);
  }
}

// Summaries

/** Complete blocks of the first `total` memories without a summary, smallest first. */
export function due(levelCount, total, limit = null) {
  const blocks = [];
  for (let size = 2; size <= total; size *= 2) {
    for (let k = levelCount(size); k < Math.floor(total / size); k++) {
      blocks.push([k * size, (k + 1) * size]);
      if (limit !== null && blocks.length >= limit) return blocks;
    }
  }
  return blocks;
}

/** How many blocks `due` lists. A level with extra summaries counts as zero. */
export function dueCount(levelCount, total) {
  let count = 0;
  for (let size = 2; size <= total; size *= 2) {
    count += Math.max(0, Math.floor(total / size) - levelCount(size));
  }
  return count;
}
