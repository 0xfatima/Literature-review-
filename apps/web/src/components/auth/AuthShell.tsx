import Link from "next/link";
import AuthFrame from "@/components/auth/AuthFrame";

/** Same frame as the sign-in page, for the account-recovery pages. */
export default function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <AuthFrame title={title}>
      {children}
      <p className="text-sm">
        <Link href="/login">Back to sign in</Link>
      </p>
    </AuthFrame>
  );
}
