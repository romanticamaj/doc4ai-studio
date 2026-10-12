/** True when the URL's host is this computer (VLM spec §4.6 loopback rule); tailnet / LAN hosts are remote.
 *  null for anything that is not an absolute URL. */
export function isLoopbackUrl(url: string): boolean | null {
  let host: string;
  try {
    host = new URL(url.trim()).hostname.toLowerCase();
  } catch {
    return null;
  }
  if (!host) return null;
  const h = host.replace(/^\[|\]$/g, "");
  return h === "localhost" || h === "::1" || h === "0.0.0.0" || /^127\.\d+\.\d+\.\d+$/.test(h);
}

/** host[:port] of an endpoint URL, for the consent text; "" when unparsable. */
export function endpointHost(url: string): string {
  try {
    return new URL(url.trim()).host;
  } catch {
    return "";
  }
}
