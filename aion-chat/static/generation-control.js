/* One request id owns its fetch, followups and late events. */
class ChatGenerationControl {
  constructor({ surface, baseUrl, onStart, onStop, onFinish, onReconcile, onError, onPreparing }) {
    Object.assign(this, { surface, baseUrl, onStart, onStop, onFinish, onReconcile, onError, onPreparing });
    this.active = null;
    this.stoppedIds = new Set();
    this.retryStop = null;
  }

  begin(target) {
    if (this.active) this.onPreparing?.(null, this.active);
    const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const generation = { id, target, controller: new AbortController(), stopped: false, messageIds: new Set(), timing: {} };
    this.active = generation;
    this.onStart?.(generation);
    return generation;
  }

  isCurrent(generation) { return this.active === generation; }

  remoteStop(data) {
    if (data.surface !== this.surface) return;
    this.stoppedIds.add(data.generation_id);
    const generation = this.active;
    if (generation?.id === data.generation_id) {
      generation.stopped = true;
      clearTimeout(generation.timer);
      generation.controller.abort();
      this.onPreparing?.(null, generation);
      this.active = null;
      this.onStop?.(generation);
    }
    if (this.retryStop?.id === data.generation_id) this.retryStop = null;
    this.onReconcile?.({ id: data.generation_id, target: data.target }, data);
    if (!this.active) this.onFinish?.();
  }

  async fetch(generation, url, options) {
    if (/\/send$/.test(url) && options?.body) {
      try {
        const bridge = globalThis.AionFloatingChat || globalThis.parent?.AionFloatingChat;
        bridge?.rememberOptions(this.surface === 'private' ? 'private' : 'chatroom', String(generation.target), options.body);
      } catch (_) {}
    }
    generation.sentAt = globalThis.performance?.now?.() ?? Date.now();
    this.onPreparing?.('', generation);
    try {
      const response = await fetch(url, {
      ...options,
      headers: { ...options?.headers, 'X-Generation-Id': generation.id, 'X-Generation-Target': generation.target },
      signal: generation.controller.signal,
    });
      response.chatGeneration = generation;
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      generation.timing.response_headers_ms = this.elapsed(generation);
      return response;
    } catch (error) {
      this.onPreparing?.(null, generation);
      throw error;
    }
  }

  elapsed(generation) { return Math.round(((globalThis.performance?.now?.() ?? Date.now()) - generation.sentAt) * 100) / 100; }

  accepts(event, generation = null) {
    const id = event.generation_id || event.data?.generation_id;
    if (this.stoppedIds.has(id) || generation?.stopped) return false;
    const owner = generation || (this.active?.id === id ? this.active : null);
    if (owner && this.isCurrent(owner)) {
      if (event.type === 'preparing') this.onPreparing?.(event.sender || '', owner);
      if (/^(start|aion_start|connor_start|done|error|stream_error|aion_failed|connor_failed)$/.test(event.type)) this.onPreparing?.(null, owner);
      if (event.type === 'reply_timing') {
        owner.timing.backend = event;
        console.info('[reply-timing] backend', event);
      }
      const phase = /^(start|aion_start|connor_start)$/.test(event.type) ? event.type
        : (/^(chunk|aion_chunk|connor_chunk|replace|snapshot)$/.test(event.type) && event.content ? event.type + '_first' : null);
      if (phase && owner.timing[phase] == null) {
        owner.timing[phase] = this.elapsed(owner);
        console.info('[reply-timing] browser', { generation_id: owner.id, surface: this.surface,
          phase, elapsed_ms: owner.timing[phase], response_headers_ms: owner.timing.response_headers_ms });
      }
    }
    if (generation && /^(start|aion_start|connor_start)$/.test(event.type) && event.id) {
      generation.messageIds.add(event.id);
    }
    return true;
  }

  async requestState(generation, action, method = 'GET') {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${this.baseUrl(generation.target)}/${action}?generation_id=${encodeURIComponent(generation.id)}`, {
        method, signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally { clearTimeout(timer); }
  }

  async finish(generation) {
    this.onPreparing?.(null, generation);
    if (!this.isCurrent(generation) || generation.stopped) return;
    try {
      const state = await this.requestState(generation, 'generation-status');
      if (!this.isCurrent(generation)) return;
      state.message_ids?.forEach(id => generation.messageIds.add(id));
      if (state.active) {
        generation.timer = setTimeout(() => this.finish(generation), 1000);
        return;
      }
      this.active = null;
      this.onFinish?.(generation);
    } catch (error) {
      if (!this.isCurrent(generation)) return;
      // Keep stop available when the server cannot confirm completion.
      this.onError?.('暂时无法确认后台状态，仍可点击停止');
    }
  }

  async stop() {
    const generation = this.active || this.retryStop;
    if (!generation) return;
    if (!generation.stopped) {
      generation.stopped = true;
      this.stoppedIds.add(generation.id);
      clearTimeout(generation.timer);
      generation.controller.abort();
      this.onPreparing?.(null, generation);
      if (this.active === generation) this.active = null;
      this.onStop?.(generation);
    }
    this.retryStop = generation;
    try {
      const result = await this.requestState(generation, 'abort', 'POST');
      result.message_ids?.forEach(id => generation.messageIds.add(id));
      this.onReconcile?.(generation, result);
      if (!result.stopped) {
        this.onError?.('已停止接收，后台仍在清理；可再次点击停止确认');
        return;
      }
      if (this.retryStop === generation) this.retryStop = null;
      if (!this.active) this.onFinish?.(generation);
    } catch (error) {
      this.onError?.('已停止接收，但未确认后台停止；请点击停止重试');
    }
  }
}

window.ChatGenerationControl = ChatGenerationControl;
