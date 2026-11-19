import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { ProtocolError } from "../src/protocol.js";
import { checkLanguage, checkLine, hasProfanity, problem, secretKind } from "../src/guard.js";

// The cases and the results of src/vis_optmem/guard.py: tests/test_guard_fixture.py writes them.
const CASES = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/guard.json", import.meta.url), "utf-8"),
);

const joined = (text) => (typeof text === "string" ? text : text.join(""));

/** The refusal of the Worker for one case, or null, like verdict() in the Python test. */
function verdict(data) {
  try {
    let text = joined(data.text);
    if ("limit" in data) text = checkLine(text, data.limit, data.what);
    return problem(text, data.what, data.language);
  } catch (error) {
    assert.ok(error instanceof ProtocolError, String(error));
    assert.equal(error.status, 400);
    assert.equal(error.type, "ValueError");
    return error.message;
  }
}

describe("guard", () => {
  it("has the fixture cases", () => {
    assert.ok(CASES.length > 100);
  });

  for (const [index, data] of CASES.entries()) {
    const name = JSON.stringify(joined(data.text)).slice(0, 50);
    it(`gives the Python result for case ${index}: ${name}`, () => {
      assert.equal(verdict(data), data.problem);
    });
  }

  it("returns the code or the line on success", () => {
    assert.equal(checkLanguage(" PL "), "pl");
    assert.equal(checkLanguage(""), "");
    assert.equal(checkLine("  Use uv.  ", 340, "memory"), "Use uv.");
    assert.equal(secretKind("Use uv, not Poetry."), null);
    assert.equal(hasProfanity("Scunthorpe"), false);
  });

  it("refuses a value that is not a string with a 400", () => {
    assert.throws(
      () => checkLine(null, 340, "memory"),
      (error) => error instanceof ProtocolError && error.status === 400 &&
        error.message === "The memory must be a string.",
    );
  });
});
