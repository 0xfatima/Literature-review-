/** Prismatic: a prism that splits one beam (the search) into the bands of a review (green = include, amber = maybe,
 *  red = exclude), next to a lowercase italic wordmark. Drawn in SVG and system serif fonts, so nothing is fetched. */
export function PrismMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className={className}>
      <path d="M1 17.5 L11 15.6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" opacity=".55" />
      <path d="M14 4 L25 26 L3 26 Z" fill="rgb(var(--mark))" />
      <path d="M14 4 L25 26 L14 26 Z" fill="rgb(var(--mark))" opacity=".75" />
      <path d="M14 4 L18.5 13 L14 26 Z" fill="white" opacity=".22" />
      <path d="M20 16.2 L31 12" stroke="rgb(var(--ok))" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M20.6 18.4 L31 17.6" stroke="rgb(var(--warn))" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M21 20.6 L31 23.2" stroke="rgb(var(--danger))" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return <span className={`wordmark ${className}`}>prismatic</span>;
}

export default function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 text-ink ${className}`} aria-label="Prismatic">
      <PrismMark size={size} />
      <Wordmark className={size >= 36 ? "text-4xl" : size >= 28 ? "text-2xl" : "text-xl"} />
    </span>
  );
}
