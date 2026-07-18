import type { MDXComponents } from "mdx/types";
import Link from "next/link";
import { isValidElement } from "react";
import { CopyButton } from "@/components/copy-button";

function codeLanguage(children: React.ReactNode): string | undefined {
  if (!isValidElement<{ className?: string }>(children)) return undefined;
  return children.props.className?.match(/language-(\S+)/)?.[1];
}

export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    h1: ({ children }) => (
      <h1 className="mb-4 text-3xl font-semibold tracking-tight text-ink">
        {children}
      </h1>
    ),
    h2: ({ children, id }) => (
      <h2
        id={id}
        className="mb-4 mt-12 scroll-mt-24 text-xl font-semibold tracking-tight text-ink"
      >
        {children}
      </h2>
    ),
    h3: ({ children, id }) => (
      <h3
        id={id}
        className="mb-3 mt-8 scroll-mt-24 text-base font-semibold text-ink"
      >
        {children}
      </h3>
    ),
    p: ({ children }) => (
      <p className="mb-4 leading-relaxed text-ink-light">{children}</p>
    ),
    ul: ({ children }) => (
      <ul className="mb-4 list-disc space-y-1 pl-6 leading-relaxed text-ink-light">
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol className="mb-4 list-decimal space-y-1 pl-6 leading-relaxed text-ink-light">
        {children}
      </ol>
    ),
    code: ({ children, className, ...props }) => {
      const inline =
        typeof children === "string" && !className?.includes("language-");
      return inline ? (
        <code
          {...props}
          className="rounded bg-code-bg px-1.5 py-0.5 font-mono text-sm"
        >
          {children}
        </code>
      ) : (
        <code {...props} className={className}>
          {children}
        </code>
      );
    },
    pre: ({ children, className, ...props }) => {
      const language = codeLanguage(children);
      return (
        <div data-code-block className="group relative mb-6">
          <div className="absolute right-2.5 top-2.5 z-10">
            {language && (
              <span className="absolute right-0 top-0 hidden h-7 items-center text-[10px] font-medium uppercase tracking-wider text-ink-muted group-hover:opacity-0 sm:flex">
                {language}
              </span>
            )}
            <CopyButton />
          </div>
          <pre
            {...props}
            className={[
              className,
              "overflow-x-auto rounded-lg border border-border p-4 text-sm leading-relaxed [&>code]:bg-transparent [&>code]:p-0",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {children}
          </pre>
        </div>
      );
    },
    a: ({ href = "", children }) => {
      const className =
        "text-accent underline decoration-accent/30 underline-offset-2 transition-colors hover:text-accent-strong hover:decoration-accent";
      return href.startsWith("/") ? (
        <Link href={href} className={className}>
          {children}
        </Link>
      ) : (
        <a href={href} className={className}>
          {children}
        </a>
      );
    },
    blockquote: ({ children }) => (
      <blockquote className="my-4 border-l-2 border-accent/30 pl-4 italic text-ink-muted">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-12 border-border" />,
    table: ({ children }) => (
      <div className="my-6 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          {children}
        </table>
      </div>
    ),
    thead: ({ children }) => (
      <thead className="border-b border-border text-ink">{children}</thead>
    ),
    tbody: ({ children }) => (
      <tbody className="divide-y divide-border">{children}</tbody>
    ),
    th: ({ children }) => (
      <th className="py-2 pr-4 font-medium text-ink">{children}</th>
    ),
    td: ({ children }) => (
      <td className="py-2 pr-4 align-top text-ink-light">{children}</td>
    ),
    ...components,
  };
}
