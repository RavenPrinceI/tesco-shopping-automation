// Minimal DOM test double, not a claim about Tesco's current CSS or layout.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const extract = eval(fs.readFileSync(0, 'utf8'));
const siteMap = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const controlSelector = 'button,a[href],[role="button"]';
function fixture() {
  let next = 10;
  const points = new Map();
  class Element {
    constructor(text = '', attrs = {}, children = {}) {
      this.innerText = text; this.attrs = attrs; this.children = children;
      this.x = next++; this.hidden = false; this.disabled = false;
      points.set(this.x, this);
    }
    querySelectorAll(selector) { return this.children[selector] || []; }
    getClientRects() { return this.hidden ? [] : [this.getBoundingClientRect()]; }
    getBoundingClientRect() { return {x:this.x-1,y:9,width:2,height:2}; }
    closest() { return null; }
    getAttribute(name) { return this.attrs[name] || null; }
    contains(e) { return e === this; }
    matches(selector) {
      return selector === 'input[type="number"]' ? this.attrs.type === 'number' :
        selector === 'input,textarea,select' && !!this.attrs.type;
    }
  }
  const e = text => new Element(text);
  const bound = () => ({'.order-id':[e('order-fixture')], '.slot':[e('slot-fixture')],
    '.cutoff':[e('2099-01-01T12:00:00+00:00')]});
  const add = e('Add'), trolley = e('View trolley'), ordinary = e('Check out groceries');
  const specific = e('Check out to confirm changes');
  const line = new Element('', {}, {'.product':[new Element('', {href:'/groceries/en-GB/products/111111111'})], '.quantity':[e('2')]});
  const basket = new Element('', {}, {'.line':[line], '.count':[e('1')], '.complete':[e('All basket lines')], [controlSelector]:[specific]});
  const peas = new Element('', {}, {'.product':[new Element('', {href:'/groceries/en-GB/products/222222222'})],
    '.quantity':[e('0')], '.name':[e('Tesco Frozen Garden Peas 900g')], '.price':[e('£1.50')], [controlSelector]:[add]});
  const root = new Element('', {}, {...bound(), 'h1':[e('Search results')], '.amending':[e('Making changes')],
    '.basket':[basket], '.peas':[peas], [controlSelector]:[trolley, ordinary, specific, add]});
  const orders = new Element('', {}, {'.order-row':[new Element('', {}, bound())], '.count':[e('1')], '.complete':[e('All upcoming orders')]});
  const doc = new Element('', {}, {'.page':[root], '.orders':[orders], '.authenticated':[e('Signed in')]});
  doc.elementFromPoint = x => points.get(x);
  global.document = doc;
  global.location = {origin:'https://www.tesco.com', href:'https://www.tesco.com/groceries/en-GB/search', pathname:'/groceries/en-GB/search'};
  global.getComputedStyle = () => ({visibility:'visible',display:'block'});
  return {doc, root, orders, basket, peas, add, specific, e, Element};
}
let f = fixture();
let result = extract(siteMap);
assert.equal(result.basket_complete, true);
assert.deepEqual(result.quantities, {'111111111':2});
assert.equal(result.products[0].key, '222222222');
assert.equal(result.controls.filter(c => c.label === 'Check out groceries').length, 0);
assert.equal(result.controls.find(c => c.label === 'Check out to confirm changes').scope, 'basket');
assert.equal(result.products[0].controls[0].reachable, true);
f.basket.children['.count'][0].innerText = '2';
assert.equal(extract(siteMap).basket_complete, false);
f = fixture(); f.peas.children['.name'][0].innerText = 'Different pack';
assert.throws(() => extract(siteMap));
f = fixture(); f.doc.children['input[type="password"]'] = [f.e('')];
assert.deepEqual(extract(siteMap), {authenticated:false});
f = fixture(); f.doc.elementFromPoint = () => null;
assert.equal(extract(siteMap).products[0].controls[0].reachable, false);
f = fixture(); f.root.children['.order-id'].push(f.e('second-order'));
assert.throws(() => extract(siteMap));
f = fixture(); f.basket.children['.line'][0].hidden = true;
assert.equal(extract(siteMap).basket_complete, false);
console.log('DOM fixture checks passed');
