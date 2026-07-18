import { withBasePath } from "@/lib/base-path";

export function Footer() {
  return (
    <footer className="mx-auto max-w-3xl border-t border-border">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-8 text-sm text-ink-muted lg:px-12">
        <span>
          Created by{" "}
          <a
            href="https://taylor.page"
            className="text-accent underline decoration-accent/30 underline-offset-2 transition-colors hover:text-accent-strong hover:decoration-accent"
          >
            Taylor Bryant
          </a>
        </span>
        <a
          href={withBasePath("/llms.txt")}
          className="text-accent underline decoration-accent/30 underline-offset-2 transition-colors hover:text-accent-strong hover:decoration-accent"
        >
          llms.txt
        </a>
      </div>
    </footer>
  );
}
