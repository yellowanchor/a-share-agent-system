// 新浪K线解密常驻服务：从 stdin 读 JSON 行 {"id":N,"enc":"加密串"}，解密后输出 {"id":N,"data":[...]}
const fs = require('fs');
eval(fs.readFileSync(__dirname + '/sina_decode.js', 'utf8'));

const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    console.log(JSON.stringify({ id: -1, ok: false, err: 'bad json' }));
    return;
  }
  try {
    const data = d(req.enc);
    console.log(JSON.stringify({ id: req.id, ok: true, data: data }));
  } catch (e) {
    console.log(JSON.stringify({ id: req.id, ok: false, err: String(e) }));
  }
});
