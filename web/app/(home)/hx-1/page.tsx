import type { Metadata } from "next";
import { HX1Page } from "@/landing/hx1/HX1Page";

const description =
  "HX-1, the push-to-talk walkie Huxley's first persona speaks through: hold the lever to talk, let go to hear the answer. 3D printed around an ESP32-S3 audio board, a 40 mm speaker and two 18650 cells.";

export const metadata: Metadata = {
  title: "HX-1",
  description,
  openGraph: { title: "HX-1 · huxley", description, images: [{ url: "/og-image.png", width: 1200, height: 630 }] },
  twitter: { card: "summary_large_image", title: "HX-1 · huxley", description, images: ["/og-image.png"] },
};

export default function Page() {
  return <HX1Page />;
}
