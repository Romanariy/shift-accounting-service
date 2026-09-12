import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import "./accounting.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.SITE_URL || "http://localhost:3000"),
  title: "Свод — работы, расходы и счета",
  description: "Учёт услуг студий, начислений команде и расходов. Проверка Excel и отправка счетов через Telegram.",
  robots: { index: false, follow: false },
  openGraph: {
    title: "Свод — работы, расходы и счета",
    description: "Учёт организаций и команды в одном рабочем пространстве.",
    images: [{ url: "/og.png", width: 1731, height: 909 }],
    locale: "ru_RU", type: "website",
  },
  twitter: { card: "summary_large_image", title: "Свод — работы, расходы и счета", description: "Учёт организаций и команды в одном рабочем пространстве.", images: ["/og.png"] },
};

type RootLayoutProps = {
  children: ReactNode;
};

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
