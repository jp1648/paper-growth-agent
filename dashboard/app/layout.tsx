import "./globals.css";
export const metadata = {
  title: "Trader / Observatory",
  description: "Private paper-agent performance and research observatory",
  robots: { index: false, follow: false },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
