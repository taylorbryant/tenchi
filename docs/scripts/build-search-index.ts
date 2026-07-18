import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import GithubSlugger from "github-slugger";
import { docsRoutes, docsSections } from "../lib/docs";

const docsRoot = path.resolve(import.meta.dir, "..");
const contentRoot = path.join(docsRoot, "content");
const outputPath = path.join(docsRoot, "public", "search-index.json");
const maximumBodyLength = 500;

function cleanInline(value: string): string {
  return value
    .replace(/^\s*>+\s?/, "")
    .replace(/^\s*([-+*]|\d+\.)\s+/, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<\/?[A-Za-z][^<>]*\/?>/g, " ")
    .replace(/[`*|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function pagePath(route: string): string {
  return route === "/"
    ? path.join(contentRoot, "index.mdx")
    : path.join(contentRoot, `${route.slice(1)}.mdx`);
}

type Entry = {
  route: string;
  pageTitle: string;
  sectionLabel: string;
  heading: string;
  headingId: string;
  body: string;
};

function parsePage(
  source: string,
  metadata: Omit<Entry, "heading" | "headingId" | "body">,
): Entry[] {
  const slugger = new GithubSlugger();
  const sections: Array<{
    heading: string;
    headingId: string;
    prose: string[];
    code: string[];
  }> = [
    {
      heading: metadata.pageTitle,
      headingId: "",
      prose: [],
      code: [],
    },
  ];
  let current = sections[0];
  let inFence = false;

  for (const line of source.split("\n")) {
    if (/^```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) {
      const code = line.trim();
      if (code) current.code.push(code);
      continue;
    }
    if (/^\s*(import|export)\s/.test(line)) continue;

    const match = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (match) {
      const heading = cleanInline(match[2] ?? "");
      const headingId = slugger.slug(heading);
      const depth = match[1]?.length ?? 0;
      if (depth === 2 || depth === 3) {
        current = { heading, headingId, prose: [], code: [] };
        sections.push(current);
      }
      continue;
    }

    const prose = cleanInline(line);
    if (prose) current.prose.push(prose);
  }

  return sections.map((section) => {
    const prose = section.prose.join(" ");
    const room = Math.max(0, maximumBodyLength - prose.length);
    const code = section.code.join(" ").slice(0, room);
    return {
      ...metadata,
      heading: section.heading,
      headingId: section.headingId,
      body: `${prose} ${code}`.trim().slice(0, maximumBodyLength),
    };
  });
}

const entries: Entry[] = [];
for (const section of docsSections) {
  for (const route of section.routes) {
    entries.push(
      ...parsePage(readFileSync(pagePath(route.path), "utf8"), {
        route: route.path,
        pageTitle: route.title,
        sectionLabel: section.label,
      }),
    );
  }
}

writeFileSync(outputPath, JSON.stringify(entries));
console.log(
  `search-index: ${entries.length} entries from ${docsRoutes.length} pages`,
);
