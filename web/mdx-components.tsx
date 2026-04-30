import defaultMdxComponents from "fumadocs-ui/mdx";
import * as TabsComponents from "fumadocs-ui/components/tabs";
import { Callout } from "fumadocs-ui/components/callout";
import { Steps, Step } from "fumadocs-ui/components/steps";
import { File, Files, Folder } from "fumadocs-ui/components/files";
import { TypeTable } from "fumadocs-ui/components/type-table";
import { Accordion, Accordions } from "fumadocs-ui/components/accordion";
import { Card, Cards } from "fumadocs-ui/components/card";
import { Mermaid } from "@/components/mermaid";
import type { MDXComponents } from "mdx/types";

// All custom components available inside MDX without per-file imports.
// Adding a new primitive? Drop it here and every page can use it.
export function getMDXComponents(components?: MDXComponents): MDXComponents {
  return {
    ...defaultMdxComponents,
    ...TabsComponents,
    Callout,
    Steps,
    Step,
    File,
    Files,
    Folder,
    TypeTable,
    Accordion,
    Accordions,
    Card,
    Cards,
    Mermaid,
    ...components,
  };
}
