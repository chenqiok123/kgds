/* KGDS 双轨语音模块
 * 云端精读：edge-tts 预生成 mp3（现有方案，音质最佳）
 * 本地速读：Web Speech API 调用手机系统 TTS（零延迟、零成本、离线可用）
 *
 * 用法：
 *   LocalTTS.speak(text)   开始朗读
 *   LocalTTS.pause()       暂停
 *   LocalTTS.resume()      恢复
 *   LocalTTS.stop()        停止
 *   LocalTTS.supported()   当前浏览器是否支持
 *   LocalTTS.onStateChange = function(state){}  // 'speaking'|'paused'|'idle'
 */
(function (global) {
  var synth = global.speechSynthesis || null;
  var chunks = [];        // 分段文本队列
  var chunkIdx = 0;       // 当前朗读到第几段
  var state = 'idle';     // 'idle' | 'speaking' | 'paused'
  var zhVoice = null;

  function notify() {
    if (typeof LocalTTS.onStateChange === 'function') {
      try { LocalTTS.onStateChange(state); } catch (e) {}
    }
  }

  function pickVoice() {
    if (!synth) return null;
    var voices = synth.getVoices() || [];
    if (!voices.length) return null;
    // 优先中文（普通话），其次任何 zh 开头，最后 null（用系统默认）
    var preferred = [
      /zh[-_]CN/i, /zh[-_]HK/i, /zh[-_]TW/i, /^zh/i, /Chinese/i, /Mandarin/i
    ];
    for (var p = 0; p < preferred.length; p++) {
      for (var i = 0; i < voices.length; i++) {
        if (preferred[p].test(voices[i].lang) || preferred[p].test(voices[i].name)) {
          return voices[i];
        }
      }
    }
    return null;
  }

  // 部分浏览器语音列表异步加载
  if (synth && typeof synth.onvoiceschanged !== 'undefined') {
    synth.onvoiceschanged = function () { zhVoice = pickVoice(); };
  }

  /* 长文本分段：按句号/换行切，每段 <= 180 字。
   * 部分浏览器单次 utterance 有长度限制（iOS 约 200 字），
   * 分段串行朗读最稳定。 */
  function splitText(text) {
    var cleaned = String(text || '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!cleaned) return [];
    var sentences = cleaned.split(/(?<=[。！？；!?;\n])/);
    var out = [], buf = '';
    for (var i = 0; i < sentences.length; i++) {
      var s = sentences[i];
      if (!s) continue;
      if ((buf + s).length > 180) {
        if (buf) out.push(buf);
        // 单句本身就超长，硬切
        while (s.length > 180) {
          out.push(s.slice(0, 180));
          s = s.slice(180);
        }
        buf = s;
      } else {
        buf += s;
      }
    }
    if (buf) out.push(buf);
    return out;
  }

  function speakNext() {
    if (state !== 'speaking') return;
    if (chunkIdx >= chunks.length) {
      state = 'idle';
      notify();
      return;
    }
    var u = new SpeechSynthesisUtterance(chunks[chunkIdx]);
    u.lang = 'zh-CN';
    if (zhVoice) u.voice = zhVoice;
    u.rate = LocalTTS.rate;
    u.pitch = 1.0;
    u.onend = function () {
      chunkIdx++;
      // onend 在 stop() 时也会触发，用 state 守卫
      if (state === 'speaking') speakNext();
    };
    u.onerror = function () {
      chunkIdx++;
      if (state === 'speaking') speakNext();
    };
    synth.speak(u);
  }

  var LocalTTS = {
    rate: 1.0,
    onStateChange: null,

    supported: function () {
      return !!synth && typeof SpeechSynthesisUtterance !== 'undefined';
    },

    speak: function (text) {
      if (!LocalTTS.supported()) return false;
      LocalTTS.stop();
      if (!zhVoice) zhVoice = pickVoice();
      chunks = splitText(text);
      if (!chunks.length) return false;
      chunkIdx = 0;
      state = 'speaking';
      notify();
      speakNext();
      return true;
    },

    pause: function () {
      if (synth && state === 'speaking') {
        synth.pause();
        state = 'paused';
        notify();
      }
    },

    resume: function () {
      if (synth && state === 'paused') {
        synth.resume();
        state = 'speaking';
        notify();
      }
    },

    stop: function () {
      if (synth) synth.cancel();
      chunks = [];
      chunkIdx = 0;
      if (state !== 'idle') {
        state = 'idle';
        notify();
      }
    },

    getState: function () { return state; }
  };

  global.LocalTTS = LocalTTS;
})(window);
