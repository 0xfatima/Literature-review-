import Logo from "@/components/brand/Logo";

/* A faceted "glass" pattern: a jittered grid split into pointed triangles, in light muted blues that fade toward the
   form, like light through a prism or a glass façade. Generated once from a fixed seed, so the server and the
   browser draw the same picture. */
const W = 600;
const H = 900;
const COLS = 6;
const ROWS = 9;
const PALETTE = ["#eef3f7", "#e3ebf2", "#d8e3ec", "#cddbe6", "#c2d2e0", "#f5f8fa", "#d2dee8", "#bccddc"];

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

const FACETS = (() => {
  const r = rng(7);
  const pts: [number, number][][] = [];
  for (let y = 0; y <= ROWS; y++) {
    const row: [number, number][] = [];
    for (let x = 0; x <= COLS; x++) {
      const edgeX = x === 0 || x === COLS;
      const edgeY = y === 0 || y === ROWS;
      const jx = edgeX ? 0 : (r() - 0.5) * (W / COLS) * 0.7;
      const jy = edgeY ? 0 : (r() - 0.5) * (H / ROWS) * 0.7;
      // lean every point to the right as it goes up: long, pointed shards like the panes of a façade
      row.push([(x * W) / COLS + jx + (edgeX ? 0 : (ROWS - y) * 6), (y * H) / ROWS + jy]);
    }
    pts.push(row);
  }
  const tris: { d: string; fill: string; o: number }[] = [];
  for (let y = 0; y < ROWS; y++) {
    for (let x = 0; x < COLS; x++) {
      const at = (yy: number, xx: number): [number, number] => pts[yy]?.[xx] ?? [0, 0];
      const a = at(y, x);
      const b = at(y, x + 1);
      const c = at(y + 1, x);
      const d = at(y + 1, x + 1);
      const flip = (x + y) % 2 === 0;
      const t1: [number, number][] = flip ? [a, b, d] : [a, b, c];
      const t2: [number, number][] = flip ? [a, d, c] : [b, d, c];
      for (const t of [t1, t2]) {
        const fade = 1 - (x / COLS) * 0.55; // lighter toward the form
        tris.push({
          d: `M${t.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" L")} Z`,
          fill: PALETTE[Math.floor(r() * PALETTE.length)] ?? "#e3ebf2",
          o: +(fade * (0.75 + r() * 0.25)).toFixed(2),
        });
      }
    }
  }
  return tris;
})();

/** Sign-in / sign-up: full-screen split with no outer margins. Left, the faceted glass pattern; right, the form on
 *  white. On small screens only the form is shown. */
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
    <main className="grid min-h-screen bg-canvas md:grid-cols-[1.15fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-[#f3f6f9] md:block" aria-hidden>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid slice"
          className="absolute inset-0 h-full w-full"
        >
          <defs>
            <linearGradient id="auth-shard" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#a9bdd0" stopOpacity="0.85" />
              <stop offset="1" stopColor="#e6edf3" stopOpacity="0.5" />
            </linearGradient>
            <linearGradient id="auth-sheen" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#ffffff" stopOpacity="0.55" />
              <stop offset="0.5" stopColor="#ffffff" stopOpacity="0" />
              <stop offset="1" stopColor="#ffffff" stopOpacity="0.6" />
            </linearGradient>
          </defs>
          {FACETS.map((f, i) => (
            <path
              key={i}
              d={f.d}
              fill={f.fill}
              fillOpacity={f.o}
              stroke="#ffffff"
              strokeOpacity="0.9"
              strokeWidth="1.2"
            />
          ))}
          <rect width={W} height={H} fill="url(#auth-sheen)" />
          {/* a few large pointed shards over the pattern, with a light rim, like the panes of a prism */}
          <path
            d="M-20 120 L330 40 L150 520 Z"
            fill="url(#auth-shard)"
            stroke="#ffffff"
            strokeOpacity=".85"
            strokeWidth="14"
          />
          <path
            d="M380 -20 L640 -20 L520 300 Z"
            fill="url(#auth-shard)"
            fillOpacity=".7"
            stroke="#ffffff"
            strokeWidth="10"
          />
          <path
            d="M60 640 L360 560 L250 940 Z"
            fill="url(#auth-shard)"
            fillOpacity=".6"
            stroke="#ffffff"
            strokeWidth="10"
          />
        </svg>
        <div className="absolute bottom-8 left-8 right-8 rounded-2xl bg-white/70 p-6 backdrop-blur-md lg:bottom-12 lg:left-12 lg:right-12">
          <p className="max-w-md text-2xl font-semibold leading-snug text-[#3d4f61]">
            From a thousand papers to the ones that matter.
          </p>
          <p className="mt-2 max-w-sm text-sm text-[#5d7184]">
            Search, screen, read and synthesise a systematic review, with an AI that shows its work.
          </p>
        </div>
      </aside>
      <section className="flex flex-col justify-center px-6 py-10 sm:px-12 lg:px-20">
        <div className="mx-auto flex w-full max-w-sm flex-col gap-6">
          <Logo size={30} />
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
          </div>
          {children}
        </div>
      </section>
    </main>
  );
}
