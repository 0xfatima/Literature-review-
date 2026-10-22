"use client";
import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { ApiError, apiFetch } from "./api";
import type { User } from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => apiFetch<User>("/auth/me"), retry: false });
}

/** Redirects to /login when the API says we are not authenticated (covers stale/expired cookies). */
export function useRequireAuth() {
  const router = useRouter();
  const pathname = usePathname();
  const me = useMe();
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      const here = pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(here)}`);
    }
  }, [me.error, router, pathname]);
  return me;
}
