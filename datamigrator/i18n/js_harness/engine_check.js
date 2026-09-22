// Loads static/js/i18n.js and a phrase file under a stub DOM and prints the answers as JSON.
//   node engine_check.js <lang> <phrase file> '["English text", …]'
const fs = require('fs');
const path = require('path');
const [lang, phraseFile, inputs] = process.argv.slice(2);
const noop = () => {};
global.window = global;
global.NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2 };
global.localStorage = { getItem: () => lang, setItem: noop };
global.sessionStorage = { getItem: () => '1', setItem: noop };
global.location = { reload: noop };
global.document = {
  readyState: 'complete', currentScript: null, cookie: '',
  documentElement: { getAttribute: () => lang.toLowerCase(), setAttribute: noop },
  head: { appendChild: noop }, addEventListener: noop,
  querySelector: () => null, querySelectorAll: () => [],
  createElement: () => ({ style: {}, setAttribute: noop, appendChild: noop, classList: { add: noop } }),
  createTreeWalker: () => ({ nextNode: () => null }),
};
require(path.resolve(__dirname, '../../static/js/i18n.js'));
const src = fs.readFileSync(phraseFile, 'utf8');
new Function('anteI18n', src)(window.anteI18n);
console.log(JSON.stringify(JSON.parse(inputs).map(s => window.anteI18n.t(s))));
