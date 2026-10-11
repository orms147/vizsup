// Local preview: expose only this design folder and one explicit source video.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
const media = path.resolve(root, '../../storage/2dc712c162d7/source.mp4');
const mime = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.jpg':'image/jpeg','.png':'image/png','.woff2':'font/woff2','.ttf':'font/ttf','.mp4':'video/mp4'};
http.createServer((req,res)=>{
  let p;
  try { p = decodeURIComponent(new URL(req.url,'http://localhost').pathname); } catch { res.writeHead(400).end(); return; }
  const target = p === '/media/source.mp4' ? media : path.resolve(root, '.' + (p === '/' ? '/index.html' : p));
  if(target !== media && (!target.startsWith(root + path.sep) || !mime[path.extname(target)])) { res.writeHead(403).end(); return; }
  if(!fs.existsSync(target) || !fs.statSync(target).isFile()) { res.writeHead(404).end(); return; }
  const size = fs.statSync(target).size, type = mime[path.extname(target)] || 'application/octet-stream';
  const match = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
  res.setHeader('Content-Type',type); res.setHeader('Accept-Ranges','bytes');
  if(match) { const start=Number(match[1]), end=Math.min(Number(match[2]||size-1),size-1); if(start> end){res.writeHead(416).end();return;} res.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${size}`,'Content-Length':end-start+1});fs.createReadStream(target,{start,end}).pipe(res); }
  else { res.writeHead(200,{'Content-Length':size});fs.createReadStream(target).pipe(res); }
}).listen(4317,'127.0.0.1',()=>process.stdout.write('vizsup v2 preview: http://127.0.0.1:4317\n'));
