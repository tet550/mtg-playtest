// 録画（再生の画面）: 今の位置から自動再生し、盤面を 15 秒か 30 秒の動画にして保存する（X などに貼る用）。
// このタブの共有（getDisplayMedia）を盤面の要素に切り抜き（Region Capture。Log・ヘッダーは入らない）、MediaRecorder で
// MP4（録れなければ WebM）に録る。Chrome / Edge で動く。再生が最後まで行ったら、最後の盤面を少し見せてから止める
import { $ } from "./dom.js";
import { t } from "./i18n.js";

// avc3 を先に: 録っている間に盤面の大きさが変わっても（Log の開閉・窓の大きさ）、avc1 と違って壊れない（Chrome の勧め）
const TYPES = ["video/mp4;codecs=avc3.640028", "video/mp4;codecs=avc1.640028", "video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"];
const SETTLE_MS = 500;  // 共有を始めてから録り始めるまで（最初のコマが黒くならないように・共有中のバーで盤面の大きさが変わる）
const TAIL_MS = 1500;  // 再生が止まってから録り続ける長さ
const BLOCKED_MS = 500;  // これより早く断られたら、共有の確認を出さずに断った（ブラウザが許していない）とみなす

// ブラウザが画面の共有を許していない（Claude のブラウザペインなど）。人が確認でやめたときと同じ NotAllowedError なので、
// 許可の状態（分かるブラウザだけ）で見分ける
async function blocked() {
  try { return (await navigator.permissions.query({ name: "display-capture" })).state === "denied"; } catch (_) { return false; }
}

export const canRecord = () => !!(navigator.mediaDevices?.getDisplayMedia && window.MediaRecorder);

// ui: 盤面の状態（pos・cursor・playing）。startPlay / stopPlay: 自動再生。fileName(from, ext): 保存するファイルの名前（from は録り始めの位置）
export function createRecorder({ ui, target, startPlay, stopPlay, toast, fileName }) {
  let rec = null;  // 録っている間: { recorder, stream, timer, begin, seconds }

  const button = $("rec");
  const label = () => {
    button.classList.toggle("on", !!rec);
    if (!rec) {
      button.textContent = t("rec.button");
      button.title = t("rec.button.title");
      return;
    }
    const left = Math.max(0, Math.ceil(rec.seconds - (performance.now() - rec.begin) / 1000));
    button.textContent = t("rec.recording", { sec: left });
    button.title = t("rec.recording.title");
  };

  // 録画の選択（15 秒・30 秒）。今の速さで何回進めるかを出す
  async function ask() {
    if (await blocked()) return toast(t("rec.blocked"), true);
    const speed = $("speed");
    const interval = Number(speed.value);
    const steps = (sec) => Math.max(1, Math.floor((sec * 1000) / interval));
    const atEnd = ui.pos >= ui.cursor;
    $("recHint").textContent = t(atEnd ? "rec.hint.restart" : "rec.hint", {
      pos: ui.pos, cursor: ui.cursor, speed: speed.selectedOptions[0].textContent,
      s15: steps(15), s30: steps(30), left: ui.cursor - ui.pos });
    $("recDialog").showModal();
  }

  async function start(seconds) {
    $("recDialog").close();
    let stream;
    const asked = performance.now();
    try {
      // preferCurrentTab: 共有の選択に、このタブだけを出す（Chrome）
      stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 30 }, audio: false,
        preferCurrentTab: true, selfBrowserSurface: "include", surfaceSwitching: "exclude" });
    } catch (e) {
      if (e.name !== "NotAllowedError") toast(t("rec.failed", { message: e.message }), true);
      else if (performance.now() - asked < BLOCKED_MS) toast(t("rec.blocked"), true);  // 確認を出さずに断られた
      return;
    }
    const [track] = stream.getVideoTracks();
    try {
      if (window.CropTarget && track.cropTo) await track.cropTo(await CropTarget.fromElement(target));
    } catch (_) { /* 切り抜けない（別のタブ・画面を選んだ）ときは、選んだものをそのまま録る */ }
    const mimeType = TYPES.find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 8_000_000 });
    const chunks = [];
    const from = ui.pos >= ui.cursor ? 0 : ui.pos;  // 最後にいれば、自動再生は最初から
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      const type = recorder.mimeType || mimeType || "video/webm";
      const name = fileName(from, type.startsWith("video/mp4") ? "mp4" : "webm");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(chunks, { type }));
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
      toast(t("rec.saved", { file: name }));
    };
    track.addEventListener("ended", () => finish());  // ブラウザの「共有を停止」
    rec = { recorder, stream, timer: null, begin: 0, seconds };
    label();
    await new Promise((r) => setTimeout(r, SETTLE_MS));
    if (!rec) return;
    recorder.start(1000);
    rec.begin = performance.now();
    startPlay();
    let stoppedAt = null;
    rec.timer = setInterval(() => {
      const now = performance.now();
      if (!ui.playing) stoppedAt ??= now;
      if (now - rec.begin >= seconds * 1000 || (stoppedAt !== null && now - stoppedAt >= TAIL_MS)) finish();
      else label();
    }, 100);
  }

  function finish() {
    if (!rec) return;
    const { recorder, stream, timer } = rec;
    rec = null;
    clearInterval(timer);
    stopPlay();
    if (recorder.state !== "inactive") recorder.stop();
    stream.getTracks().forEach((tr) => tr.stop());
    label();
  }

  button.onclick = () => (rec ? finish() : ask());
  for (const b of document.querySelectorAll("#recDialog [data-sec]")) b.onclick = () => start(Number(b.dataset.sec));

  return { relabel: label, recording: () => !!rec, stop: finish };
}
