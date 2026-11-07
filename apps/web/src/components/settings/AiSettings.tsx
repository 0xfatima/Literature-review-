"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  pricingLabel,
  sortModels,
  type LLMModel,
  type LLMModelList,
  type LLMSettingsView,
} from "@/lib/account";
import { ApiError, apiFetch, json } from "@/lib/api";

const CUSTOM = "__custom__";

function Badge({ pricing }: { pricing: "free" | "paid" }) {
  return (
    <span
      className={`ml-2 rounded px-1.5 py-0.5 text-xs ${pricing === "free" ? "bg-ok/15 text-ok" : "bg-danger/10 text-danger"}`}
    >
      {pricingLabel(pricing)}
    </span>
  );
}

/** Choose an AI provider and model (free models are marked and listed first) and store your own API key. */
export default function AiSettings() {
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: ["llm-settings"],
    queryFn: () => apiFetch<LLMSettingsView>("/auth/me/llm"),
  });
  const [provider, setProvider] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [custom, setCustom] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // Initialise from what is saved, else the server default (a free model).
  useEffect(() => {
    if (!view.data || provider) return;
    setProvider(view.data.saved?.provider ?? view.data.default.provider);
    setModel(view.data.saved?.model ?? view.data.default.model);
  }, [view.data, provider]);

  const info = view.data?.providers.find((p) => p.key === provider);
  const live = useQuery({
    queryKey: ["llm-models", provider, view.data?.saved?.has_key],
    queryFn: () => apiFetch<LLMModelList>("/auth/me/llm/models", { method: "POST", ...json({ provider }) }),
    enabled: !!provider,
  });
  const models: LLMModel[] = useMemo(
    () => sortModels(live.data?.models ?? info?.models ?? [], info?.default_model ?? ""),
    [live.data, info],
  );
  const inList = models.some((m) => m.id === model);
  const chosenModel =
    model === CUSTOM || (!inList && model) ? custom || (model === CUSTOM ? "" : model) : model;
  const chosen = models.find((m) => m.id === chosenModel);
  const savedSameProvider = view.data?.saved?.provider === provider;

  const fail = (e: unknown) =>
    setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not reach the server." });
  const save = useMutation({
    mutationFn: (clearKey: boolean) =>
      apiFetch<LLMSettingsView>("/auth/me/llm", {
        method: "PUT",
        ...json({ provider, model: chosenModel, api_key: apiKey || null, clear_key: clearKey }),
      }),
    onSuccess: (v) => {
      qc.setQueryData(["llm-settings"], v);
      setApiKey("");
      setMessage({ ok: true, text: "Saved. New AI jobs use this model." });
    },
    onError: fail,
  });
  const reset = useMutation({
    mutationFn: () => apiFetch<LLMSettingsView>("/auth/me/llm", { method: "DELETE" }),
    onSuccess: (v) => {
      qc.setQueryData(["llm-settings"], v);
      setProvider(v.default.provider);
      setModel(v.default.model);
      setMessage({ ok: true, text: "Your personal settings were removed; the server default applies." });
    },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean; message: string }>("/auth/me/llm/test", {
        method: "POST",
        ...json({ provider, model: chosenModel, api_key: apiKey || null }),
      }),
    onSuccess: (r) => setMessage({ ok: r.ok, text: r.message }),
    onError: fail,
  });

  if (view.isPending) return <p className="text-muted">Loading…</p>;
  if (view.isError || !view.data) return <p className="text-danger">Could not load your AI settings.</p>;
  const v = view.data;
  if (!v.allow_user_keys)
    return <p className="text-sm text-muted">Personal AI settings are switched off on this server.</p>;

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded border border-line p-4 text-sm">
        <h2 className="section-title mb-1">In use now</h2>
        {v.effective.configured ? (
          <p>
            {v.effective.provider} · <code>{v.effective.model}</code>
            {v.effective.pricing && <Badge pricing={v.effective.pricing} />}
            <span className="ml-2 text-muted">
              ({v.effective.key_source === "user" ? "your own API key" : "the server's key"})
            </span>
          </p>
        ) : (
          <p className="text-muted">
            No AI model is connected. Everything still works without one; add a free key below to get AI
            relevance screening, extraction suggestions and AI-built search queries.
          </p>
        )}
        {v.saved && !v.saved.key_readable && (
          <p className="mt-2 text-danger">
            Your saved key can no longer be read (the server key changed). Please enter it again.
          </p>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="provider" className="text-sm font-medium">
            Provider
          </label>
          <select
            id="provider"
            className="input"
            value={provider}
            onChange={(e) => {
              const p = v.providers.find((x) => x.key === e.target.value);
              setProvider(e.target.value);
              setModel(p?.default_model ?? "");
              setApiKey("");
              setMessage(null);
            }}
          >
            {v.providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
                {p.models.some((m) => m.pricing === "free") ? " (has free models)" : " (paid only)"}
              </option>
            ))}
          </select>
          {info && <p className="text-xs text-muted">{info.free_policy}</p>}
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="model" className="text-sm font-medium">
            Model
          </label>
          <select
            id="model"
            className="input"
            value={inList ? model : CUSTOM}
            onChange={(e) => {
              setModel(e.target.value);
              if (e.target.value !== CUSTOM) setCustom("");
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.pricing === "free" ? "[Free] " : "[Paid] "}
                {m.label}
                {m.label !== m.id ? ` (${m.id})` : ""}
                {m.note ? ` - ${m.note}` : ""}
              </option>
            ))}
            <option value={CUSTOM}>Other model id…</option>
          </select>
          {(!inList || model === CUSTOM) && (
            <input
              aria-label="Model id"
              className="input font-mono text-xs"
              placeholder="e.g. gemini-2.5-flash"
              value={custom || (model !== CUSTOM ? model : "")}
              onChange={(e) => setCustom(e.target.value.trim())}
            />
          )}
          <p className="text-xs text-muted">
            {chosen ? (
              <>
                Selected: <code>{chosen.id}</code>
                <Badge pricing={chosen.pricing} />
              </>
            ) : (
              "Free/paid status unknown for a model id typed by hand."
            )}{" "}
            {live.data?.source === "live"
              ? "List fetched live from the provider."
              : "Suggested models (add your key to see the provider's full list)."}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="apikey" className="text-sm font-medium">
            API key
          </label>
          <input
            id="apikey"
            type="password"
            autoComplete="off"
            className="input font-mono text-xs"
            placeholder={
              savedSameProvider && v.saved?.has_key
                ? `Saved (${v.saved.key_hint}). Leave empty to keep it.`
                : "Paste your key"
            }
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          {info && (
            <p className="text-xs text-muted">
              Get a key at{" "}
              <a href={info.key_url} target="_blank" rel="noreferrer noopener">
                {info.key_url.replace(/^https:\/\//, "")}
              </a>
              . It is stored encrypted, used only for your own projects and never shown again.
              {info.data_notice ? ` ${info.data_notice}` : ""}
            </p>
          )}
          {!apiKey && !(savedSameProvider && v.saved?.has_key) && v.server.provider !== provider && (
            <p className="text-xs text-danger">Without a key for this provider the server default is used.</p>
          )}
        </div>

        <div
          role="status"
          aria-live="polite"
          className={`min-h-5 text-sm ${message?.ok ? "text-ok" : "text-danger"}`}
        >
          {message?.text}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn"
            disabled={!chosenModel || save.isPending}
            onClick={() => save.mutate(false)}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            className="btn-secondary"
            disabled={!chosenModel || test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? "Testing…" : "Test connection"}
          </button>
          {savedSameProvider && v.saved?.has_key && (
            <button className="btn-ghost" onClick={() => save.mutate(true)}>
              Remove saved key
            </button>
          )}
          {v.saved && (
            <button className="btn-ghost" onClick={() => reset.mutate()}>
              Use server default
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
