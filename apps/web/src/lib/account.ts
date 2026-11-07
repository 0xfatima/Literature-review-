/** Types and helpers for account recovery and personal AI settings (backend: /api/v1/auth/...). */
export interface AuthConfig {
  password_reset_available: boolean;
  email_verification_required: boolean;
}

export interface RegisterResult {
  id: string;
  email: string;
  email_verified: boolean;
  verification_required: boolean;
  verification_sent: boolean;
}

export type Pricing = "free" | "paid";

export interface LLMModel {
  id: string;
  label: string;
  pricing: Pricing;
  note: string;
}

export interface LLMProvider {
  key: string;
  label: string;
  key_url: string;
  free_policy: string;
  default_model: string;
  data_notice: string;
  models: LLMModel[];
}

export interface LLMDescribe {
  configured: boolean;
  provider: string | null;
  model: string | null;
  key_source: "user" | "server" | null;
  pricing: Pricing | null;
}

export interface LLMSettingsView {
  allow_user_keys: boolean;
  saved: {
    provider: string;
    model: string;
    pricing: Pricing;
    has_key: boolean;
    key_hint: string | null;
    key_readable: boolean;
    updated_at: string;
  } | null;
  server: { configured: boolean; provider: string | null; model: string | null };
  effective: LLMDescribe;
  default: { provider: string; model: string };
  providers: LLMProvider[];
}

export interface LLMModelList {
  provider: string;
  source: "live" | "static";
  models: LLMModel[];
}

export function pricingLabel(p: Pricing | null | undefined): string {
  return p === "free" ? "Free" : p === "paid" ? "Paid" : "";
}

/** Free models first, then by label; the provider's default always leads. */
export function sortModels(models: LLMModel[], defaultId: string): LLMModel[] {
  return [...models].sort(
    (a, b) =>
      Number(b.id === defaultId) - Number(a.id === defaultId) ||
      Number(b.pricing === "free") - Number(a.pricing === "free") ||
      a.label.localeCompare(b.label),
  );
}
