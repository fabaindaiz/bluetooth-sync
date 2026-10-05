// Reading the device's QR from the page itself (2026-10-04): the camera with `getUserMedia` and the
// browser's own `BarcodeDetector` (Chrome on Android and desktop; not Safari or Firefox), so no QR
// library is shipped. Where the browser has no detector, it says to use the phone's camera app: the
// QR is a link that opens this page with the device in it (link.ts).
import { useEffect, useRef, useState } from "preact/hooks";

interface Detector {
  detect(source: HTMLVideoElement): Promise<{ rawValue: string }[]>;
}

type DetectorClass = new (options: { formats: string[] }) => Detector;

const EVERY_MS = 250;

export function qrSupported(): boolean {
  return typeof (globalThis as { BarcodeDetector?: unknown }).BarcodeDetector === "function" && Boolean(navigator.mediaDevices?.getUserMedia);
}

export function QrScan({ onResult }: { onResult: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!open) return;
    let stream: MediaStream | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;
    const Detector = (globalThis as { BarcodeDetector?: DetectorClass }).BarcodeDetector as DetectorClass;
    const detector = new Detector({ formats: ["qr_code"] });
    const stop = (): void => {
      alive = false;
      if (timer !== null) clearTimeout(timer);
      for (const track of stream?.getTracks() ?? []) track.stop();
    };
    const look = async (): Promise<void> => {
      if (!alive || !video.current) return;
      try {
        const [first] = await detector.detect(video.current);
        if (first && alive) {
          stop();
          setOpen(false);
          onResult(first.rawValue);
          return;
        }
      } catch {
        // a frame that is not ready yet: try the next one
      }
      timer = setTimeout(() => void look(), EVERY_MS);
    };
    void navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(async (s) => {
        stream = s;
        if (!alive || !video.current) {
          stop();
          return;
        }
        video.current.srcObject = s;
        await video.current.play();
        void look();
      })
      .catch(() => {
        setNote("No se pudo usar la cámara (¿se negó el permiso?). Escribí el código de conexión.");
        setOpen(false);
      });
    return stop;
  }, [open]);

  return (
    <>
      <button
        type="button"
        class="btn btn-ghost"
        data-qr-scan="1"
        onClick={() => {
          setNote(null);
          if (qrSupported()) setOpen(true);
          else setNote("Este navegador no lee códigos QR desde la página: abrí la cámara del teléfono y apuntá al QR, o escribí el código de conexión.");
        }}
      >
        Escanear QR
      </button>
      {note && (
        <p class="muted small w-full" role="status" data-qr-note="1">
          {note}
        </p>
      )}
      {open && (
        <div class="w-full" data-qr-view="1">
          <video ref={video} class="w-full max-w-sm rounded-xl" muted playsInline aria-label="Vista de la cámara" />
          <button type="button" class="btn btn-ghost mt-1" onClick={() => setOpen(false)}>
            Cancelar
          </button>
        </div>
      )}
    </>
  );
}
