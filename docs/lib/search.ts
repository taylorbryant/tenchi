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

export const MAX_RESULTS = 12;

const DEFAULT_ROUTES = [
  "/getting-started",
  "/concepts",
  "/contracts",
  "/server",
  "/client",
  "/openapi",
];

const FIELD_WEIGHTS = {
  heading: 10,
  pageTitle: 8,
  sectionLabel: 3,
  body: 1.2,
} as const;

export function tokenize(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
}

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && /[a-z0-9]/i.test(char);
}

// Match quality for one term in one lowercased field:
// exact > prefix > word-start > substring > none.
function matchQuality(fieldLower: string, term: string): number {
  const index = fieldLower.indexOf(term);
  if (index < 0) return 0;
  if (fieldLower === term) return 4;
  if (index === 0) return 3;
  if (!isWordChar(fieldLower[index - 1])) return 2.4;
  return 1.2;
}

type LoweredEntry = {
  entry: SearchEntry;
  heading: string;
  pageTitle: string;
  sectionLabel: string;
  body: string;
};

function lower(entry: SearchEntry): LoweredEntry {
  return {
    entry,
    heading: entry.heading.toLowerCase(),
    pageTitle: entry.pageTitle.toLowerCase(),
    sectionLabel: entry.sectionLabel.toLowerCase(),
    body: entry.body.toLowerCase(),
  };
}

// Every term must match somewhere (AND semantics). Each term contributes its
// best field match; page-intro entries get a small boost so page-level hits
// outrank their own subsections on similar scores.
function scoreEntry(lowered: LoweredEntry, terms: string[]): number {
  let total = 0;
  for (const term of terms) {
    const best = Math.max(
      FIELD_WEIGHTS.heading * matchQuality(lowered.heading, term),
      FIELD_WEIGHTS.pageTitle * matchQuality(lowered.pageTitle, term),
      FIELD_WEIGHTS.sectionLabel * matchQuality(lowered.sectionLabel, term),
      FIELD_WEIGHTS.body * matchQuality(lowered.body, term),
    );
    if (best === 0) return 0;
    total += best;
  }
  return lowered.entry.headingId === "" ? total * 1.15 : total;
}

export function makeSnippet(body: string, terms: string[]): string {
  const maxLength = 140;
  const bodyLower = body.toLowerCase();
  let matchIndex = -1;
  for (const term of terms) {
    const index = bodyLower.indexOf(term);
    if (index >= 0 && (matchIndex < 0 || index < matchIndex)) {
      matchIndex = index;
    }
  }
  if (matchIndex < 0) {
    return body.length > maxLength
      ? `${body.slice(0, maxLength).trimEnd()}…`
      : body;
  }
  let start = Math.max(0, matchIndex - 40);
  if (start > 0) {
    const space = body.indexOf(" ", start);
    if (space >= 0 && space < matchIndex) start = space + 1;
  }
  const end = Math.min(body.length, start + maxLength);
  const prefix = start > 0 ? "…" : "";
  const suffix = end < body.length ? "…" : "";
  return `${prefix}${body.slice(start, end).trim()}${suffix}`;
}

function defaultResults(entries: SearchEntry[]): SearchResult[] {
  const results: SearchResult[] = [];
  for (const route of DEFAULT_ROUTES) {
    const entry = entries.find(
      (candidate) => candidate.route === route && candidate.headingId === "",
    );
    if (entry) {
      results.push({ entry, score: 0, snippet: makeSnippet(entry.body, []) });
    }
  }
  return results;
}

export function search(entries: SearchEntry[], query: string): SearchResult[] {
  const terms = tokenize(query);
  if (terms.length === 0) return defaultResults(entries);

  const results: SearchResult[] = [];
  for (const entry of entries) {
    const score = scoreEntry(lower(entry), terms);
    if (score > 0) {
      results.push({ entry, score, snippet: "" });
    }
  }
  // Stable sort keeps docsSections priority order for equal scores.
  results.sort((a, b) => b.score - a.score);
  const top = results.slice(0, MAX_RESULTS);
  for (const result of top) {
    result.snippet = makeSnippet(result.entry.body, terms);
  }
  return top;
}
