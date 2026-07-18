import { describe, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import GithubSlugger from "github-slugger";
import { docsRoutes } from "./lib/docs";
import { cleanPage } from "./scripts/build-llms-txt";

const docsRoot = import.meta.dir;
const contentRoot = path.join(docsRoot, "content");

function pagePath(route: string): string {
  return route === "/"
    ? path.join(contentRoot, "index.mdx")
    : path.join(contentRoot, `${route.slice(1)}.mdx`);
}

function withoutCode(source: string): string {
  return source.replace(/```[^\n`]*\n[\s\S]*?```/g, "");
}

function headingIds(source: string): Set<string> {
  const slugger = new GithubSlugger();
  const ids = new Set<string>();
  for (const match of withoutCode(source).matchAll(/^#{1,6}\s+(.+)$/gm)) {
    const heading = (match[1] ?? "")
      .replace(/\s+#+\s*$/, "")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/[`*]/g, "")
      .trim();
    ids.add(slugger.slug(heading));
  }
  return ids;
}

describe("documentation", () => {
  test("every registered route has an MDX page", () => {
    expect(
      docsRoutes
        .map((route) => pagePath(route.path))
        .filter((file) => !existsSync(file)),
    ).toEqual([]);
  });

  test("internal links resolve to registered pages and headings", () => {
    const liveRoutes = new Set<string>(docsRoutes.map((route) => route.path));
    const pageHeadings = new Map<string, Set<string>>(
      docsRoutes.map((route) => {
        const source = readFileSync(pagePath(route.path), "utf8");
        return [route.path, headingIds(source)] as const;
      }),
    );
    const problems: string[] = [];

    for (const route of docsRoutes) {
      const source = withoutCode(readFileSync(pagePath(route.path), "utf8"));
      for (const match of source.matchAll(/\]\(([/#][^()\s]*)\)/g)) {
        const target = match[1] ?? "";
        const [rawPath, anchor] = target.split("#", 2);
        const targetPath = rawPath === "" ? route.path : rawPath;
        if (targetPath.includes(".")) continue;
        if (!liveRoutes.has(targetPath)) {
          problems.push(`${route.path}: missing page ${targetPath}`);
        } else if (anchor && !pageHeadings.get(targetPath)?.has(anchor)) {
          problems.push(`${route.path}: missing anchor ${target}`);
        }
      }
    }

    expect(problems).toEqual([]);
  });

  test("agent text extraction keeps prose and code", () => {
    const cleaned = cleanPage(
      `# Title\n\nText.\n\n\`\`\`python\nprint(1)\n\`\`\``,
    );
    expect(cleaned).toContain("Text.");
    expect(cleaned).toContain("print(1)");
    expect(cleaned).not.toContain("# Title");
  });
});
