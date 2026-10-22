import { Suspense } from "react";
import AuthForm from "@/components/auth/AuthForm";

export default function LoginPage() {
  // AuthForm reads ?next= (useSearchParams), which requires a Suspense boundary for static rendering.
  return (
    <Suspense>
      <AuthForm mode="login" />
    </Suspense>
  );
}
