import { Wordmark } from "@/components/brand/Logo";

/* Rounded capsules at 45°, like the reference design, in ice blue and the app's golden/olive. Each capsule is drawn
   from its rounded end (ex, ey), pointing along `angle`. */
function Capsule({
  ex,
  ey,
  thick,
  len = 900,
  angle = 225,
  ...rest
}: { ex: number; ey: number; thick: number; len?: number; angle?: number } & React.SVGProps<SVGRectElement>) {
  return (
    <rect
      x={-thick / 2}
      y={-thick / 2}
      width={len}
      height={thick}
      rx={thick / 2}
      transform={`translate(${ex} ${ey}) rotate(${angle})`}
      {...rest}
    />
  );
}

function Art() {
  return (
    <svg
      viewBox="0 0 420 800"
      preserveAspectRatio="xMaxYMid slice"
      className="absolute inset-0 h-full w-full"
    >
      {/* gradients run along each capsule from its rounded end (offset 0) toward the edge of the panel */}
      <defs>
        <linearGradient id="cap-gold-ice" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#b8954a" />
          <stop offset="0.22" stopColor="#c9b27a" />
          <stop offset="0.5" stopColor="#bcdcee" />
        </linearGradient>
        <linearGradient id="cap-olive-ice" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#8f9445" />
          <stop offset="0.35" stopColor="#c4d8e2" />
        </linearGradient>
        <linearGradient id="cap-ice-gold" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#a7d0e7" />
          <stop offset="0.35" stopColor="#d9c38d" />
        </linearGradient>
        <linearGradient id="cap-gold-olive" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#a39a4a" />
          <stop offset="0.3" stopColor="#dcc27e" />
        </linearGradient>
      </defs>
      {/* pale ring around the large capsule */}
      <Capsule ex={262} ey={372} thick={330} fill="none" stroke="#e3eff6" strokeWidth="30" />
      {/* the large capsule */}
      <Capsule ex={240} ey={350} thick={250} fill="url(#cap-gold-ice)" />
      {/* top, partly off the edge */}
      <Capsule ex={330} ey={70} thick={150} fill="url(#cap-olive-ice)" />
      {/* bottom left */}
      <Capsule ex={70} ey={700} thick={120} fill="url(#cap-gold-olive)" />
      {/* bottom, pointing the other way */}
      <Capsule ex={250} ey={700} thick={180} angle={45} fill="url(#cap-ice-gold)" />
    </svg>
  );
}

/** Sign-in / sign-up: one white surface edge to edge (no outer margins). Left, the capsule pattern; right, the form,
 *  centred. On small screens only the form is shown. */
export default function AuthFrame({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <main className="grid min-h-screen bg-white text-[#1f2328] md:grid-cols-[1fr_1fr]">
      <aside className="relative hidden overflow-hidden md:block" aria-hidden>
        <Art />
      </aside>
      <section className="flex flex-col justify-center px-6 py-10 sm:px-12">
        <div className="mx-auto flex w-full max-w-sm flex-col items-stretch gap-6">
          <div className="text-center">
            <Wordmark className="text-2xl text-[#5f6b16]" />
            <h1 className="mt-4 text-3xl font-bold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-[#59636e]">{subtitle}</p>}
          </div>
          {children}
        </div>
      </section>
    </main>
  );
}
