const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/js/map.js'),'utf8');
const code=source.slice(source.indexOf('async function checkPickedPoint('),source.indexOf('\nfunction recheckEndpoints()'));
function fixture(replies){
  const calls=[];
  const ctx={pointCheckSerial:{start:0,end:0},pointChecks:{start:null,end:null},
    startInput:{value:'1,1,1'},endInput:{value:'2,2,2'},
    pickedMarkers:{start:{point:{},position:null},end:{point:{},position:null}},
    document:{getElementById:()=>({value:'10'})},mapPickStatus:{textContent:'',style:{}},
    Cesium:{Color:{LIME:'green',RED:'red'}},voxelToCartesian:p=>p.join(','),refreshPointButtons:()=>{},
    fetch:async(_url,init)=>{calls.push(JSON.parse(init.body).point);const next=replies.shift();return {ok:true,json:async()=>typeof next==='function'?await next():next};}};
  vm.createContext(ctx);vm.runInContext(code,ctx);return {ctx,calls};
}
test('start and end automatically apply independently validated safe coordinates',async()=>{
  for(const kind of ['start','end']){
    const {ctx,calls}=fixture([{valid:false,reason:'obstacle',suggested_point:[3,4,5]},{valid:true}]);
    await ctx.checkPickedPoint(kind,[1,1,1]);
    assert.equal(ctx[kind+'Input'].value,'3,4,5');assert.equal(ctx.pickedMarkers[kind].position,'3,4,5');
    assert.equal(ctx.pointChecks[kind].valid,true);assert.equal(calls.length,2);
    assert.match(ctx.mapPickStatus.textContent,/자동 보정 완료/);
  }
});
test('unavailable or invalid suggestions never enable the endpoint or mutate its input',async()=>{
  for(const replies of [[{valid:false,reason:'blocked'}],[{valid:false,reason:'blocked',suggested_point:[3,4,5]},{valid:false,reason:'blocked'}]]){
    const {ctx}=fixture(replies);await ctx.checkPickedPoint('start',[1,1,1]);
    assert.equal(ctx.pointChecks.start.valid,false);assert.equal(ctx.startInput.value,'1,1,1');
  }
});
test('background validation does not silently alter manually entered coordinates',async()=>{
  const {ctx,calls}=fixture([{valid:false,reason:'blocked',suggested_point:[3,4,5]}]);
  await ctx.checkPickedPoint('start',[1,1,1],false);
  assert.equal(calls.length,1);assert.equal(ctx.startInput.value,'1,1,1');assert.equal(ctx.pointChecks.start.valid,false);
});
test('a late correction cannot overwrite a newer endpoint selection',async()=>{
  let release;const waiting=new Promise(resolve=>{release=resolve;});
  const {ctx}=fixture([{valid:false,suggested_point:[3,4,5]},()=>waiting,{valid:true}]);
  const old=ctx.checkPickedPoint('start',[1,1,1]);
  await new Promise(resolve=>setImmediate(resolve));
  ctx.startInput.value='7,8,9';await ctx.checkPickedPoint('start',[7,8,9]);
  release({valid:true});await old;
  assert.equal(ctx.startInput.value,'7,8,9');assert.equal(ctx.pointChecks.start.point,'7,8,9');
});
