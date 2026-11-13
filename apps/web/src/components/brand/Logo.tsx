/** Prismatic: a clear glass prism splitting one beam (the search) into muted bands (include / maybe / exclude),
 *  next to a lowercase italic wordmark. SVG and system fonts only; nothing is fetched. */
export function PrismMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className={className}>
      <path d="M1 18 L10.6 16.2" stroke="#8a9bab" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M20.2 16.4 L31 12.6" stroke="#8fbf9f" strokeWidth="2" strokeLinecap="round" />
      <path d="M20.7 18.4 L31 17.8" stroke="#d9bf7f" strokeWidth="2" strokeLinecap="round" />
      <path d="M21.2 20.4 L31 23" stroke="#d99a96" strokeWidth="2" strokeLinecap="round" />
      <path
        d="M14 4 L25 26 L3 26 Z"
        fill="#dce6ef"
        fillOpacity=".35"
        stroke="#6f8597"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M14 4 L17.5 12 L14 26" fill="none" stroke="#6f8597" strokeOpacity=".45" strokeWidth="1" />
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
