// Pre-render LaTeX in an HTML file to inline SVG, with one shared glyph cache.
// Usage:  NODE_PATH=<dir>/node_modules node render_math.js in.html out.html
// Requires: npm install mathjax-full   (tested against 3.2.1)
const fs = require('fs');
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
const {AllPackages} = require('mathjax-full/js/input/tex/AllPackages.js');

const adaptor = liteAdaptor(); RegisterHTMLHandler(adaptor);
const svgOut = new SVG({fontCache: 'global'});
const doc = mathjax.document('', {InputJax: new TeX({packages: AllPackages}), OutputJax: svgOut});

// HTML entities inside math reach the TeX parser literally and break it
// (a bare &lt; yields "Misplaced &"). Decode them before converting.
const ENT = {'&lt;':'<','&gt;':'>','&amp;':'&','&minus;':'-','&nbsp;':' ',
             '&ndash;':'-','&mdash;':'-','&quot;':'"'};
let errs = [];
const conv = (tex, d) => {
  const raw = tex.trim();
  const clean = raw.replace(/&(lt|gt|amp|minus|nbsp|ndash|mdash|quot);/g, m => ENT[m]);
  const out = adaptor.outerHTML(doc.convert(clean, {display: d, em: 16, ex: 8, containerWidth: 640}));
  if (out.includes('data-mjx-error')) {
    const m = out.match(/data-mjx-error="([^"]*)"/);
    errs.push((m ? m[1] : '?') + '   in:  ' + raw.replace(/\s+/g, ' ').slice(0, 90));
  }
  return out;
};

let s = fs.readFileSync(process.argv[2], 'utf8'); let nD = 0, nI = 0;
s = s.replace(/\\\[([\s\S]*?)\\\]/g, (_, t) => { nD++; return '<div class="mjd">' + conv(t, true) + '</div>'; });
s = s.replace(/\\\(([\s\S]*?)\\\)/g, (_, t) => { nI++; return conv(t, false); });

const defs = adaptor.innerHTML(svgOut.fontCache.getCache());
const cache = '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true" '
  + 'style="position:absolute;width:0;height:0;overflow:hidden"><defs>' + defs + '</defs></svg>\n';
console.log('  glyph cache: ' + (defs.length / 1024).toFixed(0) + ' KB, '
  + (defs.match(/<path/g) || []).length + ' paths');
s = s.replace('<div class="wrap">', cache + '<div class="wrap">');
fs.writeFileSync(process.argv[3], s);
console.log(`rendered ${nD} display + ${nI} inline -> ${(s.length / 1024).toFixed(0)} KB`);
if (errs.length) {
  console.log('  !! ' + errs.length + ' MathJax ERROR(S):');
  errs.forEach(e => console.log('     ' + e));
  process.exitCode = 2;
} else console.log('  no MathJax errors');
