// Unit-test the shipped UI handlers with a minimal DOM, no browser dependency.
const vm=require('node:vm'),assert=require('node:assert/strict'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const proposal=input.proposal;
proposal.options[1].serp_snapshot.organic[0].title='Runner-up result';
proposal.options[0].serp_snapshot.organic[1].url='javascript:alert(1)';
proposal.options[0].serp_snapshot.organic[2].different_brand=true;
proposal.options[0].serp_snapshot.organic[3].relevance=0.2;
let nodes={};let keydown,confirms=0,overrides=0,release,overrideReply;
function node(tag='div'){return {tag,value:'',checked:false,disabled:false,children:[],tagName:tag.toUpperCase(),type:'',textContent:'',
 append(x){this.children.push(x)},replaceChildren(){this.children=[]},setAttribute(){},
 click(){if(!this.disabled&&this.onclick)return this.onclick()}}}
const text=n=>typeof n==='string'?n:(n.textContent||'')+n.children.map(text).join('');
const document={getElementById:id=>nodes[id]??=(node()),createElement:node,
 addEventListener:(name,fn)=>{if(name==='keydown')keydown=fn},body:node()};
const fetch=async(url,options)=>{
 if(url.endsWith('/confirm')){confirms++;return {ok:true,json:async()=>({confirmed:true})}}
 if(url.endsWith('/override')){overrides++;const reply=overrideReply;await new Promise(r=>release=r);return reply}
 return {ok:true,json:async()=>proposal};
};
// Each load is a fresh page: new DOM nodes, new script state.
const load=()=>{nodes={};document.body=node();vm.runInNewContext(input.html.split('<script>')[1].split('</script>')[0],
 {document,fetch,location:{pathname:'/token'},console,URL})};
load();
const key=k=>keydown({key:k,target:{tagName:'BODY'},preventDefault(){}});
const tick=async()=>{for(let i=0;i<3;i++)await new Promise(setImmediate)};
const serpCells=i=>nodes.serp.children[i].children;
(async()=>{
 await tick();

 // Results panel for the default option.
 assert.equal(nodes.serp.children.length,10,'panel renders the top 10 results');
 const first=serpCells(0);
 assert.equal(text(first[0]),'1');
 const link=first[1].children[0];
 assert.equal(link.tag,'a');
 assert.equal(link.href,'https://www.pingcap.com/existing/');
 assert.equal(link.target,'_blank');
 assert.equal(link.rel,'noopener noreferrer');
 assert.equal(text(first[2]),'www.pingcap.com');
 assert.equal(text(first[3]),'comparison');
 assert.equal(text(first[4]),'95% ✅');
 assert.equal(text(first[5]),'');
 assert.equal(serpCells(1)[1].children[0].tag,'span','non-http URLs are not linked');
 assert.equal(text(serpCells(2)[5]),'Different brand');
 assert.equal(text(serpCells(3)[4]),'20% ❌');
 assert.equal(text(nodes.serpSummary),'10 of 10 relevant (minimum 5).');
 assert.equal(nodes.google.href,'https://www.google.com/search?q=supabase%20alternative&gl=us&hl=en');

 // Acknowledgment flow.
 nodes.name.value='Writer';nodes.name.oninput();
 assert.equal(nodes.confirm.disabled,true,'warning disables Enter');
 key('Enter');assert.equal(confirms,0,'Enter cannot submit without acknowledgment');
 nodes.ack.checked=true;nodes.ack.onchange();assert.equal(nodes.confirm.disabled,false);

 // Switching options updates the panel and clears acknowledgment.
 key('ArrowDown');assert.equal(nodes.ack.checked,false,'changing keyword resets acknowledgment');
 assert.equal(nodes.rows.children[1].className,'selected','arrow selects runner-up');
 assert.equal(text(serpCells(0)[1]),'Runner-up result','panel follows the selected option');
 assert.equal(nodes.google.href,'https://www.google.com/search?q='+encodeURIComponent(proposal.options[1].keyword)+'&gl=us&hl=en');
 key('ArrowUp');assert.equal(text(serpCells(0)[1]),'Comparison');
 nodes.ack.checked=true;nodes.ack.onchange();

 nodes.override.value='unvalidated keyword';nodes.override.oninput();
 assert.equal(nodes.confirm.disabled,true,'unvalidated override blocks confirmation');

 // A failed validation keeps the existing options usable.
 overrideReply={ok:false,json:async()=>({error:'Keyword must be nonempty text of at most 100 characters.'})};
 nodes.validate.click();await tick();release();await tick();
 assert.equal(nodes.rows.children.length,proposal.options.length,'options still listed');
 assert.equal(nodes.serp.children.length,10,'results panel still shown');
 assert.match(nodes.error.textContent,/at most 100 characters.*clear it/);
 assert.equal(nodes.validate.disabled,false,'writer can retry');
 key('ArrowDown');assert.equal(nodes.rows.children[1].className,'selected','arrows still work after an error');
 key('ArrowUp');
 nodes.override.value='';nodes.override.oninput();nodes.ack.checked=true;nodes.ack.onchange();
 assert.equal(nodes.confirm.disabled,false,'clearing the override restores confirmation');

 // Repeated clicks send one request; Enter is ignored while validating.
 overrideReply={ok:true,json:async()=>({...proposal,override_text:'supabase alternatives'})};
 nodes.override.value='supabase alternatives';nodes.override.oninput();
 nodes.validate.click();await tick();
 assert.equal(nodes.validate.disabled,true,'validate disabled while running');
 assert.equal(nodes.override.disabled,true,'override field locked while running');
 nodes.validate.onclick();nodes.validate.onclick();await tick();
 assert.equal(overrides,2,'repeated clicks send one validation request');
 key('Enter');assert.equal(confirms,0,'Enter ignored while validating');
 release();await tick();
 assert.equal(nodes.validate.disabled,false,'validate re-enabled after response');
 assert.equal(nodes.override.disabled,false);
 nodes.ack.checked=true;nodes.ack.onchange();
 key('Enter');await tick();assert.equal(confirms,1);

 // A provider failure ends the run and locks the page (fresh page load).
 load();await tick();
 nodes.name.value='Writer';nodes.name.oninput();
 overrideReply={ok:false,json:async()=>({error:'DataForSEO down',run_ended:true})};
 nodes.override.value='another keyword';nodes.override.oninput();
 nodes.validate.click();await tick();release();await tick();
 assert.match(nodes.error.textContent,/DataForSEO down.*run has ended/);
 assert.equal(nodes.validate.disabled,true);
 assert.equal(nodes.confirm.disabled,true);
 console.log('UI handler tests passed');
})().catch(e=>{console.error(e);process.exit(1)});
