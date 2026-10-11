const test=require('node:test'),assert=require('node:assert/strict');
test('settled card renders configured identities safely and leaves ordinary notices alone',()=>{
  const {render}=require('../result-card.js');
  assert.equal(render([]),'');
  const html=render([{type:'billiards_result',rack:2,players:[{name:'测试用户 <img>'},{name:'另一位伴侣'}],winner:1,score:[1,1],shots:18,reason:'合法打进黑八'}]);
  assert.ok(html.includes('另一位伴侣获胜'));assert.ok(html.includes('测试用户 &lt;img&gt;落败'));
  assert.ok(html.includes('1 : 1'));assert.ok(html.includes('18 杆'));assert.ok(html.includes('合法打进黑八'));assert.ok(!html.includes('<img>'));
});
