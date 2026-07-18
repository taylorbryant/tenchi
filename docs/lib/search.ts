export type SearchEntry = {
  route: string;
  pageTitle: string;
  sectionLabel: string;
  heading: string;
  headingId: string;
  body: string;
};

export type SearchResult = {
  entry: SearchEntry;
  score: number;
  snippet: string;
};

const DEFAULT_ROUTES = [
  "/getting-started",
  "/concepts",
  "/contracts",
  "/server",
  "/client",
  "/openapi",
];
const MAX_RESULTS = 12;

export function tokenize(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
}

function matchQuality(field: string, term: string): number {
  const value = field.toLowerCase();
  const index = value.indexOf(term);
  if (index < 0) return 0;
  if (value === term) return 4;
  if (index === 0) return 3;
  if (!/[a-z0-9]/i.test(value[index - 1] ?? "")) return 2.4;
  return 1.2;
}

function score(entry: SearchEntry, terms: string[]): number {
  let total = 0;
  for (const term of terms) {
    const best = Math.max(
      10 * matchQuality(entry.heading, term),
      8 * matchQuality(entry.pageTitle, term),
      3 * matchQuality(entry.sectionLabel, term),
      1.2 * matchQuality(entry.body, term),
    );
    if (best === 0) return 0;
    total += best;
  }
  return entry.headingId === "" ? total * 1.15 : total;
}

export function makeSnippet(body: string, terms: string[]): string {
  const maximum = 140;
  const lower = body.toLowerCase();
  const match = terms
    .map((term) => lower.indexOf(term))
    .filter((index) => index >= 0)
    .sort((left, right) => left - right)[0];
  let start = match === undefined ? 0 : Math.max(0, match - 40);
  if (start > 0) {
    const space = body.indexOf(" ", start);
    if (space >= 0 && space < (match ?? 0)) start = space + 1;
  }
  const end = Math.min(body.length, start + maximum);
  return `${start > 0 ? "…" : ""}${body.slice(start, end).trim()}${end < body.length ? "…" : ""}`;
}

export function search(entries: SearchEntry[], query: string): SearchResult[] {
  const terms = tokenize(query);
  if (terms.length === 0) {
    return DEFAULT_ROUTES.flatMap((route) => {
      const entry = entries.find(
        (candidate) => candidate.route === route && candidate.headingId === "",
      );
      return entry
        ? [{ entry, score: 0, snippet: makeSnippet(entry.body, []) }]
        : [];
    });
  }

  return entries
    .map((entry) => ({ entry, score: score(entry, terms), snippet: "" }))
    .filter((result) => result.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, MAX_RESULTS)
    .map((result) => ({
      ...result,
      snippet: makeSnippet(result.entry.body, terms),
    }));
}
