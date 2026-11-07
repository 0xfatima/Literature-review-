import Link from "next/link";

/** Same frame as the sign-in page, for the account-recovery pages. */
export default function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-6">
      <div>
        <p className="mb-1 text-sm text-mark">Systematic Review Scientist</p>
        <h1 className="text-2xl font-semibold">{title}</h1>
      </div>
      {children}
      <p className="text-sm">
        <Link href="/login">Back to sign in</Link>
      </p>
    </main>
  );
}
