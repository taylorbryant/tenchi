export function LogoMark({
  className = "size-6 shrink-0",
}: {
  className?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <path d="M28 20h8v24h-8z" className="fill-brand" />
      <path d="M4 15.5 32 6l28 9.5L32 25z" className="fill-brand" />
      <path d="M4 48.5 32 39l28 9.5L32 58z" className="fill-brand-secondary" />
    </svg>
  );
}
