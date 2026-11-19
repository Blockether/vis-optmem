// The checks of src/vis_optmem/guard.py, for the Worker.
//
// Keep the rules and the messages the same as in guard.py. The patterns are the
// Python patterns: pattern() gives them the Python meaning in a JS `u` pattern.
// tests/fixtures/guard.json holds cases with the Python results, and
// test/guard.test.js checks them here.

import { ProtocolError } from "./protocol.js";

const invalid = (message) => new ProtocolError(400, "ValueError", message);

/** The characters of Python str.isspace(), for \s, strip() and split(). */
const SPACES =
  "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005" +
  "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000";
const SPACE_CLASS = [...SPACES]
  .map((char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`)
  .join("");
const WORD_CLASS = String.raw`\p{L}\p{N}_`;
const WORD_EDGE = `(?:(?<=[${WORD_CLASS}])(?![${WORD_CLASS}])|(?<![${WORD_CLASS}])(?=[${WORD_CLASS}]))`;
// Python (?i) also matches these to i and I; a JS `iu` pattern does not.
const DOTTED_I = "\u0130\u0131";

/** A RegExp with the meaning of the Python str pattern `source`; flags "i" is (?i). */
function pattern(source, flags = "") {
  const ignore = flags.includes("i");
  let out = "";
  let inClass = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (char === "\\") {
      const next = source[++i];
      if (next === "w") out += inClass ? WORD_CLASS : `[${WORD_CLASS}]`;
      else if (next === "s") out += inClass ? SPACE_CLASS : `[${SPACE_CLASS}]`;
      else if (next === "d") out += String.raw`\p{Nd}`;
      else if (next === "b") out += WORD_EDGE;
      else if (next === '"' || next === "'") out += next;
      else out += `\\${next}`;
      continue;
    }
    if (inClass) {
      if (char === "]") inClass = false;
      else if (ignore && (source.startsWith("a-z", i) || source.startsWith("A-Z", i))) {
        out += source.slice(i, i + 3) + DOTTED_I;
        i += 2;
        continue;
      }
    } else if (char === "[") {
      inClass = true;
      if (source[i + 1] === "^") {
        out += "[^";
        i += 1;
        continue;
      }
    } else if (ignore && (char === "i" || char === "I")) {
      out += `[i${DOTTED_I}]`;
      continue;
    }
    out += char;
  }
  return new RegExp(out, `${flags}u`);
}

/** Known secret formats, as [kind, pattern]. The first match names the kind. */
const SECRETS = [
  ["a private key", String.raw`-----BEGIN[A-Z ]*PRIVATE KEY`],
  ["a JSON Web Token", String.raw`\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}`],
  ["an AWS access key", String.raw`\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b`],
  ["a GitHub token", String.raw`\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})`],
  ["a GitLab token", String.raw`\bgl(?:pat|dt|ptt|rt|cbt|soat)-[\w-]{20,}`],
  [
    "a Slack token",
    String.raw`\bxox[abposre]-[A-Za-z0-9-]{10,}` +
      String.raw`|hooks\.slack\.com/(?:services|workflows)/[\w/-]{20,}`,
  ],
  ["a Google API key", String.raw`\bAIza[\w-]{35}`],
  ["a Google OAuth secret", String.raw`\bya29\.[\w-]{20,}|\bGOCSPX-[\w-]{20,}`],
  ["an Anthropic key", String.raw`\bsk-ant-[a-z]+\d*-[\w-]{20,}`],
  ["an OpenAI key", String.raw`\bsk-(?:proj-|svcacct-|admin-)?[\w-]*?[A-Za-z0-9]{20,}`],
  ["a Stripe key", String.raw`\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}`],
  ["an npm token", String.raw`\bnpm_[A-Za-z0-9]{36}\b`],
  ["a Hugging Face token", String.raw`\bhf_[A-Za-z]{34}\b`],
].map(([kind, source]) => [kind, pattern(source)]);

const BEARER = pattern(String.raw`\b(bearer|basic)\s+([\w.~+/=-]+)`, "gi");
const URL_PASSWORD = pattern(String.raw`\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@`, "gi");
const URL_USER = pattern(String.raw`\b[a-z][a-z0-9+.-]*://([^\s/:@]{20,})@`, "gi");
/** A value after a word like `password=` or `token:`. */
const ASSIGNED = pattern(
  String.raw`\b[\w.-]*?(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key` +
    String.raw`|access[_-]?key|private[_-]?key|credentials?)` +
    String.raw`[\"']?\s*(?::|=>?)\s*[\"']?([^\s\"'` + "`" + String.raw`,;]+)`,
  "gi",
);

