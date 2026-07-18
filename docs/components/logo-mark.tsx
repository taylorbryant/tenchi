export function LogoMark({
  className = "size-6 shrink-0",
}: {
  className?: string;
}) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="18" className="fill-accent" />
      <path d="M15 19h34v8H36v22h-8V27H15z" className="fill-white" />
    </svg>
  );
}
