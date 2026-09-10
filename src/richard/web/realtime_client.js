(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RichardRealtimeClient = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const WAIT_MS = 1200;
  const CUE_SPACING_MS = 8000;
  const MAX_CUES_PER_TURN = 2;

  class RealtimePresenceController {
    constructor(options) {
      this.now = options.now || (() => Date.now());
      this.setTimeout = options.setTimeout || ((fn, ms) => setTimeout(fn, ms));
      this.clearTimeout = options.clearTimeout || (id => clearTimeout(id));
      this.random = options.random || Math.random;
      this.send = options.send;
      this.playClip = options.playClip;
      this.stopClip = options.stopClip;
      this.bank = {fingerprint: null, clips: []};
      this.active = null;
      this.turnId = null;
      this.turnCueCount = 0;
      this.phase = null;
      this.waiting = false;
      this.waitInvalid = false;
      this.timer = null;
      this.cue = null;
      this.cuePlayedInGap = false;
      this.silenceSince = this.now();
      this.lastCueAt = -Infinity;
      this.lastClipId = null;
      this.playbacks = new Map();
    }

    setBank(next) {
      const replacement = next && Array.isArray(next.clips)
        ? {fingerprint: next.fingerprint || null, clips: next.clips.slice()}
        : {fingerprint: null, clips: []};
      this._stopCue();
      this.bank = replacement;
      if (!replacement.clips.length) this._cancelTimer();
      else this._arm();
    }

    responseCreated(response) {
      if (!response || !response.id) return false;
      if (!response.turn_id) {
        this._cancelTimer();
        this._stopCue();
        this.active = {id: response.id, turnId: null, unsolicited: true};
        this.waiting = false;
        this.waitInvalid = true;
        this.playbacks.set(response.id, {count: 0, playing: false, done: false});
        return true;
      }
      const sameTurn = response.turn_id === this.turnId;
      if (!sameTurn) {
        this._stopCue();
        this.turnId = response.turn_id;
        this.turnCueCount = 0;
        this.cuePlayedInGap = false;
        this.silenceSince = this.now();
      }
      this._cancelTimer();
      this.active = {
        id: response.id,
        turnId: response.turn_id,
        unsolicited: !!response.unsolicited,
      };
      this.phase = null;
      this.waiting = false;
      this.waitInvalid = false;
      this.playbacks.set(response.id, {count: 0, playing: false, done: false});
      return true;
    }

    accepts(responseId) {
      return !!this.active && !this.active.terminal && this.active.id === responseId;
    }

    activity(event) {
      if (!event || !this.accepts(event.response_id) || event.turn_id !== this.active.turnId) return false;
      if (event.unsolicited || this.active.unsolicited) {
        this.waiting = false;
        this._cancelTimer();
        return true;
      }
      if (event.phase === 'answer') {
        this.waiting = false;
        this._cancelTimer();
        this._stopCue();
        return true;
      }
      if (event.phase === 'error') {
        this.waiting = false;
        this.waitInvalid = true;
        this._cancelTimer();
        this._stopCue();
        return true;
      }
      const phase = event.phase === 'vision' ? 'visual'
        : (event.phase === 'thinking' || event.phase === 'tool' ? 'thinking' : null);
      if (!phase || this.waitInvalid) return true;
      this.phase = phase;
      this.waiting = true;
      this._arm();
      return true;
    }

    realAudioScheduled(responseId) {
      if (!this.accepts(responseId)) return false;
      this.waiting = false;
      this._cancelTimer();
      this._stopCue();
      const state = this.playbacks.get(responseId);
      state.count += 1;
      if (!state.playing) {
        state.playing = true;
        this.send({type: 'playback.update', response_id: responseId, playing: true});
      }
      return true;
    }

    realAudioEnded(responseId) {
      const state = this.playbacks.get(responseId);
      if (!state) return false;
      state.count = Math.max(0, state.count - 1);
      if (state.count === 0) {
        this.silenceSince = this.now();
        this.cuePlayedInGap = false;
      }
      this._finishPlayback(responseId, state);
      this._arm();
      return true;
    }

    realAudioFlushed(responseId) {
      const state = this.playbacks.get(responseId);
      if (!state) return false;
      state.count = 0;
      this.silenceSince = this.now();
      this.cuePlayedInGap = false;
      if (state.playing) {
        state.playing = false;
        this.send({type: 'playback.update', response_id: responseId, playing: false});
      }
      return true;
    }

    responseDone(response) {
      if (!response || !response.id) return false;
      const state = this.playbacks.get(response.id);
      if (state) {
        state.done = true;
        this._finishPlayback(response.id, state);
      }
      if (!this.accepts(response.id)) return false;
      this.active.terminal = true;
      this.waiting = false;
      this._cancelTimer();
      this._stopCue();
      return true;
    }

    speechStarted() {
      this.waiting = false;
      this._cancelTimer();
      this._stopCue();
      if (this.active) this.active.terminal = true;
      for (const [responseId, state] of this.playbacks) {
        if (state.playing) this.realAudioFlushed(responseId);
      }
    }

    protocolError(responseId) {
      if (responseId && !this.accepts(responseId)) return false;
      this.waiting = false;
      this.waitInvalid = true;
      this._cancelTimer();
      this._stopCue();
      return true;
    }

    disconnect() {
      this.waiting = false;
      this._cancelTimer();
      this._stopCue();
      this.active = null;
      this.playbacks.clear();
    }

    _finishPlayback(responseId, state) {
      if (state.done && state.count === 0 && state.playing) {
        state.playing = false;
        this.send({type: 'playback.update', response_id: responseId, playing: false});
      }
    }

    _clips() {
      return this.bank.clips.filter(clip => clip.phase === this.phase && clip.decoded);
    }

    _arm() {
      if (this.timer || this.cue || !this.waiting || this.waitInvalid || !this.active
          || this.active.unsolicited || this.cuePlayedInGap
          || this.turnCueCount >= MAX_CUES_PER_TURN || this._hasAudiblePlayback()
          || !this._clips().length) return;
      const due = Math.max(this.silenceSince + WAIT_MS, this.lastCueAt + CUE_SPACING_MS);
      this.timer = this.setTimeout(() => {
        this.timer = null;
        this._playCue();
      }, Math.max(0, due - this.now()));
    }

    _hasAudiblePlayback() {
      for (const state of this.playbacks.values()) {
        if (state.count > 0) return true;
      }
      return false;
    }

    _playCue() {
      if (!this.waiting || this.waitInvalid || !this.active || this.active.unsolicited
          || this.cuePlayedInGap || this.turnCueCount >= MAX_CUES_PER_TURN) return;
      let choices = this._clips();
      if (choices.length > 1) choices = choices.filter(clip => clip.id !== this.lastClipId);
      if (!choices.length) return;
      const index = Math.min(choices.length - 1, Math.floor(this.random() * choices.length));
      const clip = choices[index];
      const marker = {};
      const ended = () => {
        if (!this.cue || this.cue.marker !== marker) return;
        this.cue = null;
        this.silenceSince = this.now();
        this.cuePlayedInGap = false;
        this._arm();
      };
      const handle = this.playClip(clip, ended);
      this.cue = {marker, handle};
      this.lastCueAt = this.now();
      this.lastClipId = clip.id;
      this.turnCueCount += 1;
      this.cuePlayedInGap = true;
    }

    _cancelTimer() {
      if (this.timer !== null) this.clearTimeout(this.timer);
      this.timer = null;
    }

    _stopCue() {
      if (!this.cue) return;
      const current = this.cue;
      this.cue = null;
      this.stopClip(current.handle);
      this.silenceSince = this.now();
      this.cuePlayedInGap = false;
    }
  }

  class SharedCamera {
    constructor(options) {
      this.getUserMedia = options.getUserMedia;
      this.makeVideo = options.makeVideo;
      this.videoConstraints = options.videoConstraints || {
        width: {ideal: 1920}, height: {ideal: 1080}, facingMode: 'user',
      };
      this.owners = new Map();
      this.generations = new Map();
      this.current = null;
      this.pending = null;
    }

    async acquire(owner) {
      const generation = (this.generations.get(owner) || 0) + 1;
      this.generations.set(owner, generation);
      this.owners.set(owner, generation);
      try {
        const media = await this._ensure();
        return this.owners.get(owner) === generation ? media : null;
      } catch (error) {
        if (this.owners.get(owner) === generation) this.owners.delete(owner);
        throw error;
      }
    }

    release(owner) {
      this.generations.set(owner, (this.generations.get(owner) || 0) + 1);
      this.owners.delete(owner);
      if (!this.owners.size && this.current) this._dispose(this.current);
    }

    media() {
      return this.current && this.current.track.readyState !== 'ended' ? this.current : null;
    }

    async _ensure() {
      if (this.media()) return this.current;
      if (this.pending) return this.pending;
      this.pending = (async () => {
        const stream = await this.getUserMedia({video: this.videoConstraints});
        const track = stream.getVideoTracks()[0];
        if (!track) {
          stream.getTracks().forEach(item => item.stop());
          throw new Error('camera returned no video track');
        }
        const video = this.makeVideo(stream);
        if (video.play) {
          try { await video.play(); } catch (_) {}
        }
        const media = {stream, track, video};
        if (!this.owners.size) {
          this._dispose(media);
          return null;
        }
        this.current = media;
        return media;
      })();
      try { return await this.pending; }
      finally { this.pending = null; }
    }

    _dispose(media) {
      if (this.current === media) this.current = null;
      media.stream.getTracks().forEach(track => track.stop());
      if (media.video && media.video.remove) media.video.remove();
    }
  }

  class FrameUploader {
    constructor(options) {
      this.sourceId = options.sourceId;
      this.capture = options.capture;
      this.upload = options.upload;
      this.onError = options.onError || (() => {});
      this.inFlight = null;
      this.freshPending = false;
    }

    request() {
      if (this.inFlight) return this.inFlight;
      const image = this.capture();
      if (!image) return Promise.resolve(false);
      let operation;
      try { operation = Promise.resolve(this.upload(this.sourceId, image)); }
      catch (error) { operation = Promise.reject(error); }
      this.inFlight = operation.finally(() => {
        this.inFlight = null;
        if (this.freshPending) {
          this.freshPending = false;
          this.request().catch(error => this.onError(error));
        }
      });
      return this.inFlight;
    }

    requestFresh() {
      if (this.inFlight) {
        this.freshPending = true;
        return this.inFlight;
      }
      return this.request();
    }

    cancelPendingFresh() {
      this.freshPending = false;
    }
  }

  function createBrowserSourceId(cryptoObject, random) {
    let suffix = '';
    if (cryptoObject && typeof cryptoObject.randomUUID === 'function') suffix = cryptoObject.randomUUID();
    else {
      const choose = random || Math.random;
      for (let i = 0; i < 24; i++) suffix += Math.floor(choose() * 16).toString(16);
    }
    return ('browser-' + suffix).replace(/[^A-Za-z0-9._:-]/g, '').slice(0, 64);
  }

  return {
    WAIT_MS,
    CUE_SPACING_MS,
    MAX_CUES_PER_TURN,
    RealtimePresenceController,
    SharedCamera,
    FrameUploader,
    createBrowserSourceId,
  };
});
