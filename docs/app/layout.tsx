import { readFileSync } from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { Footer } from "@/components/footer";
import { Nav } from "@/components/nav";
import { Toc } from "@/components/toc";
import { withBasePath } from "@/lib/base-path";
import { getSiteUrl, siteDescription, siteName } from "@/lib/seo";
import "./globals.css";

function frameworkVersion(): string | undefined {
  try {
    const project = readFileSync(
      path.resolve(process.cwd(), "../pyproject.toml"),
      "utf8",
    );
    return project.match(/^version = "([^"]+)"$/m)?.[1];
  } catch {
    return undefined;
  }
}

const themeBootstrap = `(function(){try{var t=localStorage.getItem("tenchi-docs-theme");var d=t==="dark"||(t!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);if(d)document.documentElement.classList.add("dark")}catch(e){}})()`;

export const metadata: Metadata = {
  metadataBase: new URL(getSiteUrl()),
  applicationName: siteName,
  title: { default: siteName, template: `%s — ${siteName}` },
  description: siteDescription,
  keywords: [
    "Tenchi",
    "Python",
    "ASGI",
    "REST API",
    "contract-first",
    "OpenAPI",
    "Pydantic",
    "Starlette",
  ],
  authors: [{ name: "Taylor Bryant", url: "https://taylor.page" }],
  creator: "Taylor Bryant",
  icons: { icon: withBasePath("/icon.svg") },
  manifest: withBasePath("/manifest.webmanifest"),
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-bg font-sans text-ink antialiased">
        <script
          // biome-ignore lint/security/noDangerouslySetInnerHtml: static pre-paint theme bootstrap
          dangerouslySetInnerHTML={{ __html: themeBootstrap }}
        />
        <a
          href="#content"
          className="sr-only z-[200] rounded bg-bg px-3 py-2 text-accent focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
        >
          Skip to content
        </a>
        <Nav version={frameworkVersion()} />
        <main
          id="content"
          className="mx-auto max-w-3xl px-6 py-14 lg:px-12 lg:py-20"
        >
          {children}
        </main>
        <Toc />
        <Footer />
      </body>
    </html>
  );
}
