/* 桌面格子摆位（11-09 小猫：小组件和 App 混排）。只算位置，不碰页面。
   cell 是从第一页左上角数起的序号：page * cols * rows + row * cols + col。
   先摆被拖着的，再摆小组件，最后摆图标；摆不下原位的往后找第一个放得下的地方。 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HomeGridSolver = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function split(cell, cols, rows) {
    var perPage = cols * rows, inPage = cell % perPage;
    return { page: Math.floor(cell / perPage), row: Math.floor(inPage / cols), col: inPage % cols };
  }
  function join(page, row, col, cols, rows) { return page * cols * rows + row * cols + col; }

  // 跨到页边外的挪回来：4 格宽的小组件只能从每行第一格开始
  function clampCell(cell, w, h, cols, rows) {
    var at = split(Math.max(0, cell), cols, rows);
    return join(at.page, Math.min(at.row, Math.max(0, rows - h)), Math.min(at.col, Math.max(0, cols - w)), cols, rows);
  }

  function covered(cell, w, h, cols, rows) {
    var at = split(cell, cols, rows), out = [];
    if (at.col + w > cols || at.row + h > rows) return null;
    for (var r = 0; r < h; r++) for (var c = 0; c < w; c++) out.push(join(at.page, at.row + r, at.col + c, cols, rows));
    return out;
  }

  function place(items, cols, rows, pinnedId) {
    var used = {}, result = {}, limit = cols * rows * 200;
    var ordered = items.slice().sort(function (a, b) {
      var pa = a.id === pinnedId ? 0 : 1, pb = b.id === pinnedId ? 0 : 1;
      if (pa !== pb) return pa - pb;
      var wa = a.w * a.h > 1 ? 0 : 1, wb = b.w * b.h > 1 ? 0 : 1;
      if (wa !== wb) return wa - wb;
      return (a.cell || 0) - (b.cell || 0);
    });
    ordered.forEach(function (item) {
      var w = Math.min(item.w, cols), h = Math.min(item.h, rows);
      var start = clampCell(Number.isInteger(item.cell) && item.cell >= 0 ? item.cell : 0, w, h, cols, rows);
      for (var cell = start; cell < limit; cell++) {
        var cells = covered(cell, w, h, cols, rows);
        if (cells && cells.every(function (n) { return !used[n]; })) {
          cells.forEach(function (n) { used[n] = item.id; });
          result[item.id] = cell;
          return;
        }
      }
    });
    return result;
  }

  return { place: place, clampCell: clampCell, split: split };
});
