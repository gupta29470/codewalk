import type { Metadata } from "next";
import localFont from "next/font/local";
import { DM_Serif_Display, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { RootShell } from "@/components/RootShell";
import { AnalysisLoader } from "@/components/AnalysisLoader";
import { AnalyzeProvider } from "@/lib/analyze-context";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

const dmSerif = DM_Serif_Display({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-dm-serif",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Codewalk",
  description: "Understand any codebase in hours, not weeks",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} ${dmSerif.variable} ${inter.variable} ${jetbrainsMono.variable} antialiased`}
      >
        <AnalyzeProvider>
          <RootShell>{children}</RootShell>
          <AnalysisLoader />
        </AnalyzeProvider>
      </body>
    </html>
  );
}
