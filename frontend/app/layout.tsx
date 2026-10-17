import type { Metadata, Viewport } from "next";

import { AppShell } from "../components/AppShell";
import { DataModeProvider } from "../components/DataModeContext";
import { ServiceWorker } from "../components/ServiceWorker";
import { LanguageProvider } from "../lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "GrowthOS",
  description: "Daily growth intelligence and approval-gated agent actions for founders.",
  applicationName: "GrowthOS",
  appleWebApp: { capable: true, title: "GrowthOS", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
  icons: {
    icon: [
      { url: "/icons/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#070b0e",
  colorScheme: "dark",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <LanguageProvider>
          <DataModeProvider>
            <AppShell>{children}</AppShell>
          </DataModeProvider>
        </LanguageProvider>
        <ServiceWorker />
      </body>
    </html>
  );
}
