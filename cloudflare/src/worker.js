// A vis-optmem memory service on Cloudflare: one Worker and one Durable Object for each memory.
//
// RemoteStore sends each MemoryStore call as one POST. The Worker checks the token and the
// request, then the Durable Object of that memory runs it. A Durable Object runs one
// synchronous SQLite step at a time, so each write is atomic without retries.

import { DurableObject } from "cloudflare:workers";
import {
  ProtocolError,
  checkRequest,
  due,
  dueCount,
  failure,
  isServed,
  nearest,
  normalize,
  pythonPattern,
  sameNames,
} from "./protocol.js";

const SEARCH_MIN = 0.5;
const DUPLICATE_MIN = 0.85;
const DUPLICATE_CANDIDATES = 5;
const EMBED_BATCH = 50;

const SCHEMA = `
create table if not exists memories (id integer primary key, date text, text text, vec blob);
create table if not exists summaries (size integer, k integer, text text, primary key (size, k));
create table if not exists meta (key text primary key, value text);
`;

function reply(status, body) {
  return Response.json(body, { status });
}

async function sha256(text) {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
}

/** True when the request carries `Authorization: Bearer <token>`. */
async function isAuthorized(request, token) {
  const given = await sha256(request.headers.get("Authorization") ?? "");
  const wanted = await sha256(`Bearer ${token}`);
  return crypto.subtle.timingSafeEqual(given, wanted);
}

export default {
  async fetch(request, env) {
    try {
      if (request.method !== "POST") {
        return reply(400, failure("ValueError", "Send each request with POST."));
      }
      if (!env.MEMORY_STORE_TOKEN) {
        return reply(500, failure("RemoteError", "Set the MEMORY_STORE_TOKEN secret of the service."));
      }
      if (!(await isAuthorized(request, env.MEMORY_STORE_TOKEN))) {
        return reply(401, failure("PermissionError", "The token is not valid."));
      }
      const { op, memory, args } = checkRequest(await request.json().catch(() => null));
      if (!isServed(memory, env.MEMORIES ?? "team")) {
        return reply(404, failure("LookupError", `No memory '${memory}' in this service.`));
      }
      const { status, body } = await env.MEMORY.get(env.MEMORY.idFromName(memory)).run(op, args);
      return reply(status, body);
    } catch (error) {
      if (error instanceof ProtocolError) {
        return reply(error.status, failure(error.type, error.message));
      }
      console.error("Memory request failed", error);
      return reply(500, failure("RemoteError", error?.message ?? error));
    }
  },
};

