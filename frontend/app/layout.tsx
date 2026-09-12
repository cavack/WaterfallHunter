import './globals.css'

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#020617',
}

export const metadata = {

  title: 'WaterfallHunter — Waterfall Decision Terminal',
  description: 'Canonical USDT perpetual futures signal monitoring. Signal-only, no order execution.',

}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  )
}
