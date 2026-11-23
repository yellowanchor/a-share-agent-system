const fs = require('fs');
eval(fs.readFileSync('scripts/sina_decode.js', 'utf8'));
const enc = process.argv[2];
try {
  const data = d(enc);
  console.log(JSON.stringify({ok: true, len: data.length, first: data[0], last: data[data.length-1]}));
} catch(e) {
  console.log(JSON.stringify({ok: false, err: String(e)}));
}