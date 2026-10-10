"""Teste les interactions du carnet : detail, recherche, tri, langue, scan.

Marionette execute du JS dans la page, donc on pilote les vrais evenements
DOM et on lit l'etat observe, pas l'intention du code.
"""

import json
import os
import shutil
import sys
import time

from bt_audit import PORT, Marionette, start_firefox

SHEET_CLICK_JS = "document.querySelector('#rows button.project').click(); return 1;"

SHEET_JS = r"""
const sheet = document.querySelector("#overlay");
const body = document.querySelector("#sheet-body");
const anchors = Array.from(sheet.querySelectorAll("a[href]"));
return {
  open: sheet.classList.contains("is-open"),
  hidden: sheet.hasAttribute("hidden"),
  bodyLen: (body.textContent || "").trim().length,
  scores: sheet.querySelectorAll(".score").length,
  links: anchors.length,
  schemedLinks: anchors.filter(a => /^https?:\/\//i.test(a.getAttribute("href"))).length,
  suspicious: anchors.filter(a => !/^(https?:\/\/|#)/i.test(a.getAttribute("href")))
                    .map(a => a.getAttribute("href").slice(0, 40)),
  heading: (sheet.querySelector("#sheet-heading")?.textContent || "").trim().slice(0, 50),
  labelledBy: sheet.querySelector(".sheet").getAttribute("aria-labelledby"),
  labelResolves: !!document.getElementById("sheet-heading"),
};
"""

CLOSE_JS = r"""
const before = document.activeElement ? document.activeElement.className : null;
document.querySelector("#sheet-close").click();
return new Promise(resolve => setTimeout(() => resolve({
  closed: document.querySelector("#overlay").hidden,
  focusBefore: String(before).slice(0, 30),
  focusAfter: document.activeElement ? document.activeElement.className : null,
}), 400));
"""

SEARCH_JS = r"""
const input = document.querySelector("#search");
const all = document.querySelectorAll("#rows button.project").length;
input.value = "zzz-aucun-resultat-zzz";
input.dispatchEvent(new Event("input", {bubbles: true}));
return 1;
"""

SEARCH_RESULT_JS = r"""
const filtered = document.querySelectorAll("#rows button.project").length;
const empty = document.querySelector("#rows .empty");
return {filtered, count: (document.querySelector("#result-count")?.textContent || "").trim(),
        emptyText: empty ? empty.textContent.trim().slice(0, 50) : null};
"""

SEARCH_CLEAR_JS = r"""
const input = document.querySelector("#search");
input.value = "";
input.dispatchEvent(new Event("input", {bubbles: true}));
return 1;
"""

SORT_JS = r"""
const sel = document.querySelector("#sort");
const apply = (v) => { sel.value = v; sel.dispatchEvent(new Event("change", {bubbles: true})); };
apply("title:asc");
return 1;
"""

SORT_RESULT_JS = r"""
const titles = Array.from(document.querySelectorAll("#rows .project-title"))
  .map(e => e.textContent.trim());
const expected = titles.slice().sort((a, b) => a.localeCompare(b, "fr",
  {sensitivity: "base", numeric: true}));
const accents = titles.filter(t => /[éèêàçôûï]/i.test(t));
return {count: titles.length, localeOrdered: JSON.stringify(titles) === JSON.stringify(expected),
        distinct: new Set(titles).size === titles.length,
        accentedSample: accents.slice(0, 2)};
"""

LANG_SET_JS = r"""
const sel = document.querySelector("#lang");
sel.value = "en";
sel.dispatchEvent(new Event("change", {bubbles: true}));
return {options: Array.from(sel.options).map(o => o.value)};
"""

LANG_JS = r"""
return {title: document.querySelector("#leaf-title").textContent.trim(),
        lang: document.documentElement.lang, stored: localStorage.getItem("bt_lang"),
        docTitle: document.title,
        placeholder: document.querySelector("#search").placeholder,
        sortOption: document.querySelector("#sort option").textContent.trim(),
        scanBtn: document.querySelector("#btn-scan span").textContent.trim()};
"""

SCAN_JS = r"""
const btn = document.querySelector("#btn-scan");
return {label: (btn.textContent || "").trim(), disabled: btn.disabled,
        hasPages: !!document.querySelector("#scan-pages"),
        hasSection: !!document.querySelector("#scan-section"),
        sectionValue: document.querySelector("#scan-section").value,
        status: (document.querySelector("#scan-status")?.textContent || "").trim(),
        liveHidden: document.querySelector("#scan-live").hasAttribute("hidden")};
"""

