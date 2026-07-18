import { describe, expect, test } from "bun:test";
import type { SearchEntry } from "./search";
import { MAX_RESULTS, makeSnippet, search, tokenize } from "./search";

function entry(overrides: Partial<SearchEntry>): SearchEntry {
  return {
    route: "/contracts",
    pageTitle: "Contracts",
    sectionLabel: "Core app model",
    heading: "Contracts",
    headingId: "",
    body: "A contract is the single source of truth for an API endpoint.",
    ...overrides,
  };
}

const entries: SearchEntry[] = [
  entry({}),
  entry({
    heading: "Contract groups",
    headingId: "contract-groups",
    body: "Use route_group to bind a group of related contracts.",
  }),
  entry({
    route: "/getting-started",
    pageTitle: "Getting started",
    sectionLabel: "Start",
    heading: "Getting started",
    body: "Create a Tenchi app and run it locally.",
  }),
  entry({
    route: "/errors",
    pageTitle: "Errors",
    heading: "Error definitions",
    headingId: "error-definitions",
    body: "Application errors use stable definitions and a flat envelope.",
  }),
];

describe("tokenize", () => {
  test("lowercases and splits on whitespace", () => {
    expect(tokenize("  Contract   Groups ")).toEqual(["contract", "groups"]);
  });

  test("returns no terms for blank queries", () => {
    expect(tokenize("   ")).toEqual([]);
  });
});

describe("search", () => {
  test("empty query returns curated page-intro defaults", () => {
    const results = search(entries, "");
    expect(results.length).toBeGreaterThan(0);
    expect(results[0]?.entry.route).toBe("/getting-started");
    for (const result of results) {
      expect(result.entry.headingId).toBe("");
    }
  });

  test("heading matches outrank body matches", () => {
    const results = search(entries, "contract groups");
    expect(results[0]?.entry.headingId).toBe("contract-groups");
  });

  test("page intro outranks subsection for the bare page query", () => {
    const results = search(entries, "contracts");
    expect(results[0]?.entry.headingId).toBe("");
    expect(results[0]?.entry.route).toBe("/contracts");
  });

  test("matching is case-insensitive and substring-based", () => {
    const results = search(entries, "ROUTE_GROUP");
    expect(results.map((result) => result.entry.headingId)).toContain(
      "contract-groups",
    );
  });

  test("all terms must match somewhere", () => {
    expect(search(entries, "contracts zebra")).toEqual([]);
  });

  test("caps results", () => {
    const many = Array.from({ length: 40 }, (_, index) =>
      entry({
        route: `/page-${index}`,
        heading: `Tenchi topic ${index}`,
        headingId: `topic-${index}`,
      }),
    );
    expect(search(many, "tenchi").length).toBe(MAX_RESULTS);
  });

  test("results include a snippet around the first match", () => {
    const results = search(entries, "envelope");
    expect(results[0]?.snippet).toContain("envelope");
  });
});

describe("makeSnippet", () => {
  test("windows long bodies around the match", () => {
    const body = `${"start of the body ".repeat(20)}needle appears here${" tail".repeat(30)}`;
    const snippet = makeSnippet(body, ["needle"]);
    expect(snippet).toContain("needle");
    expect(snippet.length).toBeLessThan(160);
    expect(snippet.startsWith("…")).toBe(true);
  });

  test("falls back to the body head without a match", () => {
    const snippet = makeSnippet("short body", ["missing"]);
    expect(snippet).toBe("short body");
  });
});
