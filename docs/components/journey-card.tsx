import Link from "next/link";

export function JourneyCard({
  label,
  title,
  description,
  href,
}: {
  label: string;
  title: string;
  description: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-xl border border-border bg-surface p-5 no-underline transition-colors hover:border-accent/40 hover:bg-surface-muted"
    >
      <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">
        {label}
      </span>
      <span className="mt-2 block font-semibold text-ink group-hover:text-accent">
        {title}
      </span>
      <span className="mt-2 block text-sm leading-relaxed text-ink-light">
        {description}
      </span>
    </Link>
  );
}
