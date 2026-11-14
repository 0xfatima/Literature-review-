/** Prismatic: the lowercase italic wordmark (system serif; nothing is fetched). */
export function Wordmark({ className = "" }: { className?: string }) {
  return <span className={`wordmark ${className}`}>prismatic</span>;
}

export default function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center text-ink ${className}`} aria-label="Prismatic">
      <Wordmark className={size >= 36 ? "text-4xl" : size >= 28 ? "text-2xl" : "text-xl"} />
    </span>
  );
}
