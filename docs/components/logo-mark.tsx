export function LogoMark({
  className = "size-6 shrink-0",
}: {
  className?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="18" className="fill-brand" />
      <path
        d="M26 8h9v12h12v9H35v12.5c0 3 1.5 4.5 4.7 4.5H47v9h-8.5C30.2 55 26 50.7 26 42V29h-9v-9h9z"
        className="fill-bg"
      />
    </svg>
  );
}
