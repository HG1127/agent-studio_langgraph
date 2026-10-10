/** Dev-only same-origin proxy for the HttpOnly session cookie. */
const apiPort = process.env.STUDIO_LOCAL_API_PORT ?? "8000";
if (!/^[0-9]{2,5}$/.test(apiPort) || Number(apiPort) > 65535) {
  throw new Error("STUDIO_LOCAL_API_PORT must be a local TCP port");
}

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/backend/:path*", destination: "http://127.0.0.1:" + apiPort + "/:path*" }];
  },
};
export default nextConfig;