LLM_OPEN_JS = r"""
const box = document.querySelector("#llm-box");
box.open = true;
return {open: box.open,
        provider: document.querySelector("#llm-provider").value,
        model: document.querySelector("#llm-model").value,
        ollamaHidden: document.querySelector("#llm-ollama-field").hasAttribute("hidden"),
        baseHidden: document.querySelector("#llm-base-field").hasAttribute("hidden"),
        keyHidden: document.querySelector("#llm-key-field").hasAttribute("hidden"),
        keyValue: document.querySelector("#llm-api-key").value,
        keyNote: (document.querySelector("#llm-key-note").textContent || "").trim(),
        temperature: document.querySelector("#llm-temperature").value,
        timeout: document.querySelector("#llm-timeout").value,
        msgHidden: document.querySelector("#llm-msg").hasAttribute("hidden")};
"""

LLM_PROVIDER_JS = r"""
const sel = document.querySelector("#llm-provider");
sel.value = "openai";
sel.dispatchEvent(new Event("change"));
return {ollamaHidden: document.querySelector("#llm-ollama-field").hasAttribute("hidden"),
        baseHidden: document.querySelector("#llm-base-field").hasAttribute("hidden"),
        keyHidden: document.querySelector("#llm-key-field").hasAttribute("hidden"),
        badge: (document.querySelector("#llm-badge").textContent || "").trim()};
"""

LLM_PROVIDER_BACK_JS = r"""
const sel = document.querySelector("#llm-provider");
sel.value = "ollama";
sel.dispatchEvent(new Event("change"));
return {ollamaHidden: document.querySelector("#llm-ollama-field").hasAttribute("hidden"),
        baseHidden: document.querySelector("#llm-base-field").hasAttribute("hidden"),
        keyHidden: document.querySelector("#llm-key-field").hasAttribute("hidden")};
"""

LLM_SECURITY_JS = r"""
const box = document.querySelector("#llm-box");
const overflow = [];
for (const id of ["llm-provider", "llm-model", "llm-api-base", "llm-ollama-host",
                  "llm-temperature", "llm-timeout", "llm-test", "llm-save"]) {
  const el = document.querySelector("#" + id);
  if (!el) { overflow.push(id + ":absent"); continue; }
  const r = el.getBoundingClientRect();
  if (r.width === 0) overflow.push(id + ":largeur0");
  if (r.right > document.documentElement.clientWidth + 1) overflow.push(id + ":overflow");
}
const small = [];
for (const id of ["llm-provider", "llm-model", "llm-api-base", "llm-api-key",
                  "llm-temperature", "llm-timeout", "llm-test", "llm-save",
                  "llm-summary"]) {
  const el = document.querySelector("#" + id);
  if (el && el.getBoundingClientRect().height < 32) small.push(id);
}
return {overflow, small,
        datalist: !!document.querySelector("#llm-models"),
        toggleTriggersLoad: !box.hasAttribute("open") || true};
"""

# Les controles precedents basculent le fournisseur et declenchent une requete :
# ce delai laisse la liste se stabiliser avant de la decrire.
SETTLE_JS = r"""
return 1;
"""

LLM_MODELS_JS = r"""
const list = document.querySelector("#llm-models");
const note = document.querySelector("#llm-models-note");
return {count: list.options.length,
        sample: Array.from(list.options).slice(0, 5).map(o => o.value),
        note: (note.textContent || "").trim(),
        model: document.querySelector("#llm-model").value,
        refreshBusy: document.querySelector("#llm-refresh").classList.contains("is-busy"),
        refreshDisabled: document.querySelector("#llm-refresh").disabled};
"""

LLM_REFRESH_JS = r"""
document.querySelector("#llm-refresh").click();
return 1;
"""

LLM_PROVIDER_SWITCH_JS = r"""
document.querySelector("#llm-box").open = true;
const provider = document.querySelector("#llm-provider");
provider.value = "openai";
provider.dispatchEvent(new Event("change"));
document.querySelector("#llm-api-base").value = "http://127.0.0.1:1/v1";
document.querySelector("#llm-refresh").click();
return 1;
"""

# Un serveur qui ne repond pas doit produire un motif lisible, et surtout pas
# effacer ce que l'utilisateur a saisi entre-temps.
LLM_UNREACHABLE_JS = r"""
const model = document.querySelector("#llm-model");
model.value = "modele-saisi";
model.dispatchEvent(new Event("input"));
document.querySelector("#llm-ollama-host").value = "http://127.0.0.1:1";
document.querySelector("#llm-ollama-host").dispatchEvent(new Event("change"));
return 1;
"""

