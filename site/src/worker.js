// Runs in front of the static files: plain-http visits get a permanent
// redirect to https (otherwise typing the address into some browsers loads an
// unencrypted page marked "not secure"), and https responses tell browsers to
// use https from then on (HSTS).
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";   // wrangler dev
    if (url.protocol === "http:" && !local) {
      url.protocol = "https:";
      return Response.redirect(url.toString(), 301);
    }
    const response = await env.ASSETS.fetch(request);
    const headers = new Headers(response.headers);
    headers.set("Strict-Transport-Security", "max-age=31536000");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },
};
