import type { ReactNode } from "react";
import { HomeLayout } from "fumadocs-ui/layouts/home";
import { baseOptions } from "@/lib/layout.shared";

// Layout for the docs root (the / inside the docs sub-app, which lives at
// huxley.dev/docs once rewrites are in place). Just the nav bar — no
// sidebar — for the landing/index page.
export default function Layout({ children }: { children: ReactNode }) {
  return <HomeLayout {...baseOptions()}>{children}</HomeLayout>;
}
