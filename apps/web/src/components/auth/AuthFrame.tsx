import Logo from "@/components/brand/Logo";

/** The sign-in / sign-up frame: a rounded card on a soft warm backdrop. Left, an illustration of the idea behind the
 *  app (one beam of search results refracted into include / maybe / exclude); right, the form. On small screens only
 *  the form is shown. Colours come from the theme tokens, so light and dark mode both work. */
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
    <main className="auth-backdrop flex min-h-screen items-center justify-center p-4 sm:p-8">
      <div className="grid w-full max-w-5xl overflow-hidden rounded-3xl border border-line bg-canvas shadow-2xl md:grid-cols-[1.1fr_1fr]">
        <aside className="auth-art relative hidden min-h-[560px] flex-col justify-between p-10 text-white md:flex">
          <span className="wordmark text-3xl">prismatic</span>
          <svg viewBox="0 0 400 260" className="w-full" aria-hidden>
            {Array.from({ length: 7 }, (_, i) => (
              <line
                key={i}
                x1="0"
                y1={112 + i * 6}
                x2="150"
                y2={128}
                stroke="white"
                strokeOpacity={0.12 + i * 0.05}
                strokeWidth="2"
              />
            ))}
            <path
              d="M200 30 L290 210 L110 210 Z"
              fill="white"
              fillOpacity=".16"
              stroke="white"
              strokeOpacity=".7"
              strokeWidth="2"
            />
            <path d="M200 30 L235 100 L200 210 Z" fill="white" fillOpacity=".12" />
            <path d="M246 122 L400 70" stroke="rgb(74 222 128)" strokeWidth="6" strokeLinecap="round" />
            <path d="M250 134 L400 128" stroke="rgb(250 204 21)" strokeWidth="6" strokeLinecap="round" />
            <path d="M254 146 L400 190" stroke="rgb(248 113 113)" strokeWidth="6" strokeLinecap="round" />
            <circle cx="372" cy="79" r="5" fill="white" />
            <circle cx="380" cy="129" r="5" fill="white" fillOpacity=".7" />
            <circle cx="368" cy="182" r="5" fill="white" fillOpacity=".5" />
          </svg>
          <div>
            <p className="wordmark text-[2.1rem] leading-tight">
              From a thousand papers to the ones that matter.
            </p>
            <p className="mt-3 max-w-sm text-sm text-white/80">
              Search, screen, read and synthesise: a systematic review with an AI that shows its work.
            </p>
          </div>
        </aside>
        <section className="flex flex-col justify-center gap-6 px-6 py-10 sm:px-12">
          <Logo size={32} className="md:hidden" />
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
          </div>
          {children}
        </section>
      </div>
    </main>
  );
}