const PLACEHOLDERS = new Set(
  "password passwd pass secret token key apikey changeme example redacted hidden none null empty xxx".split(
    " ",
  ),
);

/** English and Polish swear words, as in guard.py. */
const PROFANITY = pattern(
  String.raw`\w*fuck\w*|\bf[*#@]+ck\w*|\b(?:bull|dip|horse)?shit\w*|\bsh[*#@!]+t\b` +
    String.raw`|\bcunts?\b|\bbitch\w*|\bassholes?\b|\bdickheads?\b|\bwank(?:er|ers|ing)\b` +
    String.raw`|\btwats?\b|\bbastards?\b` +
    String.raw`|\w*(?:kurw|pierdol|pierdal|jeba[cćlnł]|jebi|jebn|zjeb|pizd)\w*` +
    String.raw`|\b(?:ch|h)uj(?!i)\w*|\bkutas\w*|\bcip(?:a|e|y|ą|ę|ie)\b|\bdziwk\w*`,
  "i",
);

/** Each language: [name, common short words, letters that only it uses here]. */
const LANGUAGES = new Map(
  [
    [
      "en",
      "English",
      "the of and to in is are was were be been for on with as by at from that this " +
        "these those it its not but or if then than which who what when where how there " +
        "their they we you he she will would can could should must has have had do does " +
        "did an into about after before only also just more most other some such each",
      "",
    ],
    [
      "pl",
      "Polish",
      "się nie jest są być był była było że na w z ze do od po przez przy przed dla jak " +
        "ale lub albo oraz czy gdy jeśli już tylko też jego jej ich ten ta te tego tej " +
        "tym który która które może można trzeba ma mają będzie bo więc pod nad między " +
        "tak bez jako",
      "ąćęłńśźż",
    ],
    [
      "de",
      "German",
      "der die das und ist sind nicht mit von zu den dem des ein eine einen auf für " +
        "sich auch als wie aber oder wenn dass nur noch bei nach aus wird werden kann " +
        "haben hat",
      "äöüß",
    ],
    [
      "fr",
      "French",
      "le la les des une est sont pas pour avec dans sur que qui par ce cette mais ou " +
        "ne plus aux du au être avoir fait sans",
      "çèêàùâîôûë",
    ],
    [
      "es",
      "Spanish",
      "el los las del una es son por para con que pero como más sin sobre también " +
        "este esta muy hay ser",
      "ñ¿¡",
    ],
    [
      "it",
      "Italian",
      "il gli della delle sono non con che una più anche questo questa come nel alla dei",
      "",
    ],
    [
      "pt",
      "Portuguese",
      "os das dos uma não para com que mais mas como também este esta são pelo pela",
      "ãõ",
    ],
    ["nl", "Dutch", "het een en van zijn niet met voor op dat maar ook als bij naar wordt", "ĳ"],
  ].map(([code, name, words, letters]) => [code, [name, new Set(words.split(" ")), letters]]),
);

/** Fewer small words than this say too little about the language: the line passes. */
const LANGUAGE_MIN_WORDS = 4;

const SPLIT = new RegExp(`[${SPACE_CLASS}]+`, "u");
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
const NOT_WORD = pattern(String.raw`[^\w]|_`);

