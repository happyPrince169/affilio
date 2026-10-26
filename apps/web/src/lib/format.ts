/** Display helpers shared across the media UI. */

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "–";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function formatBitrate(bitsPerSecond: number | null | undefined): string {
  if (!bitsPerSecond) return "–";
  const mbps = bitsPerSecond / 1_000_000;
  return mbps >= 1 ? `${mbps.toFixed(1)} Mbps` : `${Math.round(bitsPerSecond / 1000)} kbps`;
}

export function formatResolution(width: number | null, height: number | null): string {
  if (!width || !height) return "–";
  return `${width}×${height}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "–";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "–";
  const diff = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return rtf.format(diff, "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function formatElapsed(start: string | null, end: string | null): string {
  if (!start) return "–";
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(from) || Number.isNaN(to)) return "–";
  return formatDuration(Math.max(0, (to - from) / 1000));
}

export function codecLabel(codec: string | null | undefined): string {
  if (!codec) return "–";
  const map: Record<string, string> = {
    h264: "H.264",
    hevc: "HEVC",
    av1: "AV1",
    vp9: "VP9",
    prores: "ProRes",
    mpeg4: "MPEG-4",
    aac: "AAC",
    pcm_s16le: "PCM",
    mp3: "MP3",
    opus: "Opus",
  };
  return map[codec] ?? codec.toUpperCase();
}

export function formatMoney(
  amount: string | number | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount === null || amount === undefined || amount === "") return "–";
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (Number.isNaN(value)) return "–";
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        maximumFractionDigits: currency === "VND" ? 0 : 2,
      }).format(value);
    } catch {
      // Unknown ISO code: fall through to plain number.
    }
  }
  return `${value.toLocaleString()}${currency ? ` ${currency}` : ""}`;
}

export function formatPercent(rate: string | number | null | undefined): string {
  if (rate === null || rate === undefined || rate === "") return "–";
  const value = typeof rate === "string" ? Number(rate) : rate;
  if (Number.isNaN(value)) return "–";
  return `${(value * 100).toFixed((value * 100) % 1 === 0 ? 0 : 2)}%`;
}
