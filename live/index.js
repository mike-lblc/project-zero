import dashboard from "../service/dashboard.html";
export default {
 async fetch(request, env) {
  const path=new URL(request.url).pathname;
  if(request.method!=="GET") return new Response("Read only",{status:405});
  const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
  if(path==="/"||path==="/dashboard") return new Response(dashboard.replace("<head>","<head><script>window.P0_LIVE=true;</script>"),{headers:{...headers,"Content-Type":"text/html; charset=utf-8"}});
  const kind=path.replace("/api/","");
  if(!["all","status","pulse","queue","execution","chat"].includes(kind)) return new Response("Not found",{status:404});
  try {
   const row=await env.STATE.prepare("SELECT body,updated_at FROM live_state WHERE id=1").first();
   if(!row) return Response.json({error:"Waiting for first cloud event"},{status:503,headers});
   const data=JSON.parse(row.body);
   const result=kind === "all" ? data : data[kind]||{};
   if(kind === "all"){ data.status.cloud_updated_at=row.updated_at; data.status.cloud_stale=Date.now()-Date.parse(row.updated_at)>25*60*1000; }
   result.cloud_updated_at=row.updated_at;
   result.cloud_stale=Date.now()-Date.parse(row.updated_at)>25*60*1000;
   return Response.json(result,{headers});
  } catch {return Response.json({error:"Cloud state unavailable"},{status:503,headers});}
 }
};
