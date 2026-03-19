// To activate, link this worker to `api.travel-log.brockmuellers.com/*`
// in cloudflare Domains -> Workers Routes.
// Or toggle with `make prod-pause` / `make prod-unpause` from the repo root.
export default {
  async fetch(request, env, ctx) {
    // Handle CORS preflight requests
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Site-Token",
        },
      });
    }

    const responseBody = JSON.stringify({
      error: "maintenance",
      message: "The backend is temporarily paused to manage traffic.",
    });

    return new Response(responseBody, {
      status: 503,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
