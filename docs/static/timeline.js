// JSON の時系列を復元する。DOM・通信・選択中の対局には依存しない。
export class Timeline {
  constructor({ cursor, frames }) {
    this.cursor = cursor;
    this.frames = frames;
    this.cache = new Map();
  }

  at(position) {
    const pos = Math.max(0, Math.min(position, this.cursor));
    if (this.cache.has(pos)) return this.cache.get(pos);
    let base = pos;
    while (!this.cache.has(base) && !this.frames[base].k) base--;
    let view = structuredClone(this.cache.get(base) ?? this.frames[base].k);
    for (let i = base + 1; i <= pos; i++) {
      for (const [operation, path, value] of this.frames[i].d) {
        if (!path.length) {
          view = structuredClone(value);
          continue;
        }
        let target = view;
        for (const key of path.slice(0, -1)) target = target[key];
        const key = path[path.length - 1];
        if (operation === "s") target[key] = structuredClone(value);
        else delete target[key];
      }
    }
    this.cache.set(pos, view);
    if (this.cache.size > 64) this.cache.delete(this.cache.keys().next().value);
    return view;
  }
}
