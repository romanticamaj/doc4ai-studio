/** True when the URL's host is this computer (VLM spec §4.6 loopback rule); tailnet / LAN hosts and 0.0.0.0 are
 *  remote. null for anything that is not an absolute URL. Same rules as the server's runner/_vlm_net.py — both are
 *  tested against __tests__/loopbackCases.json. */
export function isLoopbackUrl(url: string): boolean | null {
  let host: string;
  try {
    host = new URL(url.trim()).hostname.toLowerCase();
  } catch {
    return null;
  }
  if (!host) return null;
  const h = host.replace(/^\[|\]$/g, "");
  if (h === "localhost") return true;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) return h.split(".")[0] === "127";
  if (h.includes(":")) return ipv6IsLoopback(h);
  return false;
}

/** ::1, or an IPv4-mapped loopback (::ffff:127.x.y.z — the URL parser writes it as ::ffff:7f00:1). */
function ipv6IsLoopback(h: string): boolean {
  if (h === "::1" || /^(0{1,4}:){7}0{0,3}1$/.test(h)) return true;
  const m = /^::ffff:(.+)$/.exec(h);
  if (!m) return false;
  const rest = m[1];
  if (/^\d+\.\d+\.\d+\.\d+$/.test(rest)) return rest.split(".")[0] === "127";
  const groups = rest.split(":");
  return groups.length === 2 && parseInt(groups[0], 16) >> 8 === 127;
}

/** host[:port] of an endpoint URL, for the consent text; "" when unparsable. */
export function endpointHost(url: string): string {
  try {
    return new URL(url.trim()).host;
  } catch {
    return "";
  }
}
