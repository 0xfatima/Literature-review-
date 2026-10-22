import { Suspense } from "react";
import AuthForm from "@/components/auth/AuthForm";

export default function RegisterPage() {
  // AuthForm reads ?next= (useSearchParams), which requires a Suspense boundary for static rendering.
  return (
    <Suspense>
      <AuthForm mode="register" />
    </Suspense>
  );
}