/** `text` without the characters of `chars` at its two ends, like Python str.strip(chars). */
function strip(text, chars = SPACES) {
  let start = 0;
  let end = text.length;
  while (start < end && chars.includes(text[start])) start++;
  while (end > start && chars.includes(text[end - 1])) end--;
  return text.slice(start, end);
}

/** `text` as Python repr() writes a str. */
function repr(text) {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  const hex = (code, size) => code.toString(16).padStart(size, "0");
  let out = quote;
  for (const char of text) {
    const code = char.codePointAt(0);
    if (char === quote || char === "\\") out += `\\${char}`;
    else if (char === "\t") out += "\\t";
    else if (char === "\n") out += "\\n";
    else if (char === "\r") out += "\\r";
    else if (code < 0x20 || code === 0x7f) out += `\\x${hex(code, 2)}`;
    else if (code < 0x7f || !NOT_PRINTABLE.test(char)) out += char;
    else if (code <= 0xff) out += `\\x${hex(code, 2)}`;
    else if (code <= 0xffff) out += `\\u${hex(code, 4)}`;
    else out += `\\U${hex(code, 8)}`;
  }
  return out + quote;
}

/** `text` in NFKC form without invisible characters, so they cannot hide a word. */
function visible(text) {
  return text.normalize("NFKC").replace(/\p{Cf}/gu, "");
}

/** Shannon entropy of `value` in bits for each character, summed like Python sum(). */
function entropy(value) {
  const chars = [...value];
  const counts = new Map();
  for (const char of chars) counts.set(char, (counts.get(char) ?? 0) + 1);
  let total = 0;
  let error = 0;
  for (const count of counts.values()) {
    const term = (count / chars.length) * Math.log2(count / chars.length);
    const next = total + term;
    error += Math.abs(total) >= Math.abs(term) ? total - next + term : term - next + total;
    total = next;
  }
  if (error !== 0 && Number.isFinite(error)) total += error;
  return -total;
}

/** True when `value` looks like a generated secret, not a word or a reference. */
function isRandom(raw) {
  const value = strip(raw, ".");
  if ([...value].length < 8 || PLACEHOLDERS.has(value.toLowerCase())) return false;
  // $VAR, ${VAR}, <token>, {{ secret }}, os.environ["X"], %s and *** name a value.
  if (/[$<>{}()\[\]%]/u.test(value) || /^[*x.]+$/u.test(value)) return false;
  const classes = [/[a-z]/u, /[A-Z]/u, /[0-9]/u, NOT_WORD].filter((p) => p.test(value)).length;
  // A short value cannot reach a high entropy, so three kinds of characters also count.
  return classes >= 3 || (classes >= 2 && entropy(value) >= 3.0);
}

/** Return the language code `language` in lowercase, "" for none, or throw a 400. */
export function checkLanguage(language) {
  const code = strip(language).toLowerCase();
  if (code && !LANGUAGES.has(code)) {
    throw invalid(
      `Unknown memory language ${repr(language)}. Use one of: ${[...LANGUAGES.keys()].join(", ")}.`,
    );
  }
  return code;
}

/** For each language, how many small words of `text` are its common words or letters. */
function languageScores(text) {
  const words = visible(text)
    .split(SPLIT)
    .map((raw) => strip(raw, ".,;:!?()[]{}\"'`"))
    .filter(
      (token) =>
        /^\p{L}+$/u.test(token) &&
        /\p{Lowercase}/u.test(token) &&
        !/[\p{Uppercase}\p{Lt}]/u.test(token),
    );
  const scores = new Map([...LANGUAGES.keys()].map((code) => [code, 0]));
  if (words.length < LANGUAGE_MIN_WORDS) return scores;
  for (const word of words) {
    for (const [code, [, common, letters]] of LANGUAGES) {
      if (common.has(word) || (letters && [...word].some((char) => letters.includes(char)))) {
        scores.set(code, scores.get(code) + 1);
      }
    }
  }
  return scores;
}

