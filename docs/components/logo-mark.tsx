export function LogoMark({
  className = "size-6 shrink-0",
}: {
  className?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="18" className="fill-brand" />
      <path
        d="M28 12h9v12h12v9H37v12.5c0 3 1.5 4.5 4.7 4.5H49v9h-8.5C32.2 59 28 54.7 28 46V33h-9v-9h9z"
        className="fill-bg"
      />
    </svg>
  );
}
