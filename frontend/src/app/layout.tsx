import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Crypto Trading Office",
  description: "Kantor virtual 3D untuk simulasi paper trading kripto. Tidak ada uang sungguhan.",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#05070d" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id">
      <body>{children}</body>
    </html>
  );
}
