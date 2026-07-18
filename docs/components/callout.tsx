const styles = {
  note: {
    container: "border-accent/30 bg-accent/[0.06]",
    label: "text-accent",
  },
  warning: {
    container:
      "border-amber-500/40 bg-amber-500/[0.08] dark:border-amber-300/40 dark:bg-amber-300/[0.08]",
    label: "text-amber-700 dark:text-amber-300",
  },
} as const;

export function Callout({
  type = "note",
  title,
  children,
}: {
  type?: keyof typeof styles;
  title?: string;
  children: React.ReactNode;
}) {
  const style = styles[type];
  return (
    <div
      className={`my-6 rounded-lg border px-4 py-3 ${style.container} [&_p]:mb-0 [&_p]:text-sm [&_p+p]:mt-2`}
    >
      <div className={`mb-1 text-sm font-medium ${style.label}`}>
        {title ?? (type === "warning" ? "Warning" : "Note")}
      </div>
      <div className="text-sm leading-relaxed text-ink-light">{children}</div>
    </div>
  );
}
