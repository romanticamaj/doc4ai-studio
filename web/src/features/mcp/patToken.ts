/**
 * MCP personal access token format (aidoc.mcp.tokens, spec MCP §2.3): `doc4ai_pat_<43 base62>_<6 base62 CRC32>`.
 * Checked in the browser only, so a pasted token can fill the config snippets without ever being sent anywhere.
 */
export const PLACEHOLDER = "<YOUR_TOKEN>";
export const PAT_PREFIX = "doc4ai_pat_";
const BODY_LEN = 43;
const CRC_LEN = 6;
const ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const PAT_RE = new RegExp(`^${PAT_PREFIX}[0-9A-Za-z]{${BODY_LEN}}_[0-9A-Za-z]{${CRC_LEN}}$`);

export type PatCheck = "ok" | "empty" | "not_pat" | "bearer" | "malformed" | "checksum";

let crcTable: Uint32Array | null = null;
function crc32(s: string): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < s.length; i++) crc = crcTable[(crc ^ s.charCodeAt(i)) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function b62(n: number, width: number): string {
  let out = "";
  while (n > 0) {
    out = ALPHABET[n % 62] + out;
    n = Math.floor(n / 62);
  }
  return (out || "0").padStart(width, "0");
}

export function checkPat(raw: string): PatCheck {
  const t = raw.trim();
  if (!t) return "empty";
  if (/^bearer\s/i.test(t)) return "bearer";
  if (!t.startsWith(PAT_PREFIX)) return "not_pat";
  if (!PAT_RE.test(t)) return "malformed";
  const body = t.slice(PAT_PREFIX.length, PAT_PREFIX.length + BODY_LEN);
  const crc = t.slice(-CRC_LEN);
  return b62(crc32(body), CRC_LEN) === crc ? "ok" : "checksum";
}

export function fillToken(text: string, token: string): string {
  return text.split(PLACEHOLDER).join(token);
}
