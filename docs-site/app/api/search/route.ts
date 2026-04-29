import { source } from "@/lib/source";
import { createFromSource } from "fumadocs-core/search/server";

// Orama-backed search — index built at app start from content/docs.
// Self-hosted, free, scales to thousands of pages without paying for
// Algolia. The cmdk-style search dialog in the nav uses this route.
export const { GET } = createFromSource(source);
