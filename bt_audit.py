"""Audit de rendu via le protocole Marionette de Firefox (sans geckodriver).

Recollecte ce qu'un screenshot ne reveal pas de façon exploitable :
overflow, elements hors viewport, contrastes, cibles tactiles, erreurs console.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

PORT = 2828
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8142/"
VIEWPORTS = [(1600, 1100, "desktop"), (430, 932, "mobile")]


class Marionette:
    def __init__(self, port=PORT):
        self.port = port
        self.sock = None
        self.msg_id = 0

    def connect(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port), 5)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Marionette injoignable")
        self.sock.settimeout(180)
        self._read_packet()  # paquet d'accueil

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("connexion Marionette fermee")
            buf += chunk
        return buf

    def _read_packet(self):
        length = b""
        while not length.endswith(b":"):
            byte = self._read_exact(1)
            if byte == b":":
                break
            length += byte
        return json.loads(self._read_exact(int(length)).decode("utf-8"))

    def send(self, command, params=None):
        self.msg_id += 1
        payload = json.dumps([0, self.msg_id, command, params or {}]).encode("utf-8")
        self.sock.sendall(str(len(payload)).encode("ascii") + b":" + payload)
        while True:
            msg = self._read_packet()
            if isinstance(msg, list) and msg[0] == 1 and msg[1] == self.msg_id:
                if msg[2]:
                    raise RuntimeError(f"{command}: {msg[2]}")
                return msg[3]

    def script(self, body, args=None, timeout=60000):
        return self.send("WebDriver:ExecuteScript",
                         {"script": body, "args": args or [], "newSandbox": False},
                         )["value"]

    def set_window(self, width, height):
        self.send("WebDriver:SetWindowRect", {"x": 0, "y": 0, "width": width, "height": height})

    def close(self):
        if self.sock:
            self.sock.close()


AUDIT_JS = r"""
const out = {overflowX: false, clipped: [], offscreen: [], tiny: [], lowContrast: [],
             empty: [], unstyled: [], counts: {}};
const vw = document.documentElement.clientWidth;
out.overflowX = document.documentElement.scrollWidth > vw + 1;
out.counts.tabs = document.querySelectorAll("[data-tab]").length;
out.counts.sprite = document.querySelectorAll("symbol").length;

function parseColor(c) {
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (!m) return null;
  const p = m[1].split(",").map(s => parseFloat(s));
  return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
}
function lum(c) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
}
function bgOf(el) {
  let n = el;
  while (n && n !== document.documentElement) {
    const c = parseColor(getComputedStyle(n).backgroundColor);
    if (c && c.a > 0.6) return c;
    n = n.parentElement;
  }
  return {r: 8, g: 10, b: 14, a: 1};
}

const all = document.querySelectorAll("body *");
for (const el of all) {
  const s = getComputedStyle(el);
  if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") continue;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) continue;
  const tag = el.tagName.toLowerCase();
  const id = el.id ? "#" + el.id : "";
  const cls = (el.className && typeof el.className === "string")
    ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "";
  const sel = tag + id + cls;

  if (r.right > vw + 1 || r.left < -1) {
    out.offscreen.push({sel, left: Math.round(r.left), right: Math.round(r.right)});
  }
  if (el.scrollWidth > el.clientWidth + 2 && s.overflowX === "visible") {
    out.clipped.push({sel, scroll: el.scrollWidth, client: el.clientWidth});
  }
  if (tag === "button" || tag === "a" || el.getAttribute("role") === "tab") {
    if (r.height > 0 && r.height < 32) out.tiny.push({sel, h: Math.round(r.height), w: Math.round(r.width)});
  }
  const direct = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
  if (direct) {
    const fg = parseColor(s.color);
    if (fg) {
      const L1 = lum(fg), L2 = lum(bgOf(el));
      const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const size = parseFloat(s.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(s.fontWeight) >= 700);
      const need = large ? 3 : 4.5;
      if (ratio < need) {
        out.lowContrast.push({sel, ratio: Math.round(ratio * 100) / 100, need, size,
                              color: s.color, weight: s.fontWeight});
      }
    }
  }
  if (/^(H1|H2|H3)$/.test(el.tagName) && s.fontFamily.indexOf("serif") === -1) {
    out.unstyled.push({sel, family: s.fontFamily.slice(0, 40)});
  }
}
for (const sel of ["#paper", ".rail", ".tabs", "#kpis", ".scrim", "#sheet"]) {
  const el = document.querySelector(sel);
  if (!el) continue;
  out.counts[sel] = {h: Math.round(el.getBoundingClientRect().height),
                     w: Math.round(el.getBoundingClientRect().width)};
}
out.counts.activeLeaf = document.querySelectorAll(".leaf.is-active").length;
out.counts.leaves = document.querySelectorAll("[data-leaf]").length;
return out;
"""

LEAF_JS = r"""
const out = {leaf: null, hidden: [], h: 0, panels: [], emptyStates: 0,
             rows: 0, text: 0, kpis: 0};
