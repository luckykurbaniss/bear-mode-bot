const CACHE='bear-mode-images-v2';
self.addEventListener('install',event=>{self.skipWaiting()});
self.addEventListener('activate',event=>{event.waitUntil((async()=>{const keys=await caches.keys();await Promise.all(keys.filter(k=>k.startsWith('bear-mode-')&&k!==CACHE).map(k=>caches.delete(k)));await self.clients.claim()})())});
self.addEventListener('fetch',event=>{
  const req=event.request;
  if(req.method!=='GET')return;
  const url=new URL(req.url);
  if(url.origin!==self.location.origin||!(/\.(?:jpg|jpeg|png|webp)$/i.test(url.pathname)))return;
  event.respondWith((async()=>{
    const cache=await caches.open(CACHE);
    const hit=await cache.match(req);
    if(hit)return hit;
    try{const response=await fetch(req);if(response.ok)cache.put(req,response.clone());return response}
    catch(err){return hit||Response.error()}
  })());
});