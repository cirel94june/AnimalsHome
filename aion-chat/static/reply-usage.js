/* Only numbers from the persisted reply receipt are rendered here. */
(() => {
  function render(message) {
    const receipt = (message?.attachments || []).find(a => a?.type === 'reply_usage');
    if (!receipt) return '';
    const n = key => Number.isSafeInteger(receipt[key]) && receipt[key] >= 0 ? receipt[key] : 0;
    const calls = n('calls'), reported = n('reported_calls'), input = n('input_tokens');
    const cached = n('cache_read_tokens');
    const complete = calls > 0 && reported === calls && n('cache_reported_calls') === calls && cached <= input;
    const rate = complete && input > 0 ? `${(100 * cached / input).toFixed(1)}%` : '未知';
    const creation = n('cache_creation_reported_calls') > 0 ? ` · 写缓存 ${n('cache_creation_tokens').toLocaleString()}` : '';
    const reasoning = n('reasoning_reported_calls') > 0 ? ` · 推理 ${n('reasoning_tokens').toLocaleString()}` : '';
    const detail = reported > 0
      ? `已返回用量：输入 ${input.toLocaleString()} · 命中 ${n('cache_reported_calls') > 0 ? cached.toLocaleString() : '未知'}${creation} · 输出 ${n('output_reported_calls') > 0 ? n('output_tokens').toLocaleString() : '未知'}${reasoning}`
      : '这条线路没有返回用量数据';
    const note = complete ? '' : '；数据不完整，不能确定整轮缓存率';
    return `<details class="reply-usage"><summary>缓存 ${rate}</summary><span>${detail}${calls > 1 ? ` · ${calls} 次调用` : ''}${note}。缓存率＝缓存命中 ÷ 总输入；不代表账单折扣。</span></details>`;
  }
  window.ReplyUsage = { render };
})();