/** One memory: its log, summaries and sizes in SQLite, and its vectors in memory. */
export class MemoryObject extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(SCHEMA);
    this.model = env.EMBEDDING_MODEL || "@cf/baai/bge-m3";
    this.searchMin = Number(env.SEARCH_MIN ?? SEARCH_MIN);
    this.duplicateMin = Number(env.DUPLICATE_MIN ?? DUPLICATE_MIN);
    this.memories = null; // [{date, text, vec}] by id, once loaded
    this.loading = null;
  }

  /** Run one checked operation. Return the status and body of the answer. */
  async run(op, args) {
    try {
      return { status: 200, body: { result: await this[op](args) } };
    } catch (error) {
      if (error instanceof ProtocolError) {
        return { status: error.status, body: failure(error.type, error.message) };
      }
      console.error(`Memory op ${op} failed`, error);
      return { status: 500, body: failure("RemoteError", error?.message ?? error) };
    }
  }

  meta(key) {
    return this.sql.exec("select value from meta where key = ?", key).toArray()[0]?.value ?? null;
  }

  setMeta(key, value) {
    this.sql.exec("insert or replace into meta values (?, ?)", key, value);
  }

  total() {
    return this.sql.exec("select count(*) as n from memories").one().n;
  }

  /** Normalized embeddings of `texts`, in batches. */
  async embed(texts) {
    const vectors = [];
    for (let i = 0; i < texts.length; i += EMBED_BATCH) {
      const out = await this.env.AI.run(this.model, { text: texts.slice(i, i + EMBED_BATCH) });
      vectors.push(...out.data.map(normalize));
    }
    return vectors;
  }

  /** Every memory with its vector. Embed the memories that have none for this model. */
  async load() {
    if (this.memories) return this.memories;
    this.loading ??= (async () => {
      const rows = this.sql.exec("select id, date, text, vec from memories order by id").toArray();
      const isCurrent = this.meta("model") === this.model;
      const missing = rows.filter((row) => !isCurrent || !row.vec);
      const vectors = await this.embed(missing.map((row) => row.text));
      // No await from here, so no append runs between these steps.
      this.ctx.storage.transactionSync(() => {
        missing.forEach((row, i) => {
          row.vec = vectors[i].buffer;
          this.sql.exec("update memories set vec = ? where id = ?", row.vec, row.id);
        });
        this.setMeta("model", this.model);
      });
      const added = this.sql
        .exec("select id, date, text, vec from memories where id >= ? order by id", rows.length)
        .toArray();
      this.memories = rows
        .concat(added)
        .map(({ date, text, vec }) => ({ date, text, vec: new Float32Array(vec) }));
      return this.memories;
    })().finally(() => {
      this.loading = null;
    });
    return this.loading;
  }

  entry(id) {
    const { date, text } = this.memories[id];
    return { id, date, text };
  }

  // Memory

  exists() {
    return this.meta("created") !== null;
  }

  create() {
    if (this.exists()) return false;
    this.setMeta("created", new Date().toISOString());
    this.setMeta("model", this.model);
    return true;
  }

  overrides() {
    return JSON.parse(this.meta("overrides") ?? "{}");
  }

  write_overrides({ overrides }) {
    this.setMeta("overrides", JSON.stringify(overrides));
    return null;
  }

  count() {
    return this.total();
  }

  entries({ lo, hi }) {
    return this.sql
      .exec("select id, date, text from memories where id >= ? and id < ? order by id", lo, hi)
      .toArray();
  }

  async append({ items }) {
    if (!items.length) return this.total();
    const vectors = await this.embed(items.map(([, text]) => text));
    // No await from here: the ids and the rows go in one step.
    return this.ctx.storage.transactionSync(() => {
      const first = this.total();
      items.forEach(([date, text], i) => {
        this.sql.exec("insert into memories values (?, ?, ?, ?)", first + i, date, text, vectors[i].buffer);
      });
      // Before the first load, load() reads these rows itself.
      items.forEach(([date, text], i) => this.memories?.push({ date, text, vec: vectors[i] }));
      return first;
    });
  }

  async search({ query, limit }) {
    if (limit < 1 || !query.trim() || this.total() === 0) return [];
    const [vector] = await this.embed([query]);
    const memories = await this.load();
    const ids = nearest(vector, memories.map((m) => m.vec), this.searchMin, limit);
    return ids.map((id) => this.entry(id));
  }

  async duplicate({ text }) {
    if (!text.trim() || this.total() === 0) return null;
    const [vector] = await this.embed([text]);
    const memories = await this.load();
    const ids = nearest(vector, memories.map((m) => m.vec), this.duplicateMin, DUPLICATE_CANDIDATES);
    const id = ids.find((candidate) => sameNames(text, memories[candidate].text));
    return id === undefined ? null : this.entry(id);
  }

  matches({ pattern, newest }) {
    const regex = pythonPattern(pattern);
    const rows = this.sql.exec("select id, date, text from memories order by id").toArray();
    const found = rows.filter((row) => regex.test(`#${row.id} ${row.date} ${row.text}`));
    return [found.length, newest > 0 ? found.slice(-newest) : []];
  }

  // Summaries

  level_count({ size }) {
    return this.sql.exec("select count(*) as n from summaries where size = ?", size).one().n;
  }

  summary({ lo, hi }) {
    const size = hi - lo;
    const rows = this.sql
      .exec("select text from summaries where size = ? and k = ?", size, Math.floor(lo / size))
      .toArray();
    return rows[0]?.text ?? null;
  }

  put_summary({ lo, hi, text }) {
    const size = hi - lo;
    return this.ctx.storage.transactionSync(() => {
      if (size < 1 || this.level_count({ size }) !== Math.floor(lo / size)) return false;
      this.sql.exec("insert into summaries values (?, ?, ?)", size, Math.floor(lo / size), text);
      return true;
    });
  }

  drop_summaries({ lo, hi }) {
    return this.ctx.storage.transactionSync(() => {
      const dropped = [];
      const total = this.total();
      for (let size = hi - lo; size >= 1 && size <= total; size *= 2) {
        const keep = Math.floor(lo / size);
        const rows = this.sql
          .exec("select k from summaries where size = ? and k >= ? order by k", size, keep)
          .toArray();
        dropped.push(...rows.map(({ k }) => [k * size, (k + 1) * size]));
        this.sql.exec("delete from summaries where size = ? and k >= ?", size, keep);
      }
      return dropped;
    });
  }

  due({ total, limit }) {
    return due((size) => this.level_count({ size }), total, limit);
  }

  due_count({ total }) {
    return dueCount((size) => this.level_count({ size }), total);
  }
}

