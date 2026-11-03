/* 24 小时制的时间选择（11-03 小猫：手机自带的时间表盘只有 12 小时，怎么选都是上午，静默时段存成了上午 11 点）。
 * 页面上的 <input type="time"> 换成「几点（0～23）+ 几分」两个下拉框；原来的输入框藏起来但照旧用：
 * 页面代码读写 input.value、监听 input / change（包括 onchange 属性）都不用改。 */
(function () {
  const pad = n => String(n).padStart(2, '0');
  const nativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

  function hourLabel(h) {
    const period = h < 5 ? '凌晨' : h < 8 ? '早上' : h < 12 ? '上午' : h < 13 ? '中午' : h < 18 ? '下午' : h < 23 ? '晚上' : '深夜';
    return `${pad(h)} 点（${period}）`;
  }

  function upgrade(input) {
    if (input.dataset.time24) return;
    input.dataset.time24 = '1';
    const wrap = document.createElement('span');
    wrap.className = 'time24';
    const hour = document.createElement('select');
    const minute = document.createElement('select');
    hour.setAttribute('aria-label', `${input.getAttribute('aria-label') || '时间'}：几点`);
    minute.setAttribute('aria-label', `${input.getAttribute('aria-label') || '时间'}：几分`);
    for (let h = 0; h < 24; h++) hour.add(new Option(hourLabel(h), pad(h)));
    for (let m = 0; m < 60; m += 5) minute.add(new Option(`${pad(m)} 分`, pad(m)));
    wrap.append(hour, minute);
    input.after(wrap);
    input.hidden = true;
    input.style.display = 'none';

    const show = value => {
      const [h, m] = String(value || '').split(':');
      if (!/^\d\d$/.test(h || '') || !/^\d\d$/.test(m || '')) return;
      hour.value = h;
      if (![...minute.options].some(o => o.value === m)) minute.add(new Option(`${m} 分`, m));  // 以前存的不是 5 的倍数
      minute.value = m;
    };
    // 页面代码给输入框赋值时，下拉框跟着变
    Object.defineProperty(input, 'value', {
      configurable: true,
      get() { return nativeValue.get.call(this); },
      set(v) { nativeValue.set.call(this, v); show(v); },
    });
    show(input.value);
    const commit = () => {
      nativeValue.set.call(input, `${hour.value}:${minute.value}`);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    hour.addEventListener('change', commit);
    minute.addEventListener('change', commit);
  }

  function addStyle() {
    if (document.getElementById('time24Style')) return;
    const style = document.createElement('style');
    style.id = 'time24Style';
    style.textContent = '.time24{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap;max-width:100%}'
      + '.time24 select{min-height:38px;padding:4px 8px;border:1px solid var(--border,#ccd);border-radius:12px;'
      + 'background:var(--surface,#fff);color:var(--text,inherit);font:inherit;max-width:100%}';
    document.head.appendChild(style);
  }

  function upgradeAll() {
    addStyle();
    document.querySelectorAll('input[type="time"]').forEach(upgrade);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', upgradeAll);
  else upgradeAll();
  window.upgradeTime24 = upgradeAll;
})();
