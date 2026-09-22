/* Client-side i18n — every visible string in the app is translated at runtime.

   How it works
   • The source text of the app is English. For any other language a phrase file
     (static/js/i18n/<code>.js, built from i18n/sources/*.txt by i18n/build.py) maps
     each English phrase to its translation and is loaded on demand.
   • The whole page — server-rendered templates AND everything the Studio's JavaScript
     draws later — is translated by walking text nodes and the attributes people read
     (placeholder, title, aria-label, alt, data-sb-tip), then watching for new or changed
     nodes with a MutationObserver. Nothing has to be tagged.
   • A phrase containing {1}, {2}… is a pattern: "Page {1} of {2}" matches "Page 3 of 9" and
     the translation may reorder the values. Values that are themselves known phrases
     ("success", "failed"…) are translated too.
   • Text that isn't a known phrase — names people typed, data from an API — is left alone.
   • The chosen language is kept in localStorage (ante_lang) and mirrored to Django's
     `django_language` cookie so server-rendered dates and framework messages follow it.
   • Mark an element data-no-t to keep its subtree untranslated. */
(function () {
  'use strict';

  var LANGS = [
    { code: 'en',    flag: '🇺🇸', name: 'English',    native: 'English',   django: 'en',      locale: 'en-US' },
    { code: 'pt-BR', flag: '🇧🇷', name: 'Portuguese', native: 'Português', django: 'pt-br',   locale: 'pt-BR' },
    { code: 'es',    flag: '🇪🇸', name: 'Spanish',    native: 'Español',   django: 'es',      locale: 'es-ES' },
    { code: 'fr',    flag: '🇫🇷', name: 'French',     native: 'Français',  django: 'fr',      locale: 'fr-FR' },
    { code: 'de',    flag: '🇩🇪', name: 'German',     native: 'Deutsch',   django: 'de',      locale: 'de-DE' },
    { code: 'it',    flag: '🇮🇹', name: 'Italian',    native: 'Italiano',  django: 'it',      locale: 'it-IT' },
    { code: 'ja',    flag: '🇯🇵', name: 'Japanese',   native: '日本語',     django: 'ja',      locale: 'ja-JP' },
    { code: 'zh',    flag: '🇨🇳', name: 'Chinese',    native: '中文',       django: 'zh-hans', locale: 'zh-CN' },
  ];
  var K_LANG = 'ante_lang';
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-sb-tip'];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1 };
  var BASE = (function () {
    var s = document.currentScript;
    return s && s.src ? s.src.replace(/i18n\.js(\?.*)?$/, 'i18n/') : '/static/js/i18n/';
  }());

  var current = 'en';
  var ready = false;                   // the language file is in and the page has been translated
  var waiting = [];
  var exact = Object.create(null);     // "Save" → "Guardar"
  var patterns = [];                   // [{ re, order:[n…], out }]
  var cache = new Map();               // normalised source → translation | null
  var observer = null;

  function entry(code) { return LANGS.filter(function (l) { return l.code === code; })[0] || LANGS[0]; }
  function getLang() { try { return localStorage.getItem(K_LANG) || 'en'; } catch (e) { return 'en'; } }
  function primary(s) { return String(s || '').toLowerCase().split('-')[0]; }

  /* ── phrase tables ─────────────────────────────────────────────────────── */
  function install(dict) {
    exact = Object.create(null); patterns = []; cache.clear();
    dict = dict || {};
    var tail = / \{\d+\}$/;
    // "Delete “{1}”? {2}" is also met without its optional tail once the page has trimmed trailing whitespace.
    var entries = Object.keys(dict).map(function (src) { return [src, dict[src]]; });
    entries.slice().forEach(function (e) {
      var short = e[0].replace(tail, '');
      if (tail.test(e[0]) && tail.test(e[1]) && !(short in dict)) entries.push([short, e[1].replace(tail, '')]);
    });
    entries.forEach(function (e) {
      var src = e[0], out = e[1];
      if (!/\{\d+\}/.test(src)) { exact[src] = out; return; }
      var order = [], literal = 0;
      var re = src.split(/\{(\d+)\}/).map(function (part, i) {
        if (i % 2) { order.push(part); return '([\\s\\S]*?)'; }
        literal += part.length;
        return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      }).join('');
      patterns.push({ re: new RegExp('^' + re + '$'), order: order, out: out, weight: literal });
    });
    patterns.sort(function (a, b) { return b.weight - a.weight; });
  }

  function lookup(core) {
    var hit = exact[core];
    if (hit !== undefined) return hit;
    for (var i = 0; i < patterns.length; i++) {
      var m = core.match(patterns[i].re);
      if (!m) continue;
      var p = patterns[i];
      return p.out.replace(/\{(\d+)\}/g, function (_, n) {
        var v = m[p.order.indexOf(n) + 1];
        return v === undefined ? '' : translateValue(v);
      });
    }
    return null;
  }

  /* A value captured by a pattern: translated like any text; "A or B or C" (the module list in a permission
     message) is translated part by part. */
  function translateValue(v) {
    var out = translate(v);
    if (out !== v || !exact['or'] || v.indexOf(' or ') === -1) return out;
    var src = v.split(' or '), done = src.map(translate);
    return done.some(function (p, i) { return p !== src[i]; }) ? done.join(' ' + exact['or'] + ' ') : v;
  }

  /* Translate one string, keeping its surrounding whitespace. Unknown text comes back unchanged. */
  function translate(s) {
    if (current === 'en' || !s) return s;
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s);
    var core = m[2].replace(/\s+/g, ' ');
    if (!core) return s;
    var out = cache.get(core);
    if (out === undefined) { out = lookup(core); cache.set(core, out); }
    return out === null ? s : m[1] + out + m[3];
  }

  /* ── the DOM walk ──────────────────────────────────────────────────────── */
  function skipped(node) {
    for (var el = node.nodeType === 1 ? node : node.parentNode; el && el.nodeType === 1; el = el.parentNode) {
      if (SKIP_TAGS[el.tagName] || el.hasAttribute('data-no-t') || el.isContentEditable) return true;
    }
    return false;
  }

  /* A node we've translated remembers its English source (__src) and what we wrote (__out); if the page
     later writes different text into it, that is a new source. */
  function doText(node) {
    var src = node.__out !== undefined && node.data === node.__out ? node.__src : node.data;
    var out = translate(src);
    node.__src = src; node.__out = out;
    if (node.data !== out) node.data = out;
  }

  function doAttrs(el) {
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i];
      if (!el.hasAttribute(a)) continue;
      var rec = el.__attr || (el.__attr = {});
      var now = el.getAttribute(a);
      var src = rec[a] && rec[a].out === now ? rec[a].src : now;
      var out = translate(src);
      rec[a] = { src: src, out: out };
      if (now !== out) el.setAttribute(a, out);
    }
    if (el.tagName === 'INPUT' && /^(submit|button)$/.test(el.type) && el.value) {
      var rv = el.__val, cur = el.value, vs = rv && rv.out === cur ? rv.src : cur, vo = translate(vs);
      el.__val = { src: vs, out: vo };
      if (cur !== vo) el.value = vo;
    }
  }

  function walk(root) {
    if (!root || skipped(root)) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.nodeType === 1) doAttrs(root);
    var tw = document.createTreeWalker(root.nodeType === 9 ? root.documentElement : root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (n.nodeType === 1 && (SKIP_TAGS[n.tagName] || n.hasAttribute('data-no-t'))) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var n;
    while ((n = tw.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }

  function observe() {
    if (observer || !window.MutationObserver) return;
    observer = new MutationObserver(function (records) {
      records.forEach(function (r) {
        if (r.type === 'childList') r.addedNodes.forEach(walk);
        else if (r.type === 'characterData') { if (!skipped(r.target)) doText(r.target); }
        else if (r.type === 'attributes') { if (!skipped(r.target)) doAttrs(r.target); }
      });
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat(['value']) });
  }

  function applyAll() {
    ready = true;
    updateBtn();
    document.documentElement.setAttribute('data-lang', current);
    walk(document);
    observe();
    waiting.splice(0).forEach(function (fn) { try { fn(); } catch (e) { /* a listener must not break translation */ } });
  }

  /* ── language files ────────────────────────────────────────────────────── */
  function load(code, done) {
    if (code === 'en') { install({}); done(); return; }
    var s = document.createElement('script');
    s.src = BASE + code + '.js';
    s.onload = function () { done(); };
    s.onerror = function () { install({}); done(); };
    document.head.appendChild(s);
  }
  /* Called by each phrase file as it loads. */
  function register(code, dict) { if (code === current) install(dict); }

  function setLang(code, opts) {
    code = entry(code).code;
    try { localStorage.setItem(K_LANG, code); } catch (e) {}
    setCookie(code);
    // Server-rendered text (dates, Django's own messages) follows the cookie, so re-render the page.
    if (!(opts && opts.noReload)) { location.reload(); return; }
    current = code;
    load(code, applyAll);
  }

  function setCookie(code) {
    try { document.cookie = 'django_language=' + entry(code).django + '; path=/; max-age=31536000; SameSite=Lax'; } catch (e) {}
  }

  /* ── language selector ─────────────────────────────────────────────────── */
  function updateBtn() {
    var e = entry(current);
    var flagEl = document.querySelector('.lang-btn .lang-flag');
    if (flagEl) flagEl.textContent = e.flag;
    document.querySelectorAll('.lang-opt').forEach(function (el) {
      var active = el.dataset.code === current;
      el.classList.toggle('active', active);
      el.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function buildSelector() {
    var host = document.querySelector('[data-lang-host]') || document.querySelector('.nav-end') || document.querySelector('.auth-shell');
    if (!host || document.querySelector('.lang-selector')) return;
    var wrap = document.createElement('div');
    wrap.className = 'lang-selector' + (host.classList.contains('nav-end') || host.hasAttribute('data-lang-host') ? '' : ' lang-floating');

    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'lang-btn';
    btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', 'Select language');
    btn.innerHTML = '<span class="lang-flag">' + entry(current).flag + '</span><span class="lang-chevron">▲</span>';

    var menu = document.createElement('div');
    menu.className = 'lang-menu'; menu.setAttribute('role', 'listbox');
    LANGS.forEach(function (l) {
      var opt = document.createElement('button');
      opt.type = 'button'; opt.className = 'lang-opt'; opt.dataset.code = l.code;
      opt.setAttribute('role', 'option');
      opt.innerHTML = '<span class="lo-flag">' + l.flag + '</span><span class="lo-name" data-no-t>' + l.name + '</span><span class="lo-native" data-no-t>' + l.native + '</span>';
      opt.addEventListener('click', function () { close(); if (l.code !== current) setLang(l.code); });
      menu.appendChild(opt);
    });
    function close() { menu.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = !menu.classList.contains('open');
      menu.classList.toggle('open', open); btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', close);
    wrap.appendChild(menu); wrap.appendChild(btn);
    host.insertBefore(wrap, host.firstChild);
  }

  /* ── boot ──────────────────────────────────────────────────────────────── */
  function boot() {
    current = entry(getLang()).code;
    // The server renders <html lang> from the django_language cookie. If it disagrees with the stored choice
    // (first visit after choosing, or a cleared cookie), fix the cookie and render once more — at most once.
    var served = primary(document.documentElement.getAttribute('lang'));
    if (served && served !== primary(entry(current).django)) {
      var flag = 'ante_lang_synced_' + current;
      try {
        if (!sessionStorage.getItem(flag)) { sessionStorage.setItem(flag, '1'); setCookie(current); location.reload(); return; }
      } catch (e) {}
    }
    buildSelector();
    load(current, applyAll);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();

  /* Run `fn` once the language file is loaded (immediately if it already is). */
  function whenReady(fn) { if (ready) fn(); else waiting.push(fn); }

  window.anteI18n = {
    whenReady: whenReady,
    setLang: setLang, getLang: getLang, register: register,
    /* Translate an English string (for code that builds text itself). */
    t: translate,
    /* BCP-47 locale of the chosen language, for Intl / toLocaleString. */
    locale: function () { return entry(current).locale; },
    languages: LANGS,
  };
}());
