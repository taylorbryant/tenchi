export function LogoMark({
  className = "size-6 shrink-0",
}: {
  className?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="18" className="fill-brand" />
      <g className="fill-bg">
        <rect x="8" y="12" width="48" height="7" rx="2" />
        <rect x="17" y="19" width="7" height="33" rx="2" />
        <rect x="40" y="19" width="7" height="33" rx="2" />
        <rect x="13" y="28" width="38" height="6" rx="2" />
      </g>
    </svg>
  );
}