for (const el of document.querySelectorAll("[data-leaf]")) {
  const active = el.classList.contains("is-active");
  const h = Math.round(el.getBoundingClientRect().height);
  if (active) {
    out.leaf = el.dataset.leaf;
    out.h = h;
    out.panels = Array.from(el.querySelectorAll(".panel")).map(
      p => p.querySelector(".panel-title")?.textContent.trim() || "(sans titre)");
    out.emptyStates = el.querySelectorAll(".empty").length;
    out.rows = el.querySelectorAll(".algo-row, .tk-row, .src-row, .pool-row, tr").length;
    out.kpis = el.querySelectorAll(".kpi").length;
    out.text = (el.textContent || "").trim().length;
  } else if (h > 0) {
    out.hidden.push({leaf: el.dataset.leaf, h: h});
  }
}
return out;
"""


RAIL_JS = r"""
const rail = document.querySelector("#rail");
const scrim = document.querySelector("#scrim");
const out = {before: {open: rail.classList.contains("is-open"),
                      railLeft: Math.round(rail.getBoundingClientRect().left),
                      railW: Math.round(rail.getBoundingClientRect().width),
                      scrimHidden: scrim.hidden,
                      expanded: document.querySelector("#rail-toggle").getAttribute("aria-expanded")}};
document.querySelector("#rail-toggle").click();
return new Promise(resolve => setTimeout(() => {
  const r = rail.getBoundingClientRect();
  out.after = {open: rail.classList.contains("is-open"),
               railLeft: Math.round(r.left), railW: Math.round(r.width),
               scrimHidden: scrim.hidden,
               expanded: document.querySelector("#rail-toggle").getAttribute("aria-expanded")};
  const link = document.querySelector("#rail [data-tab]");
  link.click();
  resolve({drawer: out, leafSwitched: document.querySelector(".leaf.is-active").dataset.leaf,
           railClosedAfterNav: !rail.classList.contains("is-open"),
           scrimHiddenAfterNav: scrim.hidden});
}, 700));
"""


LEAVES = ["overview", "projects", "enrichment", "tokenomics", "mining"]


def start_firefox(profile):
    binary = shutil.which("firefox")
    env = dict(os.environ, MOZ_MARIONETTE="1", MOZ_HEADLESS="1")
    proc = subprocess.Popen(
        [binary, "--headless", "--marionette", "--no-remote", "--profile", profile,
         "about:blank"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def main():
    profile = os.path.expanduser("~/ffp-audit")
    shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(profile, exist_ok=True)
    with open(os.path.join(profile, "user.js"), "w") as fh:
        fh.write("user_pref(\"marionette.port\", %d);\n"
                 "user_pref(\"marionette.enabled\", true);\n"
                 "user_pref(\"browser.shell.checkDefaultBrowser\", false);\n"
                 'user_pref("browser.cache.disk.enable", false);\n'
                 'user_pref("browser.cache.memory.enable", false);\n'
                 'user_pref("network.http.use-cache", false);\n' % PORT)
    proc = start_firefox(profile)
    client = Marionette()
    client.connect()
    client.send("WebDriver:NewSession", {"capabilities": {}})
    report = {}
    try:
        for width, height, label in VIEWPORTS:
            client.set_window(width, height)
            client.send("WebDriver:Navigate", {"url": URL})
            time.sleep(4)
            report[label] = {"title": client.script("return document.title;"),
                             "leaves": {}}
            for leaf in LEAVES:
                client.script(
                    "document.querySelector('.tabs [data-tab=\"' + arguments[0] + '\"]')"
                    ".click(); return 1;", [leaf])
                time.sleep(1.6)
                report[label]["leaves"][leaf] = {
                    "state": client.script(LEAF_JS),
                    "layout": client.script(AUDIT_JS),
                }
            if label == "mobile":
                report[label]["drawer"] = client.script(RAIL_JS)
                time.sleep(1.5)
            report[label]["console"] = client.script(
                "return (window.__errors || []).join(' | ');") or "aucune"
    finally:
        try:
            client.send("Marionette:Quit", {})
        except Exception:
            pass
        client.close()
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