SECURITY_JS = r"""
const bad = [];
for (const a of document.querySelectorAll("a[href]")) {
  const h = a.getAttribute("href");
  if (!/^(https?:\/\/|#|mailto:)/i.test(h)) bad.push(h.slice(0, 50));
}
const scripts = Array.from(document.querySelectorAll("script[src]"))
  .map(s => s.getAttribute("src")).filter(s => !s.startsWith("/static/"));
return {badHrefs: [...new Set(bad)], foreignScripts: scripts,
        spriteRefs: Array.from(document.querySelectorAll("use"))
          .map(u => u.getAttribute("href")).filter(h => h && !document.querySelector(h)),
        inlineHandlers: Array.from(document.querySelectorAll("[onclick],[onerror],[onload]"))
          .map(e => e.tagName)};
"""

CHECKS = [
    ("ouverture detail", SHEET_CLICK_JS, None, 1.5),
    ("etat detail", SHEET_JS, None, 0),
    ("fermer detail", CLOSE_JS, None, 0),
    ("recherche", SEARCH_JS, None, 1.2),
    ("recherche resultat", SEARCH_RESULT_JS, None, 0),
    ("recherche effacee", SEARCH_CLEAR_JS, None, 1.2),
    ("tri par titre", SORT_JS, None, 1.2),
    ("tri par titre resultat", SORT_RESULT_JS, None, 0),
    ("selecteur langue", LANG_SET_JS, None, 1.6),
    ("bascule EN", LANG_JS, None, 0),
    ("panneau scan", SCAN_JS, None, 0),
    ("panneau LLM", LLM_OPEN_JS, None, 1.5),
    ("LLM fournisseur openai", LLM_PROVIDER_JS, None, 0),
    ("LLM fournisseur ollama", LLM_PROVIDER_BACK_JS, None, 0),
    ("LLM mise en page", LLM_SECURITY_JS, None, 0),
    ("LLM liste stabilisee", SETTLE_JS, None, 2.5),
    ("LLM modeles installes", LLM_MODELS_JS, None, 0),
    ("LLM rafraichissement", LLM_REFRESH_JS, None, 2.5),
    ("LLM bascule fournisseur", LLM_PROVIDER_SWITCH_JS, None, 3.0),
    ("LLM serveur injoignable", LLM_UNREACHABLE_JS, None, 3.0),
    ("securite DOM", SECURITY_JS, None, 0),
]


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8142/"
    profile = os.path.expanduser("~/ffp-audit")
    shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(profile, exist_ok=True)
    with open(os.path.join(profile, "user.js"), "w") as fh:
        fh.write('user_pref("marionette.port", %d);\n'
                 'user_pref("marionette.enabled", true);\n'
                 'user_pref("browser.cache.disk.enable", false);\n'
                 'user_pref("browser.cache.memory.enable", false);\n'
                 'user_pref("network.http.use-cache", false);\n' % PORT)
    proc = start_firefox(profile)
    client = Marionette()
    client.connect()
    client.send("WebDriver:NewSession", {"capabilities": {}})
    failures = 0
    try:
        client.set_window(1440, 1000)
        client.send("WebDriver:Navigate", {"url": url})
        time.sleep(4)
        for label, script, args, wait in CHECKS:
            try:
                r = client.script(script, args)
                if wait:
                    time.sleep(wait)
            except Exception as exc:
                failures += 1
                print(f"[ECHEC] {label}: {str(exc)[:150]}")
                continue
            flag = ""
            if isinstance(r, dict) and r.get("error"):
                failures += 1
                flag = "  <<< ECHEC"
            print(f"[ok] {label}: {json.dumps(r, ensure_ascii=False)[:250]}{flag}")
        flags = client.script("""
const note = document.querySelector("#llm-models-note");
return {modele: document.querySelector("#llm-model").value,
        options: document.querySelector("#llm-models").options.length,
        note: (note.textContent || "").trim()};
""", None)
        if flags.get("modele") != "modele-saisi":
            failures += 1
            print(f"[ECHEC] saisie effacee par un serveur injoignable: "
                  f"{json.dumps(flags, ensure_ascii=False)}  <<< ECHEC")
        else:
            print(f"[ok] saisie preservee: {json.dumps(flags, ensure_ascii=False)[:200]}")

        errs = client.script("return (window.__errors || []).join(' | ');")
        if errs:
            failures += 1
            print(f"[ECHEC] erreurs console: {errs}")
        else:
            print("[ok] erreurs console: aucune")
    finally:
        try:
            client.send("Marionette:Quit", {})
        except Exception:
            pass
        client.close()
        proc.terminate()
    print(f"\n{failures} echec(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()