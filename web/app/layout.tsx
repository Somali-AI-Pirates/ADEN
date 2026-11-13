import type { Metadata } from "next";
import "./globals.css";
import { themeInitScript } from "../lib/theme";
import { chatStartupScript } from "../lib/chat-startup";

export const metadata: Metadata = {
  title: "ADEN · 해운 문서 도우미",
  description: "문서를 근거로 답변하고, 출처의 원문을 함께 확인합니다.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <script dangerouslySetInnerHTML={{ __html: chatStartupScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
