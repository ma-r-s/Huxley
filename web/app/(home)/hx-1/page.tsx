import type { Metadata } from "next";
import { HX1Page } from "@/landing/hx1/HX1Page";

export const metadata: Metadata = {
  title: "HX-1",
  description:
    "HX-1, the push-to-talk walkie Huxley's first persona runs on: hold the lever to talk, let go to hear the answer. 3D printed, with an ESP32-S3, a 5 W speaker and two 18650 cells.",
};

export default function Page() {
  return <HX1Page />;
}
