/**
 * Recorder — thin wrapper around MediaRecorder for capturing audio blobs.
 *
 * Usage:
 *   const stream = await navigator.mediaDevices.getUserMedia({audio: true});
 *   const rec = new Recorder(stream);
 *   rec.start();
 *   // ... later ...
 *   const blob = await rec.stop();   // returns an audio Blob
 */
class Recorder {
  constructor(stream) {
    this._stream = stream;
    this._chunks = [];
    this._mr = null;
    this._resolve = null;
  }

  start() {
    this._chunks = [];
    // Try codecs in order: webm/opus (Chrome), ogg/opus (Firefox), then default
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/ogg",
    ];
    let mimeType = "";
    for (const c of candidates) {
      if (MediaRecorder.isTypeSupported(c)) { mimeType = c; break; }
    }
    const opts = mimeType ? { mimeType } : {};
    this._mr = new MediaRecorder(this._stream, opts);
    this._mr.ondataavailable = (e) => {
      if (e.data.size > 0) this._chunks.push(e.data);
    };
    this._mr.onstop = () => {
      const blob = new Blob(this._chunks, { type: this._mr.mimeType });
      this._stream.getTracks().forEach(t => t.stop());
      if (this._resolve) this._resolve(blob);
    };
    this._mr.start();
    console.log("Recorder started, mimeType:", this._mr.mimeType);
  }

  stop() {
    return new Promise((resolve) => {
      this._resolve = resolve;
      if (this._mr && this._mr.state !== "inactive") {
        this._mr.stop();
      } else {
        resolve(new Blob([], { type: "audio/webm" }));
      }
    });
  }
}