/** The name of the language of `text` when it is clearly not `language`, else null. */
export function otherLanguage(text, language) {
  const code = checkLanguage(language);
  if (!code) return null;
  const scores = languageScores(text);
  let best = null;
  for (const other of LANGUAGES.keys()) {
    if (other !== code && (best === null || scores.get(other) > scores.get(best))) best = other;
  }
  // Most lines of a language have its small words, so none of them is a sign too.
  const [mine, theirs] = [scores.get(code), scores.get(best)];
  return theirs > mine && (theirs >= 2 || mine === 0) ? LANGUAGES.get(best)[0] : null;
}

/** Return `text` without outer whitespace, or throw a 400 if it is not one short line. */
export function checkLine(text, limit, what) {
  if (typeof text !== "string") throw invalid(`The ${what} must be a string.`);
  const line = strip(text);
  if (!line) throw invalid(`The ${what} is empty. Write one line of text.`);
  if (line.includes("\n") || line.includes("\r")) {
    // The line breaks of Python str.splitlines().
    const lines = line.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/u).length;
    throw invalid(
      `The ${what} has ${lines} lines. Write one line: merge them, or save them one by one.`,
    );
  }
  let size = 0;
  let position = 0;
  for (const char of line) {
    const code = char.codePointAt(0);
    if (code >= 0xd800 && code <= 0xdfff) {
      throw invalid(
        `'utf-8' codec can't encode character '\\u${code.toString(16)}' in position ` +
          `${position}: surrogates not allowed`,
      );
    }
    size += code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
    position++;
  }
  if (size > limit) {
    throw invalid(
      `The ${what} is too long: ${size} bytes, and the limit is ${limit}. ` +
        "Letters with accents use 2 bytes. Make it shorter.",
    );
  }
  return line;
}

/** The kind of secret that `text` seems to hold, like "a GitHub token", or null. */
export function secretKind(raw) {
  const text = visible(raw);
  for (const [kind, secret] of SECRETS) {
    if (secret.test(text)) return kind;
  }
  for (const match of text.matchAll(URL_PASSWORD)) {
    const value = match[1];
    if (!PLACEHOLDERS.has(value.toLowerCase()) && !/[$<>{}%*]/u.test(value)) {
      return "a password in a URL";
    }
  }
  for (const match of text.matchAll(URL_USER)) {
    // A user name like deploy-bot-01 is not random enough to be a token.
    if (isRandom(match[1]) && entropy(match[1]) >= 3.8) return "a token in a URL";
  }
  for (const match of text.matchAll(BEARER)) {
    if (isRandom(match[2])) {
      const [first, ...rest] = match[1];
      return `a ${first.toUpperCase()}${rest.join("").toLowerCase()} authorization value`;
    }
  }
  for (const match of text.matchAll(ASSIGNED)) {
    if (isRandom(match[1])) return "a password or token value";
  }
  return null;
}

/** True when `text` has a swear word. */
export function hasProfanity(text) {
  return PROFANITY.test(visible(text));
}

/**
 * Why `text` must not be saved, without the unsafe part, or null when it is safe.
 *
 * `language` is the code of the only language that the memory keeps, like "en",
 * or "" for any language. A search text is checked only for its language.
 */
export function problem(text, what, language = "") {
  const other = otherLanguage(text, language);
  if (other !== null) {
    const wanted = LANGUAGES.get(checkLanguage(language))[0];
    const verb = what === "search text" ? "searched" : "saved";
    return (
      `Not ${verb}: the ${what} seems to be in ${other}, and this memory keeps ` +
      `${wanted} only. Write it again in ${wanted}.`
    );
  }
  if (what === "search text") return null;
  const kind = secretKind(text);
  if (kind !== null) {
    return (
      `Not saved: the ${what} seems to hold ${kind}. Memories never change, so a ` +
      "secret would stay there for ever. Save the fact without the value, like " +
      '"the deploy token is in the DEPLOY_TOKEN variable".'
    );
  }
  if (hasProfanity(text)) {
    return `Not saved: the ${what} has profanity. Write it again in professional language.`;
  }
  return null;
}
