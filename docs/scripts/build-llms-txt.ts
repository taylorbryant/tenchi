import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  defaultSiteUrl,
  docsSections,
  siteDescription,
  siteName,
} from "../lib/docs";

const docsRoot = path.resolve(import.meta.dir, "..");
const contentRoot = path.join(docsRoot, "content");
const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? defaultSiteUrl).replace(
  /\/$/,
  "",
);

function pagePath(route: string): string {
  return route === "/"
    ? path.join(contentRoot, "index.mdx")
    : path.join(contentRoot, `${route.slice(1)}.mdx`);
}

function pageUrl(route: string): string {
  return `${siteUrl}${route === "/" ? "" : route}`;
}

export function cleanPage(source: string): string {
  const output: string[] = [];
  let inFence = false;
  let skippedTitle = false;

  for (const line of source.split("\n")) {
    if (/^```/.test(line)) {
      inFence = !inFence;
      output.push(line);
      continue;
    }
    if (inFence) {
      output.push(line);
      continue;
    }
    if (/^\s*(import|export)\s/.test(line)) continue;
    if (!skippedTitle && /^#\s+/.test(line)) {
      skippedTitle = true;
      continue;
    }
    const cleaned = line
      .replace(/<\/?[A-Za-z][^<>]*\/?>/g, "")
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
    if (line.trim() && !cleaned.trim()) continue;
    output.push(cleaned.trimEnd());
  }

  return output
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function main(): void {
  const indexLines = [
    `# ${siteName}`,
    "",
    `> ${siteDescription}`,
    "",
    `Tenchi is a contract-first Python framework for typed JSON APIs. The full extracted documentation is available at ${siteUrl}/llms-full.txt.`,
  ];
  const fullPages: string[] = [];

  for (const section of docsSections) {
    indexLines.push("", `## ${section.label}`, "");
    for (const route of section.routes) {
      indexLines.push(
        `- [${route.title}](${pageUrl(route.path)}): ${route.description}`,
      );
      fullPages.push(
        [
          `# ${route.title}`,
          `Source: ${pageUrl(route.path)}`,
          "",
          cleanPage(readFileSync(pagePath(route.path), "utf8")),
        ].join("\n"),
      );
    }
  }

  indexLines.push(
    "",
    "## Optional",
    "",
    `- [llms-full.txt](${siteUrl}/llms-full.txt): Full text of every documentation page.`,
    "- [GitHub](https://github.com/taylorbryant/tenchi): Source code, changelog, examples, and issue tracker.",
  );

  writeFileSync(
    path.join(docsRoot, "public", "llms.txt"),
    `${indexLines.join("\n")}\n`,
  );
  writeFileSync(
    path.join(docsRoot, "public", "llms-full.txt"),
    `${fullPages.join("\n\n---\n\n")}\n`,
  );
  console.log(`llms-txt: ${fullPages.length} pages`);
}

if (import.meta.main) main();
