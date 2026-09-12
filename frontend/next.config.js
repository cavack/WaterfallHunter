/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: '/dashboard',
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://waterfall-backend:8000/api/:path*',
      },
    ]
  },
}
module.exports = nextConfig
