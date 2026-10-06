// Unit-test the shipped UI handlers with a minimal DOM, no browser dependency.
const vm=require('node:vm'),assert=require('node:assert/strict'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const nodes={};let keydown,confirms=0;
function node(){return {value:'',checked:false,disabled:false,children:[],tagName:'DIV',type:'',
 append(x){this.children.push(x)},replaceChildren(){this.children=[]},setAttribute(){},
 click(){if(!this.disabled&&this.onclick)return this.onclick()}}}
const document={getElementById:id=>nodes[id]??=(node()),createElement:node,
 addEventListener:(name,fn)=>{if(name==='keydown')keydown=fn},body:node()};
const fetch=async(url,options)=>({ok:true,json:async()=>{
 if(url.endsWith('/confirm')){confirms++;return {confirmed:true}}
 return input.proposal;
}});
const context={document,fetch,location:{pathname:'/token'},console};
vm.runInNewContext(input.html.split('<script>')[1].split('</script>')[0],context);
const key=k=>keydown({key:k,target:{tagName:'BODY'},preventDefault(){}});
(async()=>{
 await new Promise(setImmediate);
 nodes.name.value='Writer';nodes.name.oninput();
 assert.equal(nodes.confirm.disabled,true,'warning disables Enter');
 key('Enter');assert.equal(confirms,0,'Enter cannot submit without acknowledgment');
 nodes.ack.checked=true;nodes.ack.onchange();assert.equal(nodes.confirm.disabled,false);
 key('ArrowDown');assert.equal(nodes.ack.checked,false,'changing keyword resets acknowledgment');
 assert.equal(nodes.rows.children[1].className,'selected','arrow selects runner-up');
 key('ArrowUp');nodes.ack.checked=true;nodes.ack.onchange();
 nodes.override.value='unvalidated keyword';nodes.override.oninput();
 assert.equal(nodes.confirm.disabled,true,'unvalidated override blocks confirmation');
 nodes.override.value='';nodes.override.oninput();nodes.ack.checked=true;nodes.ack.onchange();
 key('Enter');await new Promise(setImmediate);assert.equal(confirms,1);
 console.log('UI handler tests passed');
})().catch(e=>{console.error(e);process.exit(1)});
