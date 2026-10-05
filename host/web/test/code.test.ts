import { describe, expect, it } from "vitest";
import { decodeCode, encodeCode, looksLikeCode } from "../src/connect/code.ts";

const FP = "F70C8B6BD81B17BEC942A838C84F9776956EBE60AD95BDF1CBA01D40E32B8E5B";

// The same vectors as host/tests/test_connection_code.py: both sides must agree.
const VECTORS: [string, number, string, string][] = [
  ["192.168.100.11", 8443, "042137", "341C-2J9K-XRCG-B"],
  ["10.241.98.125", 8443, "999999", "FHC9-YQM4-FZQ1-J06"],
  ["172.20.0.5", 9443, "000000", "J001-CJE6-0001-XRCG-9"],
  ["8.8.4.4", 8443, "123456", "R810-2083-RJ0Y-W680"],
];

describe("the connection code", () => {
  it.each(VECTORS)("decodes the service's code for %s:%i", (host, port, pairing, code) => {
    expect(decodeCode(code)).toEqual({ address: `${host}:${port}`, pairing, fpPrefix: FP.slice(0, 5) });
    expect(encodeCode(host, port, pairing, FP)).toBe(code);
  });

  it("forgives how it was typed and catches a typo", () => {
    const sloppy = "341c 2j9k xrcg b".replace("1", "l");
    expect(decodeCode(sloppy)?.address).toBe("192.168.100.11:8443");
    expect(decodeCode("341C-2J9K-XRCG-C")).toBeNull();
  });

  it("tells a code from an address", () => {
    expect(looksLikeCode("341C-2J9K-XRCG-B")).toBe(true);
    expect(looksLikeCode("aurasync.local:8443")).toBe(false);
    expect(looksLikeCode("192.168.100.11")).toBe(false);
    expect(decodeCode("aurasync.local:8443")).toBeNull();
  });
});
