// 入力を 16kHz に変換し、frameMs ミリ秒ごとに 16bit PCM を送る。
// ブラウザは AudioContext のサンプルレート指定を無視することが多いので、ここで変換する。
// Haas-ASR の demos/static/capture-worklet.js (Apache-2.0, Third Intelligence) を元にしている。
class Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.target = options.processorOptions.targetRate;
    this.ratio = sampleRate / this.target;
    this.frameSize = Math.max(128, Math.round((this.target * options.processorOptions.frameMs) / 1000));
    this.carry = new Float32Array(0);
    this.position = 0;
    this.out = [];
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    const merged = new Float32Array(this.carry.length + input.length);
    merged.set(this.carry, 0);
    merged.set(input, this.carry.length);
    while (this.position + 1 < merged.length) {
      const i = Math.floor(this.position), f = this.position - i;
      this.out.push(merged[i] * (1 - f) + merged[i + 1] * f);
      this.position += this.ratio;
    }
    const used = Math.floor(this.position);
    this.carry = merged.slice(used);
    this.position -= used;
    while (this.out.length >= this.frameSize) {
      const frame = this.out.splice(0, this.frameSize);
      const pcm = new Int16Array(frame.length);
      for (let i = 0; i < frame.length; i++) {
        const s = Math.max(-1, Math.min(1, frame[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("capture", Capture);
