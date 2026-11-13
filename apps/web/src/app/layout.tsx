import type { Metadata } from "next";
import { THEME_BOOT } from "@/lib/theme";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = { title: "Prismatic — systematic reviews" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
