import type { Config } from "tailwindcss";

// Tokens live as CSS variables in globals.css; Tailwind only maps names to them.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"),
        panel: v("panel"),
        raised: v("raised"),
        line: v("line"),
        ink: v("ink"),
        muted: v("muted"),
        mark: v("mark"),
        link: v("link"),
        danger: v("danger"),
        ok: v("ok"),
        warn: v("warn"),
        onmark: v("on-mark"),
      },
      fontFamily: {
        // Inter / IBM Plex Sans are used when installed; the system UI font otherwise. No web fonts are fetched.
        sans: [
          "Inter",
          "IBM Plex Sans",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Ubuntu",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        serif: ["Source Serif 4", "Literata", "Charter", "Georgia", "Cambria", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: { "2xs": ["0.6875rem", { lineHeight: "1rem" }] },
    },
  },
  plugins: [],
};
export default config;
