import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ProtocolError,
  checkRequest,
  due,
  dueCount,
  isServed,
  names,
  nearest,
  normalize,
  pythonPattern,
  sameNames,
} from "../src/protocol.js";

const good = { version: 1, memory: "team", op: "count", args: {} };

function refusal(request) {
  try {
    checkRequest(request);
  } catch (error) {
    assert.ok(error instanceof ProtocolError);
    return `${error.status} ${error.message}`;
  }
  assert.fail("The request passed.");
}

describe("checkRequest", () => {
  it("passes a valid request with checked arguments", () => {
    assert.deepEqual(checkRequest(good), { op: "count", memory: "team", args: {} });
    const append = { ...good, op: "append", args: { items: [["2026-10-08", "Use uv."]] } };
    assert.deepEqual(checkRequest(append).args, { items: [["2026-10-08", "Use uv."]] });
    assert.deepEqual(checkRequest({ ...good, op: "due", args: { total: 4, limit: null } }).args, {
      total: 4,
      limit: null,
    });
  });

  it("refuses each bad request with the message of the Python service", () => {
    const bad = [
      [[], "JSON object"],
      [null, "JSON object"],
      [{ ...good, version: 2 }, "version 1"],
      [{ ...good, op: "drop" }, "Unknown op"],
      [{ ...good, op: "toString" }, "Unknown op"],
      [{ ...good, memory: "" }, "non-empty"],
      [{ ...good, args: { lo: 0 } }, "count takes the arguments: none"],
      [{ ...good, op: "entries", args: { lo: "0", hi: 1 } }, "lo is not"],
      [{ ...good, op: "entries", args: { lo: true, hi: 1 } }, "lo is not"],
      [{ ...good, op: "append", args: { items: [["2026-01-01"]] } }, "items"],
      [{ ...good, op: "write_overrides", args: { overrides: { WAKE_LINES: false } } }, "sizes"],
    ];
    for (const [request, message] of bad) {
      const text = refusal(request);
      assert.ok(text.startsWith("400 ") && text.includes(message), `${JSON.stringify(request)}: ${text}`);
    }
  });
});

describe("isServed", () => {
  it("serves only the listed memories, or every name for *", () => {
    assert.ok(isServed("team", "team, ops"));
    assert.ok(isServed("ops", "team, ops"));
    assert.ok(!isServed("other", "team, ops"));
    assert.ok(!isServed("team", ""));
    assert.ok(isServed("anything", "*"));
  });
});

describe("names", () => {
  it("finds numbers, paths, flags, CamelCase, ALL-CAPS and environments", () => {
    assert.deepEqual(
      [...names("Run node with env -u NODE_OPTIONS on macOS in staging, see src/a.py:12.")].sort(),
      ["-u", "env:staging", "macos", "node_options", "src/a.py:12"],
    );
  });

  it("keeps capitalized words that do not start a sentence, by their first letters", () => {
    assert.deepEqual([...names("The team uses Poetry. Then Postgres runs.")].sort(), [
      "name:poetr",
      "name:postg",
    ]);
  });

  it("separates near misses that embeddings put close together", () => {
    const base = "The staging database listens on port 5432.";
    assert.ok(!sameNames(base, "The staging database listens on port 5433."));
    assert.ok(!sameNames(base, "The production database listens on port 5432."));
    assert.ok(!sameNames("Run tests with clojure -M:test.", "Run tests with clojure -M:test-native."));
    assert.ok(!sameNames("The gateway listens on 127.0.0.1:8080.", "The gateway listens on 10.0.0.5:8080."));
  });

  it("matches repeats in other words and other languages", () => {
    assert.ok(sameNames("Deploy with the blue-green script.", "Deploy with the blue green script."));
    assert.ok(sameNames("Zespół używa uv zamiast Poetry.", "The team uses uv, not Poetry."));
    assert.ok(
      sameNames(
        "CI on macOS runs on the MacBook of the user; keep it awake.",
        "The macOS CI runner is the user's MacBook, so keep it awake.",
      ),
    );
  });
});

describe("nearest", () => {
  it("ranks by score, keeps the minimum and puts the newer memory first on a tie", () => {
    const vectors = [normalize([1, 0]), normalize([1, 1]), null, normalize([1, 0]), normalize([0, 1])];
    assert.deepEqual(nearest(normalize([1, 0]), vectors, 0.5, 10), [3, 0, 1]);
    assert.deepEqual(nearest(normalize([1, 0]), vectors, 0.5, 1), [3]);
    assert.deepEqual(nearest(normalize([1, 0]), vectors, 0.5, 0), []);
  });
});

describe("pythonPattern", () => {
  it("matches without case and accepts the common Python forms", () => {
    assert.ok(pythonPattern(String.raw`memory 1\d`).test("#3 2026-10-08 Memory 12"));
    assert.ok(pythonPattern("(?i)uv|poetry").test("Use UV"));
    assert.ok(pythonPattern(String.raw`(?P<w>ab)(?P=w)\Z`).test("xabab"));
  });

  it("refuses a pattern that does not compile", () => {
    assert.throws(() => pythonPattern("(unclosed"), (error) => error.status === 400);
  });
});

describe("due", () => {
  it("lists complete blocks without a summary, smallest level first", () => {
    const levels = { 2: 1, 4: 0 };
    const levelCount = (size) => levels[size] ?? 0;
    assert.deepEqual(due(levelCount, 5), [
      [2, 4],
      [0, 4],
    ]);
    assert.deepEqual(due(levelCount, 5, 1), [[2, 4]]);
    assert.equal(dueCount(levelCount, 5), 2);
    assert.equal(dueCount(() => 9, 5), 0);
  });
});
