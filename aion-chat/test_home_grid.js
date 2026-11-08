// 桌面混排的摆位算法（11-09）。
const assert = require('node:assert/strict');
const { place, clampCell, split } = require('./static/home-grid.js');

const cols = 4, rows = 5;
const app = (id, cell) => ({ id, w: 1, h: 1, cell });
const wide = (id, cell, h = 2) => ({ id, w: 4, h, cell });
const half = (id, cell) => ({ id, w: 2, h: 2, cell });

// 没冲突：都在原位
assert.deepEqual(place([app('a', 0), app('b', 1)], cols, rows), { a: 0, b: 1 });

// 小组件放在图标上：图标让开，小组件在原位
let r = place([app('a', 0), app('b', 1), wide('w', 0)], cols, rows);
assert.equal(r.w, 0);
assert.ok(r.a >= 8 && r.b >= 8 && r.a !== r.b);

// 4 格宽的小组件拖到一行中间：挪回行首
assert.equal(clampCell(2, 4, 2, cols, rows), 0);
assert.equal(clampCell(5, 4, 2, cols, rows), 4);

// 半宽小组件最多从第 3 列开始
assert.equal(clampCell(3, 2, 2, cols, rows), 2);

// 拖到最后一行：高度放不下就往上提
assert.equal(clampCell(19, 2, 2, cols, rows), 14);

// 跨页的格子不会横跨两页
r = place([wide('w', 16, 2)], cols, rows);
assert.equal(split(r.w, cols, rows).row + 2 <= rows, true);
assert.equal(r.w, 12);

// 被拖着的优先：别的小组件让开
r = place([wide('x', 0), wide('drag', 0)], cols, rows, 'drag');
assert.equal(r.drag, 0);
assert.equal(r.x, 8);

// 一页放满：放到下一页
const many = Array.from({ length: 21 }, (_, i) => app('i' + i, 0));
r = place(many, cols, rows);
assert.equal(new Set(Object.values(r)).size, 21);
assert.equal(Math.max(...Object.values(r)), 20);

// 没有位置信息的：给 0，不报错
r = place([{ id: 'n', w: 2, h: 2 }], cols, rows);
assert.equal(r.n, 0);

// 任何情况下都不重叠
function occupied(items, result) {
  const seen = new Set();
  items.forEach(it => {
    const at = split(result[it.id], cols, rows);
    for (let a = 0; a < it.h; a++) for (let b = 0; b < it.w; b++) {
      const key = `${at.page}:${at.row + a}:${at.col + b}`;
      assert.ok(!seen.has(key), 'overlap ' + key);
      assert.ok(at.col + b < cols && at.row + a < rows, 'outside page');
      seen.add(key);
    }
  });
}
const mixed = [wide('c', 3, 3), half('n', 2), half('m', 6), wide('t', 9), ...Array.from({ length: 30 }, (_, i) => app('a' + i, i % 9))];
occupied(mixed, place(mixed, cols, rows));
occupied(mixed, place(mixed, cols, rows, 'm'));
console.log('home grid ok');
