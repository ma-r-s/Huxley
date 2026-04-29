import { redirect } from "next/navigation";

// The docs sub-app's root — visitors who hit /docs (the rewrite target)
// land here. Send them to the welcome page so the docs always have a
// canonical entry point. The rewrite from site/vercel.json maps
// huxley.dev/docs → here.
export default function HomePage() {
  redirect("/docs/welcome");
}
