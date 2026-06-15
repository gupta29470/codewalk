/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/knowledge-graph",
        destination: "/knowledge-graph/index.html",
      },
    ];
  },
};

export default nextConfig;
